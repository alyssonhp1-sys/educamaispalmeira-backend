"""Vínculo Aluno + Turma (Matricula).

Fonte de verdade na transição:
- Matricula: histórico do vínculo (pode haver várias ao longo dos anos).
- Aluno.turma_id: atalho de compatibilidade para a turma atual.

Se existe matrícula situacao="cursando", Aluno.turma_id deve apontar para essa turma.
Operações normais (cadastro enturmado, enturmar, encerrar, inativar) gravam os dois
atomicamente na mesma transação.

Divergências legadas NÃO são corrigidas em silêncio:
- atalho nulo com matrícula cursando: não cria outra matrícula;
- atalho na turma A e cursando na turma B: não cria segunda matrícula e não
  sobrescreve o atalho; fica diagnóstico.
- aluno com qualquer matrícula já existente: o backfill não inventa outra.

Uma única matrícula cursando por aluno é regra de aplicação (e testes).
UNIQUE(aluno_id, turma_id) impede duas linhas na mesma turma; não impede
duas matrículas no mesmo ano em turmas distintas (transferência).
Índices UNIQUE parciais de lançamentos vêm do Alembic (não deste backfill).

Matricula.situacao é a situação do VÍNCULO, distinta de Aluno.situacao (cadastro).
HistoricoAnual.situacao_final é coluna legada cadastral; o resultado acadêmico
por disciplina fica no JSON do snapshot (não há regra global oficial).
"""
from datetime import date

from sqlalchemy.exc import IntegrityError

from src import db
from src.models import Aluno, Matricula, RegistroFalta, RegistroNota, Turma

VINCULO_CURSANDO = "cursando"
VINCULO_CONCLUIDA = "concluida"
VINCULO_TRANSFERIDO = "transferido"
VINCULO_INATIVO = "inativo"
SITUACOES_VINCULO = frozenset({
    VINCULO_CURSANDO,
    VINCULO_CONCLUIDA,
    VINCULO_TRANSFERIDO,
    VINCULO_INATIVO,
})

MSG_JA_CURSANDO = (
    "Este aluno já possui matrícula em curso. "
    "Encerre ou inative o vínculo atual antes de uma nova enturmação."
)
MSG_JA_NESTA_TURMA = "Este aluno já possui matrícula nesta turma."
MSG_TURMA_INATIVA = "Só é possível enturmar em turma ativa."
MSG_SEM_MATRICULA_CURSANDO = (
    "Não há matrícula em curso deste aluno nesta turma. "
    "Nenhum lançamento foi gravado."
)
MSG_ALUNO_MATRICULA_DIVERGENTE = (
    "O aluno do registro não corresponde à matrícula informada."
)

# Último backfill (diagnósticos de casos não inequívocos). Não é persistido.
ULTIMO_DIAGNOSTICO_BACKFILL = []
ULTIMO_DIAGNOSTICO_BACKFILL_REGISTROS = []
ULTIMO_RESUMO_BACKFILL_REGISTROS = {
    "notas_associadas": 0,
    "faltas_associadas": 0,
    "notas_ambiguas": 0,
    "faltas_ambiguas": 0,
    "notas_preservadas": 0,
    "faltas_preservadas": 0,
    "duplicidades_nota": 0,
    "duplicidades_falta": 0,
    "indice_unico_nota": False,
    "indice_unico_falta": False,
}


def matriculas_cursando(aluno):
    if aluno is None or aluno.id is None:
        return []
    return Matricula.query.filter_by(
        aluno_id=aluno.id, situacao=VINCULO_CURSANDO,
    ).all()


def matricula_cursando(aluno):
    vigentes = matriculas_cursando(aluno)
    return vigentes[0] if vigentes else None


def matricula_cursando_na_turma(aluno_id, turma_id):
    """Vínculo cursando daquele aluno NAQUELA turma — não qualquer matrícula vigente."""
    if aluno_id is None or turma_id is None:
        return None
    return Matricula.query.filter_by(
        aluno_id=int(aluno_id),
        turma_id=int(turma_id),
        situacao=VINCULO_CURSANDO,
    ).first()


def matriculas_da_turma(turma_id):
    """Todas as matrículas da turma (leitura histórica inclusive após encerrar)."""
    if turma_id is None:
        return []
    return (
        Matricula.query.filter_by(turma_id=int(turma_id))
        .join(Aluno, Matricula.aluno_id == Aluno.id)
        .order_by(Aluno.nome.asc(), Matricula.id.asc())
        .all()
    )


