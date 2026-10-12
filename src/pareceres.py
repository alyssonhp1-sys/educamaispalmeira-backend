"""Parecer descritivo da Educação Infantil (RelatorioDescritivo).

Fonte de verdade: Matricula (vínculo aluno + turma daquele período).
aluno_id permanece como redundância de compatibilidade na transição.
EI não usa Disciplina neste lançamento.

Chave operacional: (matricula_id, eixo, bimestre).
Substituição/remoção nunca usa só aluno_id.
"""
from sqlalchemy import text

from src import db
from src.matriculas import (
    MSG_SEM_MATRICULA_CURSANDO,
    _matricula_inequivoca_do_aluno,
    garantir_aluno_da_matricula,
    matricula_cursando_na_turma,
)
from src.models import Matricula, RelatorioDescritivo

ULTIMO_DIAGNOSTICO_BACKFILL_RELATORIOS = []
ULTIMO_RESUMO_BACKFILL_RELATORIOS = {
    "associados": 0,
    "nao_associados": 0,
    "ambiguos": 0,
    "sem_matricula": 0,
    "preservados": 0,
    "duplicidades": 0,
    "indice_unico": False,
}


def resolver_matricula_parecer_turma(aluno_id, turma):
    """Matrícula cursando daquele aluno NA turma do contexto. Sem chute."""
    if turma is None or turma.id is None:
        raise ValueError(MSG_SEM_MATRICULA_CURSANDO)
    mat = matricula_cursando_na_turma(aluno_id, turma.id)
    if mat is None:
        raise ValueError(MSG_SEM_MATRICULA_CURSANDO)
    garantir_aluno_da_matricula(aluno_id, mat)
    if int(mat.turma_id) != int(turma.id):
        raise ValueError(MSG_SEM_MATRICULA_CURSANDO)
    return mat


def payload_parecer(reg):
    return {
        "eixo": reg.eixo,
        "bim": reg.bimestre,
        "aval": reg.avaliacao,
        "obs": reg.observacao,
    }


def pareceres_da_matricula(matricula):
    """Pareceres daquela matrícula.

    Fallback legado (matricula_id NULL) só se o aluno tiver exatamente uma
    matrícula — aí o registro órfão não pode ser de outro ano. Com várias
    matrículas, NULL não é exibido como histórico da matrícula corrente.
    Dívida: legado ambíguo permanece fora da tela específica.
    """
    if matricula is None or matricula.id is None:
        return []
    por_chave = {}
    n_mats = Matricula.query.filter_by(aluno_id=matricula.aluno_id).count()
    if n_mats == 1:
        for reg in RelatorioDescritivo.query.filter_by(
            aluno_id=matricula.aluno_id, matricula_id=None,
        ).all():
            por_chave[(reg.eixo, int(reg.bimestre))] = reg
    for reg in RelatorioDescritivo.query.filter_by(matricula_id=matricula.id).all():
        por_chave[(reg.eixo, int(reg.bimestre))] = reg
    return sorted(por_chave.values(), key=lambda r: (r.bimestre, r.eixo))


def upsert_parecer_matricula(matricula, eixo, bimestre, avaliacao, observacao=""):
    """Atualiza ou insere a célula (matrícula, eixo, bimestre). Não apaga outras."""
    garantir_aluno_da_matricula(matricula.aluno_id, matricula)
    existentes = RelatorioDescritivo.query.filter_by(
        matricula_id=matricula.id,
        eixo=eixo,
        bimestre=int(bimestre),
    ).all()
    if existentes:
        for reg in existentes:
            garantir_aluno_da_matricula(reg.aluno_id, matricula)
            reg.avaliacao = avaliacao
            reg.observacao = observacao if observacao is not None else ""
            reg.aluno_id = matricula.aluno_id
        return existentes[0]
    reg = RelatorioDescritivo(
        aluno_id=matricula.aluno_id,
        matricula_id=matricula.id,
        eixo=eixo,
        bimestre=int(bimestre),
        avaliacao=avaliacao,
        observacao=observacao if observacao is not None else "",
    )
    db.session.add(reg)
    return reg


def remover_parecer_matricula(matricula, eixo, bimestre):
    """Remove só a célula (matrícula, eixo, bimestre). Idempotente."""
    garantir_aluno_da_matricula(matricula.aluno_id, matricula)
    RelatorioDescritivo.query.filter_by(
        matricula_id=matricula.id,
        eixo=eixo,
        bimestre=int(bimestre),
    ).delete(synchronize_session=False)


def _avaliacao_vazia(avaliacao):
    if avaliacao is None:
        return True
    return isinstance(avaliacao, str) and not avaliacao.strip()


def _recorte_parecer(reg, matricula):
    from src.auditoria import metadados_texto

    return {
        "matricula_id": matricula.id,
        "eixo": reg.eixo,
        "bimestre": int(reg.bimestre),
        "avaliacao": reg.avaliacao,
        "observacao": metadados_texto(reg.observacao),
    }


