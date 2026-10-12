"""Geração de PDF A4 institucional (reportlab).

Texto pesquisável, sem navegador, sem URL localhost.
Uma biblioteca: reportlab. Helvetica/Times embutidas (Windows e deploy).
"""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.documentos_institucionais import (
    CELULA_AUSENTE,
    caminho_brasao,
    celula,
    dados_timbre,
    data_emissao_extenso,
    formatar_data,
    formatar_inteiro,
    formatar_nota,
    formatar_nota_pdf,
)

MARGEM = 14 * mm
COR_LINHA = colors.HexColor("#1a1a1a")
COR_CAB = colors.HexColor("#f0f0f0")
COR_TITULO = colors.HexColor("#111111")
COR_FAIXA = colors.HexColor("#e8eef4")
# Notas numéricas: vermelho e azul escuros, legíveis em branco e na impressão.
COR_NOTA_ABAIXO = "#9E2A2A"
COR_NOTA_ATINGIDA = "#1F4E79"
_CORES_FAIXA_NOTA = {
    "abaixo": COR_NOTA_ABAIXO,
    "atingida": COR_NOTA_ATINGIDA,
}
_ESTILOS_NOTA = {}
SECRETARIA_DOC = "Secretaria Municipal de Educação — Palmeira do Piauí"
A4_PAISAGEM = landscape(A4)
LARGURA_RETRATO = A4[0] - 2 * MARGEM
LARGURA_PAISAGEM = A4_PAISAGEM[0] - 2 * MARGEM
# Marca-d'água: somente Histórico Escolar e Transferência. Nunca no _doc_base.
OPACIDADE_MARCA_DAGUA = 0.08
FRACAO_LARGURA_MARCA_DAGUA = 0.52
TIPOS_COM_MARCA_DAGUA = frozenset({"historico_escolar", "historico_transferencia"})
_CACHE_MARCA_DAGUA = {}


class DocumentoErroPdf(Exception):
    def __init__(self, message, status=500):
        super().__init__(message)
        self.message = message
        self.status = status


def _estilos():
    base = getSampleStyleSheet()
    return {
        "org": ParagraphStyle(
            "org", parent=base["Normal"], fontName="Times-Bold",
            fontSize=11, leading=13, alignment=TA_CENTER, textColor=COR_TITULO,
        ),
        "sec": ParagraphStyle(
            "sec", parent=base["Normal"], fontName="Times-Bold",
            fontSize=10, leading=12, alignment=TA_CENTER,
        ),
        "escola": ParagraphStyle(
            "escola", parent=base["Normal"], fontName="Times-Roman",
            fontSize=9, leading=11, alignment=TA_CENTER,
        ),
        "detalhe": ParagraphStyle(
            "detalhe", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=9.5, alignment=TA_CENTER, textColor=COR_TITULO,
        ),
        "detalhe_ini": ParagraphStyle(
            "detalhe_ini", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=9.5, alignment=TA_CENTER, textColor=COR_TITULO,
            spaceBefore=2,
        ),
        "escola_timbre": ParagraphStyle(
            "escola_timbre", parent=base["Normal"], fontName="Times-Bold",
            fontSize=9.5, leading=11.5, alignment=TA_CENTER, spaceBefore=2,
        ),
        "titulo": ParagraphStyle(
            "titulo", parent=base["Normal"], fontName="Times-Bold",
            fontSize=14, leading=18, alignment=TA_CENTER, spaceBefore=6, spaceAfter=8,
        ),
        "secao": ParagraphStyle(
            "secao", parent=base["Normal"], fontName="Times-Bold",
            fontSize=10, leading=13, alignment=TA_LEFT, spaceBefore=8, spaceAfter=4,
        ),
        "corpo": ParagraphStyle(
            "corpo", parent=base["Normal"], fontName="Times-Roman",
            fontSize=9, leading=12, alignment=TA_JUSTIFY,
        ),
        "rotulo": ParagraphStyle(
            "rotulo", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8, leading=10,
        ),
        "valor": ParagraphStyle(
            "valor", parent=base["Normal"], fontName="Times-Roman",
            fontSize=9, leading=11,
        ),
        "cel": ParagraphStyle(
            "cel", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10, alignment=TA_CENTER,
        ),
        "cel_esq": ParagraphStyle(
            "cel_esq", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10, alignment=TA_LEFT,
        ),
        "th": ParagraphStyle(
            "th", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8, leading=10, alignment=TA_CENTER,
        ),
        "th_esq": ParagraphStyle(
            "th_esq", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8, leading=10, alignment=TA_LEFT,
        ),
        "pequeno": ParagraphStyle(
            "pequeno", parent=base["Normal"], fontName="Times-Italic",
            fontSize=8, leading=10, alignment=TA_RIGHT,
        ),
        "assin": ParagraphStyle(
            "assin", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10, alignment=TA_CENTER,
        ),
        "obs": ParagraphStyle(
            "obs", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=11, alignment=TA_JUSTIFY,
        ),
        "grade": ParagraphStyle(
            "grade", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10, alignment=TA_CENTER,
        ),
        "grade_esq": ParagraphStyle(
            "grade_esq", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10, alignment=TA_LEFT,
        ),
        "cel_hist": ParagraphStyle(
            "cel_hist", parent=base["Normal"], fontName="Times-Roman",
            fontSize=9, leading=11, alignment=TA_LEFT,
        ),
        "cel_hist_c": ParagraphStyle(
            "cel_hist_c", parent=base["Normal"], fontName="Times-Roman",
            fontSize=9, leading=11, alignment=TA_CENTER,
        ),
        "th_hist": ParagraphStyle(
            "th_hist", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8.5, leading=10.5, alignment=TA_CENTER,
        ),
        "th_hist_esq": ParagraphStyle(
            "th_hist_esq", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8.5, leading=10.5, alignment=TA_LEFT,
        ),
        "resumo_hist": ParagraphStyle(
            "resumo_hist", parent=base["Normal"], fontName="Times-Italic",
            fontSize=8, leading=10, alignment=TA_LEFT,
        ),
    }