def resolver_matricula_academica(aluno_id):
    """Matrícula para boletim/compatibilidade: cursando única, senão atalho, senão única.

    Não escolhe a mais recente nem 'qualquer cursando' quando há ambiguidade.
    """
    if aluno_id is None:
        return None
    aluno = db.session.get(Aluno, int(aluno_id))
    if aluno is None:
        return None
    vigentes = matriculas_cursando(aluno)
    if len(vigentes) == 1:
        return vigentes[0]
    if aluno.turma_id:
        na_turma = Matricula.query.filter_by(
            aluno_id=aluno.id, turma_id=aluno.turma_id,
        ).first()
        if na_turma is not None:
            return na_turma
    todas = Matricula.query.filter_by(aluno_id=aluno.id).all()
    if len(todas) == 1:
        return todas[0]
    return None


def garantir_aluno_da_matricula(aluno_id, matricula):
    """aluno_id do lançamento deve ser o da matrícula. Não mistura João com Maria."""
    if matricula is None:
        raise ValueError("Matrícula é obrigatória.")
    if aluno_id is None or int(aluno_id) != int(matricula.aluno_id):
        raise ValueError(MSG_ALUNO_MATRICULA_DIVERGENTE)
    return matricula


def contar_matriculas_cursando(turma):
    if turma is None or turma.id is None:
        return 0
    return Matricula.query.filter_by(
        turma_id=turma.id, situacao=VINCULO_CURSANDO,
    ).count()


def diagnostico_consistencia_aluno(aluno):
    """Lista problemas de sincronia atalho × matrícula. Não altera dados."""
    if aluno is None:
        return []
    issues = []
    vigentes = matriculas_cursando(aluno)
    if len(vigentes) > 1:
        issues.append({
            "aluno_id": aluno.id,
            "tipo": "multiplas_cursando",
            "detalhe": "Aluno possui mais de uma matrícula cursando.",
            "turma_ids": [m.turma_id for m in vigentes],
        })
    if not vigentes and aluno.turma_id:
        issues.append({
            "aluno_id": aluno.id,
            "tipo": "atalho_sem_cursando",
            "turma_id_atalho": aluno.turma_id,
            "detalhe": "Aluno.turma_id preenchido sem matrícula cursando.",
        })
    if vigentes and aluno.turma_id is None:
        issues.append({
            "aluno_id": aluno.id,
            "tipo": "cursando_sem_atalho",
            "turma_id_cursando": vigentes[0].turma_id,
            "detalhe": "Existe matrícula cursando e Aluno.turma_id está vazio.",
        })
    if len(vigentes) == 1 and aluno.turma_id and aluno.turma_id != vigentes[0].turma_id:
        issues.append({
            "aluno_id": aluno.id,
            "tipo": "divergencia_atalho_cursando",
            "turma_id_atalho": aluno.turma_id,
            "turma_id_cursando": vigentes[0].turma_id,
            "detalhe": "Atalho e matrícula cursando apontam turmas diferentes.",
        })
    return issues


def abrir_matricula_cursando(aluno, turma, data_inicio=None):
    """Cria matrícula cursando e atualiza o atalho. Não faz commit.

    Retorna mensagem de erro ou None. O chamador commita a transação junto
    com as demais alterações (cadastro/enturmação).
    """
    if aluno is None or turma is None:
        return "Aluno e turma são obrigatórios."
    if (turma.status or "").lower() != "ativa":
        return MSG_TURMA_INATIVA
    if aluno.id is None:
        db.session.flush()
    if matricula_cursando(aluno):
        return MSG_JA_CURSANDO
    if Matricula.query.filter_by(aluno_id=aluno.id, turma_id=turma.id).first():
        return MSG_JA_NESTA_TURMA
    db.session.add(Matricula(
        aluno_id=aluno.id,
        turma_id=turma.id,
        data_inicio=data_inicio or date.today(),
        data_fim=None,
        situacao=VINCULO_CURSANDO,
    ))
    aluno.turma_id = turma.id
    return None


def encerrar_vinculo_vigente(aluno, nova_situacao, data_fim=None):
    """Fecha matrícula(s) cursando. Não altera Aluno.situacao cadastral."""
    if nova_situacao not in SITUACOES_VINCULO or nova_situacao == VINCULO_CURSANDO:
        raise ValueError("Situação de encerramento de vínculo inválida.")
    data_fim = data_fim or date.today()
    for m in matriculas_cursando(aluno):
        m.situacao = nova_situacao
        m.data_fim = data_fim


