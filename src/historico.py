"""Snapshot anual (HistoricoAnual) por Matrícula.

Fonte de verdade: Matricula (vínculo aluno + turma daquele período).
aluno_id e ano_letivo permanecem no registro para compatibilidade.

Encerramento de turma percorre as matrículas daquela Turma — não Aluno.turma_id.
Uma matrícula gera no máximo um HistoricoAnual. O snapshot não é recalculado.
Sem cascade: apagar matrícula não apaga o histórico anual.
UNIQUE parcial (matricula_id IS NOT NULL) está na migration Alembic.

O JSON novo complementa as chaves legadas (notas/faltas/frequencias/pareceres)
com metadados congelados e resultado por disciplina via o motor oficial (src.notas).
Não há regra global oficial de aprovação da matrícula: resultado_academico fica nulo.
Snapshots já persistidos não são regravados nem enriquecidos com dados atuais.

Reemissão histórica lê somente HistoricoAnual/dados_fechamento. Não consulta
RegistroNota, RelatorioDescritivo, ConfiguracaoAcademica nem Aluno.turma_id.
"""
import json
from datetime import date

from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload

from src import db
from src.models import (
    Alocacao,
    Aluno,
    Disciplina,
    HistoricoAnual,
    Matricula,
    RegistroFalta,
    RegistroFrequencia,
    RegistroNota,
    Turma,
)

CHAVES_LEGADO_FECHAMENTO = ("notas", "faltas", "frequencias", "pareceres")
CAMPOS_RESULTADO_MOTOR = (
    "ma", "mb1", "mb2", "mb3", "mb4", "ms1", "ms2", "rs1", "rs2", "final",
    "media_minima", "precisa_pf", "precisa_rp1", "precisa_rp2", "precisa_rp3",
    "precisa_rp4", "precisa_rs1", "precisa_rs2", "faltas",
)

RESULTADO_ENCERRADA = "encerrada"
RESULTADO_JA_ENCERRADA = "ja_encerrada"

ULTIMO_DIAGNOSTICO_BACKFILL_HISTORICO = []
ULTIMO_RESUMO_BACKFILL_HISTORICO = {
    "associados": 0,
    "nao_associados": 0,
    "ambiguos": 0,
    "sem_matricula": 0,
    "preservados": 0,
    "duplicidades": 0,
    "indice_unico": False,
}


def historico_da_matricula(matricula):
    """No máximo o primeiro snapshot daquela matrícula. Não cria nem recalcula."""
    if matricula is None or getattr(matricula, "id", None) is None:
        return None
    return (
        HistoricoAnual.query.filter_by(matricula_id=matricula.id)
        .order_by(HistoricoAnual.id.asc())
        .first()
    )


def _notas_da_matricula(matricula):
    """Somente RegistroNota.matricula_id. Sem fallback por aluno_id."""
    return (
        RegistroNota.query.filter_by(matricula_id=matricula.id)
        .order_by(RegistroNota.disciplina_id, RegistroNota.bimestre, RegistroNota.id)
        .all()
    )


def _faltas_da_matricula(matricula):
    """Somente RegistroFalta.matricula_id. Sem fallback por aluno_id."""
    return (
        RegistroFalta.query.filter_by(matricula_id=matricula.id)
        .order_by(RegistroFalta.disciplina_id, RegistroFalta.bimestre, RegistroFalta.id)
        .all()
    )


def _frequencias_da_matricula(matricula):
    """Frequência diária da matrícula, com fallback legado inequívoco.

    Inclui matricula_id == matrícula e legado NULL do mesmo aluno_id + turma_id.
    Nunca aluno_id sozinho.
    """
    return (
        RegistroFrequencia.query.filter(
            or_(
                RegistroFrequencia.matricula_id == matricula.id,
                and_(
                    RegistroFrequencia.matricula_id.is_(None),
                    RegistroFrequencia.aluno_id == matricula.aluno_id,
                    RegistroFrequencia.turma_id == matricula.turma_id,
                ),
            )
        )
        .order_by(RegistroFrequencia.data, RegistroFrequencia.id)
        .all()
    )


def _pareceres_da_matricula(matricula):
    """Parecer EI da matrícula; fallback legado só pela regra do Bloco 5."""
    from src.pareceres import pareceres_da_matricula

    return pareceres_da_matricula(matricula)


