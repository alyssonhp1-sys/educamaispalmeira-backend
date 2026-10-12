import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class CargaGradeTest(unittest.TestCase):
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
            cls.cie = Disciplina.query.filter_by(nome="Ciências").first().id

        cls.inep = "94100001"
        cls.inep_b = "94100002"
        cls.cpf_prof = "94111111111"
        cls.login_coord = "coord941"

        temps = {}
        for nome, inep in (("Escola Grade Norte", cls.inep), ("Escola Grade Sul", cls.inep_b)):
            resp = client.post("/api/escolas", json={
                "nome": nome, "inep": inep,
                "oferta_infantil": True, "oferta_fundamental": True,
            })
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[inep] = senha_temporaria(resp)
        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep)
        cls.escola_b = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_b)
        resp = client.post(f"/api/escolas/{cls.escola_id}/coordenadores", json={"login": cls.login_coord, "nome": "Coord Grade"})
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temps[cls.login_coord] = senha_temporaria(resp)
        resp = client.post("/api/professores", json={"nome": "Prof Grade", "cpf": cls.cpf_prof, "escola_id": cls.escola_id})
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temps[cls.cpf_prof] = senha_temporaria(resp)
        for login, temp in temps.items():
            concluir_primeiro_acesso(client, login, temp)

        cls._login_client(client, cls.inep, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano Grade", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_id = dash["turmas"][0]["id"]
        cls.prof_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof)
        client.post("/api/alunos", json={
            "nome": "Aluno Grade", "cpf": "94122222222",
            "turma_id": cls.turma_id, "nome_mae": "Mãe Grade",
        })

        cls._login_client(client, "admin", SENHA_DEMO_ADMIN)
        resp = client.post("/api/anos-letivos", json={
            "ano": 2026,
            "data_inicio": "2026-02-02",
            "data_fim": "2026-02-14",
            "ativo": True,
            "gerar_calendario": True,
            "periodos": [
                {"numero": 1, "nome": "1º Bimestre", "data_inicio": "2026-02-02", "data_fim": "2026-02-14"},
            ],
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        cls.ano_id = _json(resp)[0]["id"]

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _alocar(self, extra=None):
        body = {
            "professor_id": self.prof_id,
            "turma_id": self.turma_id,
            "disciplina_id": self.cie,
            "aulas_semanais": 2,
            "duracao_aula_minutos": 50,
            "carga_horaria_prevista": 4,
            "grade": [
                {"dia_semana": 1, "quantidade_aulas": 1},
                {"dia_semana": 3, "quantidade_aulas": 1},
            ],
        }
        if extra:
            body.update(extra)
        return self.client.post("/api/alocacoes", json=body)

    def test_11_alocacao_com_carga(self):
        self._login(self.inep)
        resp = self._alocar()
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash, _ = _json(self.client.get("/api/dashboard"))
        aloc = dash["turmas"][0]["alocacoes"]
        cie = next(a for a in aloc if a["disciplinaId"] == self.cie)
        self.assertEqual(cie["cargaHorariaPrevista"], 4)
        self.assertEqual(cie["aulasSemanais"], 2)
        self.assertEqual(cie["duracaoAulaMinutos"], 50)
        self.aloc_id = cie["id"]

    def test_12_grade_semanal(self):
        self._login(self.inep)
        self._alocar()
        dash, _ = _json(self.client.get("/api/dashboard"))
        cie = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.cie)
        dias = {h["diaSemana"]: h["quantidadeAulas"] for h in cie["grade"]}
        self.assertEqual(dias[1], 1)
        self.assertEqual(dias[3], 1)
        detalhe, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-14"))
        self.assertEqual(len(detalhe["grade"]), 2)

    def test_13_dia_letivo_gera_aula_prevista(self):
        self._login(self.inep)
        self._alocar()
        dash, _ = _json(self.client.get("/api/dashboard"))
        cie = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.cie)
        detalhe, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-05"))
        self.assertEqual(detalhe["aulasPrevistasAteHoje"], 2)

    def test_14_feriado_elimina_aula_prevista(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        self.client.put(f"/api/anos-letivos/{self.ano_id}/dias", json={"data": "2026-02-03", "tipo": "FERIADO"})
        self._login(self.inep)
        self._alocar()
        dash, _ = _json(self.client.get("/api/dashboard"))
        cie = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.cie)
        detalhe, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-05"))
        self.assertEqual(detalhe["aulasPrevistasAteHoje"], 1)
        self._login("admin", SENHA_DEMO_ADMIN)
        self.client.put(f"/api/anos-letivos/{self.ano_id}/dias", json={"data": "2026-02-03", "tipo": "LETIVO"})

    def test_15_sabado_reposicao_com_grade_conta(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        self.client.put(f"/api/anos-letivos/{self.ano_id}/dias", json={"data": "2026-02-07", "tipo": "REPOSICAO"})
        self._login(self.inep)
        self._alocar({"grade": [
            {"dia_semana": 1, "quantidade_aulas": 1},
            {"dia_semana": 3, "quantidade_aulas": 1},
            {"dia_semana": 5, "quantidade_aulas": 1},
        ]})
        dash, _ = _json(self.client.get("/api/dashboard"))
        cie = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.cie)
        detalhe, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-07"))
        self.assertEqual(detalhe["aulasPrevistasAteHoje"], 3)
        self._login("admin", SENHA_DEMO_ADMIN)
        self.client.put(f"/api/anos-letivos/{self.ano_id}/dias", json={"data": "2026-02-07", "tipo": "NAO_LETIVO"})

    def test_16_calculo_ate_determinada_data(self):
        self._login(self.inep)
        self._alocar()
        dash, _ = _json(self.client.get("/api/dashboard"))
        cie = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.cie)
        ate5, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-05"))
        ate12, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-12"))
        self.assertEqual(ate5["aulasPrevistasAteHoje"], 2)
        self.assertEqual(ate12["aulasPrevistasAteHoje"], 4)

    def _aula(self, data, qtd, status=None):
        url = f"/api/turmas/{self.turma_id}/disciplinas/{self.cie}/registros-aula"
        self._login(self.cpf_prof)
        resp = self.client.post(url, json={
            "data": data, "bimestre": 1, "quantidade_aulas": qtd,
            "conteudo": "Aula grade %s" % data, "metodologia": "Expositiva",
            "lancamentos": [],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aula_id = _json(resp)[0]["id"]
        if status:
            self._login(self.login_coord)
            body = {"status": status}
            if status == "invalidada":
                body["motivo"] = "Fora do plano."
            resp = self.client.post(f"/api/coordenacao/registros-aula/{aula_id}", json=body)
            self.assertEqual(resp.status_code, 200, _json(resp)[1])
        return aula_id

    def test_17_aula_validada_conta(self):
        self._login(self.inep)
        self._alocar()
        self._aula("2026-02-03", 1, "validada")
        self._login(self.inep)
        dash, _ = _json(self.client.get("/api/dashboard"))
        cie = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.cie)
        detalhe, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-12"))
        self.assertGreaterEqual(detalhe["aulasValidadas"], 1)

    def test_18_pendente_nao_conta(self):
        self._login(self.inep)
        self._alocar()
        dash, _ = _json(self.client.get("/api/dashboard"))
        cie = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.cie)
        antes, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-12"))
        self._aula("2026-02-04", 2)
        self._login(self.inep)
        depois, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-12"))
        self.assertEqual(depois["aulasValidadas"], antes["aulasValidadas"])
        self.assertGreaterEqual(depois["aulasPendentes"], 2)

    def test_19_invalidada_nao_conta(self):
        self._login(self.inep)
        self._alocar()
        dash, _ = _json(self.client.get("/api/dashboard"))
        cie = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.cie)
        antes, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-12"))
        self._aula("2026-02-05", 3, "invalidada")
        self._login(self.inep)
        depois, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-12"))
        self.assertEqual(depois["aulasValidadas"], antes["aulasValidadas"])
        self.assertGreaterEqual(depois["aulasInvalidadas"], 3)

    def test_20_percentual_correto(self):
        self._login(self.inep)
        self._alocar({"carga_horaria_prevista": 40})
        dash, _ = _json(self.client.get("/api/dashboard"))
        cie = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.cie)
        detalhe, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-12"))
        previstas = detalhe["aulasPrevistasAteHoje"]
        self.assertEqual(previstas, 4)
        self._aula("2026-02-10", 2, "validada")
        self._login(self.inep)
        depois, _ = _json(self.client.get(f"/api/alocacoes/{cie['id']}?data=2026-02-12"))
        self.assertEqual(depois["percentualCumprimentoAteHoje"], round(100.0 * depois["aulasValidadas"] / previstas, 1))
        self.assertIsNotNone(depois["progressoAnual"])

    def test_21_sem_carga_nao_calcula_anual(self):
        self._login(self.inep)
        resp = self.client.post("/api/alocacoes", json={
            "professor_id": self.prof_id,
            "turma_id": self.turma_id,
            "disciplina_id": self.mat,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash, _ = _json(self.client.get("/api/dashboard"))
        mat = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.mat)
        detalhe, _ = _json(self.client.get(f"/api/alocacoes/{mat['id']}"))
        self.assertTrue(detalhe["cargaHorariaNaoConfigurada"])
        self.assertIsNone(detalhe["progressoAnual"])
        self.assertTrue(detalhe["semPrevisaoAteData"])

    def test_22_coordenador_nao_altera_carga(self):
        self._login(self.inep)
        self._alocar()
        dash, _ = _json(self.client.get("/api/dashboard"))
        cie = next(a for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == self.cie)
        self._login(self.login_coord)
        resp = self.client.put(f"/api/alocacoes/{cie['id']}", json={"carga_horaria_prevista": 99})
        self.assertEqual(resp.status_code, 403)

    def test_23_alterar_calendario_nao_apaga_aula(self):
        self._login(self.inep)
        self._alocar()
        aula_id = self._aula("2026-02-12", 1, "validada")
        self._login("admin", SENHA_DEMO_ADMIN)
        self.client.put(f"/api/anos-letivos/{self.ano_id}/dias", json={"data": "2026-02-12", "tipo": "FERIADO"})
        self._login(self.cpf_prof)
        detalhe, erro = _json(self.client.get(f"/api/registros-aula/{aula_id}"))
        self.assertIsNone(erro)
        self.assertEqual(detalhe["id"], aula_id)
        self.assertEqual(detalhe["status"], "validada")
        self._login("admin", SENHA_DEMO_ADMIN)
        self.client.put(f"/api/anos-letivos/{self.ano_id}/dias", json={"data": "2026-02-12", "tipo": "LETIVO"})


if __name__ == "__main__":
    unittest.main()