def _bytes_marca_dagua(opacidade=None):
    """PNG colorido com alpha reduzido. Não altera o arquivo original do brasão."""
    opacidade = OPACIDADE_MARCA_DAGUA if opacidade is None else opacidade
    chave = round(float(opacidade), 4)
    if chave in _CACHE_MARCA_DAGUA:
        return _CACHE_MARCA_DAGUA[chave]
    path = caminho_brasao()
    if not path:
        _CACHE_MARCA_DAGUA[chave] = None
        return None
    from PIL import Image as PILImage
    with PILImage.open(path) as original:
        img = original.convert("RGBA")
        tamanho = img.size
        canais = img.split()
        alpha = canais[3].point(lambda p, o=chave: int(p * o))
        img = PILImage.merge("RGBA", (canais[0], canais[1], canais[2], alpha))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
    _CACHE_MARCA_DAGUA[chave] = (tamanho, buf.getvalue())
    return _CACHE_MARCA_DAGUA[chave]


def desenhar_marca_dagua_brasao(canvas, doc):
    """Callback de página. Chamar SOMENTE em Histórico e Transferência.

    Pintado em onFirstPage/onLaterPages (antes dos flowables) para ficar atrás
    do texto. Não usar como padrão de _doc_base/_build_pdf.
    """
    dados = _bytes_marca_dagua()
    if not dados:
        return
    (iw, ih), bruto = dados
    if not iw or not ih:
        return
    page_w, page_h = doc.pagesize
    usable_w = page_w - doc.leftMargin - doc.rightMargin
    target_w = usable_w * FRACAO_LARGURA_MARCA_DAGUA
    target_h = target_w * (ih / float(iw))
    max_h = page_h * 0.72
    if target_h > max_h:
        target_h = max_h
        target_w = target_h * (iw / float(ih))
    x = (page_w - target_w) / 2.0
    y = (page_h - target_h) / 2.0
    canvas.saveState()
    canvas.drawImage(
        ImageReader(io.BytesIO(bruto)),
        x,
        y,
        width=target_w,
        height=target_h,
        mask="auto",
        preserveAspectRatio=True,
        anchor="sw",
    )
    canvas.restoreState()


def _p(texto, estilo):
    return Paragraph(_escape(texto if texto is not None else ""), estilo)


def _regra_nota(registro, fallback=None):
    """Média já anexada ao registro ou a do documento. Não consulta a vigente."""
    if isinstance(registro, dict) and registro.get("media_minima") is not None:
        return registro.get("media_minima")
    return fallback