def carregar_dados_fechamento(hist_ou_json):
    """Leitura compatível de snapshot legado ou novo.

    Não consulta Disciplina/Escola/Turma atuais e não grava de volta.
    Chaves legadas ausentes viram lista vazia só na leitura.
    """
    if hist_ou_json is None:
        bruto = {}
    elif hasattr(hist_ou_json, "dados_fechamento"):
        bruto = hist_ou_json.dados_fechamento
    else:
        bruto = hist_ou_json
    if isinstance(bruto, str):
        dados = json.loads(bruto or "{}")
    elif isinstance(bruto, dict):
        dados = dict(bruto)
    else:
        dados = {}
    if not isinstance(dados, dict):
        dados = {}
    for chave in CHAVES_LEGADO_FECHAMENTO:
        if chave not in dados or dados[chave] is None:
            dados[chave] = []
    return dados


MSG_LEGADO_INSUFICIENTE = (
    "O registro legado não possui dados suficientes para reemitir este documento."
)
MSG_BOLETIM_EI = (
    "Este histórico é da Educação Infantil. Use o parecer do ano letivo."
)
MSG_PARECER_NAO_EI = (
    "Parecer descritivo histórico aplica-se apenas à Educação Infantil."
)


def obter_historico_anual(historico_id):
    """Carrega HistoricoAnual pelo id. Não resolve por aluno+ano."""
    if historico_id is None:
        return None
    return db.session.get(HistoricoAnual, historico_id)


def obter_historico_da_matricula(matricula):
    """Alias de historico_da_matricula: no máximo um snapshot daquela matrícula."""
    return historico_da_matricula(matricula)


def _bloco(dados, chave):
    valor = dados.get(chave) if isinstance(dados, dict) else None
    return valor if isinstance(valor, dict) else {}


def snapshot_eh_novo(dados):
    """Snapshot novo: identidade do aluno congelada no JSON. Sem lookup atual."""
    aluno = _bloco(dados, "aluno")
    return bool(aluno.get("nome"))


def identidade_congelada(hist, dados=None):
    """Identidade só do snapshot e das colunas do HistoricoAnual.

    Não consulta Aluno, Turma, Escola ou Disciplina atuais.
    """
    dados = dados if dados is not None else carregar_dados_fechamento(hist)
    aluno = _bloco(dados, "aluno")
    turma = _bloco(dados, "turma")
    escola = _bloco(dados, "escola")
    matricula = _bloco(dados, "matricula")
    ano = turma.get("ano_letivo")
    if ano is None and hist is not None:
        ano = hist.ano_letivo
    return {
        "aluno_id": aluno.get("id") if aluno.get("id") is not None else (
            hist.aluno_id if hist is not None else None
        ),
        "aluno_nome": aluno.get("nome"),
        "turma_nome": turma.get("nome") or (hist.turma_nome if hist is not None else None),
        "ano_letivo": ano,
        "etapa": (turma.get("etapa") or (hist.etapa if hist is not None else None) or ""),
        "escola_id": escola.get("id"),
        "escola_nome": escola.get("nome") or (hist.escola_nome if hist is not None else None),
        "escola_inep": escola.get("inep"),
        "matricula_id": matricula.get("id") if matricula.get("id") is not None else (
            hist.matricula_id if hist is not None else None
        ),
    }


def escola_id_para_acl(hist):
    """Escola autorizadora do histórico: Matricula histórica → Turma → Escola.

    Não usa Aluno.turma_id, Aluno.escola_id, matrícula atual nem escola_nome.
    O JSON congelado não autoriza: só o vínculo estrutural da matrícula do snapshot.
    """
    if hist is None or getattr(hist, "matricula_id", None) is None:
        return None
    matricula = getattr(hist, "matricula", None)
    if matricula is None:
        matricula = db.session.get(Matricula, hist.matricula_id)
    if matricula is None:
        return None
    turma = matricula.turma
    if turma is None:
        return None
    return turma.escola_id


def etapa_do_historico(hist, dados=None):
    etapa = (identidade_congelada(hist, dados).get("etapa") or "")
    return str(etapa).strip().lower()


def historico_e_infantil(hist, dados=None):
    return etapa_do_historico(hist, dados) == "infantil"


