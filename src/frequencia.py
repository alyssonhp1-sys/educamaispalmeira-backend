"""Chamada diária e agregação de faltas.

Compatibilidade com RegistroFalta (não destrutiva):
- RegistroFrequencia é a fonte detalhada (um lançamento por matrícula/disciplina/data).
- Matricula é o contexto acadêmico principal da frequência do aluno.
- RegistroAula permanece fato da Turma (sem matricula_id).
- aluno_id e turma_id ficam na transição e devem coincidir com a matrícula.
- RegistroFalta permanece intacto: a chamada NUNCA apaga nem reescreve faltas bimestrais.
- Novos lançamentos via RegistroAula: só a frequência de aulas VALIDADAS entra no total.
- Chamada legada (sem registro_aula_id) continua contando como antes.
- Se houver fonte diária no bimestre, não se mistura com RegistroFalta.
- Se não houver chamada/aula naquele bimestre, usa-se RegistroFalta.quantidade (legado).
- O total anual do boletim/relatório é a soma desses totais bimestrais.
- O cálculo de médias/notas em src.notas permanece inalterado.
"""
from sqlalchemy import and_, or_, text
from sqlalchemy.orm import joinedload

from src import db
from src.alunos import aluno_esta_ativo, parse_data
from src.models import Alocacao, Matricula, RegistroFalta, RegistroFrequencia

PRESENTE = "PRESENTE"
FALTA = "FALTA"
SITUACOES = frozenset({PRESENTE, FALTA})

MSG_FREQUENCIA_INCONSISTENTE = (
    "O aluno ou a turma da frequência não correspondem à matrícula informada."
)

# Último backfill de frequência (diagnósticos). Não é persistido.
ULTIMO_DIAGNOSTICO_BACKFILL_FREQUENCIA = []
ULTIMO_RESUMO_BACKFILL_FREQUENCIA = {
    "associadas": 0,
    "nao_associadas": 0,
    "sem_matricula": 0,
    "ambiguas": 0,
    "preservadas": 0,
    "duplicidades": 0,
    "indice_unico": False,
}


def disciplina_armazenada(turma, disciplina_id):
    if turma is not None and turma.etapa_ensino == "infantil":
        return None
    if disciplina_id in (None, 0):
        return None
    return int(disciplina_id)


def normalizar_situacao(valor):
    texto = str(valor or "").strip().upper()
    if texto in SITUACOES:
        return texto
    return None


def normalizar_bimestre(valor):
    try:
        bim = int(valor)
    except (TypeError, ValueError):
        return None
    if bim < 1 or bim > 4:
        return None
    return bim


def alunos_ativos_da_turma(turma):
    return [a for a in turma.alunos if aluno_esta_ativo(a)]


def query_chamada(turma_id, disciplina_id, data=None, aluno_id=None):
    """Lista a chamada da aula/turma. Contexto legítimo de RegistroAula/Turma."""
    q = RegistroFrequencia.query.filter_by(turma_id=turma_id)
    if disciplina_id is None:
        q = q.filter(RegistroFrequencia.disciplina_id.is_(None))
    else:
        q = q.filter_by(disciplina_id=disciplina_id)
    if data is not None:
        q = q.filter_by(data=data)
    if aluno_id is not None:
        q = q.filter_by(aluno_id=aluno_id)
    return q


def query_frequencia_matricula(matricula, disciplina_id=None):
    """Leitura acadêmica por matrícula, com fallback legado explícito e limitado.

    Inclui:
    - registros já associados (matricula_id);
    - legado com matricula_id NULL do mesmo aluno_id e turma_id da matrícula.

    Não inclui frequência de outra turma/ano (outro matricula_id ou outra turma).
    """
    if matricula is None:
        return RegistroFrequencia.query.filter_by(id=-1)
    q = RegistroFrequencia.query.filter(
        or_(
            RegistroFrequencia.matricula_id == matricula.id,
            and_(
                RegistroFrequencia.matricula_id.is_(None),
                RegistroFrequencia.aluno_id == matricula.aluno_id,
                RegistroFrequencia.turma_id == matricula.turma_id,
            ),
        )
    )
    if disciplina_id in (None, 0):
        q = q.filter(RegistroFrequencia.disciplina_id.is_(None))
    else:
        q = q.filter_by(disciplina_id=disciplina_id)
    return q


