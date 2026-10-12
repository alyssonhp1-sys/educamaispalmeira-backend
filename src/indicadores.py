"""Indicadores pedagógicos simples a partir do modelo atual.

Não há algoritmo sofisticado: risco = situação já calculada pelo motor de notas;
pendência de diário = alocação ativa sem nenhum lançamento.
Carga horária do Fundamental: calendário + grade semanal + aulas validadas.
Educação Infantil: jornada da turma + dias letivos + registros validados.
"""
from datetime import date

from src.alunos import SITUACOES_ATIVAS
from src.calendario import cumprimento_alocacao, cumprimento_turma_ei, indice_dias, indice_grades, ano_letivo_da_turma
from src.models import (
    Aluno,
    Alocacao,
    AnoLetivo,
    Disciplina,
    Escola,
    Matricula,
    Planejamento,
    Professor,
    RegistroAula,
    RegistroNota,
    RelatorioDescritivo,
    Turma,
)
from src.notas import calcular_notas_matricula, media_minima_vigente

SITUACOES_RISCO = frozenset({"EM RECUPERAÇÃO", "REPROVADO"})
LIMITACAO_CALENDARIO = (
    "Não há calendário letivo configurado. O cumprimento até a data não pode ser "
    "calculado. O progresso anual usa aulas validadas sobre a carga horária prevista, "
    "quando ela estiver informada."
)


def _cadastral_acompanhavel(aluno):
    sit = (aluno.situacao or "").lower()
    return sit in SITUACOES_ATIVAS


def listar_alunos_em_risco(escola_id=None, limite=40):
    q = Turma.query.filter_by(status="ativa", etapa_ensino="fundamental")
    if escola_id:
        q = q.filter_by(escola_id=escola_id)
    disciplinas = Disciplina.query.all()
    visto = set()
    itens = []
    for turma in q.order_by(Turma.nome).all():
        for aluno in turma.alunos:
            if aluno.id in visto or not _cadastral_acompanhavel(aluno):
                continue
            mat = Matricula.query.filter_by(
                aluno_id=aluno.id, turma_id=turma.id,
            ).first()
            if mat is None:
                continue
            for disc in disciplinas:
                resumo = calcular_notas_matricula(mat.id, disc.id)
                if resumo["situacao"] not in SITUACOES_RISCO:
                    continue
                visto.add(aluno.id)
                itens.append({
                    "id": aluno.id,
                    "nome": aluno.nome,
                    "turmaId": turma.id,
                    "turmaNome": turma.nome,
                    "disciplinaNome": disc.nome,
                    "situacao": resumo["situacao"],
                    "ma": resumo["ma"],
                })
                break
            if limite and len(itens) >= limite:
                return itens
    return itens


def listar_diarios_pendentes(escola_id=None, limite=40):
    q = Alocacao.query.join(Turma).filter(Turma.status == "ativa")
    if escola_id:
        q = q.filter(Turma.escola_id == escola_id)
    itens = []
    for aloc in q.all():
        turma = aloc.turma
        if not turma:
            continue
        alunos = [a for a in turma.alunos if _cadastral_acompanhavel(a)]
        if not alunos:
            continue
        ids = [a.id for a in alunos]
        if turma.etapa_ensino == "infantil":
            ids_mat = [m.id for m in Matricula.query.filter_by(turma_id=turma.id).all()]
            if ids_mat:
                n = RelatorioDescritivo.query.filter(
                    RelatorioDescritivo.matricula_id.in_(ids_mat),
                ).count()
            else:
                n = 0
            tipo = "parecer"
            disc_nome = "Educação Infantil"
        else:
            if not aloc.disciplina_id:
                continue
            ids_mat = [m.id for m in Matricula.query.filter_by(turma_id=turma.id).all()]
            if ids_mat:
                n = RegistroNota.query.filter(
                    RegistroNota.matricula_id.in_(ids_mat),
                    RegistroNota.disciplina_id == aloc.disciplina_id,
                ).count()
            else:
                n = 0
            tipo = "notas"
            disc_nome = aloc.disciplina.nome if aloc.disciplina else None
        if n:
            continue
        itens.append({
            "turmaId": turma.id,
            "turmaNome": turma.nome,
            "disciplinaNome": disc_nome,
            "professorNome": aloc.professor.nome if aloc.professor else None,
            "tipo": tipo,
        })
        if limite and len(itens) >= limite:
            break
    return itens