def historico_e_fundamental(hist, dados=None):
    return etapa_do_historico(hist, dados) == "fundamental"


def _identidade_emitivel(hist, dados):
    ident = identidade_congelada(hist, dados)
    if not snapshot_eh_novo(dados):
        return False
    if not ident.get("aluno_nome"):
        return False
    if not ident.get("turma_nome"):
        return False
    if ident.get("ano_letivo") is None:
        return False
    if not ident.get("escola_nome"):
        return False
    return True


def snapshot_suficiente_para_boletim(hist, dados=None):
    """Boletim histórico só com snapshot novo e nomes de disciplina congelados."""
    dados = dados if dados is not None else carregar_dados_fechamento(hist)
    if historico_e_infantil(hist, dados):
        return False
    if not historico_e_fundamental(hist, dados):
        return False
    if not _identidade_emitivel(hist, dados):
        return False
    discs = dados.get("disciplinas")
    if not isinstance(discs, list):
        return False
    for item in discs:
        if not isinstance(item, dict) or not item.get("disciplina_nome"):
            return False
    return True


def snapshot_suficiente_para_parecer(hist, dados=None):
    """Parecer histórico só com snapshot novo EI e lista de pareceres do JSON."""
    dados = dados if dados is not None else carregar_dados_fechamento(hist)
    if not historico_e_infantil(hist, dados):
        return False
    if not _identidade_emitivel(hist, dados):
        return False
    return isinstance(dados.get("pareceres"), list)


def media_minima_do_snapshot(dados):
    """Critério congelado. Não chama media_minima_vigente()."""
    criterios = _bloco(dados, "criterios_academicos")
    valor = criterios.get("media_minima")
    return valor


def boletim_do_snapshot(dados):
    """Linhas do boletim a partir de disciplinas[] congeladas. Sem recálculo."""
    itens = []
    for item in dados.get("disciplinas") or []:
        if not isinstance(item, dict):
            continue
        itens.append({
            "disciplina": item.get("disciplina_nome"),
            "mb1": item.get("mb1"),
            "mb2": item.get("mb2"),
            "mb3": item.get("mb3"),
            "mb4": item.get("mb4"),
            "faltas": item.get("faltas"),
            "ma": item.get("ma"),
            "situacao": item.get("resultado"),
            "carga_horaria_anual": item.get("carga_horaria_anual"),
        })
    return itens


def pareceres_do_snapshot(dados):
    """Pareceres do JSON, agrupados por bimestre. Sem RelatorioDescritivo."""
    por_bimestre = {1: [], 2: [], 3: [], 4: []}
    for item in dados.get("pareceres") or []:
        if not isinstance(item, dict):
            continue
        bim = item.get("bim")
        try:
            bim = int(bim)
        except (TypeError, ValueError):
            continue
        if bim not in por_bimestre:
            continue
        por_bimestre[bim].append({
            "eixo": item.get("eixo"),
            "avaliacao": item.get("aval"),
            "observacao": item.get("obs"),
        })
    return por_bimestre


def _payload_listagem_historico(hist, dados):
    ident = identidade_congelada(hist, dados)
    etapa = etapa_do_historico(hist, dados)
    if snapshot_suficiente_para_parecer(hist, dados):
        documento = "parecer"
    elif snapshot_suficiente_para_boletim(hist, dados):
        documento = "boletim"
    else:
        documento = None
    return {
        "historico_id": hist.id,
        "matricula_id": ident.get("matricula_id"),
        "ano_letivo": ident.get("ano_letivo"),
        "escola_nome": ident.get("escola_nome"),
        "turma_nome": ident.get("turma_nome"),
        "tipo": etapa or None,
        "documento": documento,
        "_escola_id": escola_id_para_acl(hist),
    }


def listar_historicos_anuais_do_aluno(aluno_id):
    """Históricos do aluno a partir de HistoricoAnual. Sem join de disciplina."""
    if aluno_id is None:
        return []
    registros = (
        HistoricoAnual.query.filter_by(aluno_id=aluno_id)
        .options(joinedload(HistoricoAnual.matricula).joinedload(Matricula.turma))
        .order_by(HistoricoAnual.ano_letivo.desc(), HistoricoAnual.id.asc())
        .all()
    )
    itens = []
    for hist in registros:
        dados = carregar_dados_fechamento(hist)
        itens.append(_payload_listagem_historico(hist, dados))
    return itens