def registro_do_dia(aluno_id, turma_id, disciplina_id, data, matricula_id=None):
    if matricula_id is not None:
        q = RegistroFrequencia.query.filter_by(matricula_id=matricula_id, data=data)
        if disciplina_id is None:
            q = q.filter(RegistroFrequencia.disciplina_id.is_(None))
        else:
            q = q.filter_by(disciplina_id=disciplina_id)
        encontrado = q.filter(RegistroFrequencia.registro_aula_id.is_(None)).first()
        if encontrado is not None:
            return encontrado
    return query_chamada(turma_id, disciplina_id, data=data, aluno_id=aluno_id).filter(
        RegistroFrequencia.registro_aula_id.is_(None)
    ).first()


def alocacao_do_professor(professor_id, turma, disciplina_id):
    q = Alocacao.query.filter_by(professor_id=professor_id, turma_id=turma.id)
    if turma.etapa_ensino != "infantil" and disciplina_id not in (None, 0):
        q = q.filter_by(disciplina_id=disciplina_id)
    return q.first()


def resolver_matricula_da_chamada(aluno_id, turma_id):
    """Matrícula daquele aluno NAQUELA turma, preferindo vínculo cursando.

    Não usa a matrícula mais recente, o atalho atual nem o AnoLetivo ativo.
    Se houver zero ou mais de uma candidata válida, retorna None.
    """
    from src.matriculas import VINCULO_CURSANDO

    if aluno_id is None or turma_id is None:
        return None
    mats = Matricula.query.filter_by(
        aluno_id=int(aluno_id), turma_id=int(turma_id),
    ).all()
    if not mats:
        return None
    cursando = [m for m in mats if (m.situacao or "") == VINCULO_CURSANDO]
    if len(cursando) == 1:
        return cursando[0]
    if len(cursando) > 1:
        return None
    return None


def garantir_consistencia_frequencia(matricula, aluno_id, turma_id):
    """aluno_id e turma_id do lançamento devem ser os da matrícula."""
    from src.matriculas import garantir_aluno_da_matricula

    if matricula is None:
        raise ValueError("Matrícula é obrigatória.")
    garantir_aluno_da_matricula(aluno_id, matricula)
    if turma_id is None or int(turma_id) != int(matricula.turma_id):
        raise ValueError(MSG_FREQUENCIA_INCONSISTENTE)
    return matricula


def aplicar_frequencia(
    matricula,
    disciplina_id,
    data,
    bimestre,
    situacao,
    professor_id=None,
    alocacao_id=None,
    registro_aula_id=None,
    aluno_id=None,
    turma_id=None,
):
    """Cria ou atualiza frequência da matrícula. Recusa inconsistência. Não faz commit.

    Unicidade de aplicação: matrícula + disciplina + data + registro_aula_id.
    registro_aula_id NULL (chamada legada) não recebe constraint de banco.
    """
    if matricula is None:
        raise ValueError("Matrícula é obrigatória.")
    aluno_ref = aluno_id if aluno_id is not None else matricula.aluno_id
    turma_ref = turma_id if turma_id is not None else matricula.turma_id
    garantir_consistencia_frequencia(matricula, aluno_ref, turma_ref)

    q = RegistroFrequencia.query.filter_by(matricula_id=matricula.id, data=data)
    if disciplina_id is None:
        q = q.filter(RegistroFrequencia.disciplina_id.is_(None))
    else:
        q = q.filter_by(disciplina_id=disciplina_id)
    if registro_aula_id is None:
        q = q.filter(RegistroFrequencia.registro_aula_id.is_(None))
    else:
        q = q.filter_by(registro_aula_id=registro_aula_id)
    existente = q.first()
    if existente is None and registro_aula_id is None:
        existente = registro_do_dia(
            matricula.aluno_id, matricula.turma_id, disciplina_id, data,
        )

    if existente:
        if existente.matricula_id not in (None, matricula.id):
            raise ValueError(MSG_FREQUENCIA_INCONSISTENTE)
        garantir_consistencia_frequencia(matricula, existente.aluno_id, existente.turma_id)
        situacao_anterior = existente.situacao
        existente.situacao = situacao
        existente.bimestre = bimestre
        existente.matricula_id = matricula.id
        existente.aluno_id = matricula.aluno_id
        existente.turma_id = matricula.turma_id
        if professor_id is not None:
            existente.professor_id = professor_id
        if alocacao_id is not None:
            existente.alocacao_id = alocacao_id
        if (situacao_anterior or "").upper() != situacao:
            _auditar_frequencia(
                existente,
                matricula,
                situacao_anterior,
                situacao,
                criado=False,
            )
        return existente

    reg = RegistroFrequencia(
        aluno_id=matricula.aluno_id,
        turma_id=matricula.turma_id,
        matricula_id=matricula.id,
        disciplina_id=disciplina_id,
        professor_id=professor_id,
        alocacao_id=alocacao_id,
        registro_aula_id=registro_aula_id,
        data=data,
        bimestre=bimestre,
        situacao=situacao,
    )
    db.session.add(reg)
    db.session.flush()
    if situacao == FALTA:
        _auditar_frequencia(reg, matricula, None, situacao, criado=True)
    return reg


