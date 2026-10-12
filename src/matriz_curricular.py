"""Matrizes curriculares por vigência.

A matriz 2026 replica o PDF municipal (anos iniciais e finais).
Não reconstrói 2025/2024/2023. Sem vigência aplicável, não há componente oficial.

Aliases só servem para cruzar nome operacional (ex.: Português) com o nome
oficial de 2026 na hora de obter CH. Não reescrevem snapshot antigo.
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import inspect

from src import db
from src.models import MatrizComponente, MatrizCurricular

ANO_MATRIZ_2026 = 2026
ETAPA_INICIAIS = "fundamental_iniciais"
ETAPA_FINAIS = "fundamental_finais"
CAT_COMUM = "BASE_COMUM"
CAT_DIV = "BASE_DIVERSIFICADA"

# (nome, categoria, área, aulas_semanais, carga_horaria_anual)
_INICIAIS_COMUM = (
    ("Língua Portuguesa", CAT_COMUM, "Linguagens", 8, 320),
    ("Arte", CAT_COMUM, "Linguagens", 1, 40),
    ("Educação Física", CAT_COMUM, "Linguagens", 2, 80),
    ("Redação", CAT_COMUM, "Linguagens", 1, 40),
    ("Língua Estrangeira (Inglês)", CAT_COMUM, "Linguagens", 1, 40),
    ("História", CAT_COMUM, "Ciências Humanas", 2, 80),
    ("Geografia", CAT_COMUM, "Ciências Humanas", 2, 80),
    ("Ciências", CAT_COMUM, "Ciências da Natureza", 3, 120),
    ("Matemática", CAT_COMUM, "Matemática", 8, 320),
    ("Formação Humana", CAT_COMUM, "Ensino Religioso", 1, 40),
    ("Cultura Digital", CAT_COMUM, "Computação", 1, 40),
)
_INICIAIS_DIV = (
    ("Acompanhamento Pedagógico — Linguagens/Português", CAT_DIV, "Base diversificada", 1, 40),
    ("Acompanhamento Pedagógico — Matemática", CAT_DIV, "Base diversificada", 1, 40),
    ("Eletivas", CAT_DIV, "Base diversificada", 3, 120),
)
_FINAIS_LING = (
    ("Língua Portuguesa", CAT_COMUM, "Linguagens", 8, 320),
    ("Língua Estrangeira — Inglês", CAT_COMUM, "Linguagens", 2, 80),
    ("Arte", CAT_COMUM, "Linguagens", 1, 40),
    ("Educação Física", CAT_COMUM, "Linguagens", 2, 80),
    ("Redação", CAT_COMUM, "Linguagens", 1, 40),
    ("História", CAT_COMUM, "Ciências Humanas", 2, 80),
    ("Geografia", CAT_COMUM, "Ciências Humanas", 2, 80),
    ("Matemática", CAT_COMUM, "Matemática", 8, 320),
    ("Ensino Religioso", CAT_COMUM, "Ensino Religioso", 1, 40),
    ("Cultura Digital", CAT_COMUM, "Computação", 1, 40),
)
_FINAIS_DIV = (
    ("Acompanhamento Pedagógico — Linguagens/Língua Portuguesa", CAT_DIV, "Base diversificada", 1, 40),
    ("Acompanhamento Pedagógico — Matemática", CAT_DIV, "Base diversificada", 1, 40),
    ("Projeto de Vida", CAT_DIV, "Base diversificada", 1, 40),
    ("Eletiva", CAT_DIV, "Base diversificada", 1, 40),
)

_RE_SERIE = re.compile(
    r"(?i)(?<![\d])([1-9])\s*(?:º|°|o|ª)?\s*(?:ano)?\b"
)


def normalizar_nome_componente(nome):
    texto = unicodedata.normalize("NFD", str(nome or ""))
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    texto = texto.casefold()
    texto = texto.replace("—", " ").replace("-", " ")
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    return " ".join(texto.split())


_ALIAS_OFICIAL = {
    "portugues": "lingua portuguesa",
    "lingua portuguesa": "lingua portuguesa",
    "artes": "arte",
    "arte": "arte",
    "ed fisica": "educacao fisica",
    "educacao fisica": "educacao fisica",
    "ingles": "lingua estrangeira ingles",
    "lingua estrangeira": "lingua estrangeira ingles",
    "lingua estrangeira ingles": "lingua estrangeira ingles",
    "redacao": "redacao",
    "historia": "historia",
    "geografia": "geografia",
    "ciencias": "ciencias",
    "matematica": "matematica",
    "formacao humana": "formacao humana",
    "ensino religioso": "ensino religioso",
    "cultura digital": "cultura digital",
    "biologia": "biologia",
    "quimica": "quimica",
    "fisica": "fisica",
    "eletivas": "eletivas",
    "eletiva": "eletiva",
    "projeto de vida": "projeto de vida",
    "acompanhamento pedagogico linguagens portugues": (
        "acompanhamento pedagogico linguagens portugues"
    ),
    "acompanhamento pedagogico linguagens lingua portuguesa": (
        "acompanhamento pedagogico linguagens lingua portuguesa"
    ),
    "acompanhamento pedagogico matematica": "acompanhamento pedagogico matematica",
}


def chave_componente(nome):
    n = normalizar_nome_componente(nome)
    return _ALIAS_OFICIAL.get(n, n)


def inferir_serie(turma_nome):
    """1–9 a partir do nome da turma (ex.: '5º A'). Sem chute se não houver dígito."""
    if not turma_nome:
        return None
    achado = _RE_SERIE.search(str(turma_nome))
    if not achado:
        return None
    serie = int(achado.group(1))
    if serie < 1 or serie > 9:
        return None
    return serie


def etapa_da_serie(serie):
    if serie is None:
        return None
    if 1 <= serie <= 5:
        return ETAPA_INICIAIS
    if 6 <= serie <= 9:
        return ETAPA_FINAIS
    return None


def matriz_vigente(ano_letivo, etapa):
    """Matriz ativa cuja vigência cobre o ano.

    Sem cobertura: None (não aplica matriz futura a ano anterior).
    Se duas matrizes ativas se sobrepõem (ex.: 2026 com ano_fim nulo e
    2027+), escolhe a de maior ano_inicio. Assim 2026 permanece 2026
    quando surgir a matriz seguinte.
    """
    if ano_letivo is None or not etapa:
        return None
    try:
        ano = int(ano_letivo)
    except (TypeError, ValueError):
        return None
    candidatas = (
        MatrizCurricular.query.filter_by(etapa=etapa, ativa=True)
        .order_by(MatrizCurricular.ano_inicio.desc(), MatrizCurricular.id.desc())
        .all()
    )
    vigentes = []
    for m in candidatas:
        if m.ano_inicio is None or ano < int(m.ano_inicio):
            continue
        if m.ano_fim is not None and ano > int(m.ano_fim):
            continue
        vigentes.append(m)
    if not vigentes:
        return None
    vigentes.sort(key=lambda m: (int(m.ano_inicio), m.id or 0), reverse=True)
    return vigentes[0]


def componentes_da_serie(ano_letivo, serie):
    """Componentes oficiais daquele ano/série. Lista vazia se não houver matriz vigente."""
    etapa = etapa_da_serie(serie)
    matriz = matriz_vigente(ano_letivo, etapa)
    if matriz is None or serie is None:
        return []
    return (
        MatrizComponente.query.filter_by(matriz_id=matriz.id, ano_serie=int(serie))
        .order_by(MatrizComponente.ordem.asc(), MatrizComponente.id.asc())
        .all()
    )


def carga_oficial(ano_letivo, serie, nome_componente):
    """CH anual da matriz vigente. None se o ano não é regido pela matriz ou o nome não casa."""
    chave = chave_componente(nome_componente)
    if not chave:
        return None
    for item in componentes_da_serie(ano_letivo, serie):
        if chave_componente(item.nome) == chave:
            return item.carga_horaria_anual
    return None


def nomes_oficiais_da_serie(ano_letivo, serie):
    return [c.nome for c in componentes_da_serie(ano_letivo, serie)]


def ano_regido_pela_matriz(ano_letivo, serie):
    return matriz_vigente(ano_letivo, etapa_da_serie(serie)) is not None


def _criar_matriz(nome, etapa, componentes_por_serie):
    existente = MatrizCurricular.query.filter_by(
        nome=nome, ano_inicio=ANO_MATRIZ_2026, etapa=etapa,
    ).first()
    if existente is not None:
        return existente
    matriz = MatrizCurricular(
        nome=nome,
        ano_inicio=ANO_MATRIZ_2026,
        ano_fim=None,
        etapa=etapa,
        carga_horaria_semanal=35,
        dias_letivos=200,
        carga_horaria_anual=1400,
        ativa=True,
    )
    db.session.add(matriz)
    db.session.flush()
    ordem = 0
    for serie, lista in componentes_por_serie:
        for nome_c, cat, area, aulas, ch in lista:
            ordem += 1
            db.session.add(MatrizComponente(
                matriz_id=matriz.id,
                nome=nome_c,
                categoria=cat,
                area=area,
                ano_serie=serie,
                aulas_semanais=aulas,
                carga_horaria_anual=ch,
                ordem=ordem,
            ))
    return matriz


def _componentes_finais(serie):
    natureza = []
    if serie in (6, 7, 8):
        natureza = (("Ciências", CAT_COMUM, "Ciências da Natureza", 3, 120),)
    elif serie == 9:
        natureza = (
            ("Biologia", CAT_COMUM, "Ciências da Natureza", 1, 40),
            ("Química", CAT_COMUM, "Ciências da Natureza", 1, 40),
            ("Física", CAT_COMUM, "Ciências da Natureza", 1, 40),
        )
    # Ordem da matriz: linguagens/humanas, natureza, matemática/ER/digital, diversificada.
    lingua_hum = _FINAIS_LING[:7]
    resto_comum = _FINAIS_LING[7:]
    return lingua_hum + natureza + resto_comum + _FINAIS_DIV


def garantir_matriz_2026():
    """Idempotente. Não altera matriz já persistida. Não inventa anos anteriores."""
    if "matriz_curricular" not in inspect(db.engine).get_table_names():
        return
    iniciais = [(serie, _INICIAIS_COMUM + _INICIAIS_DIV) for serie in range(1, 6)]
    finais = [(serie, _componentes_finais(serie)) for serie in range(6, 10)]
    _criar_matriz(
        "Matriz Curricular Municipal — Anos Iniciais 2026",
        ETAPA_INICIAIS,
        iniciais,
    )
    _criar_matriz(
        "Matriz Curricular Municipal — Anos Finais 2026",
        ETAPA_FINAIS,
        finais,
    )
    db.session.commit()
