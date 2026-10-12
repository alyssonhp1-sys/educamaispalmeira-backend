import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class CalendarioLetivoTest(unittest.TestCase):
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
        cls.inep = "94000001"
        cls.cpf_prof = "94011111111"
        cls.login_coord = "coord940"
        resp = client.post("/api/escolas", json={
            "nome": "Escola Calendário 940", "inep": cls.inep,
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep)
        resp = client.post(f"/api/escolas/{cls.escola_id}/coordenadores", json={"login": cls.login_coord, "nome": "Coord Cal"})
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_coord = senha_temporaria(resp)
        resp = client.post("/api/professores", json={"nome": "Prof Cal", "cpf": cls.cpf_prof, "escola_id": cls.escola_id})
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(client, cls.inep, temp_gestor)
        concluir_primeiro_acesso(client, cls.login_coord, temp_coord)
        concluir_primeiro_acesso(client, cls.cpf_prof, temp_prof)

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _criar_ano(self, ano=2026, inicio="2026-02-02", fim="2026-12-18", ativo=True, periodos=None):
        payload = {
            "ano": ano, "data_inicio": inicio, "data_fim": fim, "ativo": ativo,
        }
        if periodos is not None:
            payload["periodos"] = periodos
        return self.client.post("/api/anos-letivos", json=payload)

    def _periodos_padrao(self):
        return [
            {"numero": 1, "nome": "1º Bimestre", "data_inicio": "2026-02-02", "data_fim": "2026-04-30"},
            {"numero": 2, "nome": "2º Bimestre", "data_inicio": "2026-05-01", "data_fim": "2026-06-30"},
            {"numero": 3, "nome": "3º Bimestre", "data_inicio": "2026-08-01", "data_fim": "2026-09-30"},
            {"numero": 4, "nome": "4º Bimestre", "data_inicio": "2026-10-01", "data_fim": "2026-12-18"},
        ]

    def test_01_criar_ano_letivo(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2030, inicio="2030-02-03", fim="2030-12-18")
        data, erro = _json(resp)
        self.assertEqual(resp.status_code, 200, erro)
        self.assertEqual(data["ano"], 2030)
        self.assertTrue(data["ativo"])
        self.assertEqual(data["dataInicio"], "2030-02-03")
        self.assertEqual(data["dataFim"], "2030-12-18")

    def test_02_impedir_datas_invertidas(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2031, inicio="2031-12-01", fim="2031-02-01")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("fim", (_json(resp)[1] or "").lower())

    def test_03_criar_bimestres(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2026, periodos=self._periodos_padrao())
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.ano_id = _json(resp)[0]["id"]
        detalhe, _ = _json(self.client.get(f"/api/anos-letivos/{self.ano_id}"))
        self.assertEqual(len(detalhe["periodos"]), 4)
        self.assertEqual(detalhe["periodos"][0]["nome"], "1º Bimestre")

    def test_04_impedir_periodos_sobrepostos(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2027, inicio="2027-02-01", fim="2027-12-15")
        ano_id = _json(resp)[0]["id"]
        resp = self.client.put(f"/api/anos-letivos/{ano_id}/periodos", json={
            "periodos": [
                {"numero": 1, "nome": "1º Bimestre", "data_inicio": "2027-02-01", "data_fim": "2027-05-15"},
                {"numero": 2, "nome": "2º Bimestre", "data_inicio": "2027-05-01", "data_fim": "2027-07-01"},
                {"numero": 3, "nome": "3º Bimestre", "data_inicio": "2027-08-01", "data_fim": "2027-09-30"},
                {"numero": 4, "nome": "4º Bimestre", "data_inicio": "2027-10-01", "data_fim": "2027-12-15"},
            ]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("sobrepost", (_json(resp)[1] or "").lower())

    def test_05_gerar_calendario_inicial(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2028, inicio="2028-02-07", fim="2028-02-13")
        ano_id = _json(resp)[0]["id"]
        resp = self.client.post(f"/api/anos-letivos/{ano_id}/gerar-calendario")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dias, _ = _json(self.client.get(f"/api/anos-letivos/{ano_id}/dias"))
        tipos = {d["data"]: d["tipo"] for d in dias["dias"]}
        self.assertEqual(tipos["2028-02-07"], "LETIVO")
        self.assertEqual(tipos["2028-02-11"], "LETIVO")
        self.assertEqual(tipos["2028-02-12"], "NAO_LETIVO")
        self.assertEqual(tipos["2028-02-13"], "NAO_LETIVO")
        self.assertEqual(dias["diasLetivosPrevistos"], 5)

    def test_06_feriado_nao_conta(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        from src import db
        from src.calendario import dia_conta_como_letivo, indice_dias
        from src.models import AnoLetivo
        from datetime import date
        resp = self._criar_ano(ano=2029, inicio="2029-02-05", fim="2029-02-09")
        ano_id = _json(resp)[0]["id"]
        self.client.post(f"/api/anos-letivos/{ano_id}/gerar-calendario")
        self.client.put(f"/api/anos-letivos/{ano_id}/dias", json={"data": "2029-02-06", "tipo": "FERIADO"})
        with self.app.app_context():
            ano = db.session.get(AnoLetivo, ano_id)
            indice = indice_dias(ano)
            self.assertFalse(dia_conta_como_letivo(indice[date(2029, 2, 6)]))
            self.assertTrue(dia_conta_como_letivo(indice[date(2029, 2, 5)]))
            self.assertEqual(ano.dias_letivos_previstos, 4)

    def test_07_recesso_nao_conta(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2032, inicio="2032-02-02", fim="2032-02-06")
        ano_id = _json(resp)[0]["id"]
        self.client.post(f"/api/anos-letivos/{ano_id}/gerar-calendario")
        self.client.put(f"/api/anos-letivos/{ano_id}/dias", json={"data": "2032-02-03", "tipo": "RECESSO"})
        detalhe, _ = _json(self.client.get(f"/api/anos-letivos/{ano_id}"))
        self.assertEqual(detalhe["diasLetivosPrevistos"], 4)
        dias, _ = _json(self.client.get(f"/api/anos-letivos/{ano_id}/dias"))
        tipo = next(d["tipo"] for d in dias["dias"] if d["data"] == "2032-02-03")
        self.assertEqual(tipo, "RECESSO")
        self.assertFalse(next(d["contaComoLetivo"] for d in dias["dias"] if d["data"] == "2032-02-03"))

    def test_08_sabado_letivo_conta(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2033, inicio="2033-02-07", fim="2033-02-12")
        ano_id = _json(resp)[0]["id"]
        self.client.post(f"/api/anos-letivos/{ano_id}/gerar-calendario")
        self.client.put(f"/api/anos-letivos/{ano_id}/dias", json={"data": "2033-02-12", "tipo": "LETIVO"})
        detalhe, _ = _json(self.client.get(f"/api/anos-letivos/{ano_id}"))
        self.assertEqual(detalhe["diasLetivosPrevistos"], 6)

    def test_09_reposicao_conta(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2034, inicio="2034-02-06", fim="2034-02-11")
        ano_id = _json(resp)[0]["id"]
        self.client.post(f"/api/anos-letivos/{ano_id}/gerar-calendario")
        antes, _ = _json(self.client.get(f"/api/anos-letivos/{ano_id}"))
        self.client.put(f"/api/anos-letivos/{ano_id}/dias", json={"data": "2034-02-11", "tipo": "REPOSICAO"})
        depois, _ = _json(self.client.get(f"/api/anos-letivos/{ano_id}"))
        self.assertEqual(depois["diasLetivosPrevistos"], antes["diasLetivosPrevistos"] + 1)

    def test_10_detectar_bimestre_por_data(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2035, inicio="2035-02-01", fim="2035-12-15", periodos=[
            {"numero": 1, "nome": "1º Bimestre", "data_inicio": "2035-02-01", "data_fim": "2035-04-30"},
            {"numero": 2, "nome": "2º Bimestre", "data_inicio": "2035-05-01", "data_fim": "2035-06-30"},
            {"numero": 3, "nome": "3º Bimestre", "data_inicio": "2035-08-01", "data_fim": "2035-09-30"},
            {"numero": 4, "nome": "4º Bimestre", "data_inicio": "2035-10-01", "data_fim": "2035-12-15"},
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, erro = _json(self.client.get("/api/calendario/periodo?data=2035-05-10"))
        self.assertIsNone(erro)
        self.assertEqual(data["periodo"]["numero"], 2)
        fora, _ = _json(self.client.get("/api/calendario/periodo?data=2035-07-15"))
        self.assertIsNone(fora["periodo"])
        self.assertEqual(fora["motivo"], "fora_de_periodo")

    def test_11_acl_somente_secretaria_altera(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2036, inicio="2036-02-04", fim="2036-02-28")
        ano_id = _json(resp)[0]["id"]
        self._login(self.inep)
        resp = self.client.put(f"/api/anos-letivos/{ano_id}/dias", json={"data": "2036-02-04", "tipo": "FERIADO"})
        self.assertEqual(resp.status_code, 403)
        self._login(self.login_coord)
        resp = self.client.post(f"/api/anos-letivos/{ano_id}/gerar-calendario")
        self.assertEqual(resp.status_code, 403)
        self._login(self.cpf_prof)
        resp = self.client.post("/api/anos-letivos", json={
            "ano": 2037, "data_inicio": "2037-02-01", "data_fim": "2037-12-01",
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get("/api/anos-letivos")
        self.assertEqual(resp.status_code, 200)

    def test_12_periodo_fora_do_ano_rejeitado(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._criar_ano(ano=2038, inicio="2038-03-01", fim="2038-11-30")
        ano_id = _json(resp)[0]["id"]
        resp = self.client.put(f"/api/anos-letivos/{ano_id}/periodos", json={
            "periodos": [
                {"numero": 1, "nome": "1º Bimestre", "data_inicio": "2038-02-01", "data_fim": "2038-04-30"},
            ]
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("fora", (_json(resp)[1] or "").lower())


if __name__ == "__main__":
    unittest.main()