def _estilo_nota(estilo, cor_hex):
    chave = (id(estilo), cor_hex)
    achado = _ESTILOS_NOTA.get(chave)
    if achado is not None:
        return achado
    novo = ParagraphStyle(
        "%s_nota_%s" % (estilo.name, cor_hex[1:]),
        parent=estilo,
        textColor=colors.HexColor(cor_hex),
    )
    _ESTILOS_NOTA[chave] = novo
    return novo


def _p_nota(valor, estilo, regra_academica=None):
    """Nota numérica colorida. Conceito, traço e ausência permanecem na cor do estilo."""
    apresentacao = formatar_nota_pdf(valor, regra_academica)
    cor = _CORES_FAIXA_NOTA.get(apresentacao["faixa"])
    if cor:
        estilo = _estilo_nota(estilo, cor)
    return _p(apresentacao["texto"], estilo)


def _escape(texto):
    return (
        str(texto)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _linha_escola_timbre(escola):
    """Última linha do timbre. Nome e INEP vêm da escola já escolhida pelo gerador."""
    if not escola:
        return None
    nome = (escola.get("nome") or "").strip()
    inep = escola.get("inep")
    inep_txt = "" if inep is None else str(inep).strip()
    if nome and inep_txt:
        return "%s (INEP: %s)" % (nome, inep_txt)
    if nome:
        return nome
    if inep_txt:
        return "INEP: %s" % inep_txt
    return None


def _timbre_flowable(st, escola=None, largura=None):
    largura = largura if largura is not None else LARGURA_RETRATO
    t = dados_timbre()
    textos = [
        _p(t["prefeitura"], st["org"]),
        _p(t["secretaria"], st["sec"]),
        _p(t["cnpj_linha"], st["detalhe_ini"]),
        _p(t["municipio_uf"], st["detalhe"]),
        _p(t["autorizacao"], st["detalhe"]),
    ]
    if escola and escola.get("endereco"):
        textos.append(_p(escola["endereco"], st["detalhe"]))
    linha_escola = _linha_escola_timbre(escola)
    if linha_escola:
        textos.append(_p(linha_escola, st["escola_timbre"]))
    bloco_texto = textos
    logo = t.get("logo")
    if logo:
        img = Image(logo, width=28 * mm, height=18 * mm)
        img.preserveAspectRatio = True
        img.hAlign = "CENTER"
        tab = Table([[img, bloco_texto]], colWidths=[32 * mm, max(largura - 32 * mm, 80 * mm)])
        tab.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (0, 0), (0, 0), "CENTER"),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LINEBELOW", (0, 0), (-1, -1), 1.2, COR_LINHA),
        ]))
        return tab
    tab = Table([[bloco_texto]], colWidths=[largura])
    tab.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, COR_LINHA),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tab


def _par_rotulo_valor(st, pares, colunas=2, largura=None):
    """pares: lista de (rótulo, valor). Valor vazio vira linha em branco, não 'None'."""
    cells = []
    row = []
    for rotulo, valor in pares:
        mostrado = celula(valor) if valor not in (None, "") else ""
        bloco = [
            _p(rotulo, st["rotulo"]),
            _p(mostrado if mostrado else " ", st["valor"]),
        ]
        row.append(bloco)
        if len(row) == colunas:
            cells.append(row)
            row = []
    if row:
        while len(row) < colunas:
            row.append("")
        cells.append(row)
    if not cells:
        return Spacer(1, 1)
    total = largura if largura is not None else LARGURA_RETRATO
    col_w = total / colunas
    tab = Table(cells, colWidths=[col_w] * colunas)
    tab.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BOX", (0, 0), (-1, -1), 0.4, COR_LINHA),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#bbbbbb")),
        ("BACKGROUND", (0, 0), (-1, 0), COR_CAB),
    ]))
    return tab


