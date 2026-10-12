"""Cor das notas numéricas nos PDFs oficiais.

Abaixo da média configurada para aquele documento: vermelho.
Igual ou acima: azul. Conceito, traço, falta e situação ficam na cor normal.
A média não é um número fixo: vem da regra já resolvida pelo documento.
"""
import inspect
import re
import unittest
from io import BytesIO
from pathlib import Path

from src.documentos_institucionais import CELULA_AUSENTE, formatar_nota, formatar_nota_pdf
from src.pdf_oficial import (
    COR_NOTA_ABAIXO,
    COR_NOTA_ATINGIDA,
    montar_pdf_boletim,
    montar_pdf_declaracao,
    montar_pdf_historico,
    montar_pdf_matricula,
    montar_pdf_parecer,
    montar_pdf_rendimento,
)

ARTEFATOS = Path(__file__).resolve().parent / "_pdf_artefatos" / "cor_notas"
_NOTA_NUMERICA = re.compile(r"^\d+(?:[.,]\d+)?$")


def _hex_rgb(cor):
    return tuple(int(cor[i:i + 2], 16) for i in (1, 3, 5))


def _faixa_rgb(r, g, b):
    atual = (round(float(r) * 255), round(float(g) * 255), round(float(b) * 255))
    if all(abs(a - b) <= 1 for a, b in zip(atual, _hex_rgb(COR_NOTA_ABAIXO))):
        return "abaixo"
    if all(abs(a - b) <= 1 for a, b in zip(atual, _hex_rgb(COR_NOTA_ATINGIDA))):
        return "atingida"
    return None


def _decode_literal(raw):
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i] == "\\" and i + 1 < len(raw):
            n = raw[i + 1]
            if n in "nrtbf":
                out.append({"n": 10, "r": 13, "t": 9, "b": 8, "f": 12}[n])
                i += 2
                continue
            if n in "01234567":
                octal = n
                i += 2
                for _ in range(2):
                    if i < len(raw) and raw[i] in "01234567":
                        octal += raw[i]
                        i += 1
                    else:
                        break
                out.append(int(octal, 8) & 0xFF)
                continue
            out.append(ord(n) & 0xFF)
            i += 2
            continue
        out.append(ord(raw[i]) & 0xFF)
        i += 1
    return out.decode("latin-1")


