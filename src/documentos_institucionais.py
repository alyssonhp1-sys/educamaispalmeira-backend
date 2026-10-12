"""Identidade institucional dos documentos oficiais.

Um único lugar para timbre, município de emissão, logo e nomes de arquivo.
Não replica esses textos em dezenas de templates.
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime

from src.config import Config

PREFEITURA = "PREFEITURA MUNICIPAL DE PALMEIRA DO PIAUÍ"
SECRETARIA = "SECRETARIA MUNICIPAL DE EDUCAÇÃO"
MUNICIPIO_EMISSAO = "Palmeira do Piauí"
UF_EMISSAO = "PI"
CNPJ_INSTITUCIONAL = "06.077.363/0001-01"
LINHA_CNPJ = "CNPJ: " + CNPJ_INSTITUCIONAL
MUNICIPIO_UF_CABECALHO = "PALMEIRA DO PIAUÍ - PI"
AUTORIZACAO_RESOLUCAO = "AUTORIZADO PELA RESOLUÇÃO C.E.E/PI Nº 133/2026"
LOGO_RELATIVO = os.path.join("src", "static", "img", "logo2.png")
BRASAO_RELATIVO = os.path.join("src", "static", "img", "brasao-palmeira-piaui.png")

_MESES = {
    1: "janeiro",
    2: "fevereiro",
    3: "março",
    4: "abril",
    5: "maio",
    6: "junho",
    7: "julho",
    8: "agosto",
    9: "setembro",
    10: "outubro",
    11: "novembro",
    12: "dezembro",
}

CELULA_AUSENTE = "—"


def caminho_logo():
    path = os.path.join(Config.ROOT_DIR, LOGO_RELATIVO)
    if os.path.isfile(path):
        return path
    return None


def caminho_brasao():
    """Brasão municipal original. Usado na marca-d'água; não substitui o logo do timbre."""
    path = os.path.join(Config.ROOT_DIR, BRASAO_RELATIVO)
    if os.path.isfile(path):
        return path
    return None


def dados_timbre():
    return {
        "prefeitura": PREFEITURA,
        "secretaria": SECRETARIA,
        "municipio": MUNICIPIO_EMISSAO,
        "uf": UF_EMISSAO,
        "cnpj": CNPJ_INSTITUCIONAL,
        "cnpj_linha": LINHA_CNPJ,
        "municipio_uf": MUNICIPIO_UF_CABECALHO,
        "autorizacao": AUTORIZACAO_RESOLUCAO,
        "logo": caminho_logo(),
    }


def data_emissao_extenso(quando=None):
    """Palmeira do Piauí – PI, 03 de setembro de 2026."""
    hoje = quando or datetime.now()
    dia = "%02d" % hoje.day
    mes = _MESES[hoje.month]
    return "%s – %s, %s de %s de %s." % (
        MUNICIPIO_EMISSAO, UF_EMISSAO, dia, mes, hoje.year,
    )


def data_curta(quando=None):
    hoje = quando or datetime.now()
    return hoje.strftime("%d/%m/%Y")


def formatar_data(valor):
    if valor is None:
        return None
    if hasattr(valor, "strftime"):
        return valor.strftime("%d/%m/%Y")
    texto = str(valor).strip()
    if len(texto) >= 10 and texto[4] == "-":
        try:
            ano, mes, dia = texto[:10].split("-")
            return "%s/%s/%s" % (dia, mes, ano)
        except ValueError:
            return texto
    return texto or None


def slug_arquivo(nome, prefixo, cpf=None):
    """Nome de arquivo sem CPF. Letras/números do nome viram hífen."""
    bruto = unicodedata.normalize("NFKD", str(nome or ""))
    bruto = "".join(c for c in bruto if not unicodedata.combining(c))
    bruto = bruto.casefold()
    bruto = re.sub(r"[^a-z0-9]+", "-", bruto).strip("-")
    if cpf:
        digits = re.sub(r"\D+", "", str(cpf))
        if digits:
            bruto = bruto.replace(digits, "")
            bruto = re.sub(r"-{2,}", "-", bruto).strip("-")
        if re.fullmatch(r"\d{11}", digits) and digits in (bruto or ""):
            bruto = bruto.replace(digits, "")
    if not bruto:
        bruto = "aluno"
    nome_arq = "%s-%s.pdf" % (prefixo, bruto)
    nome_arq = re.sub(r"-{2,}", "-", nome_arq)
    if cpf:
        digits = re.sub(r"\D+", "", str(cpf))
        if digits and digits in nome_arq:
            nome_arq = nome_arq.replace(digits, "")
            nome_arq = re.sub(r"-{2,}", "-", nome_arq)
    return nome_arq


def formatar_nota(valor):
    """None → ausente. 0,0 permanece 0,0."""
    if valor is None:
        return CELULA_AUSENTE
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        texto = str(valor).strip()
        return texto if texto else CELULA_AUSENTE
    texto = ("%.1f" % numero).replace(".", ",")
    return texto


# Só o texto já formatado. Não aceita MB, APTO, CURSANDO, APROVADO, traço.
_NOTA_NUMERICA = re.compile(r"^\d+(?:[.,]\d+)?$")


def _numero_da_nota_apresentada(texto):
    """Converte 7, 7,0, 7.0, 8,5 ou 10,0 só para comparar. Não reescreve o texto."""
    if texto is None:
        return None
    bruto = str(texto).strip()
    if not bruto or bruto == CELULA_AUSENTE or not _NOTA_NUMERICA.fullmatch(bruto):
        return None
    try:
        return float(bruto.replace(",", "."))
    except ValueError:
        return None


def media_da_regra_academica(regra_academica):
    """Média já resolvida pelo documento. Não lê a configuração vigente.

    Aceita o número ou um dict com media_minima / mediaMinima.
    None significa que não há critério confiável para colorir.
    """
    if regra_academica is None or isinstance(regra_academica, bool):
        return None
    if isinstance(regra_academica, dict):
        if "media_minima" not in regra_academica and "mediaMinima" not in regra_academica:
            return None
        bruto = regra_academica.get("media_minima", regra_academica.get("mediaMinima"))
    else:
        bruto = regra_academica
    if bruto is None or isinstance(bruto, bool):
        return None
    if isinstance(bruto, (int, float)):
        return float(bruto)
    return _numero_da_nota_apresentada(str(bruto).strip())


def formatar_nota_pdf(valor, regra_academica=None):
    """Apresentação da nota no PDF. O texto é o de formatar_nota.

    faixa: None (cor normal), "abaixo" ou "atingida".
    Abaixo da média: valor < média. Igual ou acima: valor >= média.
    É o mesmo corte de src.notas, sem recalcular boletim e sem consultar
    a média vigente. Conceito textual, traço e ausência ficam sem faixa.
    """
    texto = formatar_nota(valor)
    media = media_da_regra_academica(regra_academica)
    numero = _numero_da_nota_apresentada(texto)
    if media is None or numero is None:
        faixa = None
    elif numero < media:
        faixa = "abaixo"
    else:
        faixa = "atingida"
    return {"texto": texto, "faixa": faixa}


def formatar_inteiro(valor):
    if valor is None:
        return CELULA_AUSENTE
    try:
        return str(int(valor))
    except (TypeError, ValueError):
        texto = str(valor).strip()
        return texto if texto else CELULA_AUSENTE


def celula(valor):
    if valor is None:
        return CELULA_AUSENTE
    texto = str(valor).strip()
    return texto if texto else CELULA_AUSENTE