def _alunos_sem_turma_ativos():
    return Aluno.query.filter(
        Aluno.turma_id.is_(None),
        Aluno.situacao.in_(tuple(SITUACOES_ATIVAS)),
    ).count()


def resumo_rede():
    risco = listar_alunos_em_risco(limite=500)
    return {
        "totalEscolas": Escola.query.count(),
        "totalAlunos": Aluno.query.count(),
        "alunosAtivos": Aluno.query.filter(Aluno.situacao.in_(tuple(SITUACOES_ATIVAS))).count(),
        "alunosInativos": Aluno.query.filter_by(situacao="inativo").count(),
        "alunosSemTurma": _alunos_sem_turma_ativos(),
        "totalProfessores": Professor.query.count(),
        "turmasAtivas": Turma.query.filter_by(status="ativa").count(),
        "planejamentosPendentes": Planejamento.query.filter_by(status="pendente", arquivado=False).count(),
        "registrosAulaPendentes": RegistroAula.query.filter_by(status="pendente").count(),
        "alunosEmRisco": len(risco),
        "mediaMinima": media_minima_vigente(),
    }


def resumo_escola(escola_id):
    alunos = Aluno.query.outerjoin(Turma, Aluno.turma_id == Turma.id).filter(
        (Aluno.escola_id == escola_id) | (Turma.escola_id == escola_id)
    ).distinct().all()
    ativos = [a for a in alunos if _cadastral_acompanhavel(a)]
    return {
        "totalAlunos": len(alunos),
        "alunosAtivos": len(ativos),
        "alunosInativos": sum(1 for a in alunos if (a.situacao or "").lower() == "inativo"),
        "alunosSemTurma": sum(1 for a in ativos if not a.turma_id),
        "totalProfessores": Professor.query.filter_by(escola_id=escola_id).count(),
        "turmasAtivas": Turma.query.filter_by(escola_id=escola_id, status="ativa").count(),
        "planejamentosPendentes": (
            Planejamento.query.join(Turma)
            .filter(Turma.escola_id == escola_id, Planejamento.status == "pendente", Planejamento.arquivado == False)
            .count()
        ),
        "alunosEmRisco": len(listar_alunos_em_risco(escola_id=escola_id, limite=500)),
        "diariosPendentes": len(listar_diarios_pendentes(escola_id=escola_id, limite=500)),
        "mediaMinima": media_minima_vigente(),
        "registrosAulaPendentes": (
            RegistroAula.query.join(Turma)
            .filter(Turma.escola_id == escola_id, RegistroAula.status == "pendente")
            .count()
        ),
    }


def percentual(numerador, denominador):
    if not denominador:
        return None
    return round(100.0 * float(numerador) / float(denominador), 1)


def resumo_carga_alocacao(aloc, data_ref=None, ano=None, indice=None, grade=None):
    return cumprimento_alocacao(aloc, data_ref=data_ref, ano=ano, indice=indice, grade=grade)


def _cache_anos(turmas):
    anos_int = {t.ano_letivo for t in turmas if t}
    mapa = {}
    if anos_int:
        for ano in AnoLetivo.query.filter(AnoLetivo.ano.in_(anos_int)).all():
            mapa[ano.ano] = ano
    return mapa