def _tabela(dados, col_widths, st_table=None):
    tab = Table(dados, colWidths=col_widths, repeatRows=1)
    cmds = [
        ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 0), COR_CAB),
        ("GRID", (0, 0), (-1, -1), 0.4, COR_LINHA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if st_table:
        cmds.extend(st_table)
    tab.setStyle(TableStyle(cmds))
    return tab


def _ch_milhar(valor):
    if valor is None:
        return None
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return None
    return "{:,}".format(n).replace(",", ".")


def _ch_trajetoria(valor):
    texto = _ch_milhar(valor)
    if texto is None:
        return CELULA_AUSENTE
    return "%s h" % texto


def _municipio_uf(item):
    mun = (item.get("municipio") or "").strip()
    uf = (item.get("uf") or "").strip()
    if mun and uf:
        return "%s/%s" % (mun, uf)
    return mun or uf or None


def _rotulo_serie(serie, turma_nome=None):
    if serie:
        return "%sº ano" % serie
    return turma_nome


def _chave_grupo_linha(item):
    return (
        item.get("ano_letivo"),
        item.get("serie"),
        item.get("escola"),
        item.get("municipio_uf"),
    )


def _resumo_periodo(trajetoria, linha):
    if not trajetoria:
        return None
    candidatos = []
    for t in trajetoria:
        if t.get("ano_letivo") != linha.get("ano_letivo"):
            continue
        if t.get("serie") != linha.get("serie"):
            continue
        candidatos.append(t)
    if not candidatos:
        return None
    for t in candidatos:
        if (t.get("estabelecimento") or None) == (linha.get("escola") or None):
            return t
    return candidatos[0]


def _texto_resumo_periodo(traj, linha):
    partes = []
    ch = _ch_milhar((traj or {}).get("carga_horaria_total"))
    if ch:
        partes.append("Carga horária total do período: %s h" % ch)
    resultado = ((traj or {}).get("resultado") or "").strip()
    if resultado:
        serie = linha.get("serie_rotulo") or _rotulo_serie(linha.get("serie"))
        if serie:
            partes.append("Resultado final do %s: %s" % (serie, resultado))
        else:
            partes.append("Resultado final do período: %s" % resultado)
    return " · ".join(partes) if partes else None


def _larguras_componentes(largura):
    fr = (0.055, 0.07, 0.22, 0.14, 0.22, 0.055, 0.11, 0.13)
    return [largura * f for f in fr]


def _tabela_componentes_cursados(st, payload, largura):
    linhas_dados = payload.get("componentes_cursados") or []
    trajetoria = payload.get("trajetoria") or []
    widths = _larguras_componentes(largura)
    cab = [
        _p("Ano/Período", st["th_hist"]),
        _p("Série/Ano", st["th_hist"]),
        _p("Escola", st["th_hist_esq"]),
        _p("Cidade/UF", st["th_hist"]),
        _p("Componente Curricular", st["th_hist_esq"]),
        _p("CH", st["th_hist"]),
        _p("Nota/Conceito", st["th_hist"]),
        _p("Situação", st["th_hist"]),
    ]
    dados = [cab]
    spans = []
    if not linhas_dados:
        dados.append([
            _p("Não há componentes curriculares informados neste histórico.", st["cel_hist"]),
            "", "", "", "", "", "", "",
        ])
        spans.append(("SPAN", (0, 1), (-1, 1)))
        tab = Table(dados, colWidths=widths, repeatRows=1)
        tab.setStyle(TableStyle(_estilo_tabela_componentes(spans)))
        return [tab]

    i = 0
    while i < len(linhas_dados):
        chave = _chave_grupo_linha(linhas_dados[i])
        grupo = []
        while i < len(linhas_dados) and _chave_grupo_linha(linhas_dados[i]) == chave:
            grupo.append(linhas_dados[i])
            i += 1
        for item in grupo:
            dados.append([
                _p(celula(item.get("ano_letivo")), st["cel_hist_c"]),
                _p(celula(item.get("serie_rotulo")), st["cel_hist_c"]),
                _p(celula(item.get("escola")), st["cel_hist"]),
                _p(celula(item.get("municipio_uf")), st["cel_hist"]),
                _p(celula(item.get("componente")), st["cel_hist"]),
                _p(
                    formatar_inteiro(item.get("carga_horaria"))
                    if item.get("carga_horaria") is not None else CELULA_AUSENTE,
                    st["cel_hist_c"],
                ),
                _p_nota(item.get("nota_conceito"), st["cel_hist_c"], _regra_nota(item)),
                _p(celula(item.get("situacao")), st["cel_hist"]),
            ])
        resumo = _texto_resumo_periodo(_resumo_periodo(trajetoria, grupo[0]), grupo[0])
        if resumo:
            row_idx = len(dados)
            dados.append([_p(resumo, st["resumo_hist"]), "", "", "", "", "", "", ""])
            spans.append(("SPAN", (0, row_idx), (-1, row_idx)))
            spans.append(("BACKGROUND", (0, row_idx), (-1, row_idx), COR_FAIXA))
            spans.append(("ALIGN", (0, row_idx), (-1, row_idx), "LEFT"))
    tab = Table(dados, colWidths=widths, repeatRows=1)
    tab.setStyle(TableStyle(_estilo_tabela_componentes(spans)))
    return [tab]


def _estilo_tabela_componentes(spans):
    cmds = [
        ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8.5),
        ("FONTSIZE", (0, 1), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), COR_CAB),
        ("GRID", (0, 0), (-1, -1), 0.4, COR_LINHA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (1, -1), "CENTER"),
        ("ALIGN", (5, 0), (6, -1), "CENTER"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    cmds.extend(spans)
    return cmds


def _assinaturas(st, largura=None, cargos=None, autenticacao=False):
    """Áreas de assinatura. autenticacao=True somente no Histórico Escolar."""
    largura = largura if largura is not None else LARGURA_RETRATO
    if cargos is None:
        cargos = (("Secretário(a)", None), ("Diretor(a)", None))
    cargos = list(cargos) or [(" ", None)]
    n = max(len(cargos), 1)
    col_w = largura / n
    tem_nome = any((nome or "").strip() for _cargo, nome in cargos)
    linhas = [[""] * n]
    linhas.append([_p("________________________________", st["assin"]) for _ in cargos])
    if tem_nome:
        linhas.append([
            _p((nome or "").strip() or " ", st["assin"]) for _cargo, nome in cargos
        ])
    linhas.append([_p(cargo, st["assin"]) for cargo, _nome in cargos])
    tab = Table(linhas, colWidths=[col_w] * n)
    tab.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    blocos = [
        Spacer(1, 4 * mm),
        _p(data_emissao_extenso(), st["pequeno"]),
        Spacer(1, 6 * mm),
        tab,
    ]
    if autenticacao:
        auth = Table(
            [[_p("RESERVADO PARA AUTENTICAÇÃO", st["rotulo"])], [_p(" ", st["valor"])]],
            colWidths=[largura],
            rowHeights=[6 * mm, 22 * mm],
        )
        auth.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, COR_LINHA),
            ("BACKGROUND", (0, 0), (-1, 0), COR_CAB),
            ("ALIGN", (0, 0), (-1, 0), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        blocos.extend([Spacer(1, 5 * mm), auth])
    return [KeepTogether(blocos)]


def _secao_transferencia(st, transf, largura=None):
    if not transf:
        return []
    largura = largura if largura is not None else LARGURA_PAISAGEM
    story = [
        PageBreak(),
        _p("RENDIMENTO E FREQUÊNCIA ESCOLAR — TRANSFERÊNCIA DURANTE O PERÍODO LETIVO", st["titulo"]),
        _p(
            "Ano letivo %s · %s · %s"
            % (
                celula(transf.get("ano_letivo")),
                celula(transf.get("escola_nome")),
                celula(transf.get("turma_nome")),
            ),
            st["escola"],
        ),
        Spacer(1, 3 * mm),
    ]
    periodos = transf.get("periodos") or []
    cab = [_p("Componente", st["th_esq"])]
    for p in periodos:
        cab.append(_p("%s nota" % (p.get("nome") or ""), st["th"]))
        cab.append(_p("Faltas", st["th"]))
    linhas = [cab]
    for comp in transf.get("componentes") or []:
        row = [_p(comp.get("nome") or CELULA_AUSENTE, st["cel_esq"])]
        mapa = {x["numero"]: x for x in (comp.get("periodos") or [])}
        for p in periodos:
            item = mapa.get(p["numero"]) or {}
            if item.get("futuro"):
                row.append(_p(CELULA_AUSENTE, st["cel"]))
                row.append(_p(CELULA_AUSENTE, st["cel"]))
            else:
                row.append(_p_nota(
                    item.get("nota"), st["cel"],
                    _regra_nota(item, transf.get("media_minima")),
                ))
                faltas = item.get("faltas")
                row.append(_p(
                    formatar_inteiro(faltas) if faltas is not None else CELULA_AUSENTE,
                    st["cel"],
                ))
        linhas.append(row)
    n = max(len(periodos), 1)
    larg_comp = min(70 * mm, largura * 0.28)
    resto = (largura - larg_comp) / (n * 2)
    widths = [larg_comp] + [resto] * (n * 2)
    story.append(_tabela(linhas, widths))
    story.append(Spacer(1, 3 * mm))
    story.append(_p(
        "Períodos ainda não iniciados no calendário oficial permanecem em branco. "
        "Zero só aparece quando foi efetivamente registrado.",
        st["obs"],
    ))
    return story


def montar_pdf_historico(payload):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4_PAISAGEM,
        leftMargin=MARGEM,
        rightMargin=MARGEM,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="Histórico Escolar",
        author=SECRETARIA_DOC,
    )
    st = _estilos()
    aluno = payload.get("aluno") or {}
    escola = payload.get("escola") or {}
    titulo_tabela = payload.get("titulo_tabela") or "COMPONENTES CURRICULARES CURSADOS/CURSANDO"
    story = [
        _timbre_flowable(st, escola, LARGURA_PAISAGEM),
        Spacer(1, 4 * mm),
        _p("HISTÓRICO ESCOLAR", st["titulo"]),
        _par_rotulo_valor(st, [
            ("Nome completo do(a) aluno(a)", aluno.get("nome")),
            ("Data de nascimento", formatar_data(aluno.get("data_nascimento"))),
            ("Naturalidade / município", aluno.get("local_nascimento")),
            ("Filiação — mãe", aluno.get("nome_mae")),
            ("Filiação — pai", aluno.get("nome_pai")),
            ("Unidade escolar emissora", escola.get("nome")),
        ], colunas=3, largura=LARGURA_PAISAGEM),
        Spacer(1, 3 * mm),
        _p(titulo_tabela, st["secao"]),
    ]
    story.extend(_tabela_componentes_cursados(st, payload, LARGURA_PAISAGEM))
    obs = (payload.get("observacoes") or "").strip()
    if obs:
        story.append(Spacer(1, 3 * mm))
        story.append(_p("Observações", st["secao"]))
        story.append(_p(obs, st["obs"]))
    transf = payload.get("transferencia")
    if transf:
        story.extend(_secao_transferencia(st, transf, LARGURA_PAISAGEM))
    tipo = payload.get("tipo") or "historico_escolar"
    story.extend(_assinaturas(
        st,
        LARGURA_PAISAGEM,
        autenticacao=(tipo == "historico_escolar"),
    ))
    return _build_pdf(doc, buf, story, marca_dagua=(tipo in TIPOS_COM_MARCA_DAGUA))


