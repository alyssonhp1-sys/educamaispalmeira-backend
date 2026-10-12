import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class CicloAlunoTest(unittest.TestCase):
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
    def _login_client(cls, client, login, senha):
        resp = client.post("/api/auth/login", json={"login": login, "senha": senha})
        if resp.status_code != 200:
            raise RuntimeError("Falha no login: %s %s" % (login, _json(resp)[1]))

    @classmethod
    def _montar_rede(cls):
        client = cls.app.test_client()
        cls._login_client(client, "admin", SENHA_DEMO_ADMIN)
        cls.inep_a = "91000001"
        cls.inep_b = "91000002"
        temps = {}
        for nome, inep in (("Escola Ciclo Norte", cls.inep_a), ("Escola Ciclo Sul", cls.inep_b)):
            resp = client.post("/api/escolas", json={
                "nome": nome, "inep": inep,
                "oferta_infantil": True, "oferta_fundamental": True,
            })
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[inep] = senha_temporaria(resp)
        for inep, temp in temps.items():
            concluir_primeiro_acesso(client, inep, temp)

        cls._login_client(client, cls.inep_a, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano Norte", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_a = dash["turmas"][0]["id"]

        cls._login_client(client, cls.inep_b, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano Sul", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_b = dash["turmas"][0]["id"]

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _criar(self, nome, cpf, turma_id=None, mae="Mãe Ciclo", pai=None):
        payload = {"nome": nome, "cpf": cpf}
        if mae is not None:
            payload["nome_mae"] = mae
        if pai is not None:
            payload["nome_pai"] = pai
        if turma_id is not None:
            payload["turma_id"] = turma_id
        return self.client.post("/api/alunos", json=payload)

    def test_01_criar_aluno_cpf_unico(self):
        self._login(self.inep_a)
        resp = self._criar("Aluno Unico Ciclo", "91011111111", self.turma_a)
        data, erro = _json(resp)
        self.assertEqual(resp.status_code, 200, erro)
        self.assertTrue(data["id"])

    def test_02_cpf_duplicado_na_criacao_rejeitado(self):
        self._login(self.inep_a)
        self.assertEqual(self._criar("Primeiro Dup", "91022222222", self.turma_a).status_code, 200)
        resp = self._criar("Segundo Dup", "91022222222", self.turma_a)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cpf", (_json(resp)[1] or "").lower())

    def test_03_editar_nome_do_aluno(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("Nome Antigo Ciclo", "91033333333", self.turma_a))[0]["id"]
        resp = self.client.put(f"/api/alunos/{aluno_id}", json={
            "nome": "Nome Novo Ciclo",
            "cpf": "91033333333",
            "nome_mae": "Mãe Ciclo",
        })
        data, erro = _json(resp)
        self.assertEqual(resp.status_code, 200, erro)
        self.assertEqual(data["nome"], "Nome Novo Ciclo")
        got = _json(self.client.get(f"/api/alunos/{aluno_id}"))[0]
        self.assertEqual(got["nome"], "Nome Novo Ciclo")

    def test_04_editar_cpf_para_existente_rejeitado(self):
        self._login(self.inep_a)
        self._criar("Alvo CPF A", "91044444444", self.turma_a)
        aluno_id = _json(self._criar("Alvo CPF B", "91055555555", self.turma_a))[0]["id"]
        resp = self.client.put(f"/api/alunos/{aluno_id}", json={
            "nome": "Alvo CPF B",
            "cpf": "91044444444",
            "nome_mae": "Mãe Ciclo",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cpf", (_json(resp)[1] or "").lower())

    def test_05_gestor_a_nao_edita_aluno_da_escola_b(self):
        self._login(self.inep_b)
        aluno_b = _json(self._criar("Aluno Sul Edit", "91066666666", self.turma_b))[0]["id"]
        self._login(self.inep_a)
        resp = self.client.put(f"/api/alunos/{aluno_b}", json={
            "nome": "Hack Nome",
            "cpf": "91066666666",
            "nome_mae": "Mãe Ciclo",
        })
        self.assertEqual(resp.status_code, 403)

    def test_06_inativar_preserva_registro(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("Aluno Inativar", "91077777777", self.turma_a))[0]["id"]
        resp = self.client.post(f"/api/alunos/{aluno_id}/inativar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno
            aluno = db.session.get(Aluno, aluno_id)
            self.assertIsNotNone(aluno)
            self.assertEqual(aluno.cpf, "91077777777")
            self.assertEqual(aluno.situacao, "inativo")
            self.assertIsNone(aluno.turma_id)

    def test_07_inativo_continua_localizavel(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("Aluno Consulta Inativo", "91088888888", self.turma_a))[0]["id"]
        self.client.post(f"/api/alunos/{aluno_id}/inativar")
        lista = _json(self.client.get("/api/alunos?situacao=inativo"))[0]
        self.assertTrue(any(a["id"] == aluno_id for a in lista))
        busca = _json(self.client.get("/api/alunos/busca?q=Consulta"))[0]
        self.assertTrue(any(a["id"] == aluno_id for a in busca))

    def test_08_aluno_sem_turma_aparece_para_gestor(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("Aluno Sem Turma Norte", "91099999999"))[0]["id"]
        lista = _json(self.client.get("/api/alunos?sem_turma=1"))[0]
        self.assertTrue(any(a["id"] == aluno_id for a in lista))
        dash = _json(self.client.get("/api/dashboard"))[0]
        local = next(a for a in dash["alunos"] if a["id"] == aluno_id)
        self.assertIsNone(local["turmaId"])

    def test_09_gestor_enturma_aluno_sem_turma(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("Aluno Enturmar Norte", "91100000001"))[0]["id"]
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": self.turma_a})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        got = _json(self.client.get(f"/api/alunos/{aluno_id}"))[0]
        self.assertEqual(got["turmaId"], self.turma_a)

    def test_10_gestor_nao_enturma_em_escola_diferente(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("Aluno Nao Cruza Escola", "91100000002"))[0]["id"]
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": self.turma_b})
        self.assertEqual(resp.status_code, 403)

    def test_11_aluno_ja_enturmado_nao_recebe_segunda_turma(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={"nome": "Turma Segunda", "ano_letivo": 2026, "etapa": "fundamental"})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        outra = next(t["id"] for t in dash["turmas"] if t["nome"] == "Turma Segunda")
        aluno_id = _json(self._criar("Aluno Ja Enturmado", "91100000003", self.turma_a))[0]["id"]
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": outra})
        self.assertEqual(resp.status_code, 409)
        self.assertTrue(_json(resp)[1])

    def test_12_rematricula_apos_encerramento(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={"nome": "Turma Encerrar Ciclo", "ano_letivo": 2026, "etapa": "fundamental"})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_old = next(t["id"] for t in dash["turmas"] if t["nome"] == "Turma Encerrar Ciclo")
        aluno_id = _json(self._criar("Aluno Rematricula", "91100000004", turma_old))[0]["id"]
        resp = self.client.post(f"/api/turmas/{turma_old}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, HistoricoAnual
            aluno = db.session.get(Aluno, aluno_id)
            self.assertIsNone(aluno.turma_id)
            self.assertTrue(HistoricoAnual.query.filter_by(aluno_id=aluno_id).count())
        resp = self.client.post("/api/turmas", json={
            "nome": "5º Ano Norte 2027", "ano_letivo": 2027, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        nova = next(t["id"] for t in dash["turmas"] if t["nome"] == "5º Ano Norte 2027")
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": nova})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, HistoricoAnual
            aluno = db.session.get(Aluno, aluno_id)
            self.assertEqual(aluno.turma_id, nova)
            self.assertTrue(HistoricoAnual.query.filter_by(aluno_id=aluno_id).count())

    def test_13_capacidade_disponivel_enturma(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Cap OK", "ano_letivo": 2026, "etapa": "fundamental", "capacidade": 2,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == "Turma Cap OK")
        aluno_id = _json(self._criar("Aluno Vaga Ok", "91100000005"))[0]["id"]
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": turma_id})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_14_capacidade_cheia_bloqueia(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Cap Cheia", "ano_letivo": 2026, "etapa": "fundamental", "capacidade": 1,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == "Turma Cap Cheia")
        self.assertEqual(self._criar("Ocupa Vaga Unica", "91100000006", turma_id).status_code, 200)
        aluno_id = _json(self._criar("Sem Vaga", "91100000007"))[0]["id"]
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": turma_id})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("vaga", (_json(resp)[1] or "").lower())

    def test_15_novo_aluno_sem_responsavel_rejeitado(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/alunos", json={
            "nome": "Sem Responsavel",
            "cpf": "91100000008",
            "turma_id": self.turma_a,
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("responsável", (_json(resp)[1] or "").lower())

    def test_16_aluno_com_mae_ou_pai_aceito(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/alunos", json={
            "nome": "So Pai",
            "cpf": "91100000009",
            "nome_pai": "Pai Ciclo",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_17_busca_por_nome(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("NomeBuscaZeferino", "91100000010", self.turma_a))[0]["id"]
        busca = _json(self.client.get("/api/alunos/busca?q=Zeferino"))[0]
        self.assertTrue(any(a["id"] == aluno_id for a in busca))

    def test_18_busca_por_cpf(self):
        self._login(self.inep_a)
        self._criar("Cpf Busca Aluno", "91100000011", self.turma_a)
        busca = _json(self.client.get("/api/alunos/busca?q=91100000011"))[0]
        self.assertTrue(any(a["cpf"] == "91100000011" for a in busca))

    def test_19_filtro_sem_turma(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("Filtro Sem Turma", "91100000012"))[0]["id"]
        lista = _json(self.client.get("/api/alunos?sem_turma=1"))[0]
        ids = [a["id"] for a in lista]
        self.assertIn(aluno_id, ids)
        self.assertTrue(all(a.get("turmaId") in (None, "") for a in lista))

    def test_20_app_continua_iniciando(self):
        self.assertIsNotNone(self.app)
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.get("/api/me")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])


if __name__ == "__main__":
    unittest.main()
