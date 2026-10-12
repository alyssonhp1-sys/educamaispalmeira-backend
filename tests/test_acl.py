import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class AutorizacaoAclTest(unittest.TestCase):
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
        cls._montar_rede()

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass

    @classmethod
    def _login_client(cls, client, login, senha=""):
        resp = client.post("/api/auth/login", json={"login": login, "senha": senha})
        if resp.status_code != 200:
            raise RuntimeError("Falha no login de fixture: %s %s" % (login, _json(resp)[1]))

    @classmethod
    def _montar_rede(cls):
        client = cls.app.test_client()
        cls._login_client(client, "admin", SENHA_DEMO_ADMIN)
        from src.models import Disciplina
        with cls.app.app_context():
            cls.disc_id = Disciplina.query.filter_by(nome="Matemática").first().id
            cls.disc_outra = Disciplina.query.filter_by(nome="Português").first().id

        cls.inep_a = "61000001"
        cls.inep_b = "61000002"
        cls.cpf_prof_a = "61011111111"
        cls.cpf_prof_b = "61022222222"
        cls.login_coord_a = "coordA610"
        cls.login_coord_b = "coordB610"

        temps = {}
        for nome, inep in (("Escola ACL Norte", cls.inep_a), ("Escola ACL Sul", cls.inep_b)):
            resp = client.post("/api/escolas", json={
                "nome": nome,
                "inep": inep,
                "oferta_infantil": True,
                "oferta_fundamental": True,
            })
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[inep] = senha_temporaria(resp)

        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_a = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_a)
        cls.escola_b = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_b)

        for login, nome, escola_id in (
            (cls.login_coord_a, "Coord Norte", cls.escola_a),
            (cls.login_coord_b, "Coord Sul", cls.escola_b),
        ):
            resp = client.post(f"/api/escolas/{escola_id}/coordenadores", json={"login": login, "nome": nome})
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[login] = senha_temporaria(resp)

        for cpf, nome, escola_id in (
            (cls.cpf_prof_a, "Professor Alfa", cls.escola_a),
            (cls.cpf_prof_b, "Professor Beta", cls.escola_b),
        ):
            resp = client.post("/api/professores", json={"nome": nome, "cpf": cpf, "escola_id": escola_id})
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[cpf] = senha_temporaria(resp)
        for login, temp in temps.items():
            concluir_primeiro_acesso(client, login, temp)

        cls._login_client(client, cls.inep_a, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano Norte", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_a = dash["turmas"][0]["id"]
        cls.prof_a_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_a)
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Alpha UniqueACL",
            "cpf": "61033333333",
            "turma_id": cls.turma_a,
            "nome_mae": "Mãe Alpha",
        })
        cls.aluno_a = _json(resp)[0]["id"]
        resp = client.post("/api/alocacoes", json={
            "professor_id": cls.prof_a_id,
            "turma_id": cls.turma_a,
            "disciplina_id": cls.disc_id,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])

        cls._login_client(client, cls.inep_b, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano Sul", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_b = dash["turmas"][0]["id"]
        cls.prof_b_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_b)
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Beta UniqueACL",
            "cpf": "61044444444",
            "turma_id": cls.turma_b,
            "nome_mae": "Mãe Beta",
        })
        cls.aluno_b = _json(resp)[0]["id"]
        resp = client.post("/api/alocacoes", json={
            "professor_id": cls.prof_b_id,
            "turma_id": cls.turma_b,
            "disciplina_id": cls.disc_id,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])

        cls._login_client(client, cls.cpf_prof_a, SENHA_PESSOAL)
        resp = client.post("/api/planejamento/enviar", data={
            "turma_id": str(cls.turma_a),
            "conteudo": "Plano da Escola Norte",
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])

        cls._login_client(client, cls.cpf_prof_b, SENHA_PESSOAL)
        resp = client.post("/api/planejamento/enviar", data={
            "turma_id": str(cls.turma_b),
            "conteudo": "Plano da Escola Sul",
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])

        with cls.app.app_context():
            from src.models import Planejamento
            cls.plan_a = Planejamento.query.filter_by(turma_id=cls.turma_a).first().id
            cls.plan_b = Planejamento.query.filter_by(turma_id=cls.turma_b).first().id

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        return _json(resp)[0]

    def _assert_403(self, resp, ctx=""):
        self.assertEqual(resp.status_code, 403, ctx or _json(resp)[1])
        data, error = _json(resp)
        self.assertIsNone(data)
        self.assertTrue(error)

    def test_15_sistema_inicia(self):
        self.assertIsNotNone(self.app)
        self._login("admin", SENHA_DEMO_ADMIN)

    def test_16_download_arquivo_de_outra_escola_e_negado(self):
        self._login(self.login_coord_a)
        resp = self.client.get(f"/api/planejamentos/{self.plan_b}/arquivo")
        self._assert_403(resp)
        self._login(self.cpf_prof_a)
        resp = self.client.get(f"/api/planejamentos/{self.plan_b}/arquivo")
        self._assert_403(resp)
        self._login(self.inep_a)
        resp = self.client.get(f"/api/planejamentos/{self.plan_b}/arquivo")
        self._assert_403(resp)

    def test_01_professor_acessa_propria_turma(self):
        self._login(self.cpf_prof_a, SENHA_PESSOAL)
        resp = self.client.get(f"/api/turmas/{self.turma_a}/painel")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        self.assertEqual(data["turma"]["id"], self.turma_a)
        ids = [a["id"] for a in data["turma"]["alunos"]]
        self.assertIn(self.aluno_a, ids)
        self.assertTrue(data["podeEditar"])

    def test_02_professor_nao_acessa_turma_alheia(self):
        self._login(self.cpf_prof_a, SENHA_PESSOAL)
        resp = self.client.get(f"/api/turmas/{self.turma_b}/painel")
        self._assert_403(resp)

    def test_03_professor_nao_lanca_sem_alocacao(self):
        self._login(self.cpf_prof_a, SENHA_PESSOAL)
        payload = [{"aluno_id": self.aluno_a, "valor": "8", "tipo": "nota", "bimestre": 11}]
        resp = self.client.post(
            f"/api/turmas/{self.turma_a}/disciplinas/{self.disc_outra}/notas",
            json=payload,
        )
        self._assert_403(resp, "disciplina sem alocação")
        resp = self.client.post(
            f"/api/turmas/{self.turma_b}/disciplinas/{self.disc_id}/notas",
            json=[{"aluno_id": self.aluno_b, "valor": "8", "tipo": "nota", "bimestre": 11}],
        )
        self._assert_403(resp, "turma sem alocação")

    def test_04_coordenador_nao_lanca_notas(self):
        self._login(self.login_coord_a, SENHA_PESSOAL)
        resp = self.client.post(
            f"/api/turmas/{self.turma_a}/disciplinas/{self.disc_id}/notas",
            json=[{"aluno_id": self.aluno_a, "valor": "9", "tipo": "nota", "bimestre": 11}],
        )
        self._assert_403(resp)

    def test_05_gestor_acessa_aluno_da_propria_escola(self):
        self._login(self.inep_a, SENHA_PESSOAL)
        dash = _json(self.client.get("/api/dashboard"))[0]
        ids = [a["id"] for a in dash["alunos"]]
        self.assertIn(self.aluno_a, ids)
        busca = _json(self.client.get("/api/alunos/busca?q=Alpha"))[0]
        self.assertTrue(any(a["id"] == self.aluno_a for a in busca))
        resp = self.client.get(f"/api/alunos/{self.aluno_a}/documentos/boletim.pdf")
        self.assertEqual(resp.status_code, 200)

    def test_06_gestor_nao_acessa_aluno_de_outra_escola(self):
        self._login(self.inep_a, SENHA_PESSOAL)
        dash = _json(self.client.get("/api/dashboard"))[0]
        ids = [a["id"] for a in dash["alunos"]]
        self.assertNotIn(self.aluno_b, ids)
        resp = self.client.get(f"/api/alunos/{self.aluno_b}/documentos/boletim.pdf")
        self._assert_403(resp)

    def test_07_gestor_nao_encerra_turma_de_outra_escola(self):
        self._login(self.inep_a, SENHA_PESSOAL)
        resp = self.client.post(f"/api/turmas/{self.turma_b}/encerrar")
        self._assert_403(resp)

    def test_08_gestor_nao_exclui_recurso_de_outra_escola(self):
        self._login(self.inep_a, SENHA_PESSOAL)
        resp = self.client.delete(f"/api/excluir/aluno/{self.aluno_b}")
        self._assert_403(resp)
        resp = self.client.delete(f"/api/excluir/turma/{self.turma_b}")
        self._assert_403(resp)
        resp = self.client.delete(f"/api/excluir/professor/{self.prof_b_id}")
        self._assert_403(resp)
        resp = self.client.delete(f"/api/excluir/escola/{self.escola_b}")
        self._assert_403(resp)

    def test_09_coordenador_nao_avalia_plano_de_outra_escola(self):
        self._login(self.login_coord_a, SENHA_PESSOAL)
        resp = self.client.post(
            f"/api/coordenacao/planejamento/{self.plan_b}",
            json={"status": "aprovado"},
        )
        self._assert_403(resp)

    def test_10_professor_nao_acessa_documento_de_outra_turma(self):
        self._login(self.cpf_prof_a, SENHA_PESSOAL)
        resp = self.client.get(f"/api/alunos/{self.aluno_b}/documentos/boletim.pdf")
        self._assert_403(resp)
        resp = self.client.get(f"/api/alunos/{self.aluno_b}/documentos/declaracao/cursando.pdf")
        self._assert_403(resp)
        resp = self.client.get(f"/api/turmas/{self.turma_b}/documentos/rendimento/{self.disc_id}.pdf")
        self._assert_403(resp)

    def test_11_gestor_nao_acessa_documento_de_outra_escola(self):
        self._login(self.inep_a, SENHA_PESSOAL)
        for url in (
            f"/api/alunos/{self.aluno_b}/documentos/boletim.pdf",
            f"/api/alunos/{self.aluno_b}/documentos/parecer.pdf",
            f"/api/alunos/{self.aluno_b}/documentos/comprovante-matricula.pdf",
            f"/api/alunos/{self.aluno_b}/documentos/declaracao/cursando.pdf",
        ):
            resp = self.client.get(url)
            self._assert_403(resp, url)

    def test_12_dirigente_monitora_as_duas_escolas(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp_a = self.client.get(f"/api/escolas/{self.escola_a}/painel")
        resp_b = self.client.get(f"/api/escolas/{self.escola_b}/painel")
        self.assertEqual(resp_a.status_code, 200, _json(resp_a)[1])
        self.assertEqual(resp_b.status_code, 200, _json(resp_b)[1])
        self._login(self.cpf_prof_a, SENHA_PESSOAL)
        resp = self.client.get(f"/api/escolas/{self.escola_a}/painel")
        self._assert_403(resp)
        self._login(self.inep_a, SENHA_PESSOAL)
        resp = self.client.get(f"/api/escolas/{self.escola_b}/painel")
        self._assert_403(resp)

    def test_13_professor_busca_nao_retorna_outra_turma(self):
        self._login(self.cpf_prof_a, SENHA_PESSOAL)
        busca = _json(self.client.get("/api/alunos/busca?q=Beta"))[0]
        self.assertFalse(any(a["id"] == self.aluno_b for a in busca))
        busca = _json(self.client.get("/api/alunos/busca?q=Alpha"))[0]
        self.assertTrue(any(a["id"] == self.aluno_a for a in busca))

    def test_14_gestor_busca_nao_retorna_outra_escola(self):
        self._login(self.inep_a, SENHA_PESSOAL)
        busca = _json(self.client.get("/api/alunos/busca?q=Beta"))[0]
        self.assertFalse(any(a["id"] == self.aluno_b for a in busca))
        self._login("admin", SENHA_DEMO_ADMIN)
        busca = _json(self.client.get("/api/alunos/busca?q=Beta"))[0]
        self.assertTrue(any(a["id"] == self.aluno_b for a in busca))

    def test_professor_nao_edita_outro_professor(self):
        self._login(self.cpf_prof_a, SENHA_PESSOAL)
        resp = self.client.put(
            f"/api/editar/professor/{self.prof_b_id}",
            json={"nome": "Hack", "cpf": self.cpf_prof_b},
        )
        self._assert_403(resp)

    def test_gestor_nao_lanca_notas(self):
        self._login(self.inep_a, SENHA_PESSOAL)
        resp = self.client.post(
            f"/api/turmas/{self.turma_a}/disciplinas/{self.disc_id}/notas",
            json=[{"aluno_id": self.aluno_a, "valor": "6", "tipo": "nota", "bimestre": 11}],
        )
        self._assert_403(resp)

    def test_gestor_nao_cria_escola(self):
        self._login(self.inep_a, SENHA_PESSOAL)
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola Intrusa",
            "inep": "61999999",
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self._assert_403(resp)


if __name__ == "__main__":
    unittest.main()
