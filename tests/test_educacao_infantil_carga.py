import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class EducacaoInfantilCargaTest(unittest.TestCase):
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
        cls.inep = "94200001"
        cls.cpf_prof = "94211111111"
        cls.login_coord = "coord942"
        resp = client.post("/api/escolas", json={
            "nome": "Escola EI Carga", "inep": cls.inep,
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_inep = senha_temporaria(resp)
        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep)
        resp = client.post(f"/api/escolas/{cls.escola_id}/coordenadores", json={"login": cls.login_coord, "nome": "Coord EI"})
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_coord = senha_temporaria(resp)
        resp = client.post("/api/professores", json={"nome": "Prof EI Carga", "cpf": cls.cpf_prof, "escola_id": cls.escola_id})
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(client, cls.inep, temp_inep)
        concluir_primeiro_acesso(client, cls.login_coord, temp_coord)
        concluir_primeiro_acesso(client, cls.cpf_prof, temp_prof)

        cls._login_client(client, "admin", SENHA_DEMO_ADMIN)
        resp = client.post("/api/anos-letivos", json={
            "ano": 2026,
            "data_inicio": "2026-02-02",
            "data_fim": "2026-02-13",
            "ativo": True,
            "gerar_calendario": True,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        cls.ano_id = _json(resp)[0]["id"]

        cls._login_client(client, cls.inep, SENHA_PESSOAL)
        resp = client.post("/api/turmas", json={
            "nome": "Maternal Parcial", "ano_letivo": 2026, "etapa": "infantil",
            "tipo_jornada": "PARCIAL", "carga_horaria_diaria": 4, "carga_horaria_anual": 800,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        resp = client.post("/api/turmas", json={
            "nome": "Jardim Integral", "ano_letivo": 2026, "etapa": "infantil",
            "tipo_jornada": "INTEGRAL", "carga_horaria_diaria": 7, "carga_horaria_anual": 1400,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_parcial = next(t["id"] for t in dash["turmas"] if t["nome"] == "Maternal Parcial")
        cls.turma_integral = next(t["id"] for t in dash["turmas"] if t["nome"] == "Jardim Integral")
        cls.prof_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof)
        resp = client.post("/api/alunos", json={
            "nome": "Criança EI", "cpf": "94222222222",
            "turma_id": cls.turma_parcial, "nome_mae": "Mãe EI",
        })
        cls.aluno_id = _json(resp)[0]["id"]
        resp = client.post("/api/alocacoes", json={
            "professor_id": cls.prof_id, "turma_id": cls.turma_parcial,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_21_turma_parcial(self):
        self._login(self.inep)
        dash, _ = _json(self.client.get("/api/dashboard"))
        t = next(x for x in dash["turmas"] if x["id"] == self.turma_parcial)
        self.assertEqual(t["tipoJornada"], "PARCIAL")

    def test_22_turma_integral(self):
        self._login(self.inep)
        dash, _ = _json(self.client.get("/api/dashboard"))
        t = next(x for x in dash["turmas"] if x["id"] == self.turma_integral)
        self.assertEqual(t["tipoJornada"], "INTEGRAL")

    def test_23_carga_diaria_persistida(self):
        self._login(self.inep)
        dash, _ = _json(self.client.get("/api/dashboard"))
        t = next(x for x in dash["turmas"] if x["id"] == self.turma_parcial)
        self.assertEqual(t["cargaHorariaDiaria"], 4)
        resp = self.client.put(f"/api/turmas/{self.turma_parcial}", json={"carga_horaria_diaria": 4.5})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash, _ = _json(self.client.get("/api/dashboard"))
        t = next(x for x in dash["turmas"] if x["id"] == self.turma_parcial)
        self.assertEqual(t["cargaHorariaDiaria"], 4.5)
        self.client.put(f"/api/turmas/{self.turma_parcial}", json={"carga_horaria_diaria": 4})

    def test_24_carga_anual_persistida(self):
        self._login(self.inep)
        dash, _ = _json(self.client.get("/api/dashboard"))
        t = next(x for x in dash["turmas"] if x["id"] == self.turma_parcial)
        self.assertEqual(t["cargaHorariaAnual"], 800)
        t2 = next(x for x in dash["turmas"] if x["id"] == self.turma_integral)
        self.assertEqual(t2["cargaHorariaDiaria"], 7)
        self.assertEqual(t2["cargaHorariaAnual"], 1400)

    def test_25_dias_previstos_ate_hoje(self):
        self._login(self.inep)
        acomp, _ = _json(self.client.get("/api/acompanhamento-pedagogico?data=2026-02-06"))
        ei = next(x for x in acomp["educacaoInfantil"] if x["turmaId"] == self.turma_parcial)
        self.assertEqual(ei["diasLetivosAteHoje"], 5)
        self.assertEqual(ei["cargaPrevistaAteHoje"], 20.0)
        self.assertEqual(ei["tipoJornada"], "PARCIAL")

    def test_26_dias_com_registro_validado(self):
        self._login(self.cpf_prof)
        resp = self.client.post(f"/api/turmas/{self.turma_parcial}/disciplinas/0/registros-aula", json={
            "data": "2026-02-03", "bimestre": 1, "quantidade_aulas": 1,
            "conteudo": "Roda de conversa", "metodologia": "Brincadeira",
            "lancamentos": [{"aluno_id": self.aluno_id, "situacao": "PRESENTE"}],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aula_id = _json(resp)[0]["id"]
        self._login(self.login_coord)
        self.client.post(f"/api/coordenacao/registros-aula/{aula_id}", json={"status": "validada"})
        self._login(self.inep)
        acomp, _ = _json(self.client.get("/api/acompanhamento-pedagogico?data=2026-02-06"))
        ei = next(x for x in acomp["educacaoInfantil"] if x["turmaId"] == self.turma_parcial)
        self.assertEqual(ei["diasComRegistroValidado"], 1)
        self.assertAlmostEqual(ei["percentualRegistros"], 20.0)

    def test_27_ei_sem_disciplina_continua_funcionando(self):
        self._login(self.cpf_prof)
        resp = self.client.post(f"/api/turmas/{self.turma_parcial}/disciplinas/0/registros-aula", json={
            "data": "2026-02-04", "bimestre": 1, "quantidade_aulas": 1,
            "conteudo": "Atividade livre", "metodologia": "Cantos",
            "lancamentos": [{"aluno_id": self.aluno_id, "situacao": "PRESENTE"}],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data = _json(resp)[0]
        self.assertIsNone(data["disciplinaId"])
        self.assertEqual(data["status"], "pendente")
        self._login(self.inep)
        acomp, _ = _json(self.client.get("/api/acompanhamento-pedagogico"))
        ids = {i["turmaId"] for i in acomp.get("itens") or []}
        self.assertNotIn(self.turma_parcial, ids)
        ei_ids = {x["turmaId"] for x in acomp["educacaoInfantil"]}
        self.assertIn(self.turma_parcial, ei_ids)

    def test_28_frequencia_pf_continua_funcionando(self):
        self._login(self.cpf_prof)
        resp = self.client.post(f"/api/turmas/{self.turma_parcial}/disciplinas/0/registros-aula", json={
            "data": "2026-02-05", "bimestre": 1, "quantidade_aulas": 1,
            "conteudo": "Parque", "metodologia": "Livre",
            "lancamentos": [{"aluno_id": self.aluno_id, "situacao": "FALTA"}],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data = _json(resp)[0]
        self.assertEqual(data["faltosos"], 1)
        self.assertEqual(data["presentes"], 0)
        mapa = {f["alunoId"]: f["situacao"] for f in data["frequencia"]}
        self.assertEqual(mapa[self.aluno_id], "FALTA")
        form, _ = _json(self.client.get(f"/api/turmas/{self.turma_parcial}/disciplinas/0/registros-aula"))
        self.assertTrue(any(a["id"] == self.aluno_id for a in form["alunos"]))


if __name__ == "__main__":
    unittest.main()