def _doc_base(titulo, filename_title):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGEM, rightMargin=MARGEM,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title=filename_title,
        author=SECRETARIA_DOC,
    )
    return buf, doc, _estilos()


def _build_pdf(doc, buf, story, marca_dagua=False):
    """marca_dagua permanece False por padrão. Só Histórico/Transferência passam True."""
    if marca_dagua:
        doc.build(
            story,
            onFirstPage=desenhar_marca_dagua_brasao,
            onLaterPages=desenhar_marca_dagua_brasao,
        )
    else:
        doc.build(story)
    return buf.getvalue()


def montar_pdf_boletim(ctx):
    buf, doc, st = _doc_base("Boletim", "Boletim escolar")
    story = [
        _timbre_flowable(st, ctx.get("escola")),
        Spacer(1, 4 * mm),
        _p(ctx.get("titulo") or "BOLETIM ESCOLAR", st["titulo"]),
        _par_rotulo_valor(st, [
            ("Aluno(a)", ctx.get("aluno_nome")),
            ("Turma", ctx.get("turma_nome")),
            ("Ano letivo", ctx.get("ano_letivo")),
            ("Média mínima", formatar_nota(ctx.get("media_minima")) if ctx.get("media_minima") is not None else None),
        ]),
        Spacer(1, 3 * mm),
    ]
    linhas = [[
        _p("Componente", st["th_esq"]),
        _p("MB1", st["th"]), _p("MB2", st["th"]),
        _p("MB3", st["th"]), _p("MB4", st["th"]),
        _p("Faltas", st["th"]), _p("Média", st["th"]), _p("Situação", st["th"]),
    ]]
    for item in ctx.get("linhas") or []:
        regra = _regra_nota(item, ctx.get("media_minima"))
        linhas.append([
            _p(item.get("disciplina") or CELULA_AUSENTE, st["cel_esq"]),
            _p_nota(item.get("mb1"), st["cel"], regra),
            _p_nota(item.get("mb2"), st["cel"], regra),
            _p_nota(item.get("mb3"), st["cel"], regra),
            _p_nota(item.get("mb4"), st["cel"], regra),
            _p(formatar_inteiro(item.get("faltas")) if item.get("faltas") is not None else CELULA_AUSENTE, st["cel"]),
            _p_nota(item.get("ma"), st["cel"], regra),
            _p(celula(item.get("situacao")), st["cel"]),
        ])
    if len(linhas) == 1:
        linhas.append([_p("Nenhum componente com registro neste boletim.", st["cel_esq"])] + [_p(CELULA_AUSENTE, st["cel"])] * 7)
    story.append(_tabela(linhas, [50 * mm, 16 * mm, 16 * mm, 16 * mm, 16 * mm, 18 * mm, 20 * mm, 30 * mm]))
    if ctx.get("referencia"):
        story.append(Spacer(1, 3 * mm))
        story.append(_p(ctx["referencia"], st["obs"]))
    story.extend(_assinaturas(st))
    return _build_pdf(doc, buf, story)


