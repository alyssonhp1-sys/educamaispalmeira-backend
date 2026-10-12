import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria, texto_pdf


EIXO_A = "Eu, o outro e o nós"
EIXO_B = "Corpo, gestos e movimentos"


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class ParecerPorMatriculaTest(unittest.TestCase):
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
        ParecerPorMatriculaTest._seq += 1
        return ParecerPorMatriculaTest._seq

    def _rede_ana(self, beatriz=False):
        n = self._proximo()
        inep = f"7600{n:04d}"
        cpf_prof = f"7601{n:07d}"[:11]
        cpf_ana = f"7602{n:07d}"[:11]
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola EI {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof EI", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        self.client.post("/api/turmas", json={
            "nome": "Pré II 2026", "ano_letivo": 2026, "etapa": "infantil",
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2026 = dash["turmas"][0]["id"]
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": "Ana", "cpf": cpf_ana, "turma_id": turma_2026, "nome_mae": "Mãe Ana",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aluno_id = _json(resp)[0]["id"]
        beatriz_id = None
        if beatriz:
            cpf_b = f"7603{n:07d}"[:11]
            resp = self.client.post("/api/alunos", json={
                "nome": "Beatriz", "cpf": cpf_b, "turma_id": turma_2026, "nome_mae": "Mãe Beatriz",
            })
            self.assertEqual(resp.status_code, 200, _json(resp)[1])
            beatriz_id = _json(resp)[0]["id"]
        resp = self.client.post("/api/alocacoes", json={
            "professor_id": prof_id, "turma_id": turma_2026,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        concluir_primeiro_acesso(self.client, cpf_prof, temp_prof)
        return {
            "inep": inep,
            "cpf_prof": cpf_prof,
            "turma_2026": turma_2026,
            "aluno_id": aluno_id,
            "beatriz_id": beatriz_id,
            "prof_id": prof_id,
        }

    def _parecer(self, aluno_id, texto, eixo=EIXO_A, bimestre=1, avaliacao="Em Desenvolvimento"):
        return {
            "aluno_id": aluno_id,
            "eixo": eixo,
            "bimestre": bimestre,
            "avaliacao": avaliacao,
            "observacao": texto,
        }

    def _salvar(self, turma_id, payload):
        return self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/0/notas",
            json=payload,
        )

    def _mon(self, turma_id, aluno_id):
        dados, err = _json(self.client.get(
            f"/api/monitoramento/turma/{turma_id}/disciplina/0"
        ))
        self.assertIsNone(err)
        return next(a for a in dados if a["id"] == aluno_id)

    def _obs(self, turma_id, aluno_id, eixo=EIXO_A, bimestre=1):
        mon = self._mon(turma_id, aluno_id)
        p = next(
            (x for x in mon["pareceres"] if x["eixo"] == eixo and int(x["bim"]) == int(bimestre)),
            None,
        )
        return None if p is None else p["obs"]

    def _no_banco(self, matricula_id, eixo=EIXO_A, bimestre=1):
        from src.models import RelatorioDescritivo
        return RelatorioDescritivo.query.filter_by(
            matricula_id=matricula_id, eixo=eixo, bimestre=bimestre,
        ).first()

    def test_novo_parecer_recebe_matricula_id(self):
        rede = self._rede_ana()
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Parecer 2026"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula, RelatorioDescritivo
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertIsNotNone(mat)
            reg = RelatorioDescritivo.query.filter_by(aluno_id=rede["aluno_id"]).first()
            self.assertIsNotNone(reg.matricula_id)
            self.assertEqual(reg.matricula_id, mat.id)
            self.assertEqual(reg.aluno_id, mat.aluno_id)
            self.assertEqual(reg.observacao, "Parecer 2026")
            self.assertNotIn("disciplina_id", RelatorioDescritivo.__table__.c)

    def test_cenario_2026_2027_coexistem_e_edicao_isolada(self):
        rede = self._rede_ana()
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Parecer 2026"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.assertEqual(self._obs(rede["turma_2026"], rede["aluno_id"]), "Parecer 2026")

        with self.app.app_context():
            from src.models import Matricula
            mat_a = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            id_mat_a = mat_a.id

        self._login(rede["inep"])
        resp = self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self.client.post("/api/turmas", json={
            "nome": "Pré II 2027", "ano_letivo": 2027, "etapa": "infantil",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "Pré II 2027")
        resp = self.client.post(
            f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027,
        })

        self._login(rede["cpf_prof"])
        resp = self._salvar(turma_2027, [
            self._parecer(rede["aluno_id"], "Parecer 2027"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            from src.models import Matricula, RelatorioDescritivo
            mat_a = self._no_banco(id_mat_a)
            mat_b = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
            ).first()
            self.assertIsNotNone(mat_b)
            self.assertNotEqual(id_mat_a, mat_b.id)
            self.assertEqual(mat_a.observacao, "Parecer 2026")
            self.assertEqual(self._no_banco(mat_b.id).observacao, "Parecer 2027")
            self.assertEqual(
                RelatorioDescritivo.query.filter_by(aluno_id=rede["aluno_id"]).count(), 2,
            )
            id_mat_b = mat_b.id

        resp = self._salvar(turma_2027, [
            self._parecer(rede["aluno_id"], "Parecer 2027 revisado"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

        with self.app.app_context():
            self.assertEqual(self._no_banco(id_mat_a).observacao, "Parecer 2026")
            self.assertEqual(self._no_banco(id_mat_b).observacao, "Parecer 2027 revisado")

        self.assertEqual(self._obs(rede["turma_2026"], rede["aluno_id"]), "Parecer 2026")
        self.assertEqual(self._obs(turma_2027, rede["aluno_id"]), "Parecer 2027 revisado")

        resp = self.client.get(f"/api/alunos/{rede['aluno_id']}/documentos/parecer.pdf")
        self.assertEqual(resp.status_code, 200)
        html = texto_pdf(resp)
        self.assertIn("Parecer 2027 revisado", html)
        self.assertNotIn("Parecer 2026", html)

    def test_apagar_2027_nao_altera_2026(self):
        rede = self._rede_ana()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Texto antigo"),
        ]).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        self.client.post("/api/turmas", json={
            "nome": "Pré II 2027", "ano_letivo": 2027, "etapa": "infantil",
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "Pré II 2027")
        self.client.post(
            f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027},
        )
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027,
        })
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar(turma_2027, [
            self._parecer(rede["aluno_id"], "Texto atual"),
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
        resp = self._salvar(turma_2027, [
            self._parecer(rede["aluno_id"], "Texto atual", avaliacao=""),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertIsNone(self._no_banco(id_b))
            self.assertEqual(self._no_banco(id_a).observacao, "Texto antigo")

    def test_ana_nao_altera_beatriz(self):
        rede = self._rede_ana(beatriz=True)
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Texto Ana"),
            self._parecer(rede["beatriz_id"], "Texto Beatriz"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Texto Ana", avaliacao=""),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula
            mat_a = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            mat_b = Matricula.query.filter_by(
                aluno_id=rede["beatriz_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertIsNone(self._no_banco(mat_a.id))
            self.assertEqual(self._no_banco(mat_b.id).observacao, "Texto Beatriz")

    def test_eixo_a_nao_altera_eixo_b(self):
        rede = self._rede_ana()
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Texto A", eixo=EIXO_A),
            self._parecer(rede["aluno_id"], "Texto B", eixo=EIXO_B),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Texto A", eixo=EIXO_A, avaliacao=""),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertIsNone(self._no_banco(mat.id, eixo=EIXO_A))
            self.assertEqual(self._no_banco(mat.id, eixo=EIXO_B).observacao, "Texto B")

    def test_bimestre_1_nao_altera_bimestre_2(self):
        rede = self._rede_ana()
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Texto 1", bimestre=1),
            self._parecer(rede["aluno_id"], "Texto 2", bimestre=2),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Texto 1", bimestre=1, avaliacao=""),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertIsNone(self._no_banco(mat.id, bimestre=1))
            self.assertEqual(self._no_banco(mat.id, bimestre=2).observacao, "Texto 2")

    def test_matricula_invalida_rejeitada(self):
        rede = self._rede_ana()
        n = self._proximo()
        self._login(rede["inep"])
        resp = self.client.post("/api/alunos", json={
            "nome": "Carla Sem Turma",
            "cpf": f"7609{n:07d}"[:11],
            "nome_mae": "Mãe Carla",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        carla_id = _json(resp)[0]["id"]
        with self.app.app_context():
            from src.models import Aluno
            from src import db
            carla = db.session.get(Aluno, carla_id)
            carla.turma_id = rede["turma_2026"]
            db.session.commit()
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], [
            self._parecer(carla_id, "Não deveria gravar"),
        ])
        self.assertEqual(resp.status_code, 400)
        with self.app.app_context():
            from src.models import RelatorioDescritivo
            self.assertEqual(RelatorioDescritivo.query.filter_by(aluno_id=carla_id).count(), 0)

    def test_aluno_matricula_divergente_rejeitado(self):
        rede = self._rede_ana(beatriz=True)
        with self.app.app_context():
            from src.matriculas import MSG_ALUNO_MATRICULA_DIVERGENTE, garantir_aluno_da_matricula
            from src.models import Matricula
            from src.pareceres import upsert_parecer_matricula
            from src import db
            mat_ana = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            with self.assertRaises(ValueError) as ctx:
                garantir_aluno_da_matricula(rede["beatriz_id"], mat_ana)
            self.assertEqual(str(ctx.exception), MSG_ALUNO_MATRICULA_DIVERGENTE)
            parecer = upsert_parecer_matricula(
                mat_ana, EIXO_A, 1, "Em Desenvolvimento", "Só Ana",
            )
            self.assertEqual(parecer.aluno_id, mat_ana.aluno_id)
            self.assertEqual(parecer.matricula_id, mat_ana.id)
            db.session.rollback()

    def _payload_frontend(self, aluno_id, avaliacao, observacao="", eixo=EIXO_A, bimestre="1"):
        """Formato real do DiarioInfantil após a correção (bimestre string, null = Não Avaliado)."""
        return {
            "aluno_id": aluno_id,
            "eixo": eixo,
            "bimestre": bimestre,
            "avaliacao": avaliacao,
            "observacao": observacao,
        }

    def test_rollback_multi_item_nenhum_parcial(self):
        rede = self._rede_ana()
        n = self._proximo()
        self._login(rede["inep"])
        resp = self.client.post("/api/alunos", json={
            "nome": "Carla Sem Turma",
            "cpf": f"7609{n:07d}"[:11],
            "nome_mae": "Mãe Carla",
        })
        carla_id = _json(resp)[0]["id"]
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Eixo válido", eixo=EIXO_A),
            self._parecer(carla_id, "Eixo inválido", eixo=EIXO_B),
        ])
        self.assertEqual(resp.status_code, 400)
        with self.app.app_context():
            from src.models import RelatorioDescritivo
            self.assertEqual(RelatorioDescritivo.query.filter_by(aluno_id=rede["aluno_id"]).count(), 0)
            self.assertEqual(RelatorioDescritivo.query.filter_by(aluno_id=carla_id).count(), 0)

    def test_payload_frontend_null_remove_somente_a_celula(self):
        rede = self._rede_ana()
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], [
            self._payload_frontend(
                rede["aluno_id"], "Em Desenvolvimento", "Obs que some com o registro",
            ),
            self._payload_frontend(
                rede["aluno_id"], "Desenvolvido", "Texto B",
                eixo=EIXO_B, bimestre="1",
            ),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(rede["turma_2026"], [
            self._payload_frontend(
                rede["aluno_id"], None, "Obs que some com o registro",
            ),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(rede["turma_2026"], [
            self._payload_frontend(rede["aluno_id"], None, ""),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertIsNone(self._no_banco(mat.id, eixo=EIXO_A))
            self.assertEqual(self._no_banco(mat.id, eixo=EIXO_B).observacao, "Texto B")

    def test_item_omitido_nao_apaga(self):
        rede = self._rede_ana(beatriz=True)
        self._login(rede["cpf_prof"])
        resp = self._salvar(rede["turma_2026"], [
            self._payload_frontend(rede["aluno_id"], "Em Desenvolvimento", "Texto Ana"),
            self._payload_frontend(rede["beatriz_id"], "Desenvolvido", "Texto Beatriz"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._salvar(rede["turma_2026"], [
            self._payload_frontend(rede["beatriz_id"], "Consolidado", "Texto Beatriz editado"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula
            mat_a = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            mat_b = Matricula.query.filter_by(
                aluno_id=rede["beatriz_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertEqual(self._no_banco(mat_a.id).observacao, "Texto Ana")
            self.assertEqual(self._no_banco(mat_a.id).avaliacao, "Em Desenvolvimento")
            self.assertEqual(self._no_banco(mat_b.id).observacao, "Texto Beatriz editado")
            self.assertEqual(self._no_banco(mat_b.id).avaliacao, "Consolidado")

    def test_limpar_null_2027_nao_apaga_2026(self):
        rede = self._rede_ana()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar(rede["turma_2026"], [
            self._payload_frontend(rede["aluno_id"], "Em Desenvolvimento", "Parecer 2026"),
        ]).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_2026']}/encerrar").status_code, 200,
        )
        self.client.post("/api/turmas", json={
            "nome": "Pré II 2027", "ano_letivo": 2027, "etapa": "infantil",
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "Pré II 2027")
        self.client.post(
            f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027},
        )
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027,
        })
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar(turma_2027, [
            self._payload_frontend(rede["aluno_id"], "Desenvolvido", "Parecer 2027"),
        ]).status_code, 200)
        resp = self._salvar(turma_2027, [
            self._payload_frontend(rede["aluno_id"], None, "Parecer 2027"),
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Matricula
            mat_a = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            mat_b = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=turma_2027,
            ).first()
            self.assertEqual(self._no_banco(mat_a.id).observacao, "Parecer 2026")
            self.assertEqual(self._no_banco(mat_a.id).avaliacao, "Em Desenvolvimento")
            self.assertIsNone(self._no_banco(mat_b.id))

    def test_rollback_limpeza_nao_apaga_parcial(self):
        rede = self._rede_ana()
        n = self._proximo()
        self._login(rede["inep"])
        resp = self.client.post("/api/alunos", json={
            "nome": "Carla Sem Turma",
            "cpf": f"7609{n:07d}"[:11],
            "nome_mae": "Mãe Carla",
        })
        carla_id = _json(resp)[0]["id"]
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar(rede["turma_2026"], [
            self._payload_frontend(rede["aluno_id"], "Em Desenvolvimento", "Deve permanecer"),
        ]).status_code, 200)
        resp = self._salvar(rede["turma_2026"], [
            self._payload_frontend(rede["aluno_id"], None, ""),
            self._payload_frontend(carla_id, "Desenvolvido", "Inválido"),
        ])
        self.assertEqual(resp.status_code, 400)
        with self.app.app_context():
            from src.models import Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertEqual(self._no_banco(mat.id).observacao, "Deve permanecer")
            self.assertEqual(self._no_banco(mat.id).avaliacao, "Em Desenvolvimento")

    def test_cascade_e_protecao_nao_destroem_parecer(self):
        rede = self._rede_ana()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Parecer histórico"),
        ]).status_code, 200)
        with self.app.app_context():
            from sqlalchemy.orm import class_mapper
            from src import db
            from src.exclusao import matricula_possui_historico_academico
            from src.models import Matricula, RelatorioDescritivo
            rel = class_mapper(Matricula).relationships["relatorios_infantil"]
            self.assertFalse(rel.cascade.delete)
            self.assertFalse(rel.cascade.delete_orphan)
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_2026"],
            ).first()
            self.assertTrue(matricula_possui_historico_academico(mat))
            parecer = RelatorioDescritivo.query.filter_by(matricula_id=mat.id).first()
            parecer_id = parecer.id
            try:
                db.session.delete(mat)
                db.session.commit()
            except Exception:
                db.session.rollback()
            vivo = RelatorioDescritivo.query.filter_by(id=parecer_id).first()
            self.assertIsNotNone(vivo)
            self.assertEqual(vivo.observacao, "Parecer histórico")


class BackfillParecerTest(unittest.TestCase):
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

    def test_backfill_associa_quando_uma_matricula_e_e_idempotente(self):
        from src import create_app, db
        from src.models import Aluno, Escola, Matricula, RelatorioDescritivo, Turma
        from src.pareceres import (
            ULTIMO_RESUMO_BACKFILL_RELATORIOS,
            backfill_relatorios_descritivos,
        )
        app = create_app()
        with app.app_context():
            escola = Escola(
                nome="Escola Backfill EI", inep="76100001",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add(escola)
            db.session.flush()
            turma = Turma(
                nome="Pré Backfill", ano_letivo=2026, status="ativa",
                etapa_ensino="infantil", escola_id=escola.id,
            )
            db.session.add(turma)
            db.session.flush()
            aluno = Aluno(
                nome="Criança Uma Matricula", cpf="76100000001", situacao="ativo",
                turma_id=turma.id, escola_id=escola.id, nome_mae="Mãe Backfill",
            )
            db.session.add(aluno)
            db.session.flush()
            db.session.add(Matricula(
                aluno_id=aluno.id, turma_id=turma.id,
                data_inicio=date(2026, 2, 1), situacao="cursando",
            ))
            db.session.flush()
            db.session.add(RelatorioDescritivo(
                aluno_id=aluno.id, eixo=EIXO_A, bimestre=1,
                avaliacao="Em Desenvolvimento", observacao="Texto legado",
            ))
            db.session.commit()
            n_antes = RelatorioDescritivo.query.count()
            backfill_relatorios_descritivos()
            self.assertEqual(RelatorioDescritivo.query.count(), n_antes)
            reg = RelatorioDescritivo.query.filter_by(aluno_id=aluno.id).first()
            self.assertIsNotNone(reg.matricula_id)
            self.assertEqual(reg.observacao, "Texto legado")
            self.assertEqual(reg.eixo, EIXO_A)
            self.assertEqual(reg.bimestre, 1)
            self.assertEqual(ULTIMO_RESUMO_BACKFILL_RELATORIOS["associados"], 1)
            mat_id = reg.matricula_id
            backfill_relatorios_descritivos()
            self.assertEqual(RelatorioDescritivo.query.count(), n_antes)
            reg2 = RelatorioDescritivo.query.filter_by(aluno_id=aluno.id).first()
            self.assertEqual(reg2.matricula_id, mat_id)
            self.assertEqual(reg2.observacao, "Texto legado")
            self.assertEqual(ULTIMO_RESUMO_BACKFILL_RELATORIOS["associados"], 0)

    def test_backfill_ambiguo_nao_chuta_matricula_atual(self):
        from src import create_app, db
        from src.models import Aluno, Escola, Matricula, RelatorioDescritivo, Turma
        from src.pareceres import (
            ULTIMO_DIAGNOSTICO_BACKFILL_RELATORIOS,
            backfill_relatorios_descritivos,
        )
        app = create_app()
        with app.app_context():
            escola = Escola(
                nome="Escola Ambiguo EI", inep="76100002",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add(escola)
            db.session.flush()
            t1 = Turma(
                nome="Pré 2026", ano_letivo=2026, status="concluida",
                etapa_ensino="infantil", escola_id=escola.id,
            )
            t2 = Turma(
                nome="Pré 2027", ano_letivo=2027, status="ativa",
                etapa_ensino="infantil", escola_id=escola.id,
            )
            db.session.add_all([t1, t2])
            db.session.flush()
            aluno = Aluno(
                nome="Criança Ambiguo", cpf="76100000002", situacao="ativo",
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
            db.session.add(RelatorioDescritivo(
                aluno_id=aluno.id, eixo=EIXO_A, bimestre=1,
                avaliacao="Em Desenvolvimento", observacao="Texto antigo sem turma",
            ))
            db.session.commit()
            n_antes = RelatorioDescritivo.query.count()
            texto = "Texto antigo sem turma"
            backfill_relatorios_descritivos()
            reg = RelatorioDescritivo.query.filter_by(aluno_id=aluno.id).first()
            mat_atual = Matricula.query.filter_by(aluno_id=aluno.id, turma_id=t2.id).first()
            self.assertIsNone(reg.matricula_id)
            self.assertNotEqual(reg.matricula_id, mat_atual.id)
            self.assertEqual(reg.observacao, texto)
            self.assertEqual(RelatorioDescritivo.query.count(), n_antes)
            tipos = {d["tipo"] for d in ULTIMO_DIAGNOSTICO_BACKFILL_RELATORIOS}
            self.assertIn("ambiguo", tipos)
            self.assertTrue(any(
                d.get("motivo") == "multiplas_matriculas"
                for d in ULTIMO_DIAGNOSTICO_BACKFILL_RELATORIOS
            ))


if __name__ == "__main__":
    unittest.main()
