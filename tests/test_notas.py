import os
import tempfile
import unittest
from pathlib import Path

from src.notas import arredondar, calc_bimestre, calcular_de_notas, validar_nota_0_a_10
from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria, texto_pdf


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


def _mapa(n1, n2, rp=None, rs1=None, rs2=None, pf=None):
    dados = {
        11: n1, 12: n2,
        21: n1, 22: n2,
        31: n1, 32: n2,
        41: n1, 42: n2,
    }
    if rp is not None:
        dados[13] = dados[23] = dados[33] = dados[43] = rp
    if rs1 is not None:
        dados[51] = rs1
    if rs2 is not None:
        dados[52] = rs2
    if pf is not None:
        dados[99] = pf
    return dados


class MotorNotasTest(unittest.TestCase):
    def test_01_aprovado_diretamente(self):
        r = calcular_de_notas(_mapa(7, 7))
        self.assertEqual(r["mb1"], 7.0)
        self.assertEqual(r["mb2"], 7.0)
        self.assertEqual(r["mb3"], 7.0)
        self.assertEqual(r["mb4"], 7.0)
        self.assertEqual(r["ma"], 7.0)
        self.assertEqual(r["situacao"], "APROVADO")

    def test_02_abaixo_da_media(self):
        r = calcular_de_notas(_mapa(5, 5))
        self.assertEqual(r["mb1"], 5.0)
        self.assertEqual(r["ma"], 5.0)
        self.assertEqual(r["situacao"], "EM RECUPERAÇÃO")

    def test_03_recuperacao_bimestral_melhor_que_menor(self):
        r = calc_bimestre(3, 5, 8)
        self.assertEqual(r["mb"], 6.5)
        self.assertTrue(r["precisa_rp"])
        r = calcular_de_notas(_mapa(3, 5, rp=8))
        self.assertEqual(r["mb1"], 6.5)

    def test_04_recuperacao_bimestral_pior_que_menor(self):
        r = calc_bimestre(3, 5, 2)
        self.assertEqual(r["mb"], 4.0)
        r = calcular_de_notas(_mapa(3, 5, rp=2))
        self.assertEqual(r["mb1"], 4.0)

    def test_05_recuperacao_semestral(self):
        r = calcular_de_notas(_mapa(5, 5, rs1=9, rs2=9))
        self.assertEqual(r["mb1"], 5.0)
        self.assertEqual(r["ms1"], 7.0)
        self.assertEqual(r["ms2"], 7.0)
        self.assertEqual(r["ma"], 7.0)
        self.assertEqual(r["situacao"], "APROVADO")

    def test_06_prova_final(self):
        r = calcular_de_notas(_mapa(5, 5, pf=8))
        self.assertEqual(r["ms1"], 5.0)
        self.assertEqual(r["ms2"], 5.0)
        self.assertEqual(r["ma"], 6.5)
        self.assertEqual(r["situacao"], "APROVADO")

    def test_07_reprovado(self):
        r = calcular_de_notas(_mapa(4, 4, pf=4))
        self.assertEqual(r["ma"], 4.0)
        self.assertEqual(r["situacao"], "REPROVADO")

    def test_08_transferido(self):
        r = calcular_de_notas(_mapa(7, 7), transferido=True)
        self.assertEqual(r["situacao"], "TRANSFERIDO")

    def test_09_nota_zero_aceita_na_funcao(self):
        r = calc_bimestre(0, 0, None)
        self.assertEqual(r["mb"], 0.0)
        numero, erro = validar_nota_0_a_10(0)
        self.assertEqual(numero, 0.0)
        self.assertIsNone(erro)

    def test_10_nota_dez_aceita_na_funcao(self):
        r = calc_bimestre(10, 10, None)
        self.assertEqual(r["mb"], 10.0)
        numero, erro = validar_nota_0_a_10(10)
        self.assertEqual(numero, 10.0)
        self.assertIsNone(erro)

    def test_11_nota_negativa_rejeitada(self):
        numero, erro = validar_nota_0_a_10(-1)
        self.assertIsNone(numero)
        self.assertIn("0 a 10", erro)

    def test_12_nota_acima_de_dez_rejeitada(self):
        numero, erro = validar_nota_0_a_10(10.1)
        self.assertIsNone(numero)
        self.assertIn("0 a 10", erro)

    def test_13_string_invalida_rejeitada(self):
        numero, erro = validar_nota_0_a_10("dez")
        self.assertIsNone(numero)
        self.assertIn("inválida", erro.lower())

    def test_17_arredondamento_uma_casa_metade_para_cima(self):
        self.assertEqual(arredondar(6.25), 6.3)
        self.assertEqual(arredondar(6.24), 6.2)
        self.assertEqual(calc_bimestre(6.25, 6.25, None)["mb"], 6.3)


class NotasHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cls._db_path = tmp.name
        from src.config import Config
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + Path(cls._db_path).resolve().as_posix()
        from src import create_app
        cls.app = create_app()
        cls.app.testing = True
        cls._seq = 0

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _rede(self):
        NotasHttpTest._seq += 1
        n = NotasHttpTest._seq
        inep = f"7200{n:04d}"
        cpf_prof = f"7201{n:07d}"
        cpf_aluno = f"7202{n:07d}"
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola Notas {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Notas", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        self.client.post("/api/turmas", json={"nome": "9º Ano", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = dash["turmas"][0]["id"]
        disc_id = dash["disciplinas"][0]["id"]
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": "Aluno Notas", "cpf": cpf_aluno, "turma_id": turma_id, "nome_mae": "Mãe Notas",
        })
        aluno_id = _json(resp)[0]["id"]
        self.client.post("/api/alocacoes", json={
            "professor_id": prof_id, "turma_id": turma_id, "disciplina_id": disc_id,
        })
        concluir_primeiro_acesso(self.client, cpf_prof, temp_prof)
        return turma_id, disc_id, aluno_id, inep

    def test_18_aplicacao_inicia(self):
        self.assertIsNotNone(self.app)
        self._login("admin", SENHA_DEMO_ADMIN)

    def test_09_a_13_validacao_http(self):
        turma_id, disc_id, aluno_id, _inep = self._rede()

        def post(valor):
            return self.client.post(
                f"/api/turmas/{turma_id}/disciplinas/{disc_id}/notas",
                json=[{"aluno_id": aluno_id, "valor": valor, "tipo": "nota", "bimestre": 11}],
            )

        resp = post(0)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = post(10)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = post(-1)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("0 a 10", (_json(resp)[1] or "").lower())
        resp = post(10.1)
        self.assertEqual(resp.status_code, 400)
        resp = post("abc")
        self.assertEqual(resp.status_code, 400)

    def test_14_15_16_api_boletim_relatorio_mesma_mb(self):
        turma_id, disc_id, aluno_id, inep = self._rede()
        lancamentos = []
        for cod in (11, 12, 21, 22, 31, 32, 41, 42):
            lancamentos.append({"aluno_id": aluno_id, "valor": 7.25, "tipo": "nota", "bimestre": cod})
        resp = self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/{disc_id}/notas",
            json=lancamentos,
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        esperado = calcular_de_notas(_mapa(7.25, 7.25))
        mon = _json(self.client.get(f"/api/monitoramento/turma/{turma_id}/disciplina/{disc_id}"))[0]
        aluno = next(a for a in mon if a["id"] == aluno_id)
        self.assertEqual(aluno["resumo"]["mb1"], esperado["mb1"])
        self.assertEqual(aluno["resumo"]["mb2"], esperado["mb2"])
        self.assertEqual(aluno["resumo"]["ma"], esperado["ma"])
        self.assertEqual(esperado["mb1"], 7.3)

        self._login(inep, SENHA_PESSOAL)
        boletim = self.client.get(f"/api/alunos/{aluno_id}/documentos/boletim.pdf")
        self.assertEqual(boletim.status_code, 200)
        html = texto_pdf(boletim)
        self.assertIn("MB1", html)
        self.assertIn("MB2", html)
        self.assertIn("MB3", html)
        self.assertIn("MB4", html)
        self.assertIn("7,3", html)
        self.assertNotIn("N1", html)

        rel = self.client.get(f"/api/turmas/{turma_id}/documentos/rendimento/{disc_id}.pdf")
        self.assertEqual(rel.status_code, 200)
        rel_html = texto_pdf(rel)
        self.assertIn("7,3", rel_html)
        self.assertIn(esperado["situacao"], rel_html)


if __name__ == "__main__":
    unittest.main()
