"""Montagem acadêmica do Histórico Escolar.

Cada componente cursado em determinado ano vira uma linha da tabela.
Anos fechados leem só HistoricoAnual; ano em curso lê lançamentos oficiais.
Matriz 2026 só entra em ano vigente. Legado não recebe matriz nem notas inventadas.
Ausência não vira zero. Educação Infantil não entra na grade numérica.
"""
from __future__ import annotations

from datetime import date

from src import db
from src.documentos_institucionais import dados_timbre, data_emissao_extenso
from src.historico import (
    carregar_dados_fechamento,
    historico_da_matricula,
    historico_e_infantil,
    identidade_congelada,
    snapshot_eh_novo,
)
from src.matriz_curricular import (
    ano_regido_pela_matriz,
    carga_oficial,
    chave_componente,
    componentes_da_serie,
    inferir_serie,
    matriz_vigente,
    etapa_da_serie,
)
from src.models import (
    Alocacao,
    AnoLetivo,
    CATEGORIAS_MATRIZ,
    Disciplina,
    HistoricoAnual,
    Matricula,
    NOMES_PERIODO_PADRAO,
    ORIGEM_TRAJETORIA_LEGADA,
    PeriodoLetivo,
)
from src.matriculas import VINCULO_CURSANDO, matricula_cursando
from src.trajetoria_legada import listar_do_aluno as listar_trajetoria_legada

MSG_SEM_TRAJETORIA_EF = (
    "Não há trajetória de Ensino Fundamental registrada para emitir o histórico escolar."
)
MSG_SO_EI = (
    "Este aluno possui somente registros da Educação Infantil. "
    "O parecer descritivo é o documento próprio dessa etapa."
)
MSG_SEM_TRANSFERENCIA = (
    "Não há matrícula de Ensino Fundamental em andamento para emitir "
    "histórico de transferência durante o período letivo."
)

ORIGEM_EDUCAMAIS = "EDUCA+"
SIT_CURSANDO = "CURSANDO"
TITULO_COMPONENTES = "COMPONENTES CURRICULARES CURSADOS/CURSANDO"