def montar_pdf_declaracao(ctx):
    buf, doc, st = _doc_base("Declaração", ctx.get("titulo") or "Declaração")
    story = [
        _timbre_flowable(st, ctx.get("escola")),
        Spacer(1, 10 * mm),
        _p(ctx.get("titulo") or "DECLARAÇÃO", st["titulo"]),
        Spacer(1, 8 * mm),
        Paragraph(ctx.get("texto") or "", st["corpo"]),
    ]
    story.extend(_assinaturas(st))
    return _build_pdf(doc, buf, story)


def montar_pdf_parecer(ctx):
    buf, doc, st = _doc_base("Parecer", "Parecer da Educação Infantil")
    story = [
        _timbre_flowable(st, ctx.get("escola")),
        Spacer(1, 4 * mm),
        _p(ctx.get("titulo") or "RELATÓRIO DESCRITIVO DE DESENVOLVIMENTO", st["titulo"]),
        _par_rotulo_valor(st, [
            ("Criança", ctx.get("aluno_nome")),
            ("Turma", ctx.get("turma_nome")),
            ("Ano letivo", ctx.get("ano_letivo")),
            ("Etapa", "Educação Infantil"),
        ]),
        Spacer(1, 3 * mm),
    ]
    if ctx.get("referencia"):
        story.append(_p(ctx["referencia"], st["obs"]))
    pareceres = ctx.get("pareceres") or {}
    houve = False
    for bim in (1, 2, 3, 4):
        itens = pareceres.get(bim) or []
        if not itens:
            continue
        houve = True
        bloco = [_p("%sº BIMESTRE" % bim, st["secao"])]
        for p in itens:
            eixo = p.get("eixo") or p.get("eixo")
            aval = p.get("avaliacao") or p.get("aval")
            obs = p.get("observacao") or p.get("obs")
            linha = "<b>%s</b>: %s" % (_escape(celula(eixo)), _escape(celula(aval)))
            if obs:
                linha += "<br/><i>%s</i>" % _escape(obs)
            bloco.append(Paragraph(linha, st["corpo"]))
            bloco.append(Spacer(1, 2 * mm))
        story.append(KeepTogether(bloco))
    if not houve:
        story.append(_p("Nenhum parecer registrado para este período.", st["obs"]))
    story.extend(_assinaturas(st, cargos=[
        ("Professor(a) Titular", ctx.get("professor_titular")),
        ("Coordenação Pedagógica", ctx.get("coordenacao_pedagogica")),
    ]))
    return _build_pdf(doc, buf, story)