def concluir_matriculas_da_turma(turma, data_fim=None):
    """Fecha vínculos cursando da turma (encerramento). Não grava HistoricoAnual."""
    if turma is None or turma.id is None:
        return
    data_fim = data_fim or date.today()
    for m in Matricula.query.filter_by(
        turma_id=turma.id, situacao=VINCULO_CURSANDO,
    ).all():
        m.situacao = VINCULO_CONCLUIDA
        m.data_fim = data_fim
        if m.aluno is not None and m.aluno.turma_id == turma.id:
            m.aluno.turma_id = None


def payload_historico_matricula(matricula):
    turma = matricula.turma
    escola = turma.escola if turma else None
    return {
        "id": matricula.id,
        "turma_id": matricula.turma_id,
        "turma_nome": turma.nome if turma else None,
        "escola": escola.nome if escola else None,
        "ano_letivo": turma.ano_letivo if turma else None,
        "situacao": matricula.situacao,
        "data_inicio": matricula.data_inicio.isoformat() if matricula.data_inicio else None,
        "data_fim": matricula.data_fim.isoformat() if matricula.data_fim else None,
    }


def listar_historico_matriculas(aluno):
    itens = (
        Matricula.query.filter_by(aluno_id=aluno.id)
        .order_by(Matricula.data_inicio.asc(), Matricula.id.asc())
        .all()
    )
    return [payload_historico_matricula(m) for m in itens]


def _turma_compativel_com_escola(aluno, turma):
    if aluno.escola_id is None:
        return True
    return aluno.escola_id == turma.escola_id


def backfill_matriculas_atuais():
    """Cria matrícula cursando só para o vínculo ATUAL inequívoco.

    Seguro quando: Aluno.turma_id existe, a Turma existe, a escola coincide
    e o aluno ainda não tem nenhuma matrícula.

    Não reconstrói histórico a partir de notas nem de HistoricoAnual.
    Idempotente: UNIQUE(aluno_id, turma_id) + checagem prévia.
    """
    global ULTIMO_DIAGNOSTICO_BACKFILL
    diagnosticos = []
    criadas = 0
    alunos = Aluno.query.filter(Aluno.turma_id.isnot(None)).all()
    for aluno in alunos:
        if Matricula.query.filter_by(aluno_id=aluno.id).first():
            diagnosticos.extend(diagnostico_consistencia_aluno(aluno))
            continue
        turma = db.session.get(Turma, aluno.turma_id)
        if turma is None:
            diagnosticos.append({
                "aluno_id": aluno.id,
                "tipo": "turma_inexistente",
                "turma_id_atalho": aluno.turma_id,
                "detalhe": "Atalho aponta turma inexistente; matrícula não foi criada.",
            })
            continue
        if not _turma_compativel_com_escola(aluno, turma):
            diagnosticos.append({
                "aluno_id": aluno.id,
                "tipo": "escola_divergente",
                "escola_id_aluno": aluno.escola_id,
                "escola_id_turma": turma.escola_id,
                "detalhe": "Aluno e turma de escolas diferentes; matrícula não foi criada.",
            })
            continue
        db.session.add(Matricula(
            aluno_id=aluno.id,
            turma_id=turma.id,
            data_inicio=date.today(),
            data_fim=None,
            situacao=VINCULO_CURSANDO,
        ))
        criadas += 1
    if criadas:
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
    for aluno in Aluno.query.filter(Aluno.turma_id.is_(None)).all():
        if matricula_cursando(aluno):
            diagnosticos.extend(diagnostico_consistencia_aluno(aluno))
    ULTIMO_DIAGNOSTICO_BACKFILL = diagnosticos
    return diagnosticos


def _matricula_inequivoca_do_aluno(aluno_id):
    """Só retorna matrícula quando não há como errar o ano/vínculo.

    Caso A: exatamente uma matrícula.
    Caso B exigiria evidência de turma/ano no próprio registro; Nota/Falta
    legado não têm esses campos, então múltiplas matrículas ficam ambíguas.
    Não usa a matrícula cursando nem a mais recente como desempate.
    """
    mats = Matricula.query.filter_by(aluno_id=aluno_id).all()
    if len(mats) == 1:
        return mats[0], None
    if not mats:
        return None, "zero_matriculas"
    return None, "multiplas_matriculas"


