"""Checagens estáticas do Bloco 7C: UX, linguagem e ausência de regras paralelas."""
import re
import unittest
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
FRONTEND = RAIZ / "frontend" / "src"


def _ler(*partes):
    return (FRONTEND.joinpath(*partes)).read_text(encoding="utf-8")


class Bloco7cEstaticoTest(unittest.TestCase):
    def test_01_sem_alert_confirm_no_frontend(self):
        proibidos = ("window.alert", "window.confirm")
        ofensores = []
        for path in FRONTEND.rglob("*"):
            if path.suffix not in {".js", ".jsx", ".css"}:
                continue
            texto = path.read_text(encoding="utf-8")
            for token in proibidos:
                if token in texto:
                    ofensores.append("%s: %s" % (path.relative_to(RAIZ), token))
        self.assertEqual(ofensores, [])

    def test_02_grade_nao_usa_fonte_065(self):
        css = _ler("styles.css")
        bloco = re.search(
            r"\.table-journal th, \.table-journal td \{([^}]+)\}",
            css,
        )
        self.assertIsNotNone(bloco)
        self.assertNotIn("0.65rem", bloco.group(1))
        self.assertIn("0.875rem", bloco.group(1))
        self.assertNotIn("font-size: 0.65rem", _ler("components", "GradeNotas.jsx"))

    def test_03_linguagem_neutra(self):
        texto = "\n".join(
            p.read_text(encoding="utf-8")
            for p in FRONTEND.rglob("*")
            if p.suffix in {".js", ".jsx"}
        ).lower()
        for token in ("professor faltoso", "pior escola", "pior professor", "planos em análise"):
            self.assertNotIn(token, texto)

    def test_04_ei_nao_reintroduz_aba_notas(self):
        painel = _ler("pages", "PainelProfessor.jsx")
        self.assertIn("Acompanhamento", painel)
        trecho_ei = painel.split("infantil ? (")[1].split(") : turma.disciplinas.map")[0]
        self.assertNotIn(">Notas<", trecho_ei)
        turma = _ler("pages", "Turma.jsx")
        self.assertIn('tabs.push(["pareceres", "Pareceres"])', turma)

    def test_05_mb_e_rs_na_legenda(self):
        grade = _ler("components", "GradeNotas.jsx")
        self.assertIn("MB — média do bimestre", grade)
        self.assertIn("RS — recuperação semestral", grade)
        self.assertIn("Bimestre 1", grade)
        self.assertIn("Resumo anual", grade)
        self.assertIn("Alterações não salvas", grade)
        self.assertIn("Salvar notas", grade)

    def test_06_diario_pf_e_contadores(self):
        diario = _ler("components", "DiarioAula.jsx")
        self.assertIn("Todos iniciam como Presentes", diario)
        self.assertIn("Presentes:", diario)
        self.assertIn("Todos presentes", diario)
        self.assertIn("Pendente de validação", diario)
        self.assertIn("aria-label", diario)
        self.assertNotIn("Todos faltaram", diario)

    def test_07_documentos_sem_impressao_html_redundante(self):
        ofensores = []
        tokens = (
            "window.open(`/documentos/",
            'window.open("/documentos/',
            "window.open(`/relatorio/turma/",
            "para impressão",
            "versão para impressão",
            "Versão para impressão",
            "`/documentos/boletim/",
            "`/documentos/declaracao/",
            "`/documentos/parecer/",
            "`/documentos/comprovante-matricula/",
            "`/documentos/historico/",
            "`/relatorio/turma/",
            "\"/documentos/boletim/",
            "\"/relatorio/turma/",
        )
        for path in FRONTEND.rglob("*"):
            if path.suffix not in {".js", ".jsx"}:
                continue
            texto = path.read_text(encoding="utf-8")
            for token in tokens:
                if token in texto:
                    ofensores.append("%s: %s" % (path.relative_to(RAIZ), token))
        self.assertEqual(ofensores, [])
