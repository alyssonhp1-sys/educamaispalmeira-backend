import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


EIXO_A = "Eu, o outro e o nós"


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


def _dados(hist):
    return json.loads(hist.dados_fechamento or "{}")


class HistoricoAnualPorMatriculaTest(unittest.TestCase):
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
        HistoricoAnualPorMatriculaTest._seq += 1
        return HistoricoAnualPorMatriculaTest._seq

    def _rede(self, infantil=False, maria=False):
        n = self._proximo()
        inep = f"7700{n:04d}"
        cpf_prof = f"7701{n:07d}"[:11]
        cpf_joao = f"7702{n:07d}"[:11]
        etapa = "infantil" if infantil else "fundamental"
        nome_turma = "Pré II 2026" if infantil else "5º Ano A"
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola Hist {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Hist", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        self.client.post("/api/turmas", json={
            "nome": nome_turma, "ano_letivo": 2026, "etapa": etapa,
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2026 = dash["turmas"][0]["id"]
        port_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Português")
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": "Ana" if infantil else "João",
            "cpf": cpf_joao,
            "turma_id": turma_2026,
            "nome_mae": "Mãe Principal",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aluno_id = _json(resp)[0]["id"]
        maria_id = None
        if maria:
            cpf_m = f"7703{n:07d}"[:11]
            resp = self.client.post("/api/alunos", json={
                "nome": "Maria", "cpf": cpf_m, "turma_id": turma_2026, "nome_mae": "Mãe Maria",
            })
            self.assertEqual(resp.status_code, 200, _json(resp)[1])
            maria_id = _json(resp)[0]["id"]
        aloc = {"professor_id": prof_id, "turma_id": turma_2026}
        if not infantil:
            aloc["disciplina_id"] = port_id
        self.assertEqual(self.client.post("/api/alocacoes", json=aloc).status_code, 200)
        concluir_primeiro_acesso(self.client, cpf_prof, temp_prof)
        return {
            "inep": inep,
            "cpf_prof": cpf_prof,
            "turma_2026": turma_2026,
            "port_id": port_id,
            "aluno_id": aluno_id,
            "maria_id": maria_id,
            "prof_id": prof_id,
            "infantil": infantil,
        }

    def _nota(self, aluno_id, valor, bimestre=11, tipo="nota"):
        return {"aluno_id": aluno_id, "valor": valor, "tipo": tipo, "bimestre": bimestre}

    def _salvar_notas(self, turma_id, disc_id, payload):
        return self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/{disc_id}/notas",
            json=payload,
        )

    def _parecer(self, aluno_id, texto, eixo=EIXO_A, bimestre=1):
        return {
            "aluno_id": aluno_id,
            "eixo": eixo,
            "bimestre": bimestre,
            "avaliacao": "Em Desenvolvimento",
            "observacao": texto,
        }

    def _salvar_parecer(self, turma_id, payload):
        return self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/0/notas",
            json=payload,
        )

    def _hist_da_matricula(self, matricula_id):
        from src.models import HistoricoAnual
        return HistoricoAnual.query.filter_by(matricula_id=matricula_id).all()

    def _vals_nota(self, hist, disc_id):
        return [
            (item["bim"], item["val"])
            for item in _dados(hist).get("notas", [])
            if item["disc"] == disc_id
        ]

    def _qtd_faltas(self, hist, disc_id):
        return sum(
            item["qtd"] or 0
            for item in _dados(hist).get("faltas", [])
            if item["disc"] == disc_id
        )

    def _sits_freq(self, hist):
        return [(item["data"], item["sit"]) for item in _dados(hist).get("frequencias", [])]

    def _obs_parecer(self, hist):
        return [item["obs"] for item in _dados(hist).get("pareceres", [])]

    def test_historico_recebe_matricula_id(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "9", 11),
            self._nota(rede["aluno_id"], "8", 12),
            self._nota(rede["aluno_id"], "4", 1, tipo="falta"),
        ]).status_code, 200)
        self._login(rede["inep"])
        resp = self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import HistoricoAnual, Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            hists = self._hist_da_matricula(mat.id)
            self.assertEqual(len(hists), 1)
            self.assertEqual(hists[0].matricula_id, mat.id)
            self.assertEqual(hists[0].aluno_id, mat.aluno_id)
            self.assertEqual(hists[0].ano_letivo, 2026)
            self.assertIn("matricula_id", {c.name for c in HistoricoAnual.__table__.columns})

    def test_encerramento_usa_matriculas_nao_atalho(self):
        rede = self._rede()
        n = self._proximo()
        self._login(rede["inep"])
        resp = self.client.post("/api/alunos", json={
            "nome": "Visitante Só Atalho",
            "cpf": f"7708{n:07d}"[:11],
            "nome_mae": "Mãe Visitante",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        visitante_id = _json(resp)[0]["id"]
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula
            joao = db.session.get(Aluno, rede["aluno_id"])
            joao.turma_id = None
            visitante = db.session.get(Aluno, visitante_id)
            visitante.turma_id = rede["turma_2026"]
            db.session.commit()
            self.assertEqual(
                Matricula.query.filter_by(
                    aluno_id=visitante_id, turma_id=rede["turma_2026"],
                ).count(),
                0,
            )
            mat_joao = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertEqual(mat_joao.situacao, "cursando")
            id_mat_joao = mat_joao.id
        resp = self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, HistoricoAnual, Matricula
            self.assertEqual(len(self._hist_da_matricula(id_mat_joao)), 1)
            self.assertEqual(
                HistoricoAnual.query.filter_by(aluno_id=visitante_id).count(), 0,
            )
            self.assertEqual(db.session.get(Matricula, id_mat_joao).situacao, "concluida")
            self.assertIsNone(db.session.get(Aluno, rede["aluno_id"]).turma_id)

    def test_cenario_2026_2027_notas_faltas_frequencia_isoladas(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "9", 11),
            self._nota(rede["aluno_id"], "8", 12),
            self._nota(rede["aluno_id"], "4", 1, tipo="falta"),
        ]).status_code, 200)
        with self.app.app_context():
            from src import db
            from src.models import Matricula, RegistroFrequencia
            mat_a = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            db.session.add(RegistroFrequencia(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
                matricula_id=mat_a.id, disciplina_id=rede["port_id"],
                data=date(2026, 3, 10), bimestre=1, situacao="FALTA",
            ))
            db.session.commit()
            id_mat_a = mat_a.id
        self._login(rede["inep"])
        resp = self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual, RegistroNota
            hist_a = HistoricoAnual.query.filter_by(matricula_id=id_mat_a).one()
            json_a = hist_a.dados_fechamento
            self.assertEqual(sorted(self._vals_nota(hist_a, rede["port_id"])), [(11, 9.0), (12, 8.0)])
            self.assertEqual(self._qtd_faltas(hist_a, rede["port_id"]), 4)
            self.assertIn(("2026-03-10", "FALTA"), self._sits_freq(hist_a))
            nota = RegistroNota.query.filter_by(matricula_id=id_mat_a, bimestre=11).first()
            nota.valor = 1.0
            db.session.commit()
            hist_a2 = HistoricoAnual.query.filter_by(matricula_id=id_mat_a).one()
            self.assertEqual(hist_a2.dados_fechamento, json_a)
            self.assertEqual(sorted(self._vals_nota(hist_a2, rede["port_id"])), [(11, 9.0), (12, 8.0)])

        resp = self.client.post("/api/turmas", json={
            "nome": "6º Ano A", "ano_letivo": 2027, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º Ano A")
        self.assertEqual(self.client.post(
            f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027},
        ).status_code, 200)
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027, "disciplina_id": rede["port_id"],
        })
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(turma_2027, rede["port_id"], [
            self._nota(rede["aluno_id"], "6", 11),
            self._nota(rede["aluno_id"], "7", 12),
            self._nota(rede["aluno_id"], "2", 1, tipo="falta"),
        ]).status_code, 200)
        with self.app.app_context():
            from src import db
            from src.models import Matricula, RegistroFrequencia
            mat_b = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
            ).first()
            db.session.add(RegistroFrequencia(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
                matricula_id=mat_b.id, disciplina_id=rede["port_id"],
                data=date(2027, 3, 15), bimestre=1, situacao="FALTA",
            ))
            db.session.commit()
            id_mat_b = mat_b.id
        self._login(rede["inep"])
        resp = self.client.post(f"/api/turmas/{turma_2027}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import HistoricoAnual
            hist_a = HistoricoAnual.query.filter_by(matricula_id=id_mat_a).one()
            hist_b = HistoricoAnual.query.filter_by(matricula_id=id_mat_b).one()
            self.assertEqual(sorted(self._vals_nota(hist_a, rede["port_id"])), [(11, 9.0), (12, 8.0)])
            self.assertEqual(self._qtd_faltas(hist_a, rede["port_id"]), 4)
            self.assertEqual(sorted(self._vals_nota(hist_b, rede["port_id"])), [(11, 6.0), (12, 7.0)])
            self.assertEqual(self._qtd_faltas(hist_b, rede["port_id"]), 2)
            datas_a = [d for d, _ in self._sits_freq(hist_a)]
            datas_b = [d for d, _ in self._sits_freq(hist_b)]
            self.assertIn("2026-03-10", datas_a)
            self.assertNotIn("2027-03-15", datas_a)
            self.assertIn("2027-03-15", datas_b)
            self.assertNotIn("2026-03-10", datas_b)
            self.assertNotIn(6.0, [v for _, v in self._vals_nota(hist_a, rede["port_id"])])
            self.assertNotIn(9.0, [v for _, v in self._vals_nota(hist_b, rede["port_id"])])

    def test_ei_parecer_2026_2027_isolado(self):
        rede = self._rede(infantil=True)
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_parecer(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Parecer 2026"),
        ]).status_code, 200)
        with self.app.app_context():
            from src.models import Matricula
            id_mat_a = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first().id
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        self.assertEqual(self.client.post("/api/turmas", json={
            "nome": "Pré II 2027", "ano_letivo": 2027, "etapa": "infantil",
        }).status_code, 200)
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "Pré II 2027")
        self.assertEqual(self.client.post(
            f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027},
        ).status_code, 200)
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027,
        })
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_parecer(turma_2027, [
            self._parecer(rede["aluno_id"], "Parecer 2027"),
        ]).status_code, 200)
        with self.app.app_context():
            from src.models import Matricula
            id_mat_b = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
            ).first().id
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{turma_2027}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src.models import HistoricoAnual
            hist_a = HistoricoAnual.query.filter_by(matricula_id=id_mat_a).one()
            hist_b = HistoricoAnual.query.filter_by(matricula_id=id_mat_b).one()
            self.assertEqual(self._obs_parecer(hist_a), ["Parecer 2026"])
            self.assertEqual(self._obs_parecer(hist_b), ["Parecer 2027"])
            self.assertNotIn("Parecer 2027", self._obs_parecer(hist_a))
            self.assertNotIn("Parecer 2026", self._obs_parecer(hist_b))

    def test_joao_nao_mistura_com_maria(self):
        rede = self._rede(maria=True)
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "9", 11),
            self._nota(rede["aluno_id"], "3", 1, tipo="falta"),
            self._nota(rede["maria_id"], "5", 11),
            self._nota(rede["maria_id"], "1", 1, tipo="falta"),
        ]).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src.models import HistoricoAnual, Matricula
            mat_j = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            mat_m = Matricula.query.filter_by(
                aluno_id=rede["maria_id"], turma_id=rede["turma_2026"],
            ).first()
            hist_j = HistoricoAnual.query.filter_by(matricula_id=mat_j.id).one()
            hist_m = HistoricoAnual.query.filter_by(matricula_id=mat_m.id).one()
            vals_j = [v for _, v in self._vals_nota(hist_j, rede["port_id"])]
            vals_m = [v for _, v in self._vals_nota(hist_m, rede["port_id"])]
            self.assertEqual(vals_j, [9.0])
            self.assertEqual(vals_m, [5.0])
            self.assertEqual(self._qtd_faltas(hist_j, rede["port_id"]), 3)
            self.assertEqual(self._qtd_faltas(hist_m, rede["port_id"]), 1)
            self.assertNotEqual(hist_j.matricula_id, hist_m.matricula_id)

    def test_reencerrar_nao_duplica_nem_sobrescreve(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "9", 11),
        ]).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src.models import HistoricoAnual, Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            hist = HistoricoAnual.query.filter_by(matricula_id=mat.id).one()
            json_antes = hist.dados_fechamento
            data_fim = mat.data_fim
            n_hist = HistoricoAnual.query.filter_by(aluno_id=rede["aluno_id"]).count()
            n_mat = Matricula.query.filter_by(aluno_id=rede["aluno_id"]).count()
            ids = (mat.id, json_antes, str(data_fim), n_hist, n_mat)
        resp = self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual, Matricula, Turma
            mat_id, json_antes, data_fim, n_hist, n_mat = ids
            mat = db.session.get(Matricula, mat_id)
            hists = HistoricoAnual.query.filter_by(matricula_id=mat_id).all()
            self.assertEqual(len(hists), 1)
            self.assertEqual(hists[0].dados_fechamento, json_antes)
            self.assertEqual(str(mat.data_fim), data_fim)
            self.assertEqual(mat.situacao, "concluida")
            self.assertEqual(
                HistoricoAnual.query.filter_by(aluno_id=rede["aluno_id"]).count(), n_hist,
            )
            self.assertEqual(
                Matricula.query.filter_by(aluno_id=rede["aluno_id"]).count(), n_mat,
            )
            self.assertEqual(db.session.get(Turma, rede["turma_2026"]).status, "concluida")

    def test_rollback_integral_nao_encerra_parcial(self):
        rede = self._rede(maria=True)
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "9", 11),
            self._nota(rede["maria_id"], "5", 11),
        ]).status_code, 200)
        from src.historico import montar_dados_fechamento as original

        def flaky(matricula, etapa):
            aluno = matricula.aluno
            if aluno is not None and aluno.nome == "Maria":
                raise RuntimeError("falha simulada no histórico de Maria")
            return original(matricula, etapa)

        self._login(rede["inep"])
        with patch("src.historico.montar_dados_fechamento", side_effect=flaky):
            resp = self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar")
        self.assertEqual(resp.status_code, 500)
        with self.app.app_context():
            from src import db
            from src.models import Aluno, HistoricoAnual, Matricula, Turma
            joao = db.session.get(Aluno, rede["aluno_id"])
            maria = db.session.get(Aluno, rede["maria_id"])
            self.assertEqual(HistoricoAnual.query.filter_by(aluno_id=joao.id).count(), 0)
            self.assertEqual(HistoricoAnual.query.filter_by(aluno_id=maria.id).count(), 0)
            self.assertEqual(
                Matricula.query.filter_by(aluno_id=joao.id, situacao="cursando").count(), 1,
            )
            self.assertEqual(
                Matricula.query.filter_by(aluno_id=maria.id, situacao="cursando").count(), 1,
            )
            self.assertEqual(joao.turma_id, rede["turma_2026"])
            self.assertEqual(maria.turma_id, rede["turma_2026"])
            self.assertEqual(db.session.get(Turma, rede["turma_2026"]).status, "ativa")

    def test_cascade_e_exclusao_nao_destroem_historico(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(rede["turma_2026"], rede["port_id"], [
            self._nota(rede["aluno_id"], "9", 11),
        ]).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from sqlalchemy.orm import class_mapper
            from src import db
            from src.exclusao import matricula_possui_historico_academico
            from src.models import HistoricoAnual, Matricula
            rel = class_mapper(Matricula).relationships["historicos_anuais"]
            self.assertFalse(rel.cascade.delete)
            self.assertFalse(rel.cascade.delete_orphan)
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertTrue(matricula_possui_historico_academico(mat))
            hist = HistoricoAnual.query.filter_by(matricula_id=mat.id).one()
            hist_id = hist.id
            json_antes = hist.dados_fechamento
            try:
                db.session.delete(mat)
                db.session.commit()
            except Exception:
                db.session.rollback()
            vivo = HistoricoAnual.query.filter_by(id=hist_id).first()
            self.assertIsNotNone(vivo)
            self.assertEqual(vivo.dados_fechamento, json_antes)
        resp = self.client.delete(f"/api/excluir/aluno/{rede['aluno_id']}")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import HistoricoAnual
            self.assertTrue(HistoricoAnual.query.filter_by(aluno_id=rede["aluno_id"]).count())
        resp = self.client.delete(f"/api/excluir/turma/{rede['turma_2026']}")
        self.assertEqual(resp.status_code, 409)


class BackfillHistoricoAnualTest(unittest.TestCase):
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

    def test_backfill_associa_inequivoco_preserva_json_e_e_idempotente(self):
        from src import create_app, db
        from src.historico import (
            ULTIMO_RESUMO_BACKFILL_HISTORICO,
            backfill_historicos_anuais,
        )
        from src.models import Aluno, Escola, HistoricoAnual, Matricula, Turma
        app = create_app()
        with app.app_context():
            escola = Escola(
                nome="Escola Backfill Hist", inep="77100001",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add(escola)
            db.session.flush()
            turma = Turma(
                nome="5º Ano Backfill", ano_letivo=2026, status="concluida",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            db.session.add(turma)
            db.session.flush()
            aluno = Aluno(
                nome="João", cpf="77100000001", situacao="ativo",
                turma_id=None, escola_id=escola.id, nome_mae="Mãe Backfill",
            )
            db.session.add(aluno)
            db.session.flush()
            db.session.add(Matricula(
                aluno_id=aluno.id, turma_id=turma.id,
                data_inicio=date(2026, 2, 1), data_fim=date(2026, 12, 15),
                situacao="concluida",
            ))
            db.session.flush()
            payload = json.dumps({
                "notas": [{"disc": 1, "bim": 11, "val": 9.0}],
                "faltas": [{"disc": 1, "bim": 1, "qtd": 4}],
            })
            db.session.add(HistoricoAnual(
                ano_letivo=2026, aluno_id=aluno.id, matricula_id=None,
                escola_nome=escola.nome, turma_nome=turma.nome,
                etapa="fundamental", situacao_final="ativo",
                dados_fechamento=payload,
            ))
            db.session.commit()
            n_antes = HistoricoAnual.query.count()
            backfill_historicos_anuais()
            self.assertEqual(HistoricoAnual.query.count(), n_antes)
            hist = HistoricoAnual.query.filter_by(aluno_id=aluno.id).one()
            mat = Matricula.query.filter_by(aluno_id=aluno.id, turma_id=turma.id).one()
            self.assertEqual(hist.matricula_id, mat.id)
            self.assertEqual(hist.dados_fechamento, payload)
            self.assertEqual(ULTIMO_RESUMO_BACKFILL_HISTORICO["associados"], 1)
            mat_id = hist.matricula_id
            backfill_historicos_anuais()
            self.assertEqual(HistoricoAnual.query.count(), n_antes)
            hist2 = HistoricoAnual.query.filter_by(aluno_id=aluno.id).one()
            self.assertEqual(hist2.matricula_id, mat_id)
            self.assertEqual(hist2.dados_fechamento, payload)
            self.assertEqual(ULTIMO_RESUMO_BACKFILL_HISTORICO["associados"], 0)

    def test_backfill_ambiguo_nao_chuta_matricula_atual(self):
        from src import create_app, db
        from src.historico import (
            ULTIMO_DIAGNOSTICO_BACKFILL_HISTORICO,
            backfill_historicos_anuais,
        )
        from src.models import Aluno, Escola, HistoricoAnual, Matricula, Turma
        app = create_app()
        with app.app_context():
            escola = Escola(
                nome="Escola Ambiguo Hist", inep="77100002",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add(escola)
            db.session.flush()
            t_a = Turma(
                nome="5º Ano A Amb", ano_letivo=2026, status="concluida",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            t_b = Turma(
                nome="5º Ano B Amb", ano_letivo=2026, status="ativa",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            db.session.add_all([t_a, t_b])
            db.session.flush()
            aluno = Aluno(
                nome="João Ambiguo", cpf="77100000002", situacao="ativo",
                turma_id=t_b.id, escola_id=escola.id, nome_mae="Mãe Ambiguo",
            )
            db.session.add(aluno)
            db.session.flush()
            db.session.add(Matricula(
                aluno_id=aluno.id, turma_id=t_a.id,
                data_inicio=date(2026, 2, 1), data_fim=date(2026, 6, 30),
                situacao="concluida",
            ))
            db.session.add(Matricula(
                aluno_id=aluno.id, turma_id=t_b.id,
                data_inicio=date(2026, 7, 1), situacao="cursando",
            ))
            payload = json.dumps({"notas": [{"disc": 1, "bim": 11, "val": 8.0}]})
            db.session.add(HistoricoAnual(
                ano_letivo=2026, aluno_id=aluno.id, matricula_id=None,
                escola_nome=escola.nome, turma_nome=None,
                etapa="fundamental", situacao_final="ativo",
                dados_fechamento=payload,
            ))
            db.session.commit()
            n_antes = HistoricoAnual.query.count()
            mat_atual = Matricula.query.filter_by(turma_id=t_b.id, aluno_id=aluno.id).one()
            backfill_historicos_anuais()
            self.assertEqual(HistoricoAnual.query.count(), n_antes)
            hist = HistoricoAnual.query.filter_by(aluno_id=aluno.id).one()
            self.assertIsNone(hist.matricula_id)
            self.assertNotEqual(hist.matricula_id, mat_atual.id)
            self.assertEqual(hist.dados_fechamento, payload)
            self.assertTrue(any(
                d["tipo"] == "ambiguo" for d in ULTIMO_DIAGNOSTICO_BACKFILL_HISTORICO
            ))


CHAVES_PII_INDEVIDAS = frozenset({
    "cpf", "nis", "nis_bolsa", "endereco", "telefone", "alergia", "tem_alergia",
    "responsavel", "nome_mae", "nome_pai", "senha", "email", "senha_hash",
})


def _chaves_json(obj, acc=None):
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for chave, valor in obj.items():
            acc.add(str(chave).lower())
            _chaves_json(valor, acc)
    elif isinstance(obj, list):
        for item in obj:
            _chaves_json(item, acc)
    return acc


def _mapa_ano(n1, n2, pf=None):
    dados = {
        11: n1, 12: n2,
        21: n1, 22: n2,
        31: n1, 32: n2,
        41: n1, 42: n2,
    }
    if pf is not None:
        dados[99] = pf
    return dados


class SnapshotAcademicoConsolidadoTest(unittest.TestCase):
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
        self._definir_media(6.0)

    def tearDown(self):
        self._definir_media(6.0)
        with self.app.app_context():
            from src import db
            from src.models import Disciplina
            mat = Disciplina.query.filter_by(nome="Matemática").first()
            if mat is None:
                outro = Disciplina.query.filter(
                    Disciplina.nome.like("Matemática%"),
                ).order_by(Disciplina.id.asc()).first()
                if outro is not None:
                    outro.nome = "Matemática"
                    db.session.commit()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _definir_media(self, valor):
        with self.app.app_context():
            from src import db
            from src.models import ConfiguracaoAcademica
            cfg = ConfiguracaoAcademica.query.order_by(ConfiguracaoAcademica.id).first()
            if cfg is not None:
                cfg.media_minima = float(valor)
                db.session.commit()

    def _proximo(self):
        SnapshotAcademicoConsolidadoTest._seq += 1
        return SnapshotAcademicoConsolidadoTest._seq

    def _rede(self, infantil=False, maria=False, alocar_matematica=True):
        n = self._proximo()
        inep = f"7800{n:04d}"
        cpf_prof = f"7801{n:07d}"[:11]
        cpf_joao = f"7802{n:07d}"[:11]
        etapa = "infantil" if infantil else "fundamental"
        nome_turma = "Pré II 2026" if infantil else "5º Ano A"
        escola_nome = f"Escola Snap {inep}"
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": escola_nome,
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Snap", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        self.client.post("/api/turmas", json={
            "nome": nome_turma, "ano_letivo": 2026, "etapa": etapa,
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2026 = dash["turmas"][0]["id"]
        mat_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Matemática")
        port_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Português")
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": "Ana" if infantil else "João",
            "cpf": cpf_joao,
            "turma_id": turma_2026,
            "nome_mae": "Mãe Principal",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aluno_id = _json(resp)[0]["id"]
        maria_id = None
        if maria:
            cpf_m = f"7803{n:07d}"[:11]
            resp = self.client.post("/api/alunos", json={
                "nome": "Maria", "cpf": cpf_m, "turma_id": turma_2026, "nome_mae": "Mãe Maria",
            })
            self.assertEqual(resp.status_code, 200, _json(resp)[1])
            maria_id = _json(resp)[0]["id"]
        aloc = {"professor_id": prof_id, "turma_id": turma_2026}
        if not infantil:
            aloc["disciplina_id"] = mat_id if alocar_matematica else port_id
        self.assertEqual(self.client.post("/api/alocacoes", json=aloc).status_code, 200)
        concluir_primeiro_acesso(self.client, cpf_prof, temp_prof)
        return {
            "inep": inep,
            "cpf_prof": cpf_prof,
            "cpf_joao": cpf_joao,
            "turma_2026": turma_2026,
            "turma_nome": nome_turma,
            "escola_id": escola_id,
            "escola_nome": escola_nome,
            "mat_id": mat_id,
            "port_id": port_id,
            "aluno_id": aluno_id,
            "maria_id": maria_id,
            "prof_id": prof_id,
            "infantil": infantil,
        }

    def _nota(self, aluno_id, valor, bimestre=11, tipo="nota"):
        return {"aluno_id": aluno_id, "valor": valor, "tipo": tipo, "bimestre": bimestre}

    def _notas_ano(self, aluno_id, n1, n2, pf=None):
        payload = []
        for a, b in ((11, 12), (21, 22), (31, 32), (41, 42)):
            payload.append(self._nota(aluno_id, str(n1), a))
            payload.append(self._nota(aluno_id, str(n2), b))
        if pf is not None:
            payload.append(self._nota(aluno_id, str(pf), 99))
        return payload

    def _salvar_notas(self, turma_id, disc_id, payload):
        return self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/{disc_id}/notas",
            json=payload,
        )

    def _parecer(self, aluno_id, texto, eixo=EIXO_A, bimestre=1):
        return {
            "aluno_id": aluno_id,
            "eixo": eixo,
            "bimestre": bimestre,
            "avaliacao": "Em Desenvolvimento",
            "observacao": texto,
        }

    def _salvar_parecer(self, turma_id, payload):
        return self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/0/notas",
            json=payload,
        )

    def _hist_json(self, matricula_id):
        from src.historico import carregar_dados_fechamento
        from src.models import HistoricoAnual
        hist = HistoricoAnual.query.filter_by(matricula_id=matricula_id).one()
        return hist, carregar_dados_fechamento(hist)

    def _item_disc(self, dados, disc_id):
        return next(
            d for d in dados.get("disciplinas") or []
            if d["disciplina_id"] == disc_id
        )

    def _mat_id_aluno(self, aluno_id, turma_id):
        from src.models import Matricula
        return Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).one().id

    def test_snapshot_novo_contem_matricula_aluno_turma_escola_ano(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 7, 7),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            mat_id = self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"])
            hist, dados = self._hist_json(mat_id)
            self.assertEqual(dados["aluno"]["id"], rede["aluno_id"])
            self.assertEqual(dados["aluno"]["nome"], "João")
            self.assertEqual(dados["matricula"]["id"], mat_id)
            self.assertEqual(dados["matricula"]["situacao"], "concluida")
            self.assertEqual(dados["turma"]["id"], rede["turma_2026"])
            self.assertEqual(dados["turma"]["nome"], rede["turma_nome"])
            self.assertEqual(dados["turma"]["ano_letivo"], 2026)
            self.assertEqual(dados["turma"]["etapa"], "fundamental")
            self.assertEqual(dados["escola"]["id"], rede["escola_id"])
            self.assertEqual(dados["escola"]["nome"], rede["escola_nome"])
            self.assertEqual(dados["escola"]["inep"], rede["inep"])
            self.assertEqual(hist.ano_letivo, 2026)
            self.assertEqual(dados["situacao_cadastral"], "ativo")
            self.assertIsNone(dados["resultado_academico"])
            self.assertEqual(hist.situacao_final, "ativo")
            self.assertNotEqual(hist.situacao_final, "APROVADO")

    def test_snapshot_contem_disciplina_id_e_nome(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 7, 7),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            _, dados = self._hist_json(self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"]))
            item = self._item_disc(dados, rede["mat_id"])
            self.assertEqual(item["disciplina_id"], rede["mat_id"])
            self.assertEqual(item["disciplina_nome"], "Matemática")
            self.assertTrue(any(n["disc"] == rede["mat_id"] for n in dados["notas"]))

    def test_renomear_disciplina_depois_nao_altera_snapshot(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 7, 7),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src import db
            from src.models import Disciplina
            disc = db.session.get(Disciplina, rede["mat_id"])
            disc.nome = "Matemática Geral"
            db.session.commit()
            hist, dados = self._hist_json(self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"]))
            item = self._item_disc(dados, rede["mat_id"])
            self.assertEqual(item["disciplina_nome"], "Matemática")
            self.assertNotIn("Matemática Geral", hist.dados_fechamento)
            self.assertEqual(db.session.get(Disciplina, rede["mat_id"]).nome, "Matemática Geral")

    def test_renomear_escola_depois_nao_altera_snapshot(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src import db
            from src.models import Escola
            escola = db.session.get(Escola, rede["escola_id"])
            escola.nome = "Escola Renomeada 2099"
            db.session.commit()
            hist, dados = self._hist_json(self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"]))
            self.assertEqual(dados["escola"]["nome"], rede["escola_nome"])
            self.assertEqual(hist.escola_nome, rede["escola_nome"])
            self.assertNotEqual(escola.nome, dados["escola"]["nome"])

    def test_renomear_turma_depois_nao_altera_snapshot(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src import db
            from src.models import Turma
            turma = db.session.get(Turma, rede["turma_2026"])
            turma.nome = "5º Ano Z Renomeada"
            db.session.commit()
            hist, dados = self._hist_json(self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"]))
            self.assertEqual(dados["turma"]["nome"], rede["turma_nome"])
            self.assertEqual(hist.turma_nome, rede["turma_nome"])
            self.assertEqual(turma.nome, "5º Ano Z Renomeada")

    def test_media_minima_vigente_fica_congelada(self):
        self._definir_media(6.0)
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 7, 7),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            _, dados = self._hist_json(self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"]))
            self.assertEqual(dados["criterios_academicos"]["media_minima"], 6.0)
            self.assertEqual(self._item_disc(dados, rede["mat_id"])["media_minima"], 6.0)

    def test_mudar_media_minima_depois_nao_altera_historico(self):
        self._definir_media(6.0)
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 6.5, 6.5),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src.notas import calcular_de_notas
            hist, _dados_snap = self._hist_json(
                self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"]),
            )
            json_antes = hist.dados_fechamento
            esperado = calcular_de_notas(_mapa_ano(6.5, 6.5), media_minima=6.0)
            self.assertEqual(esperado["situacao"], "APROVADO")
        self._definir_media(7.0)
        with self.app.app_context():
            from src.notas import calcular_de_notas, media_minima_vigente
            self.assertEqual(media_minima_vigente(), 7.0)
            vivo = calcular_de_notas(_mapa_ano(6.5, 6.5))
            self.assertEqual(vivo["situacao"], "EM RECUPERAÇÃO")
            hist, dados = self._hist_json(self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"]))
            self.assertEqual(hist.dados_fechamento, json_antes)
            self.assertEqual(dados["criterios_academicos"]["media_minima"], 6.0)
            item = self._item_disc(dados, rede["mat_id"])
            self.assertEqual(item["resultado"], "APROVADO")
            self.assertEqual(item["media_minima"], 6.0)

    def test_resultado_por_disciplina_usa_motor_oficial(self):
        from src.notas import calcular_de_notas
        esperado = calcular_de_notas(_mapa_ano(5, 5), media_minima=6.0)
        self.assertEqual(esperado["situacao"], "EM RECUPERAÇÃO")
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 5, 5),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            _, dados = self._hist_json(self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"]))
            item = self._item_disc(dados, rede["mat_id"])
            self.assertEqual(item["resultado"], esperado["situacao"])
            self.assertEqual(item["ma"], esperado["ma"])
            self.assertEqual(item["precisa_pf"], esperado["precisa_pf"])
            self.assertEqual(item["ms1"], esperado["ms1"])
            self.assertEqual(item["ms2"], esperado["ms2"])

    def test_2026_2027_resultados_independentes(self):
        from src.notas import calcular_de_notas
        esperado_x = calcular_de_notas(_mapa_ano(7, 7), media_minima=6.0)
        esperado_y = calcular_de_notas(_mapa_ano(5, 5), media_minima=7.0)
        self.assertEqual(esperado_x["situacao"], "APROVADO")
        self.assertEqual(esperado_y["situacao"], "EM RECUPERAÇÃO")
        self._definir_media(6.0)
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 7, 7),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            id_mat_a = self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"])
        self._definir_media(7.0)
        self._login(rede["inep"])
        self.assertEqual(self.client.post("/api/turmas", json={
            "nome": "6º Ano A", "ano_letivo": 2027, "etapa": "fundamental",
        }).status_code, 200)
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º Ano A")
        self.assertEqual(self.client.post(
            f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027},
        ).status_code, 200)
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027,
            "disciplina_id": rede["mat_id"],
        })
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            turma_2027, rede["mat_id"], self._notas_ano(rede["aluno_id"], 5, 5),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{turma_2027}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            id_mat_b = self._mat_id_aluno(rede["aluno_id"], turma_2027)
            hist_a, dados_a = self._hist_json(id_mat_a)
            hist_b, dados_b = self._hist_json(id_mat_b)
            vals_a = [n["val"] for n in dados_a["notas"] if n["disc"] == rede["mat_id"]]
            vals_b = [n["val"] for n in dados_b["notas"] if n["disc"] == rede["mat_id"]]
            self.assertTrue(all(v == 7.0 for v in vals_a))
            self.assertTrue(all(v == 5.0 for v in vals_b))
            self.assertNotIn(5.0, vals_a)
            self.assertNotIn(7.0, vals_b)
            item_a = self._item_disc(dados_a, rede["mat_id"])
            item_b = self._item_disc(dados_b, rede["mat_id"])
            self.assertEqual(item_a["resultado"], esperado_x["situacao"])
            self.assertEqual(item_b["resultado"], esperado_y["situacao"])
            self.assertEqual(dados_a["criterios_academicos"]["media_minima"], 6.0)
            self.assertEqual(dados_b["criterios_academicos"]["media_minima"], 7.0)
            self.assertEqual(dados_a["turma"]["ano_letivo"], 2026)
            self.assertEqual(dados_b["turma"]["ano_letivo"], 2027)
            self.assertEqual(hist_a.matricula_id, id_mat_a)
            self.assertEqual(hist_b.matricula_id, id_mat_b)
            self.assertNotEqual(id_mat_a, id_mat_b)

    def test_ei_nao_recebe_aprovado_reprovado(self):
        rede = self._rede(infantil=True)
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_parecer(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Parecer EI 2026"),
        ]).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            hist, dados = self._hist_json(self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"]))
            self.assertEqual(dados["pareceres"][0]["obs"], "Parecer EI 2026")
            self.assertEqual(dados["disciplinas"], [])
            self.assertIsNone(dados["resultado_academico"])
            self.assertEqual(dados["criterios_academicos"], {})
            self.assertEqual(dados["turma"]["etapa"], "infantil")
            self.assertEqual(hist.situacao_final, "ativo")
            bruto = hist.dados_fechamento
            self.assertNotIn("APROVADO", bruto)
            self.assertNotIn("REPROVADO", bruto)
            self.assertNotIn("EM RECUPERAÇÃO", bruto)

    def test_snapshot_legado_continua_legivel(self):
        from src.historico import carregar_dados_fechamento
        payload = {
            "notas": [{"disc": 1, "bim": 11, "val": 9.0}],
            "faltas": [{"disc": 1, "bim": 1, "qtd": 4}],
        }
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Escola, HistoricoAnual, Turma
            escola = Escola(
                nome="Escola Legado Snap", inep="78100001",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add(escola)
            db.session.flush()
            turma = Turma(
                nome="5º Ano Legado", ano_letivo=2025, status="concluida",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            db.session.add(turma)
            db.session.flush()
            aluno = Aluno(
                nome="Aluno Legado", cpf="78100000001", situacao="ativo",
                turma_id=None, escola_id=escola.id, nome_mae="Mãe Legado",
            )
            db.session.add(aluno)
            db.session.flush()
            hist = HistoricoAnual(
                ano_letivo=2025, aluno_id=aluno.id, matricula_id=None,
                escola_nome=escola.nome, turma_nome=turma.nome,
                etapa="fundamental", situacao_final="ativo",
                dados_fechamento=json.dumps(payload),
            )
            db.session.add(hist)
            db.session.commit()
            lido = carregar_dados_fechamento(hist)
            self.assertEqual(lido["notas"], payload["notas"])
            self.assertEqual(lido["faltas"], payload["faltas"])
            self.assertEqual(lido["frequencias"], [])
            self.assertEqual(lido["pareceres"], [])

    def test_snapshot_legado_nao_enriquecido_com_dados_atuais(self):
        from src.historico import backfill_historicos_anuais, carregar_dados_fechamento
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Disciplina, Escola, HistoricoAnual, Matricula, Turma
            disc = Disciplina.query.filter_by(nome="Matemática").one()
            nome_antes = disc.nome
            escola = Escola(
                nome="Escola Legado Frio", inep="78100002",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add(escola)
            db.session.flush()
            turma = Turma(
                nome="5º Ano Frio", ano_letivo=2024, status="concluida",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            db.session.add(turma)
            db.session.flush()
            aluno = Aluno(
                nome="Aluno Frio", cpf="78100000002", situacao="ativo",
                turma_id=None, escola_id=escola.id, nome_mae="Mãe Frio",
            )
            db.session.add(aluno)
            db.session.flush()
            db.session.add(Matricula(
                aluno_id=aluno.id, turma_id=turma.id,
                data_inicio=date(2024, 2, 1), data_fim=date(2024, 12, 15),
                situacao="concluida",
            ))
            payload = json.dumps({
                "notas": [{"disc": disc.id, "bim": 11, "val": 8.0}],
                "faltas": [{"disc": disc.id, "bim": 1, "qtd": 2}],
            })
            db.session.add(HistoricoAnual(
                ano_letivo=2024, aluno_id=aluno.id, matricula_id=None,
                escola_nome="Escola no Snapshot Antigo", turma_nome="5º Ano Frio",
                etapa="fundamental", situacao_final="ativo",
                dados_fechamento=payload,
            ))
            db.session.commit()
            disc.nome = "Matemática Geral"
            escola.nome = "Escola Legado Quente"
            turma.nome = "Turma Atual Distinta"
            db.session.commit()
            json_antes = HistoricoAnual.query.filter_by(aluno_id=aluno.id).one().dados_fechamento
            backfill_historicos_anuais()
            hist = HistoricoAnual.query.filter_by(aluno_id=aluno.id).one()
            self.assertEqual(hist.dados_fechamento, json_antes)
            self.assertEqual(hist.dados_fechamento, payload)
            lido = carregar_dados_fechamento(hist)
            self.assertNotIn("disciplinas", json.loads(hist.dados_fechamento))
            self.assertNotIn("aluno", json.loads(hist.dados_fechamento))
            self.assertNotIn("Matemática Geral", hist.dados_fechamento)
            self.assertNotIn("Escola Legado Quente", hist.dados_fechamento)
            self.assertNotIn("disciplina_nome", hist.dados_fechamento)
            self.assertEqual(lido["notas"][0]["disc"], disc.id)
            self.assertEqual(hist.escola_nome, "Escola no Snapshot Antigo")
            disc.nome = nome_antes
            db.session.commit()

    def test_alteracao_nota_posterior_nao_altera_snapshot(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 7, 7),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src import db
            from src.models import RegistroNota
            id_mat = self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"])
            hist, dados = self._hist_json(id_mat)
            json_antes = hist.dados_fechamento
            resultado = self._item_disc(dados, rede["mat_id"])["resultado"]
            self.assertEqual(resultado, "APROVADO")
            for nota in RegistroNota.query.filter_by(matricula_id=id_mat).all():
                nota.valor = 1.0
            db.session.commit()
            hist2, dados2 = self._hist_json(id_mat)
            self.assertEqual(hist2.dados_fechamento, json_antes)
            self.assertEqual(self._item_disc(dados2, rede["mat_id"])["resultado"], "APROVADO")
            self.assertEqual(self._item_disc(dados2, rede["mat_id"])["ma"], 7.0)

    def test_reencerramento_nao_recalcula_snapshot(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 7, 7),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src import db
            from src.models import ConfiguracaoAcademica, Disciplina, Escola, Turma
            id_mat = self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"])
            hist, dados = self._hist_json(id_mat)
            json_antes = hist.dados_fechamento
            db.session.get(Disciplina, rede["mat_id"]).nome = "Matemática Geral"
            db.session.get(Escola, rede["escola_id"]).nome = "Outra Escola"
            db.session.get(Turma, rede["turma_2026"]).nome = "Turma Nova"
            cfg = ConfiguracaoAcademica.query.order_by(ConfiguracaoAcademica.id).first()
            cfg.media_minima = 9.0
            db.session.commit()
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src.models import HistoricoAnual
            hists = HistoricoAnual.query.filter_by(matricula_id=id_mat).all()
            self.assertEqual(len(hists), 1)
            self.assertEqual(hists[0].dados_fechamento, json_antes)
            dados2 = _dados(hists[0])
            self.assertEqual(self._item_disc(dados2, rede["mat_id"])["disciplina_nome"], "Matemática")
            self.assertEqual(dados2["criterios_academicos"]["media_minima"], 6.0)
            self.assertEqual(dados2["escola"]["nome"], rede["escola_nome"])
            self.assertEqual(dados2["turma"]["nome"], rede["turma_nome"])

    def test_falha_durante_montagem_mantem_rollback_integral(self):
        rede = self._rede(maria=True)
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"],
            self._notas_ano(rede["aluno_id"], 7, 7) + self._notas_ano(rede["maria_id"], 5, 5),
        ).status_code, 200)
        from src.notas import calcular_notas_matricula as original

        def flaky(matricula_id, disciplina_id):
            from src import db
            from src.models import Matricula
            mat = db.session.get(Matricula, matricula_id)
            if mat is not None and mat.aluno is not None and mat.aluno.nome == "Maria":
                raise RuntimeError("falha simulada no motor de Maria")
            return original(matricula_id, disciplina_id)

        self._login(rede["inep"])
        with patch("src.notas.calcular_notas_matricula", side_effect=flaky):
            resp = self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar")
        self.assertEqual(resp.status_code, 500)
        with self.app.app_context():
            from src import db
            from src.models import Aluno, HistoricoAnual, Matricula, Turma
            joao = db.session.get(Aluno, rede["aluno_id"])
            maria = db.session.get(Aluno, rede["maria_id"])
            self.assertEqual(HistoricoAnual.query.filter_by(aluno_id=joao.id).count(), 0)
            self.assertEqual(HistoricoAnual.query.filter_by(aluno_id=maria.id).count(), 0)
            self.assertEqual(
                Matricula.query.filter_by(aluno_id=joao.id, situacao="cursando").count(), 1,
            )
            self.assertEqual(
                Matricula.query.filter_by(aluno_id=maria.id, situacao="cursando").count(), 1,
            )
            self.assertEqual(db.session.get(Turma, rede["turma_2026"]).status, "ativa")

    def test_snapshot_novo_nao_adiciona_pii_desnecessaria(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            hist, dados = self._hist_json(self._mat_id_aluno(rede["aluno_id"], rede["turma_2026"]))
            chaves = _chaves_json(dados)
            indevidas = CHAVES_PII_INDEVIDAS.intersection(chaves)
            self.assertEqual(indevidas, set())
            self.assertNotIn(rede["cpf_joao"], hist.dados_fechamento)
            self.assertNotIn("Mãe Principal", hist.dados_fechamento)
            self.assertEqual(set(dados["aluno"].keys()), {"id", "nome"})
            self.assertIn("notas", dados)
            self.assertIn("faltas", dados)
            self.assertIn("frequencias", dados)