def historicos_visiveis_do_aluno(user, aluno):
    """Lista só históricos cuja matrícula histórica pertence à escola do usuário.

    Autorização: HistoricoAnual → Matricula → Turma → Escola.
    Não usa escola/turma/matrícula atuais do aluno. Professor não entra.
    Retorna (itens, autorizado). autorizado=False → 403, sem vazar existência.
    """
    from src import authz

    if aluno is None or user is None:
        return [], False
    if authz.professor(user):
        return [], False
    itens = listar_historicos_anuais_do_aluno(aluno.id)
    if authz.municipal(user):
        return [_publico_listagem(i) for i in itens], True
    if user.papel not in ("gestor_escolar", "coordenador_pedagogico"):
        return [], False
    da_escola = [i for i in itens if i.get("_escola_id") == user.escola_id]
    if da_escola:
        return [_publico_listagem(i) for i in da_escola], True
    return [], False


def _publico_listagem(item):
    return {
        "historico_id": item["historico_id"],
        "matricula_id": item["matricula_id"],
        "ano_letivo": item["ano_letivo"],
        "escola_nome": item["escola_nome"],
        "turma_nome": item["turma_nome"],
        "tipo": item["tipo"],
        "documento": item["documento"],
    }


def _payload_frequencias(matricula):
    return [
        {
            "disc": r.disciplina_id,
            "data": r.data.isoformat() if r.data else None,
            "sit": r.situacao,
            "bim": r.bimestre,
        }
        for r in _frequencias_da_matricula(matricula)
    ]


def _ids_disciplinas_aplicaveis(matricula):
    """Disciplinas da matrícula (lançamentos) e da grade da turma (alocações)."""
    ids = set()
    for n in _notas_da_matricula(matricula):
        if n.disciplina_id:
            ids.add(n.disciplina_id)
    for f in _faltas_da_matricula(matricula):
        if f.disciplina_id:
            ids.add(f.disciplina_id)
    for r in _frequencias_da_matricula(matricula):
        if r.disciplina_id:
            ids.add(r.disciplina_id)
    turma = matricula.turma
    if turma is not None:
        for a in Alocacao.query.filter_by(turma_id=turma.id).all():
            if a.disciplina_id:
                ids.add(a.disciplina_id)
    return ids


def _carga_horaria_no_fechamento(matricula, disciplina):
    """CH do fechamento: alocação da turma; senão matriz vigente daquele ano/série.

    Não usa carga de outra turma/ano. Sem alocação e sem matriz vigente: None.
    """
    from src.matriz_curricular import carga_oficial, inferir_serie

    turma = matricula.turma if matricula is not None else None
    if turma is not None:
        aloc = Alocacao.query.filter_by(
            turma_id=turma.id, disciplina_id=disciplina.id,
        ).first()
        if aloc is not None and aloc.carga_horaria_prevista is not None:
            return aloc.carga_horaria_prevista
        serie = inferir_serie(turma.nome)
        return carga_oficial(turma.ano_letivo, serie, disciplina.nome)
    return None


def _payload_resultado_disciplina(disciplina, resumo, carga_horaria_anual=None):
    """Congela identidade e o retorno do motor oficial. Sem fórmula paralela."""
    item = {
        "disciplina_id": disciplina.id,
        "disciplina_nome": disciplina.nome,
        "resultado": resumo.get("situacao"),
        "carga_horaria_anual": carga_horaria_anual,
    }
    for campo in CAMPOS_RESULTADO_MOTOR:
        item[campo] = resumo.get(campo)
    return item


def _snapshot_disciplinas(matricula):
    """Resultado por disciplina via calcular_notas_matricula. Nomes no instante do fechamento."""
    from src.notas import calcular_notas_matricula

    itens = []
    for disc_id in _ids_disciplinas_aplicaveis(matricula):
        disc = db.session.get(Disciplina, disc_id)
        if disc is None:
            continue
        resumo = calcular_notas_matricula(matricula.id, disc_id)
        carga = _carga_horaria_no_fechamento(matricula, disc)
        itens.append(_payload_resultado_disciplina(disc, resumo, carga))
    itens.sort(key=lambda d: ((d.get("disciplina_nome") or ""), d["disciplina_id"]))
    return itens