def _backfill_modelo(model, tipo):
    associados = 0
    ambiguos = 0
    diagnosticos = []
    registros = model.query.filter(model.matricula_id.is_(None)).all()
    for reg in registros:
        mat, motivo = _matricula_inequivoca_do_aluno(reg.aluno_id)
        if mat is None:
            ambiguos += 1
            diagnosticos.append({
                "tipo": "ambiguo" if motivo == "multiplas_matriculas" else "sem_matricula",
                "registro": tipo,
                "registro_id": reg.id,
                "aluno_id": reg.aluno_id,
                "motivo": motivo,
                "detalhe": (
                    "Aluno com mais de uma matrícula e sem evidência inequívoca "
                    "de turma/ano no registro; matricula_id permanece nulo."
                    if motivo == "multiplas_matriculas"
                    else "Aluno sem matrícula conhecida; registro preservado sem associação."
                ),
            })
            continue
        reg.matricula_id = mat.id
        associados += 1
    return associados, ambiguos, len(registros), diagnosticos


def backfill_registros_academicos():
    """Associa RegistroNota/RegistroFalta à matrícula só quando for inequívoco.

    Não cria, não apaga, não altera valor/bimestre/disciplina.
    """
    global ULTIMO_DIAGNOSTICO_BACKFILL_REGISTROS, ULTIMO_RESUMO_BACKFILL_REGISTROS
    n_notas, a_notas, p_notas, d_notas = _backfill_modelo(RegistroNota, "nota")
    n_faltas, a_faltas, p_faltas, d_faltas = _backfill_modelo(RegistroFalta, "falta")
    if n_notas or n_faltas:
        db.session.commit()
    diagnosticos = d_notas + d_faltas
    ULTIMO_DIAGNOSTICO_BACKFILL_REGISTROS[:] = diagnosticos
    ULTIMO_RESUMO_BACKFILL_REGISTROS.update({
        "notas_associadas": n_notas,
        "faltas_associadas": n_faltas,
        "notas_ambiguas": a_notas,
        "faltas_ambiguas": a_faltas,
        "notas_preservadas": p_notas,
        "faltas_preservadas": p_faltas,
    })
    return diagnosticos


def _duplicidades_lancamento(tabela):
    from src.integridade import duplicidades_incompativeis

    return [
        g for g in duplicidades_incompativeis()
        if g.get("tabela") == tabela
    ]


def garantir_unicidade_lancamentos_matricula():
    """Somente diagnostica duplicidade. Não cria índice e não apaga registros.

    UNIQUE parcial de nota/falta/frequência/parecer/histórico é da migration
    Alembic (`c4e91f7a2b08`). Este backfill não altera schema.
    """
    global ULTIMO_RESUMO_BACKFILL_REGISTROS, ULTIMO_DIAGNOSTICO_BACKFILL_REGISTROS
    dups_nota = _duplicidades_lancamento("registro_nota")
    dups_falta = _duplicidades_lancamento("registro_falta")
    ULTIMO_RESUMO_BACKFILL_REGISTROS["duplicidades_nota"] = len(dups_nota)
    ULTIMO_RESUMO_BACKFILL_REGISTROS["duplicidades_falta"] = len(dups_falta)
    ULTIMO_RESUMO_BACKFILL_REGISTROS["indice_unico_nota"] = False
    ULTIMO_RESUMO_BACKFILL_REGISTROS["indice_unico_falta"] = False
    if dups_nota:
        ULTIMO_DIAGNOSTICO_BACKFILL_REGISTROS.append({
            "tipo": "duplicidade",
            "registro": "nota",
            "quantidade": len(dups_nota),
            "detalhe": (
                "Duplicidade (matricula_id, disciplina_id, bimestre); "
                "este backfill não cria índice nem apaga linhas."
            ),
        })
    if dups_falta:
        ULTIMO_DIAGNOSTICO_BACKFILL_REGISTROS.append({
            "tipo": "duplicidade",
            "registro": "falta",
            "quantidade": len(dups_falta),
            "detalhe": (
                "Duplicidade (matricula_id, disciplina_id, bimestre); "
                "este backfill não cria índice nem apaga linhas."
            ),
        })
    return ULTIMO_RESUMO_BACKFILL_REGISTROS