class DocumentoErro(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _bloco(dados, chave):
    valor = dados.get(chave) if isinstance(dados, dict) else None
    return valor if isinstance(valor, dict) else {}


def _identidade_cadastral(aluno, dados_snapshot=None):
    """Nome/nascimento/filiação: snapshot se houver; senão cadastro atual.

    Não é dado acadêmico. Campo ausente permanece nulo (não inventa).
    """
    snap = _bloco(dados_snapshot, "aluno") if dados_snapshot else {}
    def _pick(chave_snap, attr):
        if snap.get(chave_snap) not in (None, ""):
            return snap.get(chave_snap)
        return getattr(aluno, attr, None) if aluno is not None else None

    nasc = _pick("data_nascimento", "data_nascimento")
    if hasattr(nasc, "isoformat"):
        nasc = nasc.isoformat()
    return {
        "id": aluno.id if aluno is not None else snap.get("id"),
        "nome": _pick("nome", "nome"),
        "data_nascimento": nasc,
        "local_nascimento": _pick("local_nascimento", "local_nascimento"),
        "nome_mae": _pick("nome_mae", "nome_mae"),
        "nome_pai": _pick("nome_pai", "nome_pai"),
        "cpf": None,
    }


def _escola_atual(aluno):
    if aluno is None:
        return {}
    escola = aluno.escola
    if escola is None and aluno.turma is not None:
        escola = aluno.turma.escola
    if escola is None:
        return {}
    return {
        "id": escola.id,
        "nome": escola.nome,
        "inep": escola.inep,
        "endereco": None,
    }


def valor_historico_componente(comp):
    """Nota/média/conceito do componente. Ausência permanece None (nunca 0 inventado).

    Conceito textual não é convertido para número.
    """
    if not comp:
        return None
    bruto = comp.get("nota_conceito")
    if bruto is not None:
        texto = str(bruto).strip()
        if texto:
            return texto
    media = comp.get("media")
    if media is None:
        return None
    if isinstance(media, str):
        texto = media.strip()
        return texto if texto else None
    return media


def _categoria_oficial(ano_letivo, serie, nome):
    if not ano_regido_pela_matriz(ano_letivo, serie):
        return None
    chave = chave_componente(nome)
    if not chave:
        return None
    for item in componentes_da_serie(ano_letivo, serie):
        if chave_componente(item.nome) == chave:
            cat = item.categoria
            if cat in CATEGORIAS_MATRIZ:
                return cat
    return None


def _anotar_categoria(ano_letivo, serie, comps):
    if not ano_regido_pela_matriz(ano_letivo, serie):
        return
    for c in comps:
        if c.get("categoria") in CATEGORIAS_MATRIZ:
            continue
        cat = _categoria_oficial(ano_letivo, serie, c.get("nome"))
        if cat:
            c["categoria"] = cat


def _ch_total_educamais(ano_letivo, serie):
    matriz = matriz_vigente(ano_letivo, etapa_da_serie(serie))
    if matriz is None:
        return None
    return matriz.carga_horaria_anual


def _origem_ano(ano):
    if (ano.get("fonte") or "") == "legado" or ano.get("origem") == ORIGEM_TRAJETORIA_LEGADA:
        return ORIGEM_TRAJETORIA_LEGADA
    return ORIGEM_EDUCAMAIS


def _municipio_uf_texto(mun, uf):
    mun = (mun or "").strip()
    uf = (uf or "").strip()
    if mun and uf:
        return "%s/%s" % (mun, uf)
    return mun or uf or None


def _rotulo_serie(serie, turma_nome=None):
    if serie:
        return "%sº ano" % serie
    return turma_nome


def _preparar_ano(ano):
    """Localidade e CH oficiais do próprio período. Não usa escola atual do aluno."""
    origem = _origem_ano(ano)
    if origem != ORIGEM_TRAJETORIA_LEGADA:
        if not ((ano.get("municipio") or "").strip() or (ano.get("uf") or "").strip()):
            t = dados_timbre()
            ano["municipio"] = t.get("municipio")
            ano["uf"] = t.get("uf")
        if ano_regido_pela_matriz(ano.get("ano_letivo"), ano.get("serie")):
            for c in ano.get("componentes") or []:
                if c.get("carga_horaria_anual") is None:
                    ch = carga_oficial(ano.get("ano_letivo"), ano.get("serie"), c.get("nome"))
                    if ch is not None:
                        c["carga_horaria_anual"] = ch
    return ano


def _linha_trajetoria(ano):
    origem = _origem_ano(ano)
    ch = ano.get("carga_horaria_total")
    if ch is None and origem == ORIGEM_EDUCAMAIS:
        ch = _ch_total_educamais(ano.get("ano_letivo"), ano.get("serie"))
    resultado = ano.get("resultado")
    if not resultado and origem == ORIGEM_EDUCAMAIS:
        vinculo = (ano.get("vinculo") or "").lower()
        if vinculo == VINCULO_CURSANDO or ano.get("fonte") == "operacional":
            resultado = "Em curso"
    return {
        "ano_letivo": ano.get("ano_letivo"),
        "serie": ano.get("serie"),
        "estabelecimento": ano.get("escola_nome"),
        "municipio": ano.get("municipio"),
        "uf": ano.get("uf"),
        "carga_horaria_total": ch,
        "resultado": resultado,
        "origem": origem,
        "turma_nome": ano.get("turma_nome"),
    }


def _ordem_curricular(ano_letivo, serie, nome):
    if not ano_regido_pela_matriz(ano_letivo, serie):
        return None
    chave = chave_componente(nome)
    if not chave:
        return None
    for item in componentes_da_serie(ano_letivo, serie):
        if chave_componente(item.nome) == chave:
            return item.ordem
    return None


def _chave_ordem_comp(ano, comp, idx):
    nome = (comp.get("nome") or "").strip()
    if _origem_ano(ano) == ORIGEM_TRAJETORIA_LEGADA:
        ordem = comp.get("ordem")
        if ordem is None:
            ordem = idx
        return (ordem, nome.casefold())
    curricular = _ordem_curricular(ano.get("ano_letivo"), ano.get("serie"), nome)
    if curricular is not None:
        return (curricular, nome.casefold())
    return (10000 + idx, nome.casefold())


def _situacao_linha(ano, comp):
    if _origem_ano(ano) == ORIGEM_TRAJETORIA_LEGADA:
        texto = comp.get("situacao")
        if texto is None:
            return None
        texto = str(texto).strip()
        return texto or None
    if ano.get("fonte") == "operacional":
        return SIT_CURSANDO
    texto = comp.get("resultado") or comp.get("situacao")
    if texto is None:
        return None
    texto = str(texto).strip()
    return texto or None


def _media_aplicavel_periodo(ano, comp=None):
    """Média confiável daquele período, só para cor da nota no PDF.

    Não consulta a configuração vigente e não inventa critério.
    Trajetória legada fica sem média. Snapshot usa o valor congelado do ano
    ou, se ele não existir, o corte gravado no componente.
    Ano em curso usa o corte que o motor já devolveu no componente.
    """
    if ano is None or _origem_ano(ano) == ORIGEM_TRAJETORIA_LEGADA:
        return None
    if ano.get("media_minima") is not None:
        return ano.get("media_minima")
    if comp is not None and comp.get("media_minima") is not None:
        return comp.get("media_minima")
    return None


def _media_periodo_transferencia(ano):
    """Mesma média do período em curso. Complemento manual não cria outra regra."""
    media = _media_aplicavel_periodo(ano)
    if media is not None or ano is None or _origem_ano(ano) == ORIGEM_TRAJETORIA_LEGADA:
        return media
    for item in ano.get("componentes") or []:
        if item.get("media_minima") is not None:
            return item.get("media_minima")
    return None


def _linha_componente(ano, comp, idx):
    nome = (comp.get("nome") or "").strip()
    if not nome:
        return None
    mun = ano.get("municipio")
    uf = ano.get("uf")
    return {
        "ano_letivo": ano.get("ano_letivo"),
        "serie": ano.get("serie"),
        "serie_rotulo": _rotulo_serie(ano.get("serie"), ano.get("turma_nome")),
        "escola": ano.get("escola_nome"),
        "municipio": mun,
        "uf": uf,
        "municipio_uf": _municipio_uf_texto(mun, uf),
        "componente": nome,
        "carga_horaria": comp.get("carga_horaria_anual"),
        "nota_conceito": valor_historico_componente(comp),
        "situacao": _situacao_linha(ano, comp),
        "media_minima": _media_aplicavel_periodo(ano, comp),
        "origem": _origem_ano(ano),
        "fonte": comp.get("fonte") or ano.get("fonte"),
        "ordem": idx,
    }


def _componentes_cursados(anos):
    linhas = []
    for ano in anos:
        comps = list(ano.get("componentes") or [])
        indexed = list(enumerate(comps))
        indexed.sort(key=lambda par: _chave_ordem_comp(ano, par[1], par[0]))
        for idx, comp in indexed:
            linha = _linha_componente(ano, comp, idx)
            if linha:
                linhas.append(linha)
    return linhas


def _chaves_educamais(fechados, aberto):
    chaves = set()
    for a in list(fechados) + ([aberto] if aberto else []):
        chaves.add((a.get("ano_letivo"), a.get("serie")))
    return chaves


def _legados_sem_conflito(legados, chaves):
    saida = []
    for item in legados:
        if (item.get("ano_letivo"), item.get("serie")) in chaves:
            continue
        saida.append(item)
    return saida


def _anos_de_legado(aluno_id):
    saida = []
    for item in listar_trajetoria_legada(aluno_id):
        comps = []
        for c in item.get("componentes") or []:
            comps.append({
                "nome": c.get("nome"),
                "disciplina_id": None,
                "carga_horaria_anual": c.get("carga_horaria"),
                "mb1": None,
                "mb2": None,
                "mb3": None,
                "mb4": None,
                "media": None,
                "nota_conceito": c.get("nota_conceito"),
                "situacao": c.get("situacao"),
                "faltas": c.get("faltas"),
                "frequencia": c.get("frequencia"),
                "resultado": None,
                "aplicavel": True,
                "fonte": "legado",
                "categoria": c.get("categoria"),
                "ordem": c.get("ordem"),
            })
        saida.append({
            "fonte": "legado",
            "origem": ORIGEM_TRAJETORIA_LEGADA,
            "historico_id": None,
            "legado_id": item.get("id"),
            "matricula_id": None,
            "ano_letivo": item.get("ano_letivo"),
            "serie": item.get("serie"),
            "etapa": "fundamental",
            "turma_nome": ("%sº ano" % item["serie"]) if item.get("serie") else None,
            "escola_nome": item.get("estabelecimento"),
            "escola_inep": None,
            "municipio": item.get("municipio"),
            "uf": item.get("uf"),
            "vinculo": None,
            "resultado": item.get("resultado"),
            "carga_horaria_total": item.get("carga_horaria_total"),
            "observacao": item.get("observacao"),
            "media_minima": None,
            "componentes": comps,
            "matriz_aplicada": False,
            "suficiente": True,
            "lacunas": [],
        })
    return saida


def _componente_de_snapshot(item):
    if not isinstance(item, dict):
        return None
    nome = item.get("disciplina_nome") or item.get("disciplina")
    if not nome:
        return None
    return {
        "nome": nome,
        "disciplina_id": item.get("disciplina_id"),
        "carga_horaria_anual": item.get("carga_horaria_anual"),
        "mb1": item.get("mb1"),
        "mb2": item.get("mb2"),
        "mb3": item.get("mb3"),
        "mb4": item.get("mb4"),
        "media": item.get("ma"),
        "faltas": item.get("faltas"),
        "resultado": item.get("resultado") or item.get("situacao"),
        "aplicavel": True,
        "fonte": "snapshot",
        "media_minima": item.get("media_minima"),
    }


def _ano_do_snapshot(hist, dados):
    ident = identidade_congelada(hist, dados)
    return ident.get("ano_letivo") if ident.get("ano_letivo") is not None else hist.ano_letivo


def _ano_fechado_do_historico(hist):
    dados = carregar_dados_fechamento(hist)
    if historico_e_infantil(hist, dados):
        return None
    ident = identidade_congelada(hist, dados)
    serie = inferir_serie(ident.get("turma_nome") or hist.turma_nome)
    comps = []
    for item in dados.get("disciplinas") or []:
        c = _componente_de_snapshot(item)
        if c:
            comps.append(c)
    ano_letivo = _ano_do_snapshot(hist, dados)
    _anotar_categoria(ano_letivo, serie, comps)
    return {
        "fonte": "snapshot",
        "historico_id": hist.id,
        "matricula_id": hist.matricula_id,
        "ano_letivo": _ano_do_snapshot(hist, dados),
        "serie": serie,
        "etapa": "fundamental",
        "turma_nome": ident.get("turma_nome") or hist.turma_nome,
        "escola_nome": ident.get("escola_nome") or hist.escola_nome,
        "escola_inep": ident.get("escola_inep"),
        "municipio": None,
        "uf": None,
        "vinculo": _bloco(dados, "matricula").get("situacao"),
        "resultado": None,
        "carga_horaria_total": _ch_total_educamais(_ano_do_snapshot(hist, dados), serie),
        "origem": ORIGEM_EDUCAMAIS,
        "media_minima": (_bloco(dados, "criterios_academicos") or {}).get("media_minima"),
        "componentes": comps,
        "matriz_aplicada": False,
        "suficiente": snapshot_eh_novo(dados) and bool(ident.get("aluno_nome")),
        "lacunas": [] if snapshot_eh_novo(dados) else ["snapshot_legado_insuficiente"],
    }


def _ids_componentes_operacionais(matricula):
    from src.historico import (
        _faltas_da_matricula,
        _frequencias_da_matricula,
        _ids_disciplinas_aplicaveis,
        _notas_da_matricula,
    )
    ids = set(_ids_disciplinas_aplicaveis(matricula))
    if not ids:
        for n in _notas_da_matricula(matricula):
            if n.disciplina_id:
                ids.add(n.disciplina_id)
        for f in _faltas_da_matricula(matricula):
            if f.disciplina_id:
                ids.add(f.disciplina_id)
        for r in _frequencias_da_matricula(matricula):
            if r.disciplina_id:
                ids.add(r.disciplina_id)
    return ids


def _carga_operacional(matricula, disc, serie):
    turma = matricula.turma
    if turma is not None:
        aloc = Alocacao.query.filter_by(
            turma_id=turma.id, disciplina_id=disc.id,
        ).first()
        if aloc is not None and aloc.carga_horaria_prevista is not None:
            return aloc.carga_horaria_prevista
        return carga_oficial(turma.ano_letivo, serie, disc.nome)
    return None


def _componente_operacional(matricula, disc, serie, resumo):
    return {
        "nome": disc.nome,
        "disciplina_id": disc.id,
        "carga_horaria_anual": _carga_operacional(matricula, disc, serie),
        "mb1": resumo.get("mb1"),
        "mb2": resumo.get("mb2"),
        "mb3": resumo.get("mb3"),
        "mb4": resumo.get("mb4"),
        "media": resumo.get("ma"),
        "faltas": resumo.get("faltas"),
        "resultado": resumo.get("situacao"),
        "aplicavel": True,
        "fonte": "operacional",
        "media_minima": resumo.get("media_minima"),
        "n1": resumo.get("n1"),
        "n2": resumo.get("n2"),
        "n3": resumo.get("n3"),
        "n4": resumo.get("n4"),
        "n5": resumo.get("n5"),
        "n6": resumo.get("n6"),
        "n7": resumo.get("n7"),
        "n8": resumo.get("n8"),
    }


def _preencher_matriz_vigente(ano, serie, ja_presentes):
    """Acrescenta componentes oficiais do ano vigente ainda sem registro.

    Notas/faltas ficam None (nunca zero). Não roda para ano fora da vigência.
    Não preenche trajetória anterior à adoção do Educa+.
    """
    if not ano_regido_pela_matriz(ano, serie):
        return []
    chaves = {chave_componente(c["nome"]) for c in ja_presentes}
    extras = []
    for oficial in componentes_da_serie(ano, serie):
        chave = chave_componente(oficial.nome)
        if chave in chaves:
            for c in ja_presentes:
                if chave_componente(c["nome"]) == chave and c.get("carga_horaria_anual") is None:
                    c["carga_horaria_anual"] = oficial.carga_horaria_anual
            continue
        extras.append({
            "nome": oficial.nome,
            "disciplina_id": None,
            "carga_horaria_anual": oficial.carga_horaria_anual,
            "mb1": None,
            "mb2": None,
            "mb3": None,
            "mb4": None,
            "media": None,
            "faltas": None,
            "resultado": None,
            "aplicavel": True,
            "fonte": "matriz",
            "categoria": oficial.categoria,
        })
        chaves.add(chave)
    return extras


def _ano_em_curso(matricula):
    from src.notas import calcular_notas_matricula

    turma = matricula.turma
    if turma is None or (turma.etapa_ensino or "") == "infantil":
        return None
    serie = inferir_serie(turma.nome)
    ano = turma.ano_letivo
    escola = turma.escola
    comps = []
    for disc_id in sorted(_ids_componentes_operacionais(matricula)):
        disc = db.session.get(Disciplina, disc_id)
        if disc is None:
            continue
        resumo = calcular_notas_matricula(matricula.id, disc.id)
        comps.append(_componente_operacional(matricula, disc, serie, resumo))
    comps.extend(_preencher_matriz_vigente(ano, serie, comps))
    _anotar_categoria(ano, serie, comps)
    comps.sort(key=lambda c: ((c.get("nome") or ""), c.get("disciplina_id") or 0))
    return {
        "fonte": "operacional",
        "historico_id": None,
        "matricula_id": matricula.id,
        "ano_letivo": ano,
        "serie": serie,
        "etapa": "fundamental",
        "turma_nome": turma.nome,
        "escola_nome": escola.nome if escola is not None else None,
        "escola_inep": escola.inep if escola is not None else None,
        "municipio": None,
        "uf": None,
        "vinculo": matricula.situacao,
        "resultado": "Em curso" if (matricula.situacao or "").lower() == VINCULO_CURSANDO else None,
        "carga_horaria_total": _ch_total_educamais(ano, serie),
        "origem": ORIGEM_EDUCAMAIS,
        "media_minima": None,
        "componentes": comps,
        "matriz_aplicada": ano_regido_pela_matriz(ano, serie),
        "suficiente": True,
        "lacunas": [],
        "turma_status": turma.status,
    }


def _historicos_ef_do_aluno(aluno_id):
    registros = (
        HistoricoAnual.query.filter_by(aluno_id=aluno_id)
        .order_by(HistoricoAnual.ano_letivo.asc(), HistoricoAnual.id.asc())
        .all()
    )
    ef, ei_anos = [], []
    for hist in registros:
        dados = carregar_dados_fechamento(hist)
        if historico_e_infantil(hist, dados):
            ano = _ano_do_snapshot(hist, dados)
            if ano is not None:
                ei_anos.append(ano)
            continue
        item = _ano_fechado_do_historico(hist)
        if item is not None:
            ef.append(item)
    return ef, ei_anos


def _matricula_fundamental_aberta(aluno):
    mat = matricula_cursando(aluno)
    if mat is None or mat.turma is None:
        return None
    if (mat.turma.etapa_ensino or "") == "infantil":
        return None
    if historico_da_matricula(mat) is not None:
        return None
    return mat


def _periodos_do_ano(ano_letivo):
    if ano_letivo is None:
        return []
    ano = AnoLetivo.query.filter_by(ano=int(ano_letivo)).first()
    if ano is None:
        return [
            {"numero": n, "nome": NOMES_PERIODO_PADRAO[n], "futuro": False, "datas": False}
            for n in (1, 2, 3, 4)
        ]
    lista = (
        PeriodoLetivo.query.filter_by(ano_letivo_id=ano.id)
        .order_by(PeriodoLetivo.numero.asc())
        .all()
    )
    if not lista:
        return [
            {"numero": n, "nome": NOMES_PERIODO_PADRAO[n], "futuro": False, "datas": False}
            for n in (1, 2, 3, 4)
        ]
    hoje = date.today()
    saida = []
    for p in lista:
        futuro = bool(p.data_inicio and hoje < p.data_inicio)
        saida.append({
            "numero": p.numero,
            "nome": p.nome or NOMES_PERIODO_PADRAO.get(p.numero) or ("%sº período" % p.numero),
            "futuro": futuro,
            "datas": True,
            "data_inicio": p.data_inicio.isoformat() if p.data_inicio else None,
            "data_fim": p.data_fim.isoformat() if p.data_fim else None,
        })
    return saida


def _faltas_periodo(matricula, disciplina_id):
    from src.frequencia import faltas_por_bimestre

    mapa = faltas_por_bimestre(
        matricula.aluno_id, disciplina_id, matricula_id=matricula.id,
    )
    return mapa


def _secao_transferencia(matricula, ano_payload, aplicar_manual=True):
    """Rendimento parcial do ano em curso. Período futuro: None, nunca 0 inventado.

    Complemento manual só entra na emissão de transferência e só preenche
    célula ainda ausente. Não altera o histórico escolar definitivo.
    """
    if matricula is None or ano_payload is None:
        return None
    periodos = _periodos_do_ano(ano_payload.get("ano_letivo"))
    linhas = []
    for comp in ano_payload.get("componentes") or []:
        disc_id = comp.get("disciplina_id")
        faltas = _faltas_periodo(matricula, disc_id) if disc_id else {}
        periodos_comp = []
        for p in periodos:
            n = p["numero"]
            if p.get("futuro"):
                periodos_comp.append({
                    "numero": n,
                    "nome": p["nome"],
                    "nota": None,
                    "faltas": None,
                    "futuro": True,
                })
                continue
            mb = comp.get("mb%s" % n)
            faltas_p = faltas.get(n)
            periodos_comp.append({
                "numero": n,
                "nome": p["nome"],
                "nota": mb,
                "faltas": faltas_p,
                "futuro": False,
            })
        linhas.append({
            "nome": comp.get("nome"),
            "disciplina_id": disc_id,
            "periodos": periodos_comp,
        })
    secao = {
        "ano_letivo": ano_payload.get("ano_letivo"),
        "turma_nome": ano_payload.get("turma_nome"),
        "escola_nome": ano_payload.get("escola_nome"),
        "periodos": periodos,
        "componentes": linhas,
        "media_minima": _media_periodo_transferencia(ano_payload),
    }
    if not aplicar_manual:
        return secao
    from src.complemento_transferencia import mesclar_complemento
    return mesclar_complemento(matricula, secao)


def _observacoes(anos_ef, ei_anos, transferencia):
    partes = []
    if any(_origem_ano(a) == ORIGEM_TRAJETORIA_LEGADA for a in anos_ef):
        partes.append(
            "Períodos anteriores à adoção do Educa+ foram transcritos do histórico "
            "ou documento apresentado à escola."
        )
    legado_snap = [a for a in anos_ef if "snapshot_legado_insuficiente" in (a.get("lacunas") or [])]
    if legado_snap:
        partes.append(
            "Há período letivo sem informação suficiente para reconstituir o rendimento; "
            "as células correspondentes permanecem em branco."
        )
    if ei_anos:
        anos = ", ".join(str(a) for a in sorted(set(ei_anos)))
        partes.append(
            "Há registro de Educação Infantil (%s). O parecer descritivo é documento próprio."
            % anos
        )
    if transferencia:
        partes.append(
            "A seção de rendimento e frequência refere-se à transferência durante "
            "o período letivo em curso. Períodos ainda não iniciados não recebem nota."
        )
    return " ".join(partes)


def montar_historico_escolar(aluno, para_transferencia=False):
    """Coleta fiel. Não recalcula média histórica nem aplica matriz vigente no passado.

    para_transferencia: intenção explícita de emissão para transferência
    durante o período letivo. Não se infere de turma aberta.
    """
    if aluno is None:
        raise DocumentoErro("Aluno não encontrado", 404)

    fechados, ei_anos = _historicos_ef_do_aluno(aluno.id)
    legados = _anos_de_legado(aluno.id)
    aberta = _matricula_fundamental_aberta(aluno)
    aberto = _ano_em_curso(aberta) if aberta is not None else None

    ids_matricula_fechada = {a.get("matricula_id") for a in fechados if a.get("matricula_id")}
    if aberto and aberto.get("matricula_id") in ids_matricula_fechada:
        aberto = None
        aberta = None

    legados = _legados_sem_conflito(legados, _chaves_educamais(fechados, aberto))
    anos = list(legados) + list(fechados)
    if aberto:
        anos.append(aberto)
    anos.sort(key=lambda a: (
        a.get("ano_letivo") is None,
        a.get("ano_letivo") or 0,
        a.get("serie") or 0,
        0 if a.get("fonte") == "legado" else 1,
        a.get("historico_id") or 0,
        a.get("legado_id") or 0,
    ))
    for ano in anos:
        _preparar_ano(ano)

    iniciais = [a for a in anos if a.get("serie") is not None and a["serie"] <= 5]
    finais = [a for a in anos if a.get("serie") is not None and a["serie"] >= 6]
    sem_serie = [a for a in anos if a.get("serie") is None]

    transferencia = None
    if para_transferencia:
        st = (aberto.get("turma_status") or "").lower() if aberto else ""
        if aberto is None or aberta is None or st == "concluida":
            raise DocumentoErro(MSG_SEM_TRANSFERENCIA, 400)
        transferencia = _secao_transferencia(aberta, aberto)

    if not anos:
        if ei_anos:
            raise DocumentoErro(MSG_SO_EI, 400)
        raise DocumentoErro(MSG_SEM_TRAJETORIA_EF, 400)

    ident = _identidade_cadastral(aluno)
    for hist in (
        HistoricoAnual.query.filter_by(aluno_id=aluno.id)
        .order_by(HistoricoAnual.id.desc())
        .all()
    ):
        dados = carregar_dados_fechamento(hist)
        if snapshot_eh_novo(dados):
            ident = _identidade_cadastral(aluno, dados)
            break

    escola_emissora = _escola_atual(aluno)
    if not escola_emissora.get("nome"):
        for a in reversed(anos):
            if a.get("escola_nome"):
                escola_emissora = {
                    "id": None,
                    "nome": a.get("escola_nome"),
                    "inep": a.get("escola_inep"),
                    "endereco": None,
                }
                break

    trajetoria = [_linha_trajetoria(a) for a in anos]
    linhas = _componentes_cursados(anos)

    return {
        "tipo": "historico_transferencia" if para_transferencia else "historico_escolar",
        "timbre": dados_timbre(),
        "titulo": "HISTÓRICO ESCOLAR",
        "titulo_tabela": TITULO_COMPONENTES,
        "aluno": ident,
        "escola": escola_emissora,
        "trajetoria": trajetoria,
        "componentes_cursados": linhas,
        "anos_iniciais": iniciais,
        "anos_finais": finais,
        "anos_sem_serie": sem_serie,
        "transferencia": transferencia,
        "observacoes": _observacoes(anos, ei_anos, transferencia),
        "data_emissao": data_emissao_extenso(),
        "assinaturas": {
            "secretario": "Secretário(a)",
            "diretor": "Diretor(a)",
            "autenticacao": "Reservado para autenticação",
        },
    }