def montar_pdf_matricula(ctx):
    buf, doc, st = _doc_base("Ficha de matrícula", "Ficha de matrícula")
    a = ctx.get("aluno") or {}
    story = [
        _timbre_flowable(st, ctx.get("escola")),
        Spacer(1, 4 * mm),
        _p("FICHA DE MATRÍCULA ESCOLAR", st["titulo"]),
        _p("1. Dados do aluno", st["secao"]),
        _par_rotulo_valor(st, [
            ("Nome completo", a.get("nome")),
            ("Data de nascimento", formatar_data(a.get("data_nascimento"))),
            ("Naturalidade", a.get("local_nascimento")),
            ("Turma / ano", a.get("turma_ano")),
            ("Endereço", a.get("endereco")),
            ("Telefone", a.get("telefone")),
        ]),
        _p("2. Filiação", st["secao"]),
        _par_rotulo_valor(st, [
            ("Mãe", a.get("nome_mae")),
            ("Profissão da mãe", a.get("prof_mae")),
            ("Pai", a.get("nome_pai")),
            ("Profissão do pai", a.get("prof_pai")),
        ]),
        _p("3. Informações complementares", st["secao"]),
        _par_rotulo_valor(st, [
            ("Alergia", a.get("tem_alergia")),
            ("Deficiência", a.get("tem_deficiencia")),
            ("Transporte escolar", a.get("rota_transporte")),
            ("NIS / Bolsa", a.get("nis_bolsa")),
        ]),
        Spacer(1, 4 * mm),
        _p(
            "Declaro que as informações acima são verdadeiras e assumo a "
            "responsabilidade por manter os dados atualizados junto à escola.",
            st["obs"],
        ),
    ]
    story.extend(_assinaturas(st, cargos=[
        ("Pai/Mãe ou Responsável", None),
        ("Responsável pela Matrícula", None),
    ]))
    return _build_pdf(doc, buf, story)