def _filtrar_disciplina_frequencia(q, disciplina_id):
    if disciplina_id in (None, 0):
        return q.filter(RegistroFrequencia.disciplina_id.is_(None))
    return q.filter_by(disciplina_id=disciplina_id)


def faltas_por_bimestre(aluno_id, disciplina_id, matricula_id=None):
    """Totais oficiais por bimestre: chamada/aula quando houver, senão RegistroFalta.

    Com matrícula: frequência diária por matricula_id; legado NULL só do mesmo
    aluno_id+turma_id daquela matrícula. Nunca soma outra turma/ano.
    """
    mat = None
    if matricula_id is None and aluno_id is not None:
        from src.matriculas import resolver_matricula_academica
        mat_ctx = resolver_matricula_academica(aluno_id)
        if mat_ctx is not None:
            matricula_id = mat_ctx.id
    if matricula_id is not None:
        mat = db.session.get(Matricula, matricula_id)
        if mat is not None and aluno_id is None:
            aluno_id = mat.aluno_id

    if mat is not None:
        q_dia = query_frequencia_matricula(mat, disciplina_id)
    else:
        q_dia = RegistroFrequencia.query.filter_by(aluno_id=aluno_id)
        q_dia = _filtrar_disciplina_frequencia(q_dia, disciplina_id)

    por_chamada = {}
    fonte_diaria = set()
    for reg in q_dia.options(joinedload(RegistroFrequencia.registro_aula)).all():
        bim = reg.bimestre
        if reg.registro_aula_id:
            aula = reg.registro_aula
            if aula is None:
                continue
            fonte_diaria.add(bim)
            por_chamada.setdefault(bim, 0)
            if (aula.status or "").lower() != "validada":
                continue
            if (reg.situacao or "").upper() == FALTA:
                por_chamada[bim] += 1
            continue
        fonte_diaria.add(bim)
        por_chamada.setdefault(bim, 0)
        if (reg.situacao or "").upper() == FALTA:
            por_chamada[bim] += 1

    if matricula_id is not None:
        q_leg = RegistroFalta.query.filter_by(matricula_id=matricula_id)
        if disciplina_id in (None, 0):
            q_leg = q_leg.filter(RegistroFalta.disciplina_id.is_(None))
        else:
            q_leg = q_leg.filter_by(disciplina_id=disciplina_id)
        por_legado = {f.bimestre: (f.quantidade or 0) for f in q_leg.all()}
        if not por_legado and aluno_id is not None:
            n_mats = Matricula.query.filter_by(aluno_id=aluno_id).count()
            if n_mats == 1:
                q_null = RegistroFalta.query.filter_by(aluno_id=aluno_id, matricula_id=None)
                if disciplina_id in (None, 0):
                    q_null = q_null.filter(RegistroFalta.disciplina_id.is_(None))
                else:
                    q_null = q_null.filter_by(disciplina_id=disciplina_id)
                por_legado = {f.bimestre: (f.quantidade or 0) for f in q_null.all()}
    else:
        q_leg = RegistroFalta.query.filter_by(aluno_id=aluno_id, matricula_id=None)
        if disciplina_id in (None, 0):
            q_leg = q_leg.filter(RegistroFalta.disciplina_id.is_(None))
        else:
            q_leg = q_leg.filter_by(disciplina_id=disciplina_id)
        por_legado = {f.bimestre: (f.quantidade or 0) for f in q_leg.all()}

    resultado = {}
    for bim in set(fonte_diaria) | set(por_legado):
        if bim in fonte_diaria:
            resultado[bim] = por_chamada.get(bim, 0)
        else:
            resultado[bim] = por_legado.get(bim, 0)
    return resultado