def _metadados_congelados(matricula, situacao_vinculo):
    """Identidade no fechamento. Sem CPF, NIS, endereço, alergia ou filiação.

    Nascimento e filiação do histórico escolar saem do cadastro atual na
    emissão — o snapshot acadêmico não replica PII cadastral.
    """
    aluno = matricula.aluno
    turma = matricula.turma
    escola = turma.escola if turma is not None else None
    meta = {
        "aluno": {
            "id": matricula.aluno_id,
            "nome": aluno.nome if aluno is not None else None,
        },
        "matricula": {
            "id": matricula.id,
            "situacao": situacao_vinculo,
        },
        "situacao_cadastral": aluno.situacao if aluno is not None else None,
        "resultado_academico": None,
    }
    if turma is not None:
        t = {
            "id": turma.id,
            "nome": turma.nome,
            "ano_letivo": turma.ano_letivo,
            "etapa": turma.etapa_ensino,
        }
        if turma.tipo_jornada:
            t["tipo_jornada"] = turma.tipo_jornada
        meta["turma"] = t
    if escola is not None:
        meta["escola"] = {
            "id": escola.id,
            "nome": escola.nome,
            "inep": escola.inep,
        }
    return meta


def montar_dados_fechamento(matricula, etapa, situacao_vinculo=None):
    """Monta o JSON (chaves legadas + metadados/resultados novos). Origem: a matrícula.

    frequencias entra como campo adicional; não remove notas/faltas/pareceres.
    situacao_vinculo: vínculo no fechamento (encerramento grava 'concluida').
    Resultado por disciplina reutiliza src.notas.calcular_notas_matricula.
    Não inventa resultado global nem percentual de frequência.
    """
    from src.matriculas import VINCULO_CONCLUIDA
    from src.notas import media_minima_vigente

    if situacao_vinculo is None:
        situacao_vinculo = VINCULO_CONCLUIDA
    frequencias = _payload_frequencias(matricula)
    meta = _metadados_congelados(matricula, situacao_vinculo)
    if (etapa or "") == "infantil":
        pareceres = _pareceres_da_matricula(matricula)
        dados = {
            "pareceres": [
                {
                    "eixo": r.eixo,
                    "bim": r.bimestre,
                    "aval": r.avaliacao,
                    "obs": r.observacao,
                }
                for r in pareceres
            ],
            "frequencias": frequencias,
            "disciplinas": [],
            "criterios_academicos": {},
        }
        dados.update(meta)
        return dados
    notas = _notas_da_matricula(matricula)
    faltas = _faltas_da_matricula(matricula)
    dados = {
        "notas": [
            {"disc": n.disciplina_id, "bim": n.bimestre, "val": n.valor}
            for n in notas
        ],
        "faltas": [
            {"disc": f.disciplina_id, "bim": f.bimestre, "qtd": f.quantidade}
            for f in faltas
        ],
        "frequencias": frequencias,
        "criterios_academicos": {"media_minima": media_minima_vigente()},
        "disciplinas": _snapshot_disciplinas(matricula),
    }
    dados.update(meta)
    return dados


def _criar_snapshot_se_ausente(matricula, turma):
    """Cria HistoricoAnual da matrícula. Não sobrescreve snapshot existente.

    Não faz commit. situacao_final permanece a cadastral do aluno: não há regra
    global oficial de Aprovado/Reprovado da matrícula. Resultado por disciplina
    vai no JSON. Falha na montagem propaga (rollback do encerramento).
    """
    from src.matriculas import VINCULO_CONCLUIDA

    existente = historico_da_matricula(matricula)
    if existente is not None:
        return existente
    aluno = matricula.aluno
    dados = montar_dados_fechamento(
        matricula, turma.etapa_ensino, situacao_vinculo=VINCULO_CONCLUIDA,
    )
    hist = HistoricoAnual(
        ano_letivo=turma.ano_letivo,
        aluno_id=matricula.aluno_id,
        matricula_id=matricula.id,
        escola_nome=turma.escola.nome if turma.escola else None,
        turma_nome=turma.nome,
        etapa=turma.etapa_ensino,
        situacao_final=aluno.situacao if aluno is not None else None,
        dados_fechamento=json.dumps(dados),
    )
    db.session.add(hist)
    return hist


