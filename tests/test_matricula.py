import os
import tempfile
import unittest
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class MatriculaVinculoTest(unittest.TestCase):
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
    def _proximo(cls):
        cls._seq += 1
        return cls._seq

    @classmethod
    def _login_client(cls, client, login, senha):
        resp = client.post("/api/auth/login", json={"login": login, "senha": senha})
        if resp.status_code != 200:
            raise RuntimeError("Falha no login: %s %s" % (login, _json(resp)[1]))

    @classmethod
    def _montar(cls):
        client = cls.app.test_client()
        cls._login_client(client, "admin", SENHA_DEMO_ADMIN)
        cls.inep_a = "81000001"
        cls.inep_b = "81000002"
        cls.cpf_prof_a = "81011111111"
        cls.login_coord_a = "coordMatA"
        temps = {}
        for nome, inep in (("Escola Matricula Norte", cls.inep_a), ("Escola Matricula Sul", cls.inep_b)):
            resp = client.post("/api/escolas", json={
                "nome": nome, "inep": inep,
                "oferta_infantil": True, "oferta_fundamental": True,
            })
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[inep] = senha_temporaria(resp)
        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_a = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_a)
        cls.escola_b = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_b)
        resp = client.post("/api/professores", json={
            "nome": "Prof Matricula Norte", "cpf": cls.cpf_prof_a, "escola_id": cls.escola_a,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_prof = senha_temporaria(resp)
        resp = client.post(f"/api/escolas/{cls.escola_a}/coordenadores", json={
            "login": cls.login_coord_a, "nome": "Coord Matricula Norte",
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_coord = senha_temporaria(resp)
        for inep, temp in temps.items():
            concluir_primeiro_acesso(client, inep, temp)
        concluir_primeiro_acesso(client, cls.cpf_prof_a, temp_prof)
        concluir_primeiro_acesso(client, cls.login_coord_a, temp_coord)

        cls._login_client(client, cls.inep_a, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano A", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_a = dash["turmas"][0]["id"]
        cls.disc_id = dash["disciplinas"][0]["id"]
        cls.prof_a_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_a)
        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_a_id, "turma_id": cls.turma_a, "disciplina_id": cls.disc_id,
        })

        cls._login_client(client, cls.inep_b, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano Sul", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_b = dash["turmas"][0]["id"]

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _cpf(self):
        n = self._proximo()
        return f"81{n:09d}"[:11]

    def _criar(self, nome, cpf, turma_id=None, mae="Mãe Matricula"):
        payload = {"nome": nome, "cpf": cpf, "nome_mae": mae}
        if turma_id is not None:
            payload["turma_id"] = turma_id
        return self.client.post("/api/alunos", json=payload)

    def test_01_cadastro_enturmado_cria_matricula(self):
        self._login(self.inep_a)
        cpf = self._cpf()
        resp = self._criar("Aluno Cadastro Enturmado", cpf, self.turma_a)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aluno_id = _json(resp)[0]["id"]
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula
            mats = Matricula.query.filter_by(aluno_id=aluno_id).all()
            self.assertEqual(len(mats), 1)
            self.assertEqual(mats[0].situacao, "cursando")
            self.assertEqual(mats[0].turma_id, self.turma_a)
            aluno = db.session.get(Aluno, aluno_id)
            self.assertEqual(aluno.turma_id, self.turma_a)

    def test_02_a_06_enturmar_cria_matricula_com_aluno_turma_ano_escola(self):
        self._login(self.inep_a)
        cpf = self._cpf()
        aluno_id = _json(self._criar("Aluno Enturmar Depois", cpf))[0]["id"]
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": self.turma_a})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula
            aluno = db.session.get(Aluno, aluno_id)
            mats = Matricula.query.filter_by(aluno_id=aluno_id).all()
            self.assertEqual(len(mats), 1)
            m = mats[0]
            self.assertEqual(m.aluno_id, aluno_id)
            self.assertEqual(m.turma_id, self.turma_a)
            self.assertEqual(m.situacao, "cursando")
            self.assertEqual(m.turma.ano_letivo, 2026)
            self.assertEqual(m.turma.escola_id, aluno.escola_id)
            self.assertEqual(aluno.turma_id, self.turma_a)

    def test_07_unique_aluno_turma_no_banco(self):
        self._login(self.inep_a)
        cpf = self._cpf()
        aluno_id = _json(self._criar("Aluno Unique Turma", cpf, self.turma_a))[0]["id"]
        with self.app.app_context():
            from src import db
            from src.models import Matricula
            from datetime import date
            db.session.add(Matricula(
                aluno_id=aluno_id, turma_id=self.turma_a,
                data_inicio=date.today(), situacao="cursando",
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_08_e_09_nao_duas_cursando_mesmo_com_atalho_nulo(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Extra Cursando", "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        outra = next(t["id"] for t in dash["turmas"] if t["nome"] == "Turma Extra Cursando")
        aluno_id = _json(self._criar("Aluno Duas Cursando", self._cpf(), self.turma_a))[0]["id"]
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": outra})
        self.assertEqual(resp.status_code, 409)
        with self.app.app_context():
            from src import db
            from src.models import Aluno
            aluno = db.session.get(Aluno, aluno_id)
            aluno.turma_id = None
            db.session.commit()
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": outra})
        self.assertEqual(resp.status_code, 409)
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula
            self.assertEqual(
                Matricula.query.filter_by(aluno_id=aluno_id, situacao="cursando").count(), 1,
            )
            aluno = db.session.get(Aluno, aluno_id)
            aluno.turma_id = self.turma_a
            db.session.commit()

    def test_10_turma_concluida_nao_aceita_matricula(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Ja Concluida", "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == "Turma Ja Concluida")
        self.assertEqual(self.client.post(f"/api/turmas/{turma_id}/encerrar").status_code, 200)
        aluno_id = _json(self._criar("Aluno Turma Fechada", self._cpf()))[0]["id"]
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": turma_id})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("ativa", (_json(resp)[1] or "").lower())
        resp = self._criar("Aluno Cadastro Fechada", self._cpf(), turma_id)
        self.assertEqual(resp.status_code, 400)

    def test_11_capacidade_considera_cursando(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Cap Mat", "ano_letivo": 2026, "etapa": "fundamental", "capacidade": 1,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == "Turma Cap Mat")
        self.assertEqual(self._criar("Ocupa Unica Mat", self._cpf(), turma_id).status_code, 200)
        aluno_id = _json(self._criar("Sem Vaga Mat", self._cpf()))[0]["id"]
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": turma_id})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("vaga", (_json(resp)[1] or "").lower())

    def test_12_a_15_encerramento_fecha_matricula_preserva_e_limpa_atalho(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Encerrar Mat", "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == "Turma Encerrar Mat")
        aluno_id = _json(self._criar("Aluno Encerrar Mat", self._cpf(), turma_id))[0]["id"]
        with self.app.app_context():
            from src.models import Matricula
            mid = Matricula.query.filter_by(aluno_id=aluno_id).first().id
        resp = self.client.post(f"/api/turmas/{turma_id}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula
            aluno = db.session.get(Aluno, aluno_id)
            m = db.session.get(Matricula, mid)
            self.assertEqual(m.situacao, "concluida")
            self.assertIsNotNone(m.data_fim)
            self.assertIsNone(aluno.turma_id)
            self.assertIsNotNone(db.session.get(Matricula, mid))

    def test_16_a_18_e_22_cenario_joao_2026_2027(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "5º Ano A Joao", "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2026 = next(t["id"] for t in dash["turmas"] if t["nome"] == "5º Ano A Joao")
        aluno_id = _json(self._criar("João", self._cpf(), turma_2026))[0]["id"]
        with self.app.app_context():
            from src.models import Matricula
            mats = Matricula.query.filter_by(aluno_id=aluno_id).all()
            self.assertEqual(len(mats), 1)
            self.assertEqual(mats[0].turma.nome, "5º Ano A Joao")
            self.assertEqual(mats[0].turma.ano_letivo, 2026)
            self.assertEqual(mats[0].situacao, "cursando")
            id_2026 = mats[0].id
        resp = self.client.post(f"/api/turmas/{turma_2026}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula
            m1 = db.session.get(Matricula, id_2026)
            self.assertEqual(m1.situacao, "concluida")
            self.assertIsNotNone(m1.data_fim)
            self.assertIsNone(db.session.get(Aluno, aluno_id).turma_id)
        resp = self.client.post("/api/turmas", json={
            "nome": "6º Ano A", "ano_letivo": 2027, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º Ano A")
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": turma_2027})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula
            mats = Matricula.query.filter_by(aluno_id=aluno_id).order_by(Matricula.id).all()
            self.assertEqual(len(mats), 2)
            m1 = db.session.get(Matricula, id_2026)
            self.assertEqual(m1.situacao, "concluida")
            self.assertEqual(m1.turma.ano_letivo, 2026)
            m2 = next(m for m in mats if m.id != id_2026)
            self.assertEqual(m2.situacao, "cursando")
            self.assertEqual(m2.turma.nome, "6º Ano A")
            self.assertEqual(m2.turma.ano_letivo, 2027)
            self.assertEqual(db.session.get(Aluno, aluno_id).turma_id, turma_2027)
        self._login(self.inep_a)
        hist = _json(self.client.get(f"/api/alunos/{aluno_id}/matriculas"))[0]
        anos = sorted(i["ano_letivo"] for i in hist)
        self.assertEqual(anos, [2026, 2027])
        por_ano = {i["ano_letivo"]: i for i in hist}
        self.assertEqual(por_ano[2026]["situacao"], "concluida")
        self.assertEqual(por_ano[2027]["situacao"], "cursando")
        self.assertEqual(por_ano[2026]["turma_nome"], "5º Ano A Joao")
        self.assertEqual(por_ano[2027]["turma_nome"], "6º Ano A")

    def test_19_20_inativar_fecha_vinculo_e_preserva(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("Aluno Inativar Mat", self._cpf(), self.turma_a))[0]["id"]
        with self.app.app_context():
            from src.models import Matricula
            mid = Matricula.query.filter_by(aluno_id=aluno_id).first().id
        resp = self.client.post(f"/api/alunos/{aluno_id}/inativar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula
            aluno = db.session.get(Aluno, aluno_id)
            m = db.session.get(Matricula, mid)
            self.assertEqual(aluno.situacao, "inativo")
            self.assertIsNone(aluno.turma_id)
            self.assertEqual(m.situacao, "inativo")
            self.assertIsNotNone(m.data_fim)

    def test_24_divergencia_atalho_nao_cria_segunda(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Divergente B", "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_b = next(t["id"] for t in dash["turmas"] if t["nome"] == "Turma Divergente B")
        aluno_id = _json(self._criar("Aluno Divergente", self._cpf(), self.turma_a))[0]["id"]
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula, ensure_schema
            from src.matriculas import diagnostico_consistencia_aluno
            aluno = db.session.get(Aluno, aluno_id)
            aluno.turma_id = turma_b
            db.session.commit()
            diags = diagnostico_consistencia_aluno(aluno)
            self.assertTrue(any(d["tipo"] == "divergencia_atalho_cursando" for d in diags))
            ensure_schema()
            self.assertEqual(Matricula.query.filter_by(aluno_id=aluno_id).count(), 1)
        resp = self.client.post(f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": turma_b})
        self.assertEqual(resp.status_code, 409)
        with self.app.app_context():
            from src.models import Matricula
            self.assertEqual(Matricula.query.filter_by(aluno_id=aluno_id).count(), 1)

    def test_25_exclusao_aluno_com_matricula_preserva(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("Aluno Excluir Mat", self._cpf(), self.turma_a))[0]["id"]
        resp = self.client.delete(f"/api/excluir/aluno/{aluno_id}")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula
            self.assertIsNotNone(db.session.get(Aluno, aluno_id))
            self.assertTrue(Matricula.query.filter_by(aluno_id=aluno_id).count())
            self.assertEqual(db.session.get(Aluno, aluno_id).situacao, "inativo")

    def test_26_exclusao_turma_com_matricula_historica_protegida(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Hist Prot", "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == "Turma Hist Prot")
        aluno_id = _json(self._criar("Aluno Turma Hist", self._cpf(), turma_id))[0]["id"]
        self.assertEqual(self.client.post(f"/api/turmas/{turma_id}/encerrar").status_code, 200)
        resp = self.client.delete(f"/api/excluir/turma/{turma_id}")
        self.assertEqual(resp.status_code, 409)
        with self.app.app_context():
            from src import db
            from src.models import Matricula, Turma
            self.assertIsNotNone(db.session.get(Turma, turma_id))
            self.assertTrue(Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).count())

    def test_27_endpoint_exige_autenticacao(self):
        self._login(self.inep_a)
        aluno_id = _json(self._criar("Aluno Auth Mat", self._cpf(), self.turma_a))[0]["id"]
        self.client.post("/api/auth/logout")
        resp = self.client.get(f"/api/alunos/{aluno_id}/matriculas")
        self.assertEqual(resp.status_code, 401)

    def test_28_endpoint_respeita_acl_escolas_e_professor(self):
        self._login(self.inep_a)
        aluno_a = _json(self._criar("Aluno ACL Mat A", self._cpf(), self.turma_a))[0]["id"]
        self._login(self.inep_b)
        aluno_b = _json(self._criar("Aluno ACL Mat B", self._cpf(), self.turma_b))[0]["id"]

        self._login(self.inep_a)
        resp = self.client.get(f"/api/alunos/{aluno_a}/matriculas")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self.client.get(f"/api/alunos/{aluno_b}/matriculas")
        self.assertEqual(resp.status_code, 403)

        self._login(self.login_coord_a)
        resp = self.client.get(f"/api/alunos/{aluno_a}/matriculas")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self.client.get(f"/api/alunos/{aluno_b}/matriculas")
        self.assertEqual(resp.status_code, 403)

        self._login(self.cpf_prof_a)
        resp = self.client.get(f"/api/alunos/{aluno_a}/matriculas")
        self.assertEqual(resp.status_code, 403)

        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.get(f"/api/alunos/{aluno_a}/matriculas")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_registros_nota_falta_passam_a_ter_matricula_id(self):
        with self.app.app_context():
            from src.models import HistoricoAnual, RegistroAula, RegistroFalta, RegistroFrequencia, RegistroNota, RelatorioDescritivo
            self.assertIn("matricula_id", {c.name for c in RegistroNota.__table__.columns})
            self.assertIn("matricula_id", {c.name for c in RegistroFalta.__table__.columns})
            self.assertIn("matricula_id", {c.name for c in RegistroFrequencia.__table__.columns})
            self.assertIn("aluno_id", {c.name for c in RegistroNota.__table__.columns})
            self.assertIn("aluno_id", {c.name for c in RegistroFalta.__table__.columns})
            self.assertIn("aluno_id", {c.name for c in RegistroFrequencia.__table__.columns})
            self.assertIn("turma_id", {c.name for c in RegistroFrequencia.__table__.columns})
            self.assertNotIn("matricula_id", {c.name for c in RegistroAula.__table__.columns})
            self.assertIn("matricula_id", {c.name for c in RelatorioDescritivo.__table__.columns})
            self.assertIn("aluno_id", {c.name for c in RelatorioDescritivo.__table__.columns})
            self.assertIn("matricula_id", {c.name for c in HistoricoAnual.__table__.columns})
            self.assertIn("aluno_id", {c.name for c in HistoricoAnual.__table__.columns})
            self.assertIn("ano_letivo", {c.name for c in HistoricoAnual.__table__.columns})


class MatriculaBackfillTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
        from src.config import Config
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + Path(self._db_path).resolve().as_posix()

    def tearDown(self):
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def _legado_sem_matricula(self, app, cpf="81900000001"):
        with app.app_context():
            from src import db
            from src.models import Aluno, Escola, Turma
            escola = Escola(
                nome="Escola Backfill", inep="81900001",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add(escola)
            db.session.flush()
            turma = Turma(
                nome="5º Ano Backfill", ano_letivo=2026, status="ativa",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            db.session.add(turma)
            db.session.flush()
            aluno = Aluno(
                nome="Aluno Legado Backfill", cpf=cpf, situacao="ativo",
                turma_id=turma.id, escola_id=escola.id, nome_mae="Mãe Legado",
            )
            db.session.add(aluno)
            db.session.commit()
            return aluno.id, turma.id, escola.id

    def test_21_23_backfill_cria_sem_duplicar(self):
        from src import create_app
        from src.models import Matricula, ensure_schema
        app = create_app()
        aluno_id, turma_id, _ = self._legado_sem_matricula(app)
        with app.app_context():
            self.assertEqual(Matricula.query.filter_by(aluno_id=aluno_id).count(), 0)
            ensure_schema()
            mats = Matricula.query.filter_by(aluno_id=aluno_id).all()
            self.assertEqual(len(mats), 1)
            self.assertEqual(mats[0].turma_id, turma_id)
            self.assertEqual(mats[0].situacao, "cursando")
            ensure_schema()
            self.assertEqual(Matricula.query.filter_by(aluno_id=aluno_id).count(), 1)

    def test_22_create_app_duas_vezes_e_idempotente(self):
        from src import create_app
        from src.models import Matricula
        app = create_app()
        aluno_id, turma_id, _ = self._legado_sem_matricula(app, cpf="81900000002")
        app2 = create_app()
        with app2.app_context():
            self.assertEqual(Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).count(), 1)
        app3 = create_app()
        with app3.app_context():
            self.assertEqual(Matricula.query.filter_by(aluno_id=aluno_id).count(), 1)

    def test_backfill_nao_inventa_quando_ja_existe_matricula(self):
        from src import create_app
        from src.models import Matricula, ensure_schema
        from datetime import date
        app = create_app()
        aluno_id, turma_id, _ = self._legado_sem_matricula(app, cpf="81900000003")
        with app.app_context():
            from src import db
            db.session.add(Matricula(
                aluno_id=aluno_id, turma_id=turma_id,
                data_inicio=date(2026, 2, 1), situacao="cursando",
            ))
            db.session.commit()
            ensure_schema()
            self.assertEqual(Matricula.query.filter_by(aluno_id=aluno_id).count(), 1)


if __name__ == "__main__":
    unittest.main()