def total_faltas_efetivas(aluno_id, disciplina_id, matricula_id=None):
    return sum(faltas_por_bimestre(aluno_id, disciplina_id, matricula_id=matricula_id).values())


def _auditar_frequencia(reg, matricula, situacao_anterior, situacao_nova, criado):
    from src.auditoria import (
        FREQUENCIA_ALTERADA,
        FREQUENCIA_CRIADA,
        escola_id_da_matricula,
        iso_data,
        registrar_auditoria,
    )

    recorte = {
        "registro_aula_id": reg.registro_aula_id,
        "matricula_id": matricula.id,
        "disciplina_id": reg.disciplina_id,
        "data": iso_data(reg.data),
        "bimestre": reg.bimestre,
        "situacao": situacao_nova,
    }
    if criado:
        registrar_auditoria(
            FREQUENCIA_CRIADA,
            "RegistroFrequencia",
            entidade_id=reg.id,
            escola_id=escola_id_da_matricula(matricula),
            dados_anteriores=None,
            dados_novos={**recorte, "situacao_anterior": None, "situacao_nova": situacao_nova},
        )
        return
    registrar_auditoria(
        FREQUENCIA_ALTERADA,
        "RegistroFrequencia",
        entidade_id=reg.id,
        escola_id=escola_id_da_matricula(matricula),
        dados_anteriores={
            **recorte,
            "situacao": situacao_anterior,
            "situacao_anterior": situacao_anterior,
            "situacao_nova": situacao_nova,
        },
        dados_novos={
            **recorte,
            "situacao_anterior": situacao_anterior,
            "situacao_nova": situacao_nova,
        },
    )


def upsert_falta_matricula(matricula, disciplina_id, bimestre, quantidade):
    """Atualiza ou insere falta daquela matrícula/disciplina/bimestre. Não toca outros anos."""
    from src.auditoria import (
        FALTA_ALTERADA,
        FALTA_CRIADA,
        escola_id_da_matricula,
        registrar_auditoria,
        valores_iguais,
    )
    from src.matriculas import garantir_aluno_da_matricula

    garantir_aluno_da_matricula(matricula.aluno_id, matricula)
    existentes = RegistroFalta.query.filter_by(
        matricula_id=matricula.id,
        disciplina_id=disciplina_id,
        bimestre=bimestre,
    ).all()
    if existentes:
        atual = existentes[0]
        qtd_anterior = atual.quantidade
        for reg in existentes:
            garantir_aluno_da_matricula(reg.aluno_id, matricula)
            reg.quantidade = quantidade
            reg.aluno_id = matricula.aluno_id
        if valores_iguais(qtd_anterior, quantidade):
            return atual
        recorte = {
            "matricula_id": matricula.id,
            "disciplina_id": disciplina_id,
            "bimestre": int(bimestre),
            "quantidade": quantidade,
        }
        registrar_auditoria(
            FALTA_ALTERADA,
            "RegistroFalta",
            entidade_id=atual.id,
            escola_id=escola_id_da_matricula(matricula),
            dados_anteriores={**recorte, "quantidade": qtd_anterior},
            dados_novos=recorte,
        )
        return atual
    reg = RegistroFalta(
        aluno_id=matricula.aluno_id,
        matricula_id=matricula.id,
        disciplina_id=disciplina_id,
        bimestre=bimestre,
        quantidade=quantidade,
    )
    db.session.add(reg)
    db.session.flush()
    registrar_auditoria(
        FALTA_CRIADA,
        "RegistroFalta",
        entidade_id=reg.id,
        escola_id=escola_id_da_matricula(matricula),
        dados_anteriores=None,
        dados_novos={
            "matricula_id": matricula.id,
            "disciplina_id": disciplina_id,
            "bimestre": int(bimestre),
            "quantidade": quantidade,
        },
    )
    return reg