def _matriculas_cursando_da_turma(turma):
    from src.matriculas import VINCULO_CURSANDO

    return (
        Matricula.query.filter_by(turma_id=turma.id, situacao=VINCULO_CURSANDO)
        .join(Aluno, Matricula.aluno_id == Aluno.id)
        .order_by(Aluno.nome.asc(), Matricula.id.asc())
        .all()
    )


def encerrar_turma_por_matriculas(turma, data_fim=None):
    """Fecha a turma a partir das matrículas cursando. Sem commit.

    Turma → matrículas daquela turma → dados da Matricula → HistoricoAnual.
    Reencerramento de turma já concluída e sem cursando: no-op (não duplica).
    """
    from src.matriculas import concluir_matriculas_da_turma

    if turma is None or turma.id is None:
        raise ValueError("Turma é obrigatória para encerramento.")

    cursando = _matriculas_cursando_da_turma(turma)
    ja_concluida = (turma.status or "").lower() == "concluida"
    if ja_concluida and not cursando:
        return RESULTADO_JA_ENCERRADA

    data_fim = data_fim or date.today()
    status_anterior = turma.status
    n_matriculas = len(cursando)
    n_historicos = 0
    for m in cursando:
        if int(m.turma_id) != int(turma.id):
            continue
        if historico_da_matricula(m) is None:
            n_historicos += 1
        _criar_snapshot_se_ausente(m, turma)
        aluno = m.aluno
        if aluno is not None and not aluno.escola_id:
            aluno.escola_id = turma.escola_id

    concluir_matriculas_da_turma(turma, data_fim)
    turma.status = "concluida"
    from src.auditoria import TURMA_ENCERRADA, registrar_auditoria
    registrar_auditoria(
        TURMA_ENCERRADA,
        "Turma",
        entidade_id=turma.id,
        escola_id=turma.escola_id,
        dados_anteriores={
            "turma_id": turma.id,
            "ano_letivo": turma.ano_letivo,
            "status": status_anterior,
        },
        dados_novos={
            "turma_id": turma.id,
            "ano_letivo": turma.ano_letivo,
            "status": turma.status,
            "n_matriculas_processadas": n_matriculas,
            "n_historicos_produzidos": n_historicos,
            "resultado": RESULTADO_ENCERRADA,
        },
    )
    return RESULTADO_ENCERRADA


def _turmas_candidatas_do_historico(hist):
    if not hist.turma_nome or hist.ano_letivo is None:
        return []
    turmas = Turma.query.filter_by(
        nome=hist.turma_nome, ano_letivo=hist.ano_letivo,
    ).all()
    if hist.escola_nome:
        turmas = [
            t for t in turmas
            if t.escola is not None and t.escola.nome == hist.escola_nome
        ]
    return turmas


def resolver_matricula_historico_legado(hist):
    """Associa só com evidência do próprio HistoricoAnual. Sem chute da matrícula atual.

    1) aluno_id + turma identificada (nome + ano [+ escola]) → exatamente uma Matricula.
    2) Sem turma_nome: aluno_id + ano da Turma da matrícula, se inequívoco.
    Zero ou mais de uma: None.
    """
    if hist is None or hist.aluno_id is None:
        return None, "sem_aluno"

    if hist.turma_nome:
        turmas = _turmas_candidatas_do_historico(hist)
        if len(turmas) != 1:
            motivo = "turmas_ambiguas" if len(turmas) > 1 else "turma_nao_encontrada"
            return None, motivo
        mats = Matricula.query.filter_by(
            aluno_id=hist.aluno_id, turma_id=turmas[0].id,
        ).all()
        if len(mats) == 1:
            return mats[0], None
        if not mats:
            return None, "zero_matriculas"
        return None, "multiplas_matriculas"

    if hist.ano_letivo is None:
        return None, "sem_ano"
    mats = (
        Matricula.query.filter_by(aluno_id=hist.aluno_id)
        .join(Turma, Matricula.turma_id == Turma.id)
        .filter(Turma.ano_letivo == hist.ano_letivo)
        .all()
    )
    if len(mats) == 1:
        return mats[0], None
    if not mats:
        return None, "zero_matriculas"
    return None, "multiplas_matriculas"