def aplicar_parecer_matricula(matricula, eixo, bimestre, avaliacao, observacao=""):
    """Limpeza explícita (avaliacao null/vazia) remove só a célula da matrícula.

    RelatorioDescritivo exige avaliacao; observação não existe sozinha.
    "Não Avaliado" apaga o registro inteiro (conceito + observação) daquela
    chave (matricula_id, eixo, bimestre). Item omitido do payload não chega aqui.
    """
    from src.auditoria import (
        RELATORIO_DESCRITIVO_ALTERADO,
        RELATORIO_DESCRITIVO_CRIADO,
        RELATORIO_DESCRITIVO_REMOVIDO,
        escola_id_da_matricula,
        metadados_texto,
        registrar_auditoria,
    )

    garantir_aluno_da_matricula(matricula.aluno_id, matricula)
    existentes = RelatorioDescritivo.query.filter_by(
        matricula_id=matricula.id,
        eixo=eixo,
        bimestre=int(bimestre),
    ).all()
    obs_nova = observacao if observacao is not None else ""

    if _avaliacao_vazia(avaliacao):
        if not existentes:
            return None
        anterior = existentes[0]
        registrar_auditoria(
            RELATORIO_DESCRITIVO_REMOVIDO,
            "RelatorioDescritivo",
            entidade_id=anterior.id,
            escola_id=escola_id_da_matricula(matricula),
            dados_anteriores=_recorte_parecer(anterior, matricula),
            dados_novos={
                "matricula_id": matricula.id,
                "eixo": eixo,
                "bimestre": int(bimestre),
                "avaliacao": None,
                "observacao": metadados_texto(None),
            },
        )
        remover_parecer_matricula(matricula, eixo, bimestre)
        return None

    if existentes:
        anterior = existentes[0]
        mesmo_aval = (anterior.avaliacao or "") == (avaliacao or "")
        mesma_obs = (anterior.observacao or "") == obs_nova
        if mesmo_aval and mesma_obs:
            return upsert_parecer_matricula(matricula, eixo, bimestre, avaliacao, observacao)
        snapshot = _recorte_parecer(anterior, matricula)
        reg = upsert_parecer_matricula(matricula, eixo, bimestre, avaliacao, observacao)
        registrar_auditoria(
            RELATORIO_DESCRITIVO_ALTERADO,
            "RelatorioDescritivo",
            entidade_id=reg.id,
            escola_id=escola_id_da_matricula(matricula),
            dados_anteriores=snapshot,
            dados_novos=_recorte_parecer(reg, matricula),
        )
        return reg

    reg = upsert_parecer_matricula(matricula, eixo, bimestre, avaliacao, observacao)
    db.session.flush()
    registrar_auditoria(
        RELATORIO_DESCRITIVO_CRIADO,
        "RelatorioDescritivo",
        entidade_id=reg.id,
        escola_id=escola_id_da_matricula(matricula),
        dados_anteriores=None,
        dados_novos=_recorte_parecer(reg, matricula),
    )
    return reg


def backfill_relatorios_descritivos():
    """Associa RelatorioDescritivo à matrícula só quando for inequívoco.

    O modelo legado não tem turma_id/ano. Só associa se o aluno tiver
    exatamente uma Matricula. Várias matrículas: NULL + diagnóstico.
    Não usa matrícula cursando nem a mais recente como desempate.
    Não cria, não apaga, não altera eixo/bimestre/texto.
    """
    global ULTIMO_DIAGNOSTICO_BACKFILL_RELATORIOS, ULTIMO_RESUMO_BACKFILL_RELATORIOS
    diagnosticos = []
    associados = 0
    ambiguos = 0
    sem = 0
    n_antes = RelatorioDescritivo.query.count()
    registros = RelatorioDescritivo.query.filter(
        RelatorioDescritivo.matricula_id.is_(None),
    ).all()
    for reg in registros:
        mat, motivo = _matricula_inequivoca_do_aluno(reg.aluno_id)
        if mat is None:
            if motivo == "multiplas_matriculas":
                ambiguos += 1
                tipo = "ambiguo"
                detalhe = (
                    "Aluno com mais de uma matrícula e RelatorioDescritivo sem "
                    "turma_id/ano; matricula_id permanece nulo. Não foi atribuído "
                    "à matrícula atual."
                )
            else:
                sem += 1
                tipo = "sem_matricula"
                detalhe = (
                    "Aluno sem matrícula conhecida; parecer preservado sem associação."
                )
            diagnosticos.append({
                "tipo": tipo,
                "registro": "parecer",
                "registro_id": reg.id,
                "aluno_id": reg.aluno_id,
                "eixo": reg.eixo,
                "bimestre": reg.bimestre,
                "motivo": motivo,
                "detalhe": detalhe,
            })
            continue
        reg.matricula_id = mat.id
        associados += 1
    if associados:
        db.session.commit()
    n_depois = RelatorioDescritivo.query.count()
    dups = list(db.session.execute(text(
        "SELECT matricula_id, eixo, bimestre, COUNT(*) AS n "
        "FROM {} WHERE matricula_id IS NOT NULL "
        "GROUP BY matricula_id, eixo, bimestre HAVING COUNT(*) > 1".format(
            RelatorioDescritivo.__tablename__,
        )
    )))
    if dups:
        diagnosticos.append({
            "tipo": "duplicidade",
            "registro": "parecer",
            "quantidade": len(dups),
            "detalhe": (
                "Duplicidade (matricula_id, eixo, bimestre); este backfill "
                "não cria índice UNIQUE nem apaga linhas."
            ),
        })
    if n_depois != n_antes:
        diagnosticos.append({
            "tipo": "contagem_divergente",
            "detalhe": "A contagem de pareceres mudou durante o backfill.",
            "antes": n_antes,
            "depois": n_depois,
        })
    ULTIMO_DIAGNOSTICO_BACKFILL_RELATORIOS[:] = diagnosticos
    ULTIMO_RESUMO_BACKFILL_RELATORIOS.update({
        "associados": associados,
        "nao_associados": ambiguos + sem,
        "ambiguos": ambiguos,
        "sem_matricula": sem,
        "preservados": n_antes,
        "duplicidades": len(dups),
        "indice_unico": False,
    })
    return diagnosticos
