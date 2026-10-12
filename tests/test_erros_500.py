import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class Erros500Test(unittest.TestCase):
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

    def _login_admin(self):
        resp = self.client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _gestor_com_aluno(self):
        Erros500Test._seq += 1
        inep = f"5005{Erros500Test._seq:04d}"
        self._login_admin()
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola 500 {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        concluir_primeiro_acesso(self.client, inep, senha_temporaria(resp))
        self.client.post("/api/turmas", json={"nome": "3º Ano", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = dash["turmas"][0]["id"]
        disc_id = dash["disciplinas"][0]["id"]
        cpf = f"500{Erros500Test._seq:08d}"
        resp = self.client.post("/api/alunos", json={
            "nome": "Aluno Sem Documento",
            "cpf": cpf,
            "turma_id": turma_id,
            "nome_mae": "Mãe Documento",
        })
        aluno_id = _json(resp)[0]["id"]
        return turma_id, disc_id, aluno_id

    def test_sistema_inicia(self):
        self.assertIsNotNone(self.app)
        self._login_admin()

    def test_boletim_id_inexistente_nao_gera_500(self):
        self._login_admin()
        resp = self.client.get("/api/alunos/99999/documentos/boletim.pdf")
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)
        _, error = _json(resp)
        self.assertIn("não encontrado", error.lower())

    def test_declaracao_id_inexistente_nao_gera_500(self):
        self._login_admin()
        resp = self.client.get("/api/alunos/99999/documentos/declaracao/cursando.pdf")
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)

    def test_aluno_sem_turma_nao_quebra_documento(self):
        turma_id, _, aluno_id = self._gestor_com_aluno()
        resp = self.client.post(f"/api/turmas/{turma_id}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        for url in [
            f"/api/alunos/{aluno_id}/documentos/boletim.pdf",
            f"/api/alunos/{aluno_id}/documentos/declaracao/cursando.pdf",
            f"/api/alunos/{aluno_id}/documentos/parecer.pdf",
            f"/api/alunos/{aluno_id}/documentos/comprovante-matricula.pdf",
        ]:
            resp = self.client.get(url)
            self.assertNotEqual(resp.status_code, 500, url)
            self.assertEqual(resp.status_code, 400, url)
            _, error = _json(resp)
            self.assertIn("sem turma", error.lower())

    def test_salvar_notas_turma_inexistente_nao_gera_500(self):
        self._login_admin()
        resp = self.client.post(
            "/api/turmas/99999/disciplinas/1/notas",
            json=[{"aluno_id": 1, "valor": "7", "tipo": "nota", "bimestre": 11}],
        )
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)
        _, error = _json(resp)
        self.assertIn("turma", error.lower())

    def test_editar_id_inexistente_nao_gera_500(self):
        self._login_admin()
        resp = self.client.put("/api/editar/usuario/99999", json={"login": "x", "nome": "y"})
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)

    def test_excluir_id_inexistente_nao_gera_500(self):
        self._login_admin()
        resp = self.client.delete("/api/excluir/aluno/99999")
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)

    def test_planejamento_inexistente_nao_gera_500(self):
        self._login_admin()
        resp = self.client.post("/api/coordenacao/planejamento/99999", json={"status": "aprovado"})
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post("/api/planejamento/99999/arquivar")
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)

    def test_registro_aula_inexistente_nao_gera_500(self):
        self._login_admin()
        resp = self.client.get("/api/registros-aula/99999")
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)
        resp = self.client.post("/api/coordenacao/registros-aula/99999", json={"status": "validada"})
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)

    def test_criar_diretor_escola_inexistente_nao_gera_500(self):
        self._login_admin()
        resp = self.client.post("/api/escolas/99999/diretor")
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)
        _, error = _json(resp)
        self.assertIn("escola", error.lower())

    def test_json_invalido_nao_gera_500(self):
        self._login_admin()
        resp = self.client.post("/api/escolas", data="{nao-json", content_type="application/json")
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 400)

        turma_id, disc_id, _ = self._gestor_com_aluno()
        resp = self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/{disc_id}/notas",
            json={"aluno_id": 1},
        )
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 400)

        resp = self.client.post("/api/alunos", json={})
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 400)

    def test_relatorio_recursos_inexistentes_nao_gera_500(self):
        self._login_admin()
        resp = self.client.get("/api/turmas/99999/documentos/rendimento/1.pdf")
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)
        turma_id, _, _ = self._gestor_com_aluno()
        resp = self.client.get(f"/relatorio/turma/{turma_id}/disciplina/99999")
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