def montar_pdf_rendimento(ctx):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4),
        leftMargin=MARGEM, rightMargin=MARGEM,
        topMargin=12 * mm, bottomMargin=12 * mm,
        title="Ficha de rendimento",
        author=SECRETARIA_DOC,
    )
    st = _estilos()
    story = [
        _timbre_flowable(st, ctx.get("escola")),
        Spacer(1, 3 * mm),
        _p("FICHA DE RENDIMENTO ESCOLAR", st["titulo"]),
        _p(
            "Turma %s · %s · Professor(a): %s"
            % (
                celula(ctx.get("turma_nome")),
                celula(ctx.get("disciplina_nome")),
                celula(ctx.get("professor_nome")),
            ),
            st["escola"],
        ),
        Spacer(1, 3 * mm),
    ]
    linhas = [[
        _p("Aluno(a)", st["th_esq"]),
        _p("MB1", st["th"]), _p("MB2", st["th"]), _p("RS1", st["th"]), _p("MS1", st["th"]),
        _p("MB3", st["th"]), _p("MB4", st["th"]), _p("RS2", st["th"]), _p("MS2", st["th"]),
        _p("PF", st["th"]), _p("Média", st["th"]), _p("Situação", st["th"]),
    ]]
    for a in ctx.get("alunos") or []:
        regra = _regra_nota(a, ctx.get("media_minima"))
        linhas.append([
            _p(a.get("nome") or CELULA_AUSENTE, st["cel_esq"]),
            _p_nota(a.get("mb1"), st["cel"], regra),
            _p_nota(a.get("mb2"), st["cel"], regra),
            _p_nota(a.get("rs1"), st["cel"], regra),
            _p_nota(a.get("ms1"), st["cel"], regra),
            _p_nota(a.get("mb3"), st["cel"], regra),
            _p_nota(a.get("mb4"), st["cel"], regra),
            _p_nota(a.get("rs2"), st["cel"], regra),
            _p_nota(a.get("ms2"), st["cel"], regra),
            _p_nota(a.get("final"), st["cel"], regra),
            _p_nota(a.get("ma"), st["cel"], regra),
            _p(celula(a.get("situacao")), st["cel"]),
        ])
    if len(linhas) == 1:
        linhas.append([_p("Nenhum aluno nesta turma.", st["cel_esq"])] + [_p(CELULA_AUSENTE, st["cel"])] * 11)
    larg = [48 * mm] + [18 * mm] * 11
    story.append(_tabela(linhas, larg))
    story.append(Spacer(1, 4 * mm))
    story.append(_p(data_emissao_extenso(), st["pequeno"]))
    return _build_pdf(doc, buf, story)