def parse_data_chamada(bruto):
    return parse_data(bruto)


def backfill_frequencias():
    """Associa RegistroFrequencia à matrícula só com aluno_id+turma_id inequívocos.

    Não cria, não apaga, não altera situação/data/bimestre/professor/disciplina
    /registro_aula_id/alocacao. Idempotente.
    """
    global ULTIMO_DIAGNOSTICO_BACKFILL_FREQUENCIA, ULTIMO_RESUMO_BACKFILL_FREQUENCIA
    diagnosticos = []
    associadas = 0
    sem = 0
    ambiguas = 0
    registros = RegistroFrequencia.query.filter(RegistroFrequencia.matricula_id.is_(None)).all()
    n_antes = RegistroFrequencia.query.count()
    for reg in registros:
        mats = Matricula.query.filter_by(
            aluno_id=reg.aluno_id, turma_id=reg.turma_id,
        ).all()
        if len(mats) == 1:
            reg.matricula_id = mats[0].id
            associadas += 1
            continue
        if not mats:
            sem += 1
            diagnosticos.append({
                "tipo": "sem_matricula",
                "registro": "frequencia",
                "registro_id": reg.id,
                "aluno_id": reg.aluno_id,
                "turma_id": reg.turma_id,
                "motivo": "zero_matriculas",
                "detalhe": (
                    "Frequência sem matrícula correspondente a aluno_id+turma_id; "
                    "matricula_id permanece nulo. Não foi atribuída à matrícula atual."
                ),
            })
            continue
        ambiguas += 1
        diagnosticos.append({
            "tipo": "ambiguo",
            "registro": "frequencia",
            "registro_id": reg.id,
            "aluno_id": reg.aluno_id,
            "turma_id": reg.turma_id,
            "motivo": "multiplas_matriculas",
            "detalhe": (
                "Mais de uma matrícula para o mesmo aluno_id+turma_id; "
                "matricula_id permanece nulo."
            ),
        })
    if associadas:
        db.session.commit()
    n_depois = RegistroFrequencia.query.count()
    ULTIMO_DIAGNOSTICO_BACKFILL_FREQUENCIA[:] = diagnosticos
    ULTIMO_RESUMO_BACKFILL_FREQUENCIA.update({
        "associadas": associadas,
        "nao_associadas": sem + ambiguas,
        "sem_matricula": sem,
        "ambiguas": ambiguas,
        "preservadas": n_antes,
        "duplicidades": ULTIMO_RESUMO_BACKFILL_FREQUENCIA.get("duplicidades", 0),
        "indice_unico": False,
    })
    if n_depois != n_antes:
        diagnosticos.append({
            "tipo": "contagem_divergente",
            "detalhe": "A contagem de frequência mudou durante o backfill.",
            "antes": n_antes,
            "depois": n_depois,
        })
    return diagnosticos


def auditar_duplicidades_frequencia():
    """Diagnostica duplicidade. Não apaga e não cria UNIQUE (legado com aula nula)."""
    global ULTIMO_DIAGNOSTICO_BACKFILL_FREQUENCIA, ULTIMO_RESUMO_BACKFILL_FREQUENCIA
    try:
        dups = list(db.session.execute(text(
            "SELECT matricula_id, disciplina_id, data, registro_aula_id, COUNT(*) AS n "
            "FROM registro_frequencia WHERE matricula_id IS NOT NULL "
            "GROUP BY matricula_id, disciplina_id, data, registro_aula_id "
            "HAVING COUNT(*) > 1"
        )))
    except Exception:
        dups = []
    ULTIMO_RESUMO_BACKFILL_FREQUENCIA["duplicidades"] = len(dups)
    ULTIMO_RESUMO_BACKFILL_FREQUENCIA["indice_unico"] = False
    if dups:
        ULTIMO_DIAGNOSTICO_BACKFILL_FREQUENCIA.append({
            "tipo": "duplicidade",
            "registro": "frequencia",
            "quantidade": len(dups),
            "detalhe": (
                "Duplicidade (matricula_id, disciplina_id, data, registro_aula_id); "
                "índice único não foi criado. registro_aula_id pode ser NULL no legado."
            ),
        })
    return ULTIMO_RESUMO_BACKFILL_FREQUENCIA
