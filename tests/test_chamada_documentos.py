import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class ChamadaDocumentosTest(unittest.TestCase):
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

        cls.inep_a = "92000001"
        cls.inep_b = "92000002"
        cls.cpf_prof_a = "92011111111"
        cls.cpf_prof_ei = "92022222222"
        cls.cpf_prof_b = "92033333333"

        temps = {}
        for nome, inep in (("Escola Chamada Norte", cls.inep_a), ("Escola Chamada Sul", cls.inep_b)):
            resp = client.post("/api/escolas", json={
                "nome": nome, "inep": inep, "oferta_infantil": True, "oferta_fundamental": True,
            })
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[inep] = senha_temporaria(resp)

        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_a = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_a)
        cls.escola_b = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_b)

        for nome, cpf, escola_id in (
            ("Prof Fund A", cls.cpf_prof_a, cls.escola_a),
            ("Prof EI A", cls.cpf_prof_ei, cls.escola_a),
            ("Prof Fund B", cls.cpf_prof_b, cls.escola_b),
        ):
            resp = client.post("/api/professores", json={"nome": nome, "cpf": cpf, "escola_id": escola_id})
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[cpf] = senha_temporaria(resp)
        for login, temp in temps.items():
            concluir_primeiro_acesso(client, login, temp)

        cls._login_client(client, cls.inep_a, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "6º Ano Norte", "ano_letivo": 2026, "etapa": "fundamental"})
        client.post("/api/turmas", json={"nome": "Maternal Norte", "ano_letivo": 2026, "etapa": "infantil"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_a = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º Ano Norte")
        cls.turma_ei = next(t["id"] for t in dash["turmas"] if t["nome"] == "Maternal Norte")
        cls.prof_a_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_a)
        cls.prof_ei_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_ei)

        resp = client.post("/api/alunos", json={
            "nome": "Aluno Chamada Um", "cpf": "92044444444",
            "turma_id": cls.turma_a, "nome_mae": "Mãe Um",
        })
        cls.aluno_a = _json(resp)[0]["id"]
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Chamada Dois", "cpf": "92055555555",
            "turma_id": cls.turma_a, "nome_mae": "Mãe Dois",
        })
        cls.aluno_a2 = _json(resp)[0]["id"]
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Chamada Tres", "cpf": "92066666666",
            "turma_id": cls.turma_a, "nome_mae": "Mãe Tres",
        })
        cls.aluno_a3 = _json(resp)[0]["id"]
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Inativo Chamada", "cpf": "92077777777",
            "turma_id": cls.turma_a, "nome_mae": "Mãe Inativo",
        })
        cls.aluno_inativo = _json(resp)[0]["id"]
        resp = client.post("/api/alunos", json={
            "nome": "Aluno EI Norte", "cpf": "92088888888",
            "turma_id": cls.turma_ei, "nome_mae": "Mãe EI",
        })
        cls.aluno_ei = _json(resp)[0]["id"]

        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_a_id, "turma_id": cls.turma_a, "disciplina_id": cls.mat,
        })
        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_ei_id, "turma_id": cls.turma_ei, "disciplina_id": cls.mat,
        })

        from src.models import Aluno
        from src import db
        with cls.app.app_context():
            inativo = db.session.get(Aluno, cls.aluno_inativo)
            inativo.situacao = "inativo"
            db.session.commit()

        cls._login_client(client, cls.inep_b, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "6º Ano Sul", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_b = dash["turmas"][0]["id"]
        cls.prof_b_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_b)
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Chamada Sul", "cpf": "92099999999",
            "turma_id": cls.turma_b, "nome_mae": "Mãe Sul",
        })
        cls.aluno_b = _json(resp)[0]["id"]
        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_b_id, "turma_id": cls.turma_b, "disciplina_id": cls.mat,
        })

        cls.data_aula = "2026-03-10"
        cls.bimestre = 1

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _assert_403(self, resp, ctx=""):
        self.assertEqual(resp.status_code, 403, ctx or _json(resp)[1])
        data, error = _json(resp)
        self.assertIsNone(data)
        self.assertTrue(error)

    def _url(self, turma_id, disc_id=None):
        return f"/api/turmas/{turma_id}/disciplinas/{disc_id or self.mat}/chamada"

    def _get(self, turma_id, disc_id=None, data=None, bimestre=None):
        data = data or self.data_aula
        bimestre = bimestre or self.bimestre
        return self.client.get(f"{self._url(turma_id, disc_id)}?data={data}&bimestre={bimestre}")

    def _payload(self, situacoes, data=None, bimestre=None):
        return {
            "data": data or self.data_aula,
            "bimestre": bimestre or self.bimestre,
            "lancamentos": [{"aluno_id": aid, "situacao": sit} for aid, sit in situacoes],
        }

    def test_01_professor_abre_chamada_da_propria_alocacao(self):
        self._login(self.cpf_prof_a)
        resp = self._get(self.turma_a)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        ids = [a["id"] for a in data["alunos"]]
        self.assertIn(self.aluno_a, ids)
        self.assertIn(self.aluno_a2, ids)
        self.assertTrue(data["podeEditar"])
        self.assertEqual(data["turmaId"], self.turma_a)

    def test_02_professor_nao_abre_chamada_de_outra_turma(self):
        self._login(self.cpf_prof_a)
        resp = self._get(self.turma_b)
        self._assert_403(resp)
        resp = self._get(self.turma_ei)
        self._assert_403(resp)

    def test_03_professor_salva_chamada(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload([
            (self.aluno_a, "PRESENTE"),
            (self.aluno_a2, "PRESENTE"),
            (self.aluno_a3, "PRESENTE"),
        ]))
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        self.assertTrue(data["salva"])
        self.assertEqual(data["presentes"], 3)
        self.assertEqual(data["faltosos"], 0)

    def test_04_chamada_pode_ser_reaberta(self):
        self._login(self.cpf_prof_a)
        self.client.post(self._url(self.turma_a), json=self._payload([
            (self.aluno_a, "FALTA"),
            (self.aluno_a2, "PRESENTE"),
            (self.aluno_a3, "PRESENTE"),
        ], data="2026-03-11"))
        resp = self._get(self.turma_a, data="2026-03-11")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        self.assertTrue(data["salva"])
        mapa = {a["id"]: a["situacao"] for a in data["alunos"]}
        self.assertEqual(mapa[self.aluno_a], "FALTA")
        self.assertEqual(mapa[self.aluno_a2], "PRESENTE")

    def test_05_correcao_de_chamada_funciona(self):
        self._login(self.cpf_prof_a)
        self.client.post(self._url(self.turma_a), json=self._payload([
            (self.aluno_a, "FALTA"),
            (self.aluno_a2, "PRESENTE"),
            (self.aluno_a3, "PRESENTE"),
        ], data="2026-03-12"))
        resp = self.client.post(self._url(self.turma_a), json=self._payload([
            (self.aluno_a, "PRESENTE"),
            (self.aluno_a2, "PRESENTE"),
            (self.aluno_a3, "PRESENTE"),
        ], data="2026-03-12"))
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(self._get(self.turma_a, data="2026-03-12"))
        mapa = {a["id"]: a["situacao"] for a in data["alunos"]}
        self.assertEqual(mapa[self.aluno_a], "PRESENTE")
        self.assertEqual(data["faltosos"], 0)

    def test_06_presentes_e_faltosos_sao_contabilizados(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload([
            (self.aluno_a, "FALTA"),
            (self.aluno_a2, "FALTA"),
            (self.aluno_a3, "PRESENTE"),
        ], data="2026-03-13"))
        data, _ = _json(resp)
        self.assertEqual(data["presentes"], 1)
        self.assertEqual(data["faltosos"], 2)
        mon, _ = _json(self.client.get(f"/api/monitoramento/turma/{self.turma_a}/disciplina/{self.mat}"))
        aluno = next(x for x in mon if x["id"] == self.aluno_a)
        self.assertGreaterEqual(aluno["resumo"]["faltas"], 1)

    def test_07_aluno_inativo_nao_entra_na_chamada(self):
        self._login(self.cpf_prof_a)
        data, _ = _json(self._get(self.turma_a))
        ids = [a["id"] for a in data["alunos"]]
        self.assertNotIn(self.aluno_inativo, ids)
        resp = self.client.post(self._url(self.turma_a), json=self._payload([
            (self.aluno_inativo, "FALTA"),
        ], data="2026-03-14"))
        self.assertEqual(resp.status_code, 400)

    def test_08_chamada_nao_apaga_faltas_antigas(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(
            f"/api/turmas/{self.turma_a}/disciplinas/{self.mat}/notas",
            json=[
                {"aluno_id": self.aluno_a, "valor": "4", "tipo": "falta", "bimestre": 2},
                {"aluno_id": self.aluno_a, "valor": "1", "tipo": "falta", "bimestre": 3},
            ],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        from src.models import RegistroFalta
        with self.app.app_context():
            antes = [
                (f.bimestre, f.quantidade)
                for f in RegistroFalta.query.filter_by(aluno_id=self.aluno_a, disciplina_id=self.mat).all()
            ]
        self.assertTrue(antes)
        resp = self.client.post(self._url(self.turma_a), json=self._payload([
            (self.aluno_a, "FALTA"),
            (self.aluno_a2, "PRESENTE"),
            (self.aluno_a3, "PRESENTE"),
        ], data="2026-03-15"))
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            depois = [
                (f.bimestre, f.quantidade)
                for f in RegistroFalta.query.filter_by(aluno_id=self.aluno_a, disciplina_id=self.mat).all()
            ]
        self.assertEqual(sorted(antes), sorted(depois))

    def test_09_professor_nao_emite_boletim(self):
        self._login(self.cpf_prof_a)
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/boletim.pdf"))

    def test_10_professor_nao_emite_declaracao(self):
        self._login(self.cpf_prof_a)
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/declaracao/cursando.pdf"))
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/declaracao/conclusao.pdf"))

    def test_11_professor_nao_emite_ficha_comprovante_matricula(self):
        self._login(self.cpf_prof_a)
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/comprovante-matricula.pdf"))

    def test_12_professor_emite_ficha_rendimento_da_propria_turma(self):
        self._login(self.cpf_prof_a)
        resp = self.client.get(f"/api/turmas/{self.turma_a}/documentos/rendimento/{self.mat}.pdf")
        self.assertEqual(resp.status_code, 200)

    def test_13_professor_nao_emite_rendimento_de_outra_turma(self):
        self._login(self.cpf_prof_a)
        self._assert_403(self.client.get(f"/api/turmas/{self.turma_b}/documentos/rendimento/{self.mat}.pdf"))
        self._assert_403(self.client.get(f"/api/turmas/{self.turma_a}/documentos/rendimento/{self.port}.pdf"))

    def test_14_professor_ei_acessa_parecer_da_propria_turma(self):
        self._login(self.cpf_prof_ei)
        resp = self.client.get(f"/api/alunos/{self.aluno_ei}/documentos/parecer.pdf")
        self.assertEqual(resp.status_code, 200)
        resp = self._get(self.turma_ei, disc_id=0)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_15_professor_nao_acessa_parecer_de_aluno_de_outra_turma(self):
        self._login(self.cpf_prof_a)
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_ei}/documentos/parecer.pdf"))
        self._login(self.cpf_prof_ei)
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/parecer.pdf"))

    def test_16_gestor_emite_boletim_da_propria_escola(self):
        self._login(self.inep_a)
        resp = self.client.get(f"/api/alunos/{self.aluno_a}/documentos/boletim.pdf")
        self.assertEqual(resp.status_code, 200)

    def test_17_gestor_emite_declaracao_da_propria_escola(self):
        self._login(self.inep_a)
        self.assertEqual(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/declaracao/cursando.pdf").status_code, 200)
        self.assertEqual(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/comprovante-matricula.pdf").status_code, 200)

    def test_18_gestor_nao_emite_documento_de_outra_escola(self):
        self._login(self.inep_a)
        for url in (
            f"/api/alunos/{self.aluno_b}/documentos/boletim.pdf",
            f"/api/alunos/{self.aluno_b}/documentos/declaracao/cursando.pdf",
            f"/api/alunos/{self.aluno_b}/documentos/comprovante-matricula.pdf",
            f"/api/alunos/{self.aluno_b}/documentos/parecer.pdf",
        ):
            self._assert_403(self.client.get(url), url)

    def test_19_manipulacao_direta_de_url_continua_protegida(self):
        self._login(self.cpf_prof_a)
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/boletim.pdf"))
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_b}/documentos/declaracao/cursando.pdf"))
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_b}/documentos/comprovante-matricula.pdf"))
        self._assert_403(self._get(self.turma_b))
        self._assert_403(self.client.get(f"/api/turmas/{self.turma_b}/documentos/rendimento/{self.mat}.pdf"))
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_ei}/documentos/parecer.pdf"))

    def test_20_app_inicia_com_banco_existente(self):
        from src import create_app, db
        from src.models import RegistroFalta, RegistroFrequencia, ensure_schema
        with self.app.app_context():
            n_faltas = RegistroFalta.query.count()
            n_freq = RegistroFrequencia.query.count()
            db.create_all()
            ensure_schema()
            self.assertEqual(RegistroFalta.query.count(), n_faltas)
            self.assertEqual(RegistroFrequencia.query.count(), n_freq)
        app2 = create_app()
        self.assertIsNotNone(app2)
        c = app2.test_client()
        resp = c.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
