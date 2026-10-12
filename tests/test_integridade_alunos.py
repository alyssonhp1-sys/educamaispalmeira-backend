import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class IntegridadeAlunosTest(unittest.TestCase):
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

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        return _json(resp)[0]

    def _criar_escola_e_gestor(self, nome, inep):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": nome,
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        concluir_primeiro_acesso(self.client, inep, senha_temporaria(resp))

    def test_sistema_inicia(self):
        self.assertIsNotNone(self.app)
        resp = self.client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        self.assertEqual(resp.status_code, 200)

    def test_excluir_turma_nao_apaga_aluno(self):
        self._criar_escola_e_gestor("Escola Exclusao Turma", "11111111")
        resp = self.client.post("/api/turmas", json={"nome": "5º Ano A", "ano_letivo": 2026, "etapa": "fundamental"})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = dash["turmas"][0]["id"]
        cpf = "11122233344"
        resp = self.client.post("/api/alunos", json={
            "nome": "Aluno Preservado Exclusao",
            "cpf": cpf,
            "turma_id": turma_id,
            "nome_mae": "Mãe Preservada",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aluno_id = _json(resp)[0]["id"]

        resp = self.client.delete(f"/api/excluir/turma/{turma_id}")
        self.assertEqual(resp.status_code, 409)

        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula, Turma
            aluno = Aluno.query.filter_by(id=aluno_id).first()
            self.assertIsNotNone(aluno)
            self.assertEqual(aluno.cpf, cpf)
            self.assertEqual(aluno.turma_id, turma_id)
            self.assertIsNotNone(aluno.escola_id)
            self.assertIsNotNone(db.session.get(Turma, turma_id))
            self.assertTrue(Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).count())

        dash = _json(self.client.get("/api/dashboard"))[0]
        ids = [a["id"] for a in dash["alunos"]]
        self.assertIn(aluno_id, ids)
        localizado = next(a for a in dash["alunos"] if a["id"] == aluno_id)
        self.assertEqual(localizado["cpf"], cpf)
        self.assertEqual(localizado["turmaId"], turma_id)

        busca = _json(self.client.get("/api/alunos/busca?q=Preservado"))[0]
        self.assertTrue(any(a["id"] == aluno_id and a["cpf"] == cpf for a in busca))

    def test_encerrar_turma_preserva_aluno_e_notas(self):
        self._criar_escola_e_gestor("Escola Encerrar", "22222222")
        resp = self.client.post("/api/turmas", json={"nome": "6º Ano B", "ano_letivo": 2026, "etapa": "fundamental"})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = dash["turmas"][0]["id"]
        disc_id = dash["disciplinas"][0]["id"]
        cpf = "55566677788"
        resp = self.client.post("/api/alunos", json={
            "nome": "Aluno Encerramento",
            "cpf": cpf,
            "turma_id": turma_id,
            "nome_mae": "Mãe Encerramento",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aluno_id = _json(resp)[0]["id"]
        escola_id = dash["escola"]["id"]

        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Encerramento",
            "cpf": "55500011122",
            "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        concluir_primeiro_acesso(self.client, "55500011122", senha_temporaria(resp))
        self._login("22222222", SENHA_PESSOAL)
        dash = _json(self.client.get("/api/dashboard"))[0]
        prof_id = next(p["id"] for p in dash["professores"] if p["cpf"] == "55500011122")
        resp = self.client.post("/api/alocacoes", json={
            "professor_id": prof_id,
            "turma_id": turma_id,
            "disciplina_id": disc_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._login("55500011122", SENHA_PESSOAL)
        resp = self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/{disc_id}/notas",
            json=[{"aluno_id": aluno_id, "valor": "7.5", "tipo": "nota", "bimestre": 11}],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        self._login("22222222", SENHA_PESSOAL)
        resp = self.client.post(f"/api/turmas/{turma_id}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            from src import db
            from src.models import Aluno, RegistroNota, HistoricoAnual, Turma
            aluno = Aluno.query.filter_by(id=aluno_id).first()
            self.assertIsNotNone(aluno)
            self.assertEqual(aluno.cpf, cpf)
            self.assertIsNone(aluno.turma_id)
            self.assertIsNotNone(aluno.escola_id)
            notas = RegistroNota.query.filter_by(aluno_id=aluno_id).all()
            self.assertTrue(notas)
            self.assertEqual(notas[0].valor, 7.5)
            hist = HistoricoAnual.query.filter_by(aluno_id=aluno_id).all()
            self.assertTrue(hist)
            turma = db.session.get(Turma, turma_id)
            self.assertEqual(turma.status, "concluida")

        dash = _json(self.client.get("/api/dashboard"))[0]
        ids = [a["id"] for a in dash["alunos"]]
        self.assertIn(aluno_id, ids)
        busca = _json(self.client.get("/api/alunos/busca?q=Encerramento"))[0]
        self.assertTrue(any(a["cpf"] == cpf for a in busca))

    def test_excluir_escola_com_alunos_e_bloqueada(self):
        self._criar_escola_e_gestor("Escola Bloqueio", "33333333")
        resp = self.client.post("/api/turmas", json={"nome": "1º Ano", "ano_letivo": 2026, "etapa": "fundamental"})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = dash["escola"]["id"]
        turma_id = dash["turmas"][0]["id"]
        self.client.post("/api/alunos", json={
            "nome": "Aluno da Escola",
            "cpf": "99988877766",
            "turma_id": turma_id,
            "nome_mae": "Mãe da Escola",
        })

        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.delete(f"/api/excluir/escola/{escola_id}")
        self.assertEqual(resp.status_code, 409)
        _, error = _json(resp)
        self.assertTrue(error)
        self.assertIn("vinculados", error.lower())

        with self.app.app_context():
            from src import db
            from src.models import Aluno, Escola
            self.assertIsNotNone(db.session.get(Escola, escola_id))
            self.assertIsNotNone(Aluno.query.filter_by(cpf="99988877766").first())


if __name__ == "__main__":
    unittest.main()