def _historico_ja_usa_matricula(matricula_id, ignorar_id=None):
    q = HistoricoAnual.query.filter_by(matricula_id=matricula_id)
    if ignorar_id is not None:
        q = q.filter(HistoricoAnual.id != ignorar_id)
    return q.first() is not None


def auditar_duplicidades_historico():
    """Conta duplicidade por matricula_id. Não apaga e não cria UNIQUE."""
    from sqlalchemy import text

    try:
        dups = list(db.session.execute(text(
            "SELECT matricula_id, COUNT(*) AS n FROM historico_anual "
            "WHERE matricula_id IS NOT NULL "
            "GROUP BY matricula_id HAVING COUNT(*) > 1"
        )))
    except Exception:
        dups = []
    return dups


def backfill_historicos_anuais():
    """Associa HistoricoAnual.matricula_id só quando for inequívoco.

    Não cria, não apaga, não altera JSON nem situação. Idempotente.
    Se a matrícula já tiver outro histórico, este permanece NULL (duplicidade legada).
    """
    global ULTIMO_DIAGNOSTICO_BACKFILL_HISTORICO, ULTIMO_RESUMO_BACKFILL_HISTORICO
    diagnosticos = []
    associados = 0
    ambiguos = 0
    sem = 0
    n_antes = HistoricoAnual.query.count()
    registros = HistoricoAnual.query.filter(HistoricoAnual.matricula_id.is_(None)).all()
    for hist in registros:
        json_antes = hist.dados_fechamento
        mat, motivo = resolver_matricula_historico_legado(hist)
        if mat is None:
            if motivo in ("multiplas_matriculas", "turmas_ambiguas"):
                ambiguos += 1
                tipo = "ambiguo"
                detalhe = (
                    "HistoricoAnual sem matrícula inequívoca; matricula_id permanece "
                    "nulo. Não foi atribuído à matrícula atual."
                )
            else:
                sem += 1
                tipo = "sem_matricula"
                detalhe = (
                    "Não há matrícula correspondente inequívoca; histórico preservado "
                    "com matricula_id nulo."
                )
            diagnosticos.append({
                "tipo": tipo,
                "registro": "historico_anual",
                "registro_id": hist.id,
                "aluno_id": hist.aluno_id,
                "ano_letivo": hist.ano_letivo,
                "turma_nome": hist.turma_nome,
                "motivo": motivo,
                "detalhe": detalhe,
            })
            continue
        if _historico_ja_usa_matricula(mat.id, ignorar_id=hist.id):
            ambiguos += 1
            diagnosticos.append({
                "tipo": "duplicidade_legado",
                "registro": "historico_anual",
                "registro_id": hist.id,
                "aluno_id": hist.aluno_id,
                "matricula_id": mat.id,
                "motivo": "matricula_ja_associada",
                "detalhe": (
                    "A matrícula já possui HistoricoAnual; este registro legado "
                    "permanece com matricula_id nulo para não duplicar o vínculo."
                ),
            })
            continue
        hist.matricula_id = mat.id
        hist.dados_fechamento = json_antes
        associados += 1
    if associados:
        db.session.commit()
    n_depois = HistoricoAnual.query.count()
    dups = auditar_duplicidades_historico()
    if dups:
        diagnosticos.append({
            "tipo": "duplicidade",
            "registro": "historico_anual",
            "quantidade": len(dups),
            "detalhe": (
                "Duplicidade de HistoricoAnual para a mesma matricula_id; "
                "este backfill não cria índice nem apaga linhas."
            ),
        })
    if n_depois != n_antes:
        diagnosticos.append({
            "tipo": "contagem_divergente",
            "detalhe": "A contagem de HistoricoAnual mudou durante o backfill.",
            "antes": n_antes,
            "depois": n_depois,
        })
    ULTIMO_DIAGNOSTICO_BACKFILL_HISTORICO[:] = diagnosticos
    ULTIMO_RESUMO_BACKFILL_HISTORICO.update({
        "associados": associados,
        "nao_associados": ambiguos + sem,
        "ambiguos": ambiguos,
        "sem_matricula": sem,
        "preservados": n_antes,
        "duplicidades": len(dups),
        "indice_unico": False,
    })
    return diagnosticos