def _tokens_pdf(data):
    tokens = []
    i = 0
    n = len(data)
    while i < n:
        ch = data[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            i += 1
            raw = []
            while i < n:
                if data[i] == "\\":
                    raw.append("\\")
                    if i + 1 < n:
                        raw.append(data[i + 1])
                        i += 2
                    else:
                        i += 1
                    continue
                if data[i] == ")":
                    i += 1
                    break
                raw.append(data[i])
                i += 1
            tokens.append(("str", "".join(raw)))
            continue
        j = i
        while j < n and not data[j].isspace() and data[j] not in "()":
            j += 1
        tokens.append(("op", data[i:j]))
        i = j
    return tokens


def _conteudo_pdf(pdf):
    from pypdf import PdfReader

    leitor = PdfReader(BytesIO(pdf))
    partes = []
    for pagina in leitor.pages:
        conteudo = pagina.get_contents()
        if conteudo is None:
            continue
        blocos = conteudo if isinstance(conteudo, list) else [conteudo]
        for bloco in blocos:
            partes.append(bloco.get_data().decode("latin-1", errors="replace"))
    return "\n".join(partes)


def trechos_coloridos(pdf):
    """Lista (texto, faixa) de cada Tj. faixa é abaixo, atingida ou None."""
    rgb = (0.0, 0.0, 0.0)
    pilha = []
    numeros = []
    ultimo = None
    saida = []
    for tipo, valor in _tokens_pdf(_conteudo_pdf(pdf)):
        if tipo == "str":
            ultimo = valor
            numeros = []
            continue
        if valor == "Tj" and ultimo is not None:
            texto = _decode_literal(ultimo).strip()
            ultimo = None
            numeros = []
            if texto:
                saida.append((texto, _faixa_rgb(*rgb)))
            continue
        if valor == "q":
            pilha.append(rgb)
            numeros = []
            continue
        if valor == "Q":
            rgb = pilha.pop() if pilha else (0.0, 0.0, 0.0)
            numeros = []
            continue
        if valor == "rg":
            if len(numeros) >= 3:
                rgb = tuple(numeros[-3:])
            numeros = []
            continue
        try:
            numeros.append(float(valor))
        except ValueError:
            numeros = []
    return saida


def faixas(pdf, texto):
    return [faixa for trecho, faixa in trechos_coloridos(pdf) if trecho == texto]


def _escola():
    return {"nome": "Escola Municipal Fictícia", "inep": "00000000"}


def _linha_historico(**kwargs):
    base = {
        "ano_letivo": 2026,
        "serie": 5,
        "serie_rotulo": "5º ano",
        "escola": "Escola Municipal Fictícia",
        "municipio_uf": "Palmeira do Piauí/PI",
        "componente": "Matemática",
        "carga_horaria": 160,
        "nota_conceito": None,
        "situacao": "APROVADO",
        "media_minima": 7.0,
    }
    base.update(kwargs)
    return base


class FormatarNotaPdfTest(unittest.TestCase):
    def test_texto_permanece_o_de_formatar_nota(self):
        amostras = [
            None, "", "—", "MB", "APTO", "CURSANDO", "APROVADO", "REPROVADO",
            7, 7.0, "7", "7,0", "7.0", "8,5", "8.5", 8.5, "10,0", 10, "6,9", 6.9,
        ]
        for valor in amostras:
            apresentacao = formatar_nota_pdf(valor, 7)
            self.assertEqual(apresentacao["texto"], formatar_nota(valor), valor)

    def test_01_abaixo_da_media_vermelha(self):
        self.assertEqual(formatar_nota_pdf(6.9, 7)["faixa"], "abaixo")
        self.assertEqual(formatar_nota_pdf("6,9", 7)["faixa"], "abaixo")
        self.assertEqual(formatar_nota_pdf("6,9", 7)["texto"], "6,9")

    def test_02_exatamente_na_media_azul(self):
        for valor in (7, 7.0, "7", "7,0", "7.0"):
            apresentacao = formatar_nota_pdf(valor, 7)
            self.assertEqual(apresentacao["faixa"], "atingida", valor)
        self.assertEqual(formatar_nota_pdf("7,0", 7)["texto"], "7,0")
        self.assertEqual(formatar_nota_pdf("7.0", 7)["texto"], formatar_nota("7.0"))

    def test_03_acima_da_media_azul(self):
        self.assertEqual(formatar_nota_pdf(8.5, 7)["faixa"], "atingida")
        self.assertEqual(formatar_nota_pdf("8,5", 7)["faixa"], "atingida")
        self.assertEqual(formatar_nota_pdf("10,0", 7)["faixa"], "atingida")
        self.assertEqual(formatar_nota_pdf("10,0", 7)["texto"], "10,0")

    def test_04_traco_cor_normal(self):
        for valor in (None, "", CELULA_AUSENTE, "—"):
            apresentacao = formatar_nota_pdf(valor, 7)
            self.assertIsNone(apresentacao["faixa"], valor)
            self.assertEqual(apresentacao["texto"], CELULA_AUSENTE)

    def test_05_conceito_mb_cor_normal(self):
        apresentacao = formatar_nota_pdf("MB", 7)
        self.assertEqual(apresentacao["texto"], "MB")
        self.assertIsNone(apresentacao["faixa"])

    def test_06_conceito_apto_cor_normal(self):
        apresentacao = formatar_nota_pdf("APTO", 7)
        self.assertEqual(apresentacao["texto"], "APTO")
        self.assertIsNone(apresentacao["faixa"])

    def test_situacao_textual_cor_normal(self):
        for conceito in ("CURSANDO", "APROVADO", "REPROVADO", "EM RECUPERAÇÃO"):
            apresentacao = formatar_nota_pdf(conceito, 7)
            self.assertEqual(apresentacao["texto"], conceito)
            self.assertIsNone(apresentacao["faixa"])

    def test_07_virgula_e_08_ponto_nao_alteram_a_apresentacao(self):
        virgula = formatar_nota_pdf("8,5", {"media_minima": 7})
        ponto = formatar_nota_pdf("8.5", {"mediaMinima": 6})
        self.assertEqual(virgula["texto"], "8,5")
        self.assertEqual(virgula["faixa"], "atingida")
        self.assertEqual(ponto["texto"], formatar_nota("8.5"))
        self.assertEqual(ponto["faixa"], "atingida")
        self.assertEqual(formatar_nota_pdf("7,0", 7.0)["texto"], "7,0")

    def test_14_sem_regra_nao_usa_media_implicita(self):
        for regra in (None, {}, {"media_minima": None}):
            apresentacao = formatar_nota_pdf("8,5", regra)
            self.assertEqual(apresentacao["texto"], "8,5")
            self.assertIsNone(apresentacao["faixa"], regra)
        self.assertEqual(formatar_nota_pdf(5.9, 6)["faixa"], "abaixo")
        self.assertEqual(formatar_nota_pdf(6, 6)["faixa"], "atingida")
        self.assertEqual(formatar_nota_pdf(6, 7)["faixa"], "abaixo")

    def test_helper_nao_consulta_a_media_vigente(self):
        from src import documentos_institucionais, historico_escolar, pdf_oficial

        for alvo in (
            documentos_institucionais.formatar_nota_pdf,
            documentos_institucionais.media_da_regra_academica,
            pdf_oficial._p_nota,
            pdf_oficial._regra_nota,
            historico_escolar._media_aplicavel_periodo,
            historico_escolar._media_periodo_transferencia,
        ):
            src = inspect.getsource(alvo)
            self.assertNotIn("media_minima_vigente", src, alvo.__name__)
            self.assertNotIn("< 7", src, alvo.__name__)
            self.assertNotIn("<7", src, alvo.__name__)
        src_pdf = inspect.getsource(pdf_oficial)
        self.assertNotIn("media_minima_vigente", src_pdf)


class PdfNotasColoridasTest(unittest.TestCase):
    def _assert_so_numeros_coloridos(self, pdf):
        for texto, faixa in trechos_coloridos(pdf):
            if faixa is None:
                continue
            self.assertRegex(texto, _NOTA_NUMERICA, texto)

    def _assert_faixa(self, pdf, texto, faixa):
        achados = faixas(pdf, texto)
        self.assertTrue(achados, "texto ausente no PDF: %s" % texto)
        self.assertTrue(all(item == faixa for item in achados), (texto, achados))

    def _assert_normal(self, pdf, texto):
        self._assert_faixa(pdf, texto, None)

    def test_09_boletim_colores_so_notas(self):
        pdf = montar_pdf_boletim({
            "titulo": "BOLETIM ESCOLAR",
            "aluno_nome": "Ana Fictícia",
            "turma_nome": "5º ano A",
            "ano_letivo": 2026,
            "escola": _escola(),
            "media_minima": 7,
            "linhas": [{
                "disciplina": "Matemática",
                "mb1": "6,9",
                "mb2": "7,0",
                "mb3": "8,5",
                "mb4": None,
                "faltas": 0,
                "ma": None,
                "situacao": "CURSANDO",
            }],
        })
        self._assert_so_numeros_coloridos(pdf)
        self._assert_faixa(pdf, "6,9", "abaixo")
        self.assertIn("atingida", faixas(pdf, "7,0"))
        self.assertIn(None, faixas(pdf, "7,0"))
        self._assert_faixa(pdf, "8,5", "atingida")
        self._assert_normal(pdf, "0")
        self._assert_normal(pdf, "CURSANDO")
        self._assert_normal(pdf, "Matemática")
        self.assertNotIn("6.9", _texto(pdf))
        self.assertIn("6,9", _texto(pdf))

    def test_10_ficha_de_rendimento_colores_so_notas(self):
        pdf = montar_pdf_rendimento({
            "escola": _escola(),
            "turma_nome": "5º ano A",
            "disciplina_nome": "Matemática",
            "professor_nome": "Professora Fictícia",
            "alunos": [{
                "nome": "Ana Fictícia",
                "mb1": 6.9,
                "mb2": 7,
                "rs1": None,
                "ms1": 6.9,
                "mb3": 8.5,
                "mb4": None,
                "rs2": None,
                "ms2": None,
                "final": "10,0",
                "ma": 7.0,
                "situacao": "APROVADO",
                "faltas": 4,
                "media_minima": 7,
            }],
        })
        self._assert_so_numeros_coloridos(pdf)
        self._assert_faixa(pdf, "6,9", "abaixo")
        self._assert_faixa(pdf, "7,0", "atingida")
        self._assert_faixa(pdf, "8,5", "atingida")
        self._assert_faixa(pdf, "10,0", "atingida")
        self._assert_normal(pdf, "APROVADO")
        self._assert_normal(pdf, "Ana Fictícia")
        self.assertEqual(formatar_nota(6.9), "6,9")
        self.assertIn("6,9", _texto(pdf))
        self.assertNotIn("6.9", _texto(pdf))

    def test_11_historico_e_12_transferencia_e_13_complemento(self):
        linhas = [
            _linha_historico(componente="Matemática", nota_conceito="6,9", situacao="REPROVADO"),
            _linha_historico(componente="Português", nota_conceito="7,0", situacao="APROVADO"),
            _linha_historico(componente="História", nota_conceito="8,5", situacao="APROVADO"),
            _linha_historico(componente="Arte", nota_conceito="MB", situacao="APROVADO"),
            _linha_historico(componente="Educação Física", nota_conceito="APTO", situacao="Cursado"),
            _linha_historico(componente="Ciências", nota_conceito=None, situacao="CURSANDO"),
            _linha_historico(
                ano_letivo=2024, serie=4, serie_rotulo="4º ano",
                componente="Matemática", nota_conceito="8,5", situacao="Aprovado",
                media_minima=None, carga_horaria=200,
            ),
            _linha_historico(
                ano_letivo=2024, serie=4, serie_rotulo="4º ano",
                componente="Arte", nota_conceito="MB", situacao="Cursado",
                media_minima=None, carga_horaria=40,
            ),
        ]
        transferencia = {
            "ano_letivo": 2026,
            "escola_nome": "Escola Municipal Fictícia",
            "turma_nome": "5º ano A",
            "media_minima": 7,
            "periodos": [
                {"numero": 1, "nome": "1º Bimestre"},
                {"numero": 2, "nome": "2º Bimestre"},
                {"numero": 3, "nome": "3º Bimestre"},
                {"numero": 4, "nome": "4º Bimestre"},
            ],
            "componentes": [{
                "nome": "Matemática",
                "periodos": [
                    {"numero": 1, "nota": "6,9", "faltas": 2, "futuro": False},
                    {"numero": 2, "nota": 7.0, "faltas": 0, "futuro": False},
                    {"numero": 3, "nota": "8.5", "faltas": None, "futuro": False},
                    {"numero": 4, "nota": "6,5", "faltas": 3, "futuro": False},
                ],
            }],
        }
        pdf = montar_pdf_historico({
            "tipo": "historico_transferencia",
            "aluno": {
                "nome": "Ana Fictícia",
                "data_nascimento": "2014-03-15",
                "local_nascimento": "Palmeira do Piauí",
                "nome_mae": "Maria Fictícia",
                "nome_pai": "José Fictício",
            },
            "escola": _escola(),
            "componentes_cursados": linhas,
            "trajetoria": [],
            "transferencia": transferencia,
        })
        self._assert_so_numeros_coloridos(pdf)
        abaixo = faixas(pdf, "6,9")
        self.assertTrue(abaixo)
        self.assertTrue(all(item == "abaixo" for item in abaixo))
        azul_7 = faixas(pdf, "7,0")
        self.assertTrue(azul_7)
        self.assertTrue(all(item == "atingida" for item in azul_7))
        oito = faixas(pdf, "8,5")
        self.assertIn("atingida", oito)
        self.assertIn(None, oito)
        self.assertEqual(faixas(pdf, "6,5"), ["abaixo"])
        self._assert_normal(pdf, "MB")
        self._assert_normal(pdf, "APTO")
        self._assert_normal(pdf, "APROVADO")
        self._assert_normal(pdf, "REPROVADO")
        self._assert_normal(pdf, "CURSANDO")
        self._assert_normal(pdf, "Matemática")
        self._assert_normal(pdf, "160")
        self._assert_normal(pdf, "200")
        self._assert_normal(pdf, "2")
        self._assert_normal(pdf, "0")
        self._assert_normal(pdf, "3")
        texto = _texto(pdf)
        self.assertIn("6,9", texto)
        self.assertIn("7,0", texto)
        self.assertIn("8,5", texto)
        self.assertIn("6,5", texto)
        self.assertIn("MB", texto)
        self.assertIn("APTO", texto)
        self.assertNotIn("6.9", texto)
        self.assertNotIn("8.5", texto)

    def test_14_nota_legada_sem_media_fica_na_cor_normal(self):
        pdf = montar_pdf_historico({
            "tipo": "historico_escolar",
            "aluno": {"nome": "Aluno Legado"},
            "escola": _escola(),
            "componentes_cursados": [
                _linha_historico(
                    ano_letivo=2023, serie=3, serie_rotulo="3º ano",
                    nota_conceito="8,5", media_minima=None, situacao="Aprovado",
                ),
            ],
            "trajetoria": [],
        })
        self._assert_faixa(pdf, "8,5", None)
        self._assert_normal(pdf, "Aprovado")
        self.assertEqual(_texto(pdf).count("8,5"), 1)

    def test_documentos_sem_nota_nao_recebem_cor(self):
        documentos = [
            montar_pdf_declaracao({
                "titulo": "DECLARAÇÃO",
                "texto": "Declaramos que o aluno cursa o 5º ano.",
                "escola": _escola(),
            }),
            montar_pdf_matricula({"aluno": {"nome": "Ana Fictícia"}, "escola": _escola()}),
            montar_pdf_parecer({
                "aluno_nome": "Ana Fictícia",
                "turma_nome": "Infantil V",
                "ano_letivo": 2026,
                "escola": _escola(),
                "pareceres": {1: [{"eixo": "Eu", "avaliacao": "Em desenvolvimento"}]},
            }),
        ]
        for pdf in documentos:
            coloridos = [texto for texto, faixa in trechos_coloridos(pdf) if faixa]
            self.assertEqual(coloridos, [])

    def test_boletim_sem_media_confiavel_nao_colore(self):
        pdf = montar_pdf_boletim({
            "aluno_nome": "Ana Fictícia",
            "turma_nome": "5º ano A",
            "ano_letivo": 2024,
            "escola": _escola(),
            "media_minima": None,
            "linhas": [{
                "disciplina": "Matemática",
                "mb1": 8.5,
                "mb2": 6.9,
                "mb3": None,
                "mb4": None,
                "faltas": 1,
                "ma": 7,
                "situacao": "APROVADO",
            }],
        })
        self._assert_faixa(pdf, "8,5", None)
        self._assert_faixa(pdf, "6,9", None)
        self._assert_faixa(pdf, "7,0", None)
        self._assert_normal(pdf, "APROVADO")

    def test_grava_pdfs_ficticios_para_inspecao(self):
        ARTEFATOS.mkdir(parents=True, exist_ok=True)
        boletim = montar_pdf_boletim({
            "titulo": "BOLETIM ESCOLAR",
            "aluno_nome": "Ana Fictícia",
            "turma_nome": "5º ano A",
            "ano_letivo": 2026,
            "escola": _escola(),
            "media_minima": 7,
            "linhas": [
                {
                    "disciplina": "Matemática",
                    "mb1": 6.9, "mb2": 7.0, "mb3": 8.5, "mb4": None,
                    "faltas": 2, "ma": 6.9, "situacao": "EM RECUPERAÇÃO",
                },
                {
                    "disciplina": "Arte",
                    "mb1": None, "mb2": None, "mb3": None, "mb4": None,
                    "faltas": 0, "ma": "MB", "situacao": "APROVADO",
                },
            ],
        })
        rendimento = montar_pdf_rendimento({
            "escola": _escola(),
            "turma_nome": "5º ano A",
            "disciplina_nome": "Matemática",
            "professor_nome": "Professora Fictícia",
            "alunos": [{
                "nome": "Ana Fictícia",
                "mb1": 6.9, "mb2": 7.0, "rs1": None, "ms1": 6.9,
                "mb3": 8.5, "mb4": 7.0, "rs2": None, "ms2": 7.8,
                "final": None, "ma": 7.0, "situacao": "APROVADO",
                "media_minima": 7,
            }],
        })
        linhas = [
            _linha_historico(componente="Matemática", nota_conceito=6.9, situacao="REPROVADO"),
            _linha_historico(componente="Português", nota_conceito=7.0, situacao="APROVADO"),
            _linha_historico(componente="História", nota_conceito=8.5, situacao="APROVADO"),
            _linha_historico(componente="Arte", nota_conceito="MB", situacao="APROVADO"),
            _linha_historico(componente="Educação Física", nota_conceito="APTO", situacao="Cursado"),
            _linha_historico(
                ano_letivo=2024, serie=4, serie_rotulo="4º ano",
                componente="Matemática", nota_conceito="8,5",
                situacao="Aprovado", media_minima=None,
            ),
        ]
        comum = {
            "aluno": {
                "nome": "Ana Fictícia",
                "data_nascimento": "2014-03-15",
                "local_nascimento": "Palmeira do Piauí",
                "nome_mae": "Maria Fictícia",
                "nome_pai": "José Fictício",
            },
            "escola": _escola(),
            "componentes_cursados": linhas,
            "trajetoria": [],
        }
        historico = montar_pdf_historico(dict(comum, tipo="historico_escolar"))
        transferencia = montar_pdf_historico(dict(comum, tipo="historico_transferencia", transferencia={
            "ano_letivo": 2026,
            "escola_nome": "Escola Municipal Fictícia",
            "turma_nome": "5º ano A",
            "media_minima": 7,
            "periodos": [
                {"numero": 1, "nome": "1º Bimestre"},
                {"numero": 2, "nome": "2º Bimestre"},
                {"numero": 3, "nome": "3º Bimestre"},
            ],
            "componentes": [{
                "nome": "Matemática",
                "periodos": [
                    {"numero": 1, "nota": 6.9, "faltas": 2, "futuro": False},
                    {"numero": 2, "nota": 7.0, "faltas": 0, "futuro": False},
                    {"numero": 3, "nota": 8.5, "faltas": 1, "futuro": False},
                ],
            }],
        }))
        (ARTEFATOS / "boletim.pdf").write_bytes(boletim)
        (ARTEFATOS / "rendimento.pdf").write_bytes(rendimento)
        (ARTEFATOS / "historico.pdf").write_bytes(historico)
        (ARTEFATOS / "transferencia.pdf").write_bytes(transferencia)
        self.assertTrue((ARTEFATOS / "historico.pdf").stat().st_size > 1000)


def _texto(pdf):
    from pypdf import PdfReader

    leitor = PdfReader(BytesIO(pdf))
    return "\n".join((pagina.extract_text() or "") for pagina in leitor.pages)
