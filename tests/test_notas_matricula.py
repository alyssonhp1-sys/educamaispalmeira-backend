import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class NotasPorMatriculaTest(unittest.TestCase):
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
        NotasPorMatriculaTest._seq += 1
        return NotasPorMatriculaTest._seq

    def _rede_joao(self):
        n = self._proximo()
        inep = f"7300{n:04d}"
        cpf_prof = f"7301{n:07d}"[:11]
        cpf_joao = f"7302{n:07d}"[:11]
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola João {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof João", "cpf": cpf_prof, "escola_id": escola_id,
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
            "prof_id": prof_id,
        }

    def _nota(self, aluno_id, valor, bimestre=11, tipo="nota"):
        return {"aluno_id": aluno_id, "valor": valor, "tipo": tipo, "bimestre": bimestre}

    def _salvar(self, turma_id, disc_id, payload):
        return self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/{disc_id}/notas",
            json=payload,
        )

    def _mon(self, turma_id, disc_id, aluno_id):
        dados, err = _json(self.client.get(
            f"/api/monitoramento/turma/{turma_id}/disciplina/{disc_id}"
        ))
        self.assertIsNone(err)
        return next(a for a in dados if a["id"] == aluno_id)

    def test_cenario_2026_2027_notas_faltas_e_leitura(self):
        rede = self._rede_joao()
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "8.0", 11),
            self._nota(rede["aluno_id"], "4", 1, tipo="falta"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            from src import db
            from src.models import Matricula, RegistroFrequencia, RegistroNota, RegistroFalta
            mat_a = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertIsNotNone(mat_a)
            n1_a = RegistroNota.query.filter_by(
                matricula_id=mat_a.id, disciplina_id=rede["port_id"], bimestre=11,
            ).first()
            self.assertEqual(n1_a.valor, 8.0)
            self.assertEqual(n1_a.aluno_id, mat_a.aluno_id)
            db.session.add(RegistroFrequencia(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
                disciplina_id=rede["port_id"], data=date(2026, 3, 10),
                bimestre=1, situacao="FALTA",
            ))
            db.session.commit()
            id_mat_a = mat_a.id

        self._login(rede["inep"])
        resp = self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        resp = self.client.post("/api/turmas", json={
            "nome": "6º Ano A", "ano_letivo": 2027, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º Ano A")
        resp = self.client.post(f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027, "disciplina_id": rede["port_id"],
        })

        self._login(rede["cpf_prof"])
        resp = self._salvar(turma_2027, rede["port_id"], [
            self._nota(rede["aluno_id"], "6.5", 11),
            self._nota(rede["aluno_id"], "2", 1, tipo="falta"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            from src import db
            from src.models import Matricula, RegistroFrequencia, RegistroNota, RegistroFalta
            from src.frequencia import faltas_por_bimestre
            mat_a = db.session.get(Matricula, id_mat_a)
            mat_b = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
            ).first()
            self.assertIsNotNone(mat_b)
            self.assertNotEqual(mat_a.id, mat_b.id)
            n1_a = RegistroNota.query.filter_by(
                matricula_id=mat_a.id, disciplina_id=rede["port_id"], bimestre=11,
            ).first()
            n1_b = RegistroNota.query.filter_by(
                matricula_id=mat_b.id, disciplina_id=rede["port_id"], bimestre=11,
            ).first()
            self.assertEqual(n1_a.valor, 8.0)
            self.assertEqual(n1_b.valor, 6.5)
            f_a = RegistroFalta.query.filter_by(
                matricula_id=mat_a.id, disciplina_id=rede["port_id"], bimestre=1,
            ).first()
            f_b = RegistroFalta.query.filter_by(
                matricula_id=mat_b.id, disciplina_id=rede["port_id"], bimestre=1,
            ).first()
            self.assertEqual(f_a.quantidade, 4)
            self.assertEqual(f_b.quantidade, 2)
            db.session.add(RegistroFrequencia(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
                disciplina_id=rede["port_id"], data=date(2027, 3, 10),
                bimestre=1, situacao="FALTA",
            ))
            db.session.commit()
            por_2027 = faltas_por_bimestre(
                rede["aluno_id"], rede["port_id"], matricula_id=mat_b.id,
            )
            self.assertEqual(por_2027.get(1), 1)
            id_mat_b = mat_b.id

        resp = self._salvar(turma_2027, rede["port_id"], [
            self._nota(rede["aluno_id"], "7.0", 11),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(turma_2027, rede["port_id"], [
            self._nota(rede["aluno_id"], "3", 1, tipo="falta"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            from src.models import RegistroNota, RegistroFalta
            from src.notas import calcular_notas_matricula
            n1_a = RegistroNota.query.filter_by(
                matricula_id=id_mat_a, disciplina_id=rede["port_id"], bimestre=11,
            ).first()
            n1_b = RegistroNota.query.filter_by(
                matricula_id=id_mat_b, disciplina_id=rede["port_id"], bimestre=11,
            ).first()
            self.assertEqual(n1_a.valor, 8.0)
            self.assertEqual(n1_b.valor, 7.0)
            self.assertEqual(calcular_notas_matricula(id_mat_a, rede["port_id"])["n1"], 8.0)
            self.assertEqual(calcular_notas_matricula(id_mat_b, rede["port_id"])["n1"], 7.0)
            self.assertEqual(RegistroNota.query.filter_by(aluno_id=rede["aluno_id"]).count(), 2)
            f_a = RegistroFalta.query.filter_by(
                matricula_id=id_mat_a, disciplina_id=rede["port_id"], bimestre=1,
            ).first()
            f_b = RegistroFalta.query.filter_by(
                matricula_id=id_mat_b, disciplina_id=rede["port_id"], bimestre=1,
            ).first()
            self.assertEqual(f_a.quantidade, 4)
            self.assertEqual(f_b.quantidade, 3)

        mon_2026 = self._mon(rede["turma_2026"], rede["port_id"], rede["aluno_id"])
        mon_2027 = self._mon(turma_2027, rede["port_id"], rede["aluno_id"])
        self.assertEqual({n["bimestre"]: n["valor"] for n in mon_2026["notas"]}, {11: 8.0})
        self.assertEqual({n["bimestre"]: n["valor"] for n in mon_2027["notas"]}, {11: 7.0})
        self.assertNotIn(8.0, [n["valor"] for n in mon_2027["notas"]])
        self.assertEqual({f["bimestre"]: f["quantidade"] for f in mon_2026["faltas"]}, {1: 4})
        self.assertEqual({f["bimestre"]: f["quantidade"] for f in mon_2027["faltas"]}, {1: 3})

        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "9.0", 11),
        ])
        self.assertEqual(resp.status_code, 403)

    def test_upsert_nao_apaga_outras_celulas_nem_outros_alunos(self):
        rede = self._rede_joao()
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "8.0", 11),
            self._nota(rede["aluno_id"], "7.0", 12),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "6.0", 11),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        mon = self._mon(rede["turma_2026"], rede["port_id"], rede["aluno_id"])
        mapa = {n["bimestre"]: n["valor"] for n in mon["notas"]}
        self.assertEqual(mapa[11], 6.0)
        self.assertEqual(mapa[12], 7.0)

    def test_sem_matricula_cursando_nao_grava_parcial(self):
        rede = self._rede_joao()
        n = self._proximo()
        self._login(rede["inep"])
        resp = self.client.post("/api/alunos", json={
            "nome": "Maria Sem Turma",
            "cpf": f"7309{n:07d}"[:11],
            "nome_mae": "Mãe Maria",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        maria_id = _json(resp)[0]["id"]
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "8.0", 11),
            self._nota(maria_id, "9.0", 11),
        ])
        self.assertEqual(resp.status_code, 400)
        with self.app.app_context():
            from src.models import RegistroNota
            self.assertEqual(RegistroNota.query.filter_by(aluno_id=rede["aluno_id"]).count(), 0)
            self.assertEqual(RegistroNota.query.filter_by(aluno_id=maria_id).count(), 0)

    def test_consistencia_aluno_da_matricula(self):
        rede = self._rede_joao()
        n = self._proximo()
        self._login(rede["inep"])
        resp = self.client.post("/api/alunos", json={
            "nome": "Maria",
            "cpf": f"7308{n:07d}"[:11],
            "turma_id": rede["turma_2026"],
            "nome_mae": "Mãe Maria",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        maria_id = _json(resp)[0]["id"]
        with self.app.app_context():
            from src.matriculas import MSG_ALUNO_MATRICULA_DIVERGENTE, garantir_aluno_da_matricula
            from src.models import Matricula
            from src.notas import upsert_nota_matricula
            mat_joao = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            with self.assertRaises(ValueError) as ctx:
                garantir_aluno_da_matricula(maria_id, mat_joao)
            self.assertIn("não corresponde", str(ctx.exception).lower())
            self.assertEqual(str(ctx.exception), MSG_ALUNO_MATRICULA_DIVERGENTE)
            from src import db
            nota = upsert_nota_matricula(mat_joao, rede["port_id"], 11, 8.0)
            self.assertEqual(nota.aluno_id, mat_joao.aluno_id)
            self.assertEqual(nota.matricula_id, mat_joao.id)
            db.session.rollback()

    def test_cascade_matricula_nao_apaga_notas(self):
        with self.app.app_context():
            from sqlalchemy.orm import class_mapper
            from src.models import Matricula, RegistroFalta, RegistroNota
            rel_nota = class_mapper(Matricula).relationships["notas"]
            rel_falta = class_mapper(Matricula).relationships["faltas"]
            self.assertFalse(rel_nota.cascade.delete)
            self.assertFalse(rel_nota.cascade.delete_orphan)
            self.assertFalse(rel_falta.cascade.delete)
            self.assertFalse(rel_falta.cascade.delete_orphan)
            self.assertIn("aluno_id", RegistroNota.__table__.c)
            self.assertIn("aluno_id", RegistroFalta.__table__.c)
            self.assertIn("matricula_id", RegistroNota.__table__.c)
            self.assertIn("matricula_id", RegistroFalta.__table__.c)

    def _nota_no_banco(self, matricula_id, disc_id, bimestre=11):
        from src.models import RegistroNota
        return RegistroNota.query.filter_by(
            matricula_id=matricula_id, disciplina_id=disc_id, bimestre=bimestre,
        ).first()

    def test_api_limpar_celula_remove_somente_n1(self):
        rede = self._rede_joao()
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "8.0", 11),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertEqual(self._nota_no_banco(mat.id, rede["port_id"], 11).valor, 8.0)
            mat_id = mat.id
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], None, 11),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], None, 11),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertIsNone(self._nota_no_banco(mat_id, rede["port_id"], 11))

    def test_limpar_2027_nao_apaga_2026(self):
        rede = self._rede_joao()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "9.0", 11),
        ]).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200)
        self.client.post("/api/turmas", json={
            "nome": "6º Ano A", "ano_letivo": 2027, "etapa": "fundamental",
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º Ano A")
        self.client.post(f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027})
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027, "disciplina_id": rede["port_id"],
        })
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar(turma_2027, rede["port_id"], [
            self._nota(rede["aluno_id"], "8.0", 11),
        ]).status_code, 200)
        with self.app.app_context():
            from src.models import Matricula
            mat_a = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            mat_b = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
            ).first()
            id_a, id_b = mat_a.id, mat_b.id
        resp = self._salvar(turma_2027, rede["port_id"], [
            self._nota(rede["aluno_id"], None, 11),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertIsNone(self._nota_no_banco(id_b, rede["port_id"], 11))
            self.assertEqual(self._nota_no_banco(id_a, rede["port_id"], 11).valor, 9.0)

    def test_limpar_n1_nao_apaga_n2_nem_bimestre_seguinte(self):
        rede = self._rede_joao()
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "8.0", 11),
            self._nota(rede["aluno_id"], "7.0", 12),
            self._nota(rede["aluno_id"], "9.0", 21),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], None, 11),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertIsNone(self._nota_no_banco(mat.id, rede["port_id"], 11))
            self.assertEqual(self._nota_no_banco(mat.id, rede["port_id"], 12).valor, 7.0)
            self.assertEqual(self._nota_no_banco(mat.id, rede["port_id"], 21).valor, 9.0)

    def test_limpar_joao_nao_apaga_maria(self):
        rede = self._rede_joao()
        n = self._proximo()
        self._login(rede["inep"])
        resp = self.client.post("/api/alunos", json={
            "nome": "Maria",
            "cpf": f"7308{n:07d}"[:11],
            "turma_id": rede["turma_2026"],
            "nome_mae": "Mãe Maria",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        maria_id = _json(resp)[0]["id"]
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "8.0", 11),
            self._nota(maria_id, "9.0", 11),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], None, 11),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula
            mat_j = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            mat_m = Matricula.query.filter_by(
                aluno_id=maria_id, turma_id=rede["turma_2026"],
            ).first()
            self.assertIsNone(self._nota_no_banco(mat_j.id, rede["port_id"], 11))
            self.assertEqual(self._nota_no_banco(mat_m.id, rede["port_id"], 11).valor, 9.0)

    def test_limpar_com_aluno_sem_matricula_faz_rollback(self):
        rede = self._rede_joao()
        n = self._proximo()
        self._login(rede["inep"])
        resp = self.client.post("/api/alunos", json={
            "nome": "Maria Sem Turma",
            "cpf": f"7309{n:07d}"[:11],
            "nome_mae": "Mãe Maria",
        })
        maria_id = _json(resp)[0]["id"]
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "8.0", 11),
        ]).status_code, 200)
        resp = self._salvar(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], None, 11),
            self._nota(maria_id, "9.0", 11),
        ])
        self.assertEqual(resp.status_code, 400)
        with self.app.app_context():
            from src.models import Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertEqual(self._nota_no_banco(mat.id, rede["port_id"], 11).valor, 8.0)


