import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class DiarioPlanejamentoIndicadoresTest(unittest.TestCase):
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
        cls._montar()

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass

    @classmethod
    def _login_client(cls, client, login, senha):
        resp = client.post("/api/auth/login", json={"login": login, "senha": senha})
        if resp.status_code != 200:
            raise RuntimeError("login %s: %s" % (login, _json(resp)[1]))

    @classmethod
    def _montar(cls):
        client = cls.app.test_client()
        cls._login_client(client, "admin", SENHA_DEMO_ADMIN)
        from src.models import Disciplina
        with cls.app.app_context():
            cls.mat = Disciplina.query.filter_by(nome="Matemática").first().id
            cls.port = Disciplina.query.filter_by(nome="Português").first().id
        cls.inep = "87000001"
        cls.cpf_prof = "87011111111"
        cls.login_coord = "coord870"
        resp = client.post("/api/escolas", json={
            "nome": "Escola Etapa8 Demo", "inep": cls.inep,
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep)

        resp = client.post(f"/api/escolas/{cls.escola_id}/coordenadores", json={"login": cls.login_coord, "nome": "Coord E8"})
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_coord = senha_temporaria(resp)
        resp = client.post("/api/professores", json={"nome": "Prof E8", "cpf": cls.cpf_prof, "escola_id": cls.escola_id})
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(client, cls.inep, temp_gestor)
        concluir_primeiro_acesso(client, cls.login_coord, temp_coord)
        concluir_primeiro_acesso(client, cls.cpf_prof, temp_prof)

        cls._login_client(client, cls.inep, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano E8", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_id = dash["turmas"][0]["id"]
        cls.prof_id = dash["professores"][0]["id"]
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Risco E8", "cpf": "87022222222",
            "turma_id": cls.turma_id, "nome_mae": "Mãe E8",
        })
        cls.aluno_id = _json(resp)[0]["id"]
        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_id, "turma_id": cls.turma_id, "disciplina_id": cls.mat,
        })
        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_id, "turma_id": cls.turma_id, "disciplina_id": cls.port,
        })

        cls._login_client(client, cls.cpf_prof, SENHA_PESSOAL)
        resp = client.post("/api/planejamento/enviar", data={
            "turma_id": str(cls.turma_id),
            "conteudo": "Plano inicial E8",
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        with cls.app.app_context():
            from src.models import Planejamento
            cls.plan_id = Planejamento.query.filter_by(turma_id=cls.turma_id).first().id

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_01_salvar_faltas_nao_apaga_notas(self):
        self._login(self.cpf_prof)
        notas = [
            {"aluno_id": self.aluno_id, "valor": "8", "tipo": "nota", "bimestre": 11},
            {"aluno_id": self.aluno_id, "valor": "7", "tipo": "nota", "bimestre": 12},
        ]
        resp = self.client.post(
            f"/api/turmas/{self.turma_id}/disciplinas/{self.mat}/notas", json=notas,
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        faltas = [
            {"aluno_id": self.aluno_id, "valor": "3", "tipo": "falta", "bimestre": 1},
            {"aluno_id": self.aluno_id, "valor": "1", "tipo": "falta", "bimestre": 2},
        ]
        resp = self.client.post(
            f"/api/turmas/{self.turma_id}/disciplinas/{self.mat}/notas", json=faltas,
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dados, err = _json(self.client.get(
            f"/api/monitoramento/turma/{self.turma_id}/disciplina/{self.mat}"
        ))
        self.assertIsNone(err)
        aluno = next(x for x in dados if x["id"] == self.aluno_id)
        self.assertEqual({n["bimestre"]: n["valor"] for n in aluno["notas"]}, {11: 8.0, 12: 7.0})
        self.assertEqual({f["bimestre"]: f["quantidade"] for f in aluno["faltas"]}, {1: 3, 2: 1})

    def test_02_devolver_sem_feedback_e_recusado(self):
        self._login(self.login_coord)
        resp = self.client.post(
            f"/api/coordenacao/planejamento/{self.plan_id}",
            json={"status": "revisar", "feedback": "  "},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("feedback", (_json(resp)[1] or "").lower())

    def test_03_devolver_com_feedback_e_professor_ve(self):
        self._login(self.login_coord)
        resp = self.client.post(
            f"/api/coordenacao/planejamento/{self.plan_id}",
            json={"status": "revisar", "feedback": "Detalhe a avaliação da semana."},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._login(self.cpf_prof)
        dash, err = _json(self.client.get("/api/dashboard"))
        self.assertIsNone(err)
        plano = next(p for p in dash["planejamentos"] if p["id"] == self.plan_id)
        self.assertEqual(plano["status"], "revisar")
        self.assertIn("avaliação", (plano["feedback"] or "").lower())

    def test_04_professor_reenvia_apos_revisao(self):
        self._login(self.login_coord)
        resp = self.client.post(
            f"/api/coordenacao/planejamento/{self.plan_id}",
            json={"status": "revisar", "feedback": "Ajuste a metodologia e reenvie."},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._login(self.cpf_prof)
        resp = self.client.post(
            f"/api/planejamento/{self.plan_id}/reenviar",
            json={"conteudo": "Plano corrigido E8 com critérios."},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._login(self.login_coord)
        dash, _ = _json(self.client.get("/api/dashboard"))
        plano = next(p for p in dash["planejamentos"] if p["id"] == self.plan_id)
        self.assertEqual(plano["status"], "pendente")
        self.assertIn("critérios", (plano["conteudo"] or "").lower())

    def test_05_coordenador_aprova(self):
        self._login(self.login_coord)
        resp = self.client.post(
            f"/api/coordenacao/planejamento/{self.plan_id}",
            json={"status": "aprovado"},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._login(self.cpf_prof)
        resp = self.client.post(
            f"/api/planejamento/{self.plan_id}/reenviar",
            json={"conteudo": "tentativa indevida"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_06_indicadores_coordenador(self):
        self._login(self.cpf_prof)
        payload = []
        for bim, val in ((11, 4), (12, 4), (21, 4), (22, 4), (31, 4), (32, 4), (41, 4), (42, 4)):
            payload.append({"aluno_id": self.aluno_id, "valor": str(val), "tipo": "nota", "bimestre": bim})
        resp = self.client.post(
            f"/api/turmas/{self.turma_id}/disciplinas/{self.mat}/notas", json=payload,
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._login(self.login_coord)
        dash, err = _json(self.client.get("/api/dashboard"))
        self.assertIsNone(err)
        self.assertEqual(dash["painel"], "coordenador")
        self.assertTrue(any(a["id"] == self.aluno_id for a in dash["alunosEmRisco"]))
        self.assertTrue(any(d["disciplinaNome"] == "Português" for d in dash["diariosPendentes"]))
        self.assertIn("alunosEmRisco", dash["indicadores"])

    def test_07_documentos_e_relatorio_do_professor_alocado(self):
        self._login(self.cpf_prof)
        self.assertEqual(self.client.get(f"/api/alunos/{self.aluno_id}/documentos/boletim.pdf").status_code, 403)
        self.assertEqual(self.client.get(f"/api/alunos/{self.aluno_id}/documentos/declaracao/cursando.pdf").status_code, 403)
        self.assertEqual(self.client.get(f"/api/alunos/{self.aluno_id}/documentos/comprovante-matricula.pdf").status_code, 403)
        self.assertEqual(
            self.client.get(f"/api/turmas/{self.turma_id}/documentos/rendimento/{self.mat}.pdf").status_code, 200
        )

    def test_08_dashboard_municipal_traz_indicadores(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        dash, err = _json(self.client.get("/api/dashboard"))
        self.assertIsNone(err)
        self.assertEqual(dash["painel"], "municipal")
        self.assertIn("indicadores", dash)
        self.assertIn("totalEscolas", dash["indicadores"])
        self.assertIn("turmasAtivas", dash["indicadores"])
        self.assertIn("alunosEmRisco", dash["indicadores"])
        self.assertGreaterEqual(dash["indicadores"]["totalEscolas"], 1)


if __name__ == "__main__":
    unittest.main()