def acompanhamento_escola(escola_id, data_ref=None):
    data_ref = data_ref or date.today()
    turmas = Turma.query.filter_by(escola_id=escola_id, status="ativa").all()
    anos_por_int = _cache_anos(turmas)
    anos_usados = {}
    for t in turmas:
        ano = anos_por_int.get(t.ano_letivo) or ano_letivo_da_turma(t)
        if ano:
            anos_usados[ano.id] = ano
    indices = {aid: indice_dias(a) for aid, a in anos_usados.items()}
    alocs = (
        Alocacao.query.join(Turma)
        .filter(Turma.escola_id == escola_id, Turma.status == "ativa")
        .all()
    )
    grades = indice_grades([a.id for a in alocs])
    itens = []
    infantil = []
    vistos_ei = set()
    for aloc in alocs:
        turma = aloc.turma
        if not turma:
            continue
        ano = anos_por_int.get(turma.ano_letivo) or ano_letivo_da_turma(turma)
        indice = indices.get(ano.id) if ano else {}
        if turma.etapa_ensino == "infantil":
            if turma.id not in vistos_ei:
                infantil.append(cumprimento_turma_ei(turma, data_ref=data_ref, ano=ano, indice=indice))
                vistos_ei.add(turma.id)
            continue
        itens.append(cumprimento_alocacao(
            aloc,
            data_ref=data_ref,
            ano=ano,
            indice=indice,
            grade=grades.get(aloc.id, {}),
        ))
    for turma in turmas:
        if turma.etapa_ensino == "infantil" and turma.id not in vistos_ei:
            ano = anos_por_int.get(turma.ano_letivo) or ano_letivo_da_turma(turma)
            indice = indices.get(ano.id) if ano else {}
            infantil.append(cumprimento_turma_ei(turma, data_ref=data_ref, ano=ano, indice=indice))
            vistos_ei.add(turma.id)

    carga_total = sum(i["cargaPrevista"] or 0 for i in itens)
    validadas = sum(i["aulasValidadas"] for i in itens)
    lancadas = sum(i["aulasLancadas"] for i in itens)
    invalidadas = sum(i["aulasInvalidadas"] for i in itens)
    pendentes = sum(i["aulasPendentes"] for i in itens)
    previstas_ate = 0
    tem_previsao = False
    validadas_previsao = 0
    for i in itens:
        if i["aulasPrevistasAteHoje"] is not None:
            tem_previsao = True
            previstas_ate += i["aulasPrevistasAteHoje"]
            validadas_previsao += i["aulasValidadas"]
    percentual_anual = round(100.0 * validadas / carga_total, 1) if carga_total else None
    percentual_hoje = percentual(validadas_previsao, previstas_ate) if tem_previsao else None
    tem_calendario = any(indices.values())
    return {
        "escolaId": escola_id,
        "dataReferencia": data_ref.isoformat(),
        "itens": itens,
        "educacaoInfantil": infantil,
        "cargaPrevista": carga_total or None,
        "aulasLancadas": lancadas,
        "aulasValidadas": validadas,
        "aulasInvalidadas": invalidadas,
        "aulasPendentes": pendentes,
        "aulasPrevistasAteHoje": previstas_ate if tem_previsao else None,
        "percentualCumprido": percentual_anual,
        "progressoAnual": percentual_anual,
        "percentualCumprimentoAteHoje": percentual_hoje,
        "limitacaoCalendario": not tem_calendario,
        "observacaoCalendario": None if tem_calendario else LIMITACAO_CALENDARIO,
    }


def acompanhamento_rede(data_ref=None):
    data_ref = data_ref or date.today()
    escolas = Escola.query.order_by(Escola.nome).all()
    lista = []
    tem_calendario = False
    for e in escolas:
        r = acompanhamento_escola(e.id, data_ref=data_ref)
        if not r["limitacaoCalendario"]:
            tem_calendario = True
        lista.append({
            "escolaId": e.id,
            "escolaNome": e.nome,
            "cargaPrevista": r["cargaPrevista"],
            "aulasLancadas": r["aulasLancadas"],
            "aulasValidadas": r["aulasValidadas"],
            "aulasInvalidadas": r["aulasInvalidadas"],
            "aulasPendentes": r.get("aulasPendentes") or 0,
            "aulasPrevistasAteHoje": r["aulasPrevistasAteHoje"],
            "percentualCumprido": r["percentualCumprido"],
            "progressoAnual": r["progressoAnual"],
            "percentualCumprimentoAteHoje": r["percentualCumprimentoAteHoje"],
        })
    return {
        "escolas": lista,
        "dataReferencia": data_ref.isoformat(),
        "limitacaoCalendario": not tem_calendario,
        "observacaoCalendario": None if tem_calendario else LIMITACAO_CALENDARIO,
    }
