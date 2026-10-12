import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class ProtecaoExclusaoTest(unittest.TestCase):
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
        return _json(resp)[0]

    def _proximo(self):
        ProtecaoExclusaoTest._seq += 1
        return ProtecaoExclusaoTest._seq

    def _rede(self, com_aluno=True, alocar=True):
        n = self._proximo()
        inep = f"74{n:06d}"
        cpf_prof = f"741{n:08d}"
        cpf_aluno = f"742{n:08d}"
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola Protecao {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Protecao", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        self._login(inep)
        self.client.post("/api/turmas", json={"nome": "5º Ano Prot", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = dash["turmas"][0]["id"]
        disc_id = dash["disciplinas"][0]["id"]
        prof_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cpf_prof)
        aluno_id = None
        if com_aluno:
            resp = self.client.post("/api/alunos", json={
                "nome": "Aluno Protecao", "cpf": cpf_aluno, "turma_id": turma_id, "nome_mae": "Mãe Protecao",
            })
            self.assertEqual(resp.status_code, 200, _json(resp)[1])
            aluno_id = _json(resp)[0]["id"]
        if alocar:
            resp = self.client.post("/api/alocacoes", json={
                "professor_id": prof_id, "turma_id": turma_id, "disciplina_id": disc_id,
            })
            self.assertEqual(resp.status_code, 200, _json(resp)[1])
        concluir_primeiro_acesso(self.client, cpf_prof, temp_prof)
        return {
            "inep": inep,
            "escola_id": escola_id,
            "turma_id": turma_id,
            "disc_id": disc_id,
            "prof_id": prof_id,
            "cpf_prof": cpf_prof,
            "aluno_id": aluno_id,
            "cpf_aluno": cpf_aluno,
        }

    def _lancar_nota(self, rede, valor="7.5", bimestre=11):
        self._login(rede["cpf_prof"])
        resp = self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=[{"aluno_id": rede["aluno_id"], "valor": valor, "tipo": "nota", "bimestre": bimestre}],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _lancar_falta(self, rede, quantidade=2, bimestre=1):
        self._login(rede["cpf_prof"])
        resp = self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=[{"aluno_id": rede["aluno_id"], "valor": quantidade, "tipo": "falta", "bimestre": bimestre}],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _registrar_aula(self, rede, data="2026-04-07"):
        self._login(rede["cpf_prof"])
        resp = self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/registros-aula",
            json={
                "data": data,
                "bimestre": 1,
                "quantidade_aulas": 1,
                "conteudo": "Conteúdo de proteção",
                "metodologia": "Expositiva",
                "lancamentos": [{"aluno_id": rede["aluno_id"], "situacao": "PRESENTE"}],
            },
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        return _json(resp)[0]["id"]

    def test_01_aluno_com_notas_nao_sofre_exclusao_fisica(self):
        rede = self._rede()
        self._lancar_nota(rede)
        self._login(rede["inep"])
        resp = self.client.delete(f"/api/excluir/aluno/{rede['aluno_id']}")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, RegistroNota
            aluno = db.session.get(Aluno, rede["aluno_id"])
            self.assertIsNotNone(aluno)
            self.assertEqual(aluno.situacao, "inativo")
            self.assertTrue(RegistroNota.query.filter_by(aluno_id=rede["aluno_id"]).count())

    def test_02_a_05_exclusao_inativacao_preserva_notas_faltas_frequencia_historico(self):
        rede = self._rede()
        self._lancar_nota(rede)
        self._lancar_falta(rede)
        self._registrar_aula(rede)
        self._login(rede["inep"])
        resp = self.client.post(f"/api/turmas/{rede['turma_id']}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            from src.models import HistoricoAnual, RegistroFalta, RegistroFrequencia, RegistroNota
            self.assertTrue(RegistroNota.query.filter_by(aluno_id=rede["aluno_id"]).count())
            self.assertTrue(RegistroFalta.query.filter_by(aluno_id=rede["aluno_id"]).count())
            self.assertTrue(RegistroFrequencia.query.filter_by(aluno_id=rede["aluno_id"]).count())
            self.assertTrue(HistoricoAnual.query.filter_by(aluno_id=rede["aluno_id"]).count())

        resp = self.client.delete(f"/api/excluir/aluno/{rede['aluno_id']}")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self.client.post(f"/api/alunos/{rede['aluno_id']}/inativar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            from src import db
            from src.models import Aluno, HistoricoAnual, RegistroFalta, RegistroFrequencia, RegistroNota
            aluno = db.session.get(Aluno, rede["aluno_id"])
            self.assertIsNotNone(aluno)
            self.assertEqual(aluno.cpf, rede["cpf_aluno"])
            self.assertEqual(aluno.situacao, "inativo")
            self.assertTrue(RegistroNota.query.filter_by(aluno_id=rede["aluno_id"]).count())
            self.assertTrue(RegistroFalta.query.filter_by(aluno_id=rede["aluno_id"]).count())
            self.assertTrue(RegistroFrequencia.query.filter_by(aluno_id=rede["aluno_id"]).count())
            self.assertTrue(HistoricoAnual.query.filter_by(aluno_id=rede["aluno_id"]).count())

    def test_06_turma_com_historico_nao_pode_ser_apagada(self):
        rede = self._rede()
        self._lancar_nota(rede)
        self._registrar_aula(rede)
        self._login(rede["inep"])
        resp = self.client.delete(f"/api/excluir/turma/{rede['turma_id']}")
        self.assertEqual(resp.status_code, 409)
        _, error = _json(resp)
        self.assertIn("histórico", (error or "").lower())
        with self.app.app_context():
            from src import db
            from src.models import RegistroAula, RegistroNota, Turma
            self.assertIsNotNone(db.session.get(Turma, rede["turma_id"]))
            self.assertTrue(RegistroNota.query.filter_by(aluno_id=rede["aluno_id"]).count())
            self.assertTrue(RegistroAula.query.filter_by(turma_id=rede["turma_id"]).count())

    def test_07_escola_com_vinculos_nao_pode_ser_apagada(self):
        rede = self._rede()
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.delete(f"/api/excluir/escola/{rede['escola_id']}")
        self.assertEqual(resp.status_code, 409)
        _, error = _json(resp)
        self.assertIn("vinculados", (error or "").lower())
        with self.app.app_context():
            from src import db
            from src.models import Escola
            self.assertIsNotNone(db.session.get(Escola, rede["escola_id"]))

    def test_08_professor_com_diario_nao_pode_ser_apagado(self):
        rede = self._rede()
        self._registrar_aula(rede)
        self._login(rede["inep"])
        resp = self.client.delete(f"/api/excluir/professor/{rede['prof_id']}")
        self.assertEqual(resp.status_code, 409)
        _, error = _json(resp)
        self.assertTrue(error)
        with self.app.app_context():
            from src import db
            from src.models import Professor, RegistroAula
            self.assertIsNotNone(db.session.get(Professor, rede["prof_id"]))
            self.assertTrue(RegistroAula.query.filter_by(professor_id=rede["prof_id"]).count())

    def test_09_api_direta_protege_mesmo_sem_frontend(self):
        rede = self._rede()
        self._lancar_nota(rede)
        self._login(rede["inep"])
        resp = self.client.delete(f"/api/excluir/aluno/{rede['aluno_id']}")
        self.assertEqual(resp.status_code, 200)
        with self.app.app_context():
            from src import db
            from src.models import Aluno, RegistroNota
            self.assertIsNotNone(db.session.get(Aluno, rede["aluno_id"]))
            self.assertTrue(RegistroNota.query.filter_by(aluno_id=rede["aluno_id"]).count())
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.delete(f"/api/excluir/escola/{rede['escola_id']}")
        self.assertEqual(resp.status_code, 409)

    def test_10_exclusao_bloqueada_retorna_409(self):
        rede = self._rede()
        self._lancar_nota(rede)
        self._login(rede["inep"])
        resp = self.client.delete(f"/api/excluir/turma/{rede['turma_id']}")
        self.assertEqual(resp.status_code, 409)
        data, error = _json(resp)
        self.assertIsNone(data)
        self.assertTrue(error)
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.delete(f"/api/excluir/escola/{rede['escola_id']}")
        self.assertEqual(resp.status_code, 409)

    def test_11_inativacao_de_aluno_continua_funcionando(self):
        rede = self._rede()
        self._login(rede["inep"])
        resp = self.client.post(f"/api/alunos/{rede['aluno_id']}/inativar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno
            aluno = db.session.get(Aluno, rede["aluno_id"])
            self.assertEqual(aluno.situacao, "inativo")
            self.assertIsNone(aluno.turma_id)

    def test_12_encerramento_de_turma_continua_funcionando(self):
        rede = self._rede()
        self._lancar_nota(rede)
        self._login(rede["inep"])
        resp = self.client.post(f"/api/turmas/{rede['turma_id']}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, HistoricoAnual, RegistroNota, Turma
            turma = db.session.get(Turma, rede["turma_id"])
            self.assertEqual(turma.status, "concluida")
            aluno = db.session.get(Aluno, rede["aluno_id"])
            self.assertIsNone(aluno.turma_id)
            self.assertTrue(RegistroNota.query.filter_by(aluno_id=rede["aluno_id"]).count())
            self.assertTrue(HistoricoAnual.query.filter_by(aluno_id=rede["aluno_id"]).count())
        resp = self.client.delete(f"/api/excluir/turma/{rede['turma_id']}")
        self.assertEqual(resp.status_code, 409)

    def test_13_professor_nao_transfere_aluno_pelo_lancamento_de_nota(self):
        rede = self._rede()
        self._lancar_nota(rede, valor="8")
        self._login(rede["cpf_prof"])
        resp = self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=[{"aluno_id": rede["aluno_id"], "valor": "T", "tipo": "status", "bimestre": 0}],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, RegistroNota
            aluno = db.session.get(Aluno, rede["aluno_id"])
            self.assertEqual(aluno.situacao, "ativo")
            self.assertNotEqual(aluno.situacao, "transferido")
            self.assertTrue(RegistroNota.query.filter_by(aluno_id=rede["aluno_id"]).count())

    def test_14_exclusoes_seguras_sem_vinculos_continuam(self):
        n = self._proximo()
        inep = f"75{n:06d}"
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola Vazia {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.delete(f"/api/excluir/escola/{escola_id}")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        rede = self._rede(com_aluno=False)
        self._login(rede["inep"])
        resp = self.client.delete(f"/api/excluir/turma/{rede['turma_id']}")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self.client.delete(f"/api/excluir/professor/{rede['prof_id']}")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        nome_disc = f"Disciplina Extra {n}"
        resp = self.client.post("/api/disciplinas", json={"nome": nome_disc})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        disc_extra = next(d["id"] for d in dash["disciplinas"] if d["nome"] == nome_disc)
        resp = self.client.delete(f"/api/excluir/disciplina/{disc_extra}")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_15_cascade_nao_destroi_registros_academicos(self):
        rede = self._rede()
        self._lancar_nota(rede)
        self._lancar_falta(rede)
        self._registrar_aula(rede)
        self._login(rede["inep"])
        resp = self.client.post(f"/api/alunos/{rede['aluno_id']}/inativar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            from src import db
            from src.models import Aluno, RegistroAula, RegistroFalta, RegistroFrequencia, RegistroNota
            nota_id = RegistroNota.query.filter_by(aluno_id=rede["aluno_id"]).first().id
            falta_id = RegistroFalta.query.filter_by(aluno_id=rede["aluno_id"]).first().id
            freq_id = RegistroFrequencia.query.filter_by(aluno_id=rede["aluno_id"]).first().id
            aula_id = RegistroAula.query.filter_by(professor_id=rede["prof_id"]).first().id
            aluno = db.session.get(Aluno, rede["aluno_id"])
            try:
                db.session.delete(aluno)
                db.session.commit()
            except Exception:
                db.session.rollback()
            self.assertIsNotNone(RegistroNota.query.filter_by(id=nota_id).first())
            self.assertIsNotNone(RegistroFalta.query.filter_by(id=falta_id).first())
            self.assertIsNotNone(RegistroFrequencia.query.filter_by(id=freq_id).first())
            self.assertIsNotNone(RegistroAula.query.filter_by(id=aula_id).first())


if __name__ == "__main__":
    unittest.main()