class BackfillNotasFaltasTest(unittest.TestCase):
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

    def test_backfill_associa_quando_uma_matricula_e_preserva_contagem(self):
        from src import create_app, db
        from src.models import (
            Aluno, Disciplina, Escola, Matricula, RegistroFalta, RegistroNota, Turma,
        )
        from src.matriculas import backfill_registros_academicos, ULTIMO_RESUMO_BACKFILL_REGISTROS
        app = create_app()
        with app.app_context():
            disc = Disciplina.query.filter_by(nome="Português").first()
            escola = Escola(
                nome="Escola Backfill Notas", inep="73100001",
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
                nome="Aluno Uma Matricula", cpf="73100000001", situacao="ativo",
                turma_id=turma.id, escola_id=escola.id, nome_mae="Mãe Backfill",
            )
            db.session.add(aluno)
            db.session.flush()
            db.session.add(Matricula(
                aluno_id=aluno.id, turma_id=turma.id,
                data_inicio=date(2026, 2, 1), situacao="cursando",
            ))
            db.session.flush()
            db.session.add(RegistroNota(
                aluno_id=aluno.id, disciplina_id=disc.id,
                bimestre=11, valor=8.5, tipo="bimestral",
            ))
            db.session.add(RegistroFalta(
                aluno_id=aluno.id, disciplina_id=disc.id,
                bimestre=1, quantidade=4,
            ))
            db.session.commit()
            n_notas = RegistroNota.query.count()
            n_faltas = RegistroFalta.query.count()
            backfill_registros_academicos()
            self.assertEqual(RegistroNota.query.count(), n_notas)
            self.assertEqual(RegistroFalta.query.count(), n_faltas)
            nota = RegistroNota.query.filter_by(aluno_id=aluno.id).first()
            falta = RegistroFalta.query.filter_by(aluno_id=aluno.id).first()
            self.assertIsNotNone(nota.matricula_id)
            self.assertIsNotNone(falta.matricula_id)
            self.assertEqual(nota.valor, 8.5)
            self.assertEqual(nota.bimestre, 11)
            self.assertEqual(nota.disciplina_id, disc.id)
            self.assertEqual(falta.quantidade, 4)
            self.assertEqual(ULTIMO_RESUMO_BACKFILL_REGISTROS["notas_associadas"], 1)
            self.assertEqual(ULTIMO_RESUMO_BACKFILL_REGISTROS["faltas_associadas"], 1)

    def test_backfill_ambiguo_nao_atribui(self):
        from src import create_app, db
        from src.models import (
            Aluno, Disciplina, Escola, Matricula, RegistroNota, Turma,
        )
        from src.matriculas import (
            ULTIMO_DIAGNOSTICO_BACKFILL_REGISTROS,
            backfill_registros_academicos,
        )
        app = create_app()
        with app.app_context():
            disc = Disciplina.query.filter_by(nome="Português").first()
            escola = Escola(
                nome="Escola Ambiguo", inep="73100002",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add(escola)
            db.session.flush()
            t1 = Turma(
                nome="5º Ano A", ano_letivo=2026, status="concluida",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            t2 = Turma(
                nome="6º Ano A", ano_letivo=2027, status="ativa",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            db.session.add_all([t1, t2])
            db.session.flush()
            aluno = Aluno(
                nome="Aluno Ambiguo", cpf="73100000002", situacao="ativo",
                turma_id=t2.id, escola_id=escola.id, nome_mae="Mãe Ambiguo",
            )
            db.session.add(aluno)
            db.session.flush()
            db.session.add(Matricula(
                aluno_id=aluno.id, turma_id=t1.id,
                data_inicio=date(2026, 2, 1), data_fim=date(2026, 12, 15),
                situacao="concluida",
            ))
            db.session.add(Matricula(
                aluno_id=aluno.id, turma_id=t2.id,
                data_inicio=date(2027, 2, 1), situacao="cursando",
            ))
            db.session.flush()
            db.session.add(RegistroNota(
                aluno_id=aluno.id, disciplina_id=disc.id,
                bimestre=11, valor=5.0, tipo="bimestral",
            ))
            db.session.commit()
            valor_antes = 5.0
            n_antes = RegistroNota.query.count()
            backfill_registros_academicos()
            nota = RegistroNota.query.filter_by(aluno_id=aluno.id).first()
            self.assertIsNone(nota.matricula_id)
            self.assertEqual(nota.valor, valor_antes)
            self.assertEqual(nota.bimestre, 11)
            self.assertEqual(RegistroNota.query.count(), n_antes)
            tipos = {d["tipo"] for d in ULTIMO_DIAGNOSTICO_BACKFILL_REGISTROS}
            self.assertIn("ambiguo", tipos)
            self.assertTrue(any(
                d.get("motivo") == "multiplas_matriculas"
                for d in ULTIMO_DIAGNOSTICO_BACKFILL_REGISTROS
            ))


if __name__ == "__main__":
    unittest.main()
