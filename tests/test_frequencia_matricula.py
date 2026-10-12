import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class FrequenciaPorMatriculaTest(unittest.TestCase):
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

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _proximo(self):
        FrequenciaPorMatriculaTest._seq += 1
        return FrequenciaPorMatriculaTest._seq

    def _rede_joao(self, com_maria=False):
        n = self._proximo()
        inep = f"7400{n:04d}"
        cpf_prof = f"7401{n:07d}"[:11]
        cpf_joao = f"7402{n:07d}"[:11]
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola Freq {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Freq", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        self.client.post("/api/turmas", json={
            "nome": "5º Ano A", "ano_letivo": 2026, "etapa": "fundamental",
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2026 = dash["turmas"][0]["id"]
        port_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Português")
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": "João", "cpf": cpf_joao, "turma_id": turma_2026, "nome_mae": "Mãe João",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aluno_id = _json(resp)[0]["id"]
        maria_id = None
        if com_maria:
            resp = self.client.post("/api/alunos", json={
                "nome": "Maria", "cpf": f"7403{n:07d}"[:11],
                "turma_id": turma_2026, "nome_mae": "Mãe Maria",
            })
            self.assertEqual(resp.status_code, 200, _json(resp)[1])
            maria_id = _json(resp)[0]["id"]
        self.client.post("/api/alocacoes", json={
            "professor_id": prof_id, "turma_id": turma_2026, "disciplina_id": port_id,
        })
        concluir_primeiro_acesso(self.client, cpf_prof, temp_prof)
        return {
            "inep": inep,
            "cpf_prof": cpf_prof,
            "turma_2026": turma_2026,
            "port_id": port_id,
            "aluno_id": aluno_id,
            "maria_id": maria_id,
            "prof_id": prof_id,
        }

    def _url_aula(self, turma_id, disc_id):
        return f"/api/turmas/{turma_id}/disciplinas/{disc_id}/registros-aula"

    def _payload_aula(self, data, lancamentos, bimestre=1):
        return {
            "data": data,
            "bimestre": bimestre,
            "quantidade_aulas": 1,
            "conteudo": "Conteúdo da aula",
            "metodologia": "Expositiva",
            "lancamentos": lancamentos,
        }

    def _registrar_aula(self, turma_id, disc_id, data, lancamentos):
        resp = self.client.post(
            self._url_aula(turma_id, disc_id),
            json=self._payload_aula(data, lancamentos),
        )
        return resp

    def _validar_aulas_da_turma(self, turma_id):
        with self.app.app_context():
            from src import db
            from src.models import RegistroAula
            for aula in RegistroAula.query.filter_by(turma_id=turma_id).all():
                aula.status = "validada"
            db.session.commit()

    def _abrir_2027(self, rede):
        self._login(rede["inep"])
        resp = self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self.client.post("/api/turmas", json={
            "nome": "6º Ano A", "ano_letivo": 2027, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º Ano A")
        resp = self.client.post(
            f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027, "disciplina_id": rede["port_id"],
        })
        return turma_2027

    def test_cenario_2026_2027_frequencia_pertence_a_matricula(self):
        rede = self._rede_joao()
        self._login(rede["cpf_prof"])
        resp = self._registrar_aula(
            rede["turma_2026"], rede["port_id"], "2026-03-10",
            [{"aluno_id": rede["aluno_id"], "situacao": "FALTA"}],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aula_2026_id = _json(resp)[0]["id"]

        with self.app.app_context():
            from src import db
            from src.models import Matricula, RegistroAula, RegistroFrequencia
            mat_a = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertIsNotNone(mat_a)
            freq_a = RegistroFrequencia.query.filter_by(
                aluno_id=rede["aluno_id"], data=date(2026, 3, 10),
            ).first()
            self.assertIsNotNone(freq_a)
            self.assertEqual(freq_a.matricula_id, mat_a.id)
            self.assertEqual(freq_a.aluno_id, mat_a.aluno_id)
            self.assertEqual(freq_a.turma_id, mat_a.turma_id)
            self.assertEqual(freq_a.situacao, "FALTA")
            aula = db.session.get(RegistroAula, aula_2026_id)
            self.assertIsNotNone(aula)
            self.assertNotIn("matricula_id", RegistroAula.__table__.c)
            self.assertEqual(aula.turma_id, rede["turma_2026"])
            id_mat_a = mat_a.id

        turma_2027 = self._abrir_2027(rede)
        self._login(rede["cpf_prof"])
        resp = self._registrar_aula(
            turma_2027, rede["port_id"], "2027-03-10",
            [{"aluno_id": rede["aluno_id"], "situacao": "PRESENTE"}],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            from src import db
            from src.models import Matricula, RegistroFrequencia
            mat_a = db.session.get(Matricula, id_mat_a)
            mat_b = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
            ).first()
            self.assertIsNotNone(mat_b)
            self.assertNotEqual(mat_a.id, mat_b.id)
            freq_a = RegistroFrequencia.query.filter_by(matricula_id=mat_a.id).all()
            freq_b = RegistroFrequencia.query.filter_by(matricula_id=mat_b.id).all()
            self.assertEqual(len(freq_a), 1)
            self.assertEqual(len(freq_b), 1)
            self.assertEqual(freq_a[0].data, date(2026, 3, 10))
            self.assertEqual(freq_a[0].situacao, "FALTA")
            self.assertEqual(freq_a[0].turma_id, rede["turma_2026"])
            self.assertEqual(freq_b[0].data, date(2027, 3, 10))
            self.assertEqual(freq_b[0].situacao, "PRESENTE")
            self.assertEqual(freq_b[0].turma_id, turma_2027)
            misturados = RegistroFrequencia.query.filter_by(
                matricula_id=mat_a.id, turma_id=turma_2027,
            ).count()
            self.assertEqual(misturados, 0)

    def test_faltas_por_bimestre_isoladas_por_matricula(self):
        rede = self._rede_joao()
        self._login(rede["cpf_prof"])
        for dia in ("2026-03-10", "2026-03-11"):
            resp = self._registrar_aula(
                rede["turma_2026"], rede["port_id"], dia,
                [{"aluno_id": rede["aluno_id"], "situacao": "FALTA"}],
            )
            self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._validar_aulas_da_turma(rede["turma_2026"])

        with self.app.app_context():
            from src.frequencia import faltas_por_bimestre
            from src.models import Matricula
            mat_a = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            por_2026 = faltas_por_bimestre(
                rede["aluno_id"], rede["port_id"], matricula_id=mat_a.id,
            )
            self.assertEqual(por_2026.get(1), 2)
            id_mat_a = mat_a.id

        turma_2027 = self._abrir_2027(rede)
        self._login(rede["cpf_prof"])
        resp = self._registrar_aula(
            turma_2027, rede["port_id"], "2027-03-10",
            [{"aluno_id": rede["aluno_id"], "situacao": "FALTA"}],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._validar_aulas_da_turma(turma_2027)

        with self.app.app_context():
            from src.frequencia import faltas_por_bimestre
            from src.models import Matricula
            mat_b = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
            ).first()
            por_2026 = faltas_por_bimestre(
                rede["aluno_id"], rede["port_id"], matricula_id=id_mat_a,
            )
            por_2027 = faltas_por_bimestre(
                rede["aluno_id"], rede["port_id"], matricula_id=mat_b.id,
            )
            self.assertEqual(por_2026.get(1), 2)
            self.assertEqual(por_2027.get(1), 1)
            id_mat_b = mat_b.id

        resp = self._registrar_aula(
            turma_2027, rede["port_id"], "2027-03-11",
            [{"aluno_id": rede["aluno_id"], "situacao": "FALTA"}],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._validar_aulas_da_turma(turma_2027)

        with self.app.app_context():
            from src.frequencia import faltas_por_bimestre
            por_2026 = faltas_por_bimestre(
                rede["aluno_id"], rede["port_id"], matricula_id=id_mat_a,
            )
            por_2027 = faltas_por_bimestre(
                rede["aluno_id"], rede["port_id"], matricula_id=id_mat_b,
            )
            self.assertEqual(por_2026.get(1), 2)
            self.assertEqual(por_2027.get(1), 2)

    def test_consistencia_recusa_aluno_ou_turma_divergentes(self):
        rede = self._rede_joao(com_maria=True)
        turma_2027 = self._abrir_2027(rede)
        with self.app.app_context():
            from src import db
            from src.frequencia import (
                MSG_FREQUENCIA_INCONSISTENTE,
                aplicar_frequencia,
                garantir_consistencia_frequencia,
            )
            from src.matriculas import MSG_ALUNO_MATRICULA_DIVERGENTE
            from src.models import Matricula, RegistroFrequencia
            mat_b = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
            ).first()
            n_antes = RegistroFrequencia.query.count()
            with self.assertRaises(ValueError) as ctx_aluno:
                garantir_consistencia_frequencia(
                    mat_b, rede["maria_id"], turma_2027,
                )
            self.assertEqual(str(ctx_aluno.exception), MSG_ALUNO_MATRICULA_DIVERGENTE)
            with self.assertRaises(ValueError) as ctx_turma:
                garantir_consistencia_frequencia(
                    mat_b, rede["aluno_id"], rede["turma_2026"],
                )
            self.assertEqual(str(ctx_turma.exception), MSG_FREQUENCIA_INCONSISTENTE)
            with self.assertRaises(ValueError):
                aplicar_frequencia(
                    mat_b, rede["port_id"], date(2027, 3, 10), 1, "PRESENTE",
                    aluno_id=rede["maria_id"],
                )
            with self.assertRaises(ValueError):
                aplicar_frequencia(
                    mat_b, rede["port_id"], date(2027, 3, 10), 1, "PRESENTE",
                    turma_id=rede["turma_2026"],
                )
            db.session.rollback()
            self.assertEqual(RegistroFrequencia.query.count(), n_antes)

    def test_rollback_aula_quando_aluno_sem_matricula(self):
        rede = self._rede_joao()
        n = self._proximo()
        self._login(rede["inep"])
        resp = self.client.post("/api/alunos", json={
            "nome": "Maria Sem Matricula",
            "cpf": f"7409{n:07d}"[:11],
            "nome_mae": "Mãe Maria",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        maria_id = _json(resp)[0]["id"]
        with self.app.app_context():
            from src import db
            from src.models import Aluno, RegistroAula, RegistroFrequencia
            maria = db.session.get(Aluno, maria_id)
            maria.turma_id = rede["turma_2026"]
            db.session.commit()
            n_aula = RegistroAula.query.count()
            n_freq = RegistroFrequencia.query.count()

        self._login(rede["cpf_prof"])
        resp = self._registrar_aula(
            rede["turma_2026"], rede["port_id"], "2026-03-10",
            [
                {"aluno_id": rede["aluno_id"], "situacao": "PRESENTE"},
                {"aluno_id": maria_id, "situacao": "FALTA"},
            ],
        )
        self.assertEqual(resp.status_code, 400, _json(resp)[1])
        _, error = _json(resp)
        self.assertTrue(error)

        with self.app.app_context():
            from src.models import RegistroAula, RegistroFrequencia
            self.assertEqual(RegistroAula.query.count(), n_aula)
            self.assertEqual(RegistroFrequencia.query.count(), n_freq)
            self.assertEqual(
                RegistroFrequencia.query.filter_by(aluno_id=rede["aluno_id"]).count(), 0,
            )
            self.assertEqual(
                RegistroFrequencia.query.filter_by(aluno_id=maria_id).count(), 0,
            )

    def test_cascade_matricula_nao_apaga_frequencia(self):
        with self.app.app_context():
            from sqlalchemy.orm import class_mapper
            from src.models import Matricula, RegistroAula, RegistroFrequencia
            rel = class_mapper(Matricula).relationships["frequencias"]
            self.assertFalse(rel.cascade.delete)
            self.assertFalse(rel.cascade.delete_orphan)
            self.assertIn("aluno_id", RegistroFrequencia.__table__.c)
            self.assertIn("turma_id", RegistroFrequencia.__table__.c)
            self.assertIn("matricula_id", RegistroFrequencia.__table__.c)
            self.assertNotIn("matricula_id", RegistroAula.__table__.c)

    def test_matricula_com_frequencia_e_protegida(self):
        rede = self._rede_joao()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._registrar_aula(
            rede["turma_2026"], rede["port_id"], "2026-03-10",
            [{"aluno_id": rede["aluno_id"], "situacao": "FALTA"}],
        ).status_code, 200)
        with self.app.app_context():
            from src.exclusao import matricula_possui_historico_academico
            from src.models import Matricula, RegistroFrequencia
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertTrue(matricula_possui_historico_academico(mat))
            self.assertTrue(RegistroFrequencia.query.filter_by(matricula_id=mat.id).count())

    def test_chamada_legado_grava_matricula_id(self):
        rede = self._rede_joao()
        self._login(rede["cpf_prof"])
        resp = self.client.post(
            f"/api/turmas/{rede['turma_2026']}/disciplinas/{rede['port_id']}/chamada",
            json={
                "data": "2026-03-12",
                "bimestre": 1,
                "lancamentos": [{"aluno_id": rede["aluno_id"], "situacao": "FALTA"}],
            },
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula, RegistroFrequencia
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            freq = RegistroFrequencia.query.filter_by(data=date(2026, 3, 12)).first()
            self.assertEqual(freq.matricula_id, mat.id)
            self.assertIsNone(freq.registro_aula_id)

    def test_faltas_nao_contam_a_mesma_linha_duas_vezes(self):
        rede = self._rede_joao()
        with self.app.app_context():
            from src import db
            from src.frequencia import faltas_por_bimestre
            from src.models import Matricula, RegistroFrequencia
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            db.session.add(RegistroFrequencia(
                aluno_id=mat.aluno_id,
                turma_id=mat.turma_id,
                matricula_id=mat.id,
                disciplina_id=rede["port_id"],
                data=date(2026, 3, 10),
                bimestre=1,
                situacao="FALTA",
            ))
            db.session.commit()
            self.assertEqual(RegistroFrequencia.query.filter_by(matricula_id=mat.id).count(), 1)
            por = faltas_por_bimestre(
                mat.aluno_id, rede["port_id"], matricula_id=mat.id,
            )
            self.assertEqual(por.get(1), 1)

    def test_fonte_diaria_nao_soma_registro_falta_do_mesmo_bimestre(self):
        rede = self._rede_joao()
        with self.app.app_context():
            from src import db
            from src.frequencia import faltas_por_bimestre
            from src.models import Matricula, RegistroFalta, RegistroFrequencia
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            db.session.add(RegistroFalta(
                aluno_id=mat.aluno_id,
                matricula_id=mat.id,
                disciplina_id=rede["port_id"],
                bimestre=1,
                quantidade=5,
            ))
            db.session.add(RegistroFrequencia(
                aluno_id=mat.aluno_id,
                turma_id=mat.turma_id,
                matricula_id=mat.id,
                disciplina_id=rede["port_id"],
                data=date(2026, 3, 10),
                bimestre=1,
                situacao="FALTA",
            ))
            db.session.commit()
            por = faltas_por_bimestre(
                mat.aluno_id, rede["port_id"], matricula_id=mat.id,
            )
            self.assertEqual(por.get(1), 1)
            self.assertNotEqual(por.get(1), 6)

    def test_turma_concluida_nao_recebe_nova_chamada_nem_aula(self):
        rede = self._rede_joao()
        self._login(rede["cpf_prof"])
        resp = self._registrar_aula(
            rede["turma_2026"], rede["port_id"], "2026-03-10",
            [{"aluno_id": rede["aluno_id"], "situacao": "FALTA"}],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula, RegistroAula, RegistroFrequencia
            n_aula = RegistroAula.query.count()
            n_freq = RegistroFrequencia.query.count()
            sit = RegistroFrequencia.query.first().situacao
            mat_id = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first().id

        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        self._login(rede["cpf_prof"])
        resp_aula = self._registrar_aula(
            rede["turma_2026"], rede["port_id"], "2026-03-11",
            [{"aluno_id": rede["aluno_id"], "situacao": "PRESENTE"}],
        )
        self.assertEqual(resp_aula.status_code, 403)
        resp_chamada = self.client.post(
            f"/api/turmas/{rede['turma_2026']}/disciplinas/{rede['port_id']}/chamada",
            json={
                "data": "2026-03-11",
                "bimestre": 1,
                "lancamentos": [{"aluno_id": rede["aluno_id"], "situacao": "PRESENTE"}],
            },
        )
        self.assertEqual(resp_chamada.status_code, 403)
        with self.app.app_context():
            from src.models import RegistroAula, RegistroFrequencia
            self.assertEqual(RegistroAula.query.count(), n_aula)
            self.assertEqual(RegistroFrequencia.query.count(), n_freq)
            freq = RegistroFrequencia.query.filter_by(matricula_id=mat_id).first()
            self.assertEqual(freq.situacao, sit)
            self.assertEqual(freq.data, date(2026, 3, 10))


class BackfillFrequenciaTest(unittest.TestCase):
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

    def test_backfill_associa_quando_uma_matricula_aluno_turma(self):
        from src import create_app, db
        from src.frequencia import (
            ULTIMO_RESUMO_BACKFILL_FREQUENCIA,
            auditar_duplicidades_frequencia,
            backfill_frequencias,
        )
        from src.models import Aluno, Disciplina, Escola, Matricula, RegistroFrequencia, Turma
        app = create_app()
        with app.app_context():
            disc = Disciplina.query.filter_by(nome="Português").first()
            escola = Escola(
                nome="Escola Backfill Freq", inep="74100001",
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
                nome="João Backfill", cpf="74100000001", situacao="ativo",
                turma_id=turma.id, escola_id=escola.id, nome_mae="Mãe Backfill",
            )
            db.session.add(aluno)
            db.session.flush()
            db.session.add(Matricula(
                aluno_id=aluno.id, turma_id=turma.id,
                data_inicio=date(2026, 2, 1), situacao="cursando",
            ))
            db.session.flush()
            db.session.add(RegistroFrequencia(
                aluno_id=aluno.id, turma_id=turma.id, disciplina_id=disc.id,
                data=date(2026, 3, 10), bimestre=1, situacao="FALTA",
            ))
            db.session.commit()
            n_antes = RegistroFrequencia.query.count()
            sit_antes = RegistroFrequencia.query.first().situacao
            data_antes = RegistroFrequencia.query.first().data
            disc_antes = RegistroFrequencia.query.first().disciplina_id
            backfill_frequencias()
            self.assertEqual(RegistroFrequencia.query.count(), n_antes)
            freq = RegistroFrequencia.query.filter_by(aluno_id=aluno.id).first()
            mat = Matricula.query.filter_by(aluno_id=aluno.id, turma_id=turma.id).first()
            self.assertEqual(freq.matricula_id, mat.id)
            self.assertEqual(freq.situacao, sit_antes)
            self.assertEqual(freq.data, data_antes)
            self.assertEqual(freq.disciplina_id, disc_antes)
            self.assertEqual(ULTIMO_RESUMO_BACKFILL_FREQUENCIA["associadas"], 1)
            backfill_frequencias()
            self.assertEqual(RegistroFrequencia.query.count(), n_antes)
            self.assertEqual(RegistroFrequencia.query.first().matricula_id, mat.id)
            self.assertEqual(ULTIMO_RESUMO_BACKFILL_FREQUENCIA["associadas"], 0)
            resumo = auditar_duplicidades_frequencia()
            self.assertFalse(resumo["indice_unico"])

    def test_backfill_sem_matricula_nao_atribui_a_atual(self):
        from src import create_app, db
        from src.frequencia import (
            ULTIMO_DIAGNOSTICO_BACKFILL_FREQUENCIA,
            backfill_frequencias,
        )
        from src.models import Aluno, Disciplina, Escola, Matricula, RegistroFrequencia, Turma
        app = create_app()
        with app.app_context():
            disc = Disciplina.query.filter_by(nome="Português").first()
            escola = Escola(
                nome="Escola Ambiguo Freq", inep="74100002",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add(escola)
            db.session.flush()
            t_2026 = Turma(
                nome="5º Ano A", ano_letivo=2026, status="concluida",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            t_2027 = Turma(
                nome="6º Ano A", ano_letivo=2027, status="ativa",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            db.session.add_all([t_2026, t_2027])
            db.session.flush()
            aluno = Aluno(
                nome="João Ambiguo", cpf="74100000002", situacao="ativo",
                turma_id=t_2027.id, escola_id=escola.id, nome_mae="Mãe Ambiguo",
            )
            db.session.add(aluno)
            db.session.flush()
            db.session.add(Matricula(
                aluno_id=aluno.id, turma_id=t_2027.id,
                data_inicio=date(2027, 2, 1), situacao="cursando",
            ))
            db.session.flush()
            db.session.add(RegistroFrequencia(
                aluno_id=aluno.id, turma_id=t_2026.id, disciplina_id=disc.id,
                data=date(2026, 3, 10), bimestre=1, situacao="FALTA",
            ))
            db.session.commit()
            n_antes = RegistroFrequencia.query.count()
            mat_atual = Matricula.query.filter_by(aluno_id=aluno.id, turma_id=t_2027.id).first()
            backfill_frequencias()
            freq = RegistroFrequencia.query.filter_by(aluno_id=aluno.id).first()
            self.assertIsNone(freq.matricula_id)
            self.assertNotEqual(freq.matricula_id, mat_atual.id)
            self.assertEqual(freq.situacao, "FALTA")
            self.assertEqual(freq.turma_id, t_2026.id)
            self.assertEqual(RegistroFrequencia.query.count(), n_antes)
            tipos = {d["tipo"] for d in ULTIMO_DIAGNOSTICO_BACKFILL_FREQUENCIA}
            self.assertIn("sem_matricula", tipos)
            self.assertTrue(any(
                d.get("motivo") == "zero_matriculas"
                for d in ULTIMO_DIAGNOSTICO_BACKFILL_FREQUENCIA
            ))


if __name__ == "__main__":
    unittest.main()
