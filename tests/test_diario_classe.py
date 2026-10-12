import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class DiarioClasseTest(unittest.TestCase):
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

        cls.inep_a = "93000001"
        cls.inep_b = "93000002"
        cls.cpf_prof_a = "93011111111"
        cls.cpf_prof_ei = "93022222222"
        cls.cpf_prof_b = "93033333333"
        cls.login_coord_a = "coord930a"
        cls.login_coord_b = "coord930b"

        temps = {}
        for nome, inep in (("Escola Diário Norte", cls.inep_a), ("Escola Diário Sul", cls.inep_b)):
            resp = client.post("/api/escolas", json={
                "nome": nome, "inep": inep, "oferta_infantil": True, "oferta_fundamental": True,
            })
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[inep] = senha_temporaria(resp)

        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_a = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_a)
        cls.escola_b = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_b)

        for login, nome, escola_id in (
            (cls.login_coord_a, "Coord Norte", cls.escola_a),
            (cls.login_coord_b, "Coord Sul", cls.escola_b),
        ):
            resp = client.post(f"/api/escolas/{escola_id}/coordenadores", json={"login": login, "nome": nome})
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[login] = senha_temporaria(resp)
        for nome, cpf, escola_id in (
            ("Prof Diario A", cls.cpf_prof_a, cls.escola_a),
            ("Prof Diario EI", cls.cpf_prof_ei, cls.escola_a),
            ("Prof Diario B", cls.cpf_prof_b, cls.escola_b),
        ):
            resp = client.post("/api/professores", json={"nome": nome, "cpf": cpf, "escola_id": escola_id})
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[cpf] = senha_temporaria(resp)
        for login, temp in temps.items():
            concluir_primeiro_acesso(client, login, temp)

        cls._login_client(client, cls.inep_a, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "6º Ano Diario", "ano_letivo": 2026, "etapa": "fundamental"})
        client.post("/api/turmas", json={"nome": "Maternal Diario", "ano_letivo": 2026, "etapa": "infantil"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_a = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º Ano Diario")
        cls.turma_ei = next(t["id"] for t in dash["turmas"] if t["nome"] == "Maternal Diario")
        cls.prof_a_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_a)
        cls.prof_ei_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_ei)

        resp = client.post("/api/alunos", json={
            "nome": "Aluno Diario Um", "cpf": "93044444444",
            "turma_id": cls.turma_a, "nome_mae": "Mãe Um",
        })
        cls.aluno_a = _json(resp)[0]["id"]
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Diario Dois", "cpf": "93055555555",
            "turma_id": cls.turma_a, "nome_mae": "Mãe Dois",
        })
        cls.aluno_a2 = _json(resp)[0]["id"]
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Diario Tres", "cpf": "93066666666",
            "turma_id": cls.turma_a, "nome_mae": "Mãe Tres",
        })
        cls.aluno_a3 = _json(resp)[0]["id"]
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Inativo Diario", "cpf": "93077777777",
            "turma_id": cls.turma_a, "nome_mae": "Mãe Inativo",
        })
        cls.aluno_inativo = _json(resp)[0]["id"]
        resp = client.post("/api/alunos", json={
            "nome": "Aluno EI Diario", "cpf": "93088888888",
            "turma_id": cls.turma_ei, "nome_mae": "Mãe EI",
        })
        cls.aluno_ei = _json(resp)[0]["id"]

        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_a_id, "turma_id": cls.turma_a, "disciplina_id": cls.mat,
            "carga_horaria_prevista": 40,
        })
        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_ei_id, "turma_id": cls.turma_ei, "disciplina_id": cls.mat,
            "carga_horaria_prevista": 20,
        })

        from src.models import Aluno
        from src import db
        with cls.app.app_context():
            inativo = db.session.get(Aluno, cls.aluno_inativo)
            inativo.situacao = "inativo"
            db.session.commit()

        cls._login_client(client, cls.inep_b, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "6º Ano Sul Diario", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_b = dash["turmas"][0]["id"]
        cls.prof_b_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_b)
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Diario Sul", "cpf": "93099999999",
            "turma_id": cls.turma_b, "nome_mae": "Mãe Sul",
        })
        cls.aluno_b = _json(resp)[0]["id"]
        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_b_id, "turma_id": cls.turma_b, "disciplina_id": cls.mat,
            "carga_horaria_prevista": 40,
        })

        cls.data_aula = "2026-04-10"
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
        return f"/api/turmas/{turma_id}/disciplinas/{disc_id if disc_id is not None else self.mat}/registros-aula"

    def _payload(self, situacoes=None, quantidade=2, conteudo="Conteúdo da aula", metodologia="Expositiva", **extra):
        body = {
            "data": extra.pop("data", self.data_aula),
            "bimestre": extra.pop("bimestre", self.bimestre),
            "quantidade_aulas": quantidade,
            "conteudo": conteudo,
            "metodologia": metodologia,
            "observacao": extra.pop("observacao", "Obs opcional"),
            "lancamentos": [{"aluno_id": aid, "situacao": sit} for aid, sit in (situacoes or [])],
        }
        body.update(extra)
        return body

    def test_01_professor_registra_aula_da_propria_alocacao(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload([
            (self.aluno_a, "PRESENTE"),
            (self.aluno_a2, "PRESENTE"),
            (self.aluno_a3, "PRESENTE"),
        ], data="2026-04-01"))
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        self.assertEqual(data["status"], "pendente")
        self.assertEqual(data["quantidadeAulas"], 2)
        self.assertEqual(data["conteudo"], "Conteúdo da aula")
        self.assertEqual(data["metodologia"], "Expositiva")
        self.assertEqual(data["presentes"], 3)
        self.assertEqual(data["faltosos"], 0)
        self.aula_ok_id = data["id"]

    def test_02_professor_nao_registra_aula_de_outra_alocacao(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_b), json=self._payload([
            (self.aluno_b, "PRESENTE"),
        ], data="2026-04-02"))
        self._assert_403(resp)
        resp = self.client.post(self._url(self.turma_ei, 0), json=self._payload([
            (self.aluno_ei, "PRESENTE"),
        ], data="2026-04-02"))
        self._assert_403(resp)

    def test_03_todos_comecam_p(self):
        self._login(self.cpf_prof_a)
        resp = self.client.get(self._url(self.turma_a))
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        ids = [a["id"] for a in data["alunos"]]
        self.assertIn(self.aluno_a, ids)
        self.assertTrue(all(a["situacao"] == "PRESENTE" for a in data["alunos"]))
        self.assertEqual(data["presentes"], len(data["alunos"]))
        self.assertEqual(data["faltosos"], 0)

    def test_04_aluno_pode_ser_marcado_f(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload([
            (self.aluno_a, "FALTA"),
            (self.aluno_a2, "PRESENTE"),
            (self.aluno_a3, "PRESENTE"),
        ], data="2026-04-03"))
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        self.assertEqual(data["faltosos"], 1)
        self.assertEqual(data["presentes"], 2)
        mapa = {f["alunoId"]: f["situacao"] for f in data["frequencia"]}
        self.assertEqual(mapa[self.aluno_a], "FALTA")
        self.assertEqual(mapa[self.aluno_a2], "PRESENTE")

    def test_05_salva_conteudo_metodologia_frequencia_pendente(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload(
            [(self.aluno_a, "FALTA"), (self.aluno_a2, "PRESENTE"), (self.aluno_a3, "PRESENTE")],
            data="2026-04-04",
            conteudo="Frações equivalentes",
            metodologia="Resolução de problemas",
            observacao="Revisar amanhã",
            quantidade=3,
        ))
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aula_id = _json(resp)[0]["id"]
        detalhe, _ = _json(self.client.get(f"/api/registros-aula/{aula_id}"))
        self.assertEqual(detalhe["status"], "pendente")
        self.assertEqual(detalhe["conteudo"], "Frações equivalentes")
        self.assertEqual(detalhe["metodologia"], "Resolução de problemas")
        self.assertEqual(detalhe["observacao"], "Revisar amanhã")
        self.assertEqual(detalhe["quantidadeAulas"], 3)
        self.assertTrue(any(f["situacao"] == "FALTA" for f in detalhe["frequencia"]))
        lista, _ = _json(self.client.get(self._url(self.turma_a)))
        item = next(r for r in lista["registros"] if r["id"] == aula_id)
        self.assertEqual(item["status"], "pendente")

    def test_06_quantidade_zero_recusada(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload(
            [(self.aluno_a, "PRESENTE")], data="2026-04-05", quantidade=0,
        ))
        self.assertEqual(resp.status_code, 400)

    def test_07_coordenador_da_escola_valida(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload(
            [(self.aluno_a, "PRESENTE"), (self.aluno_a2, "PRESENTE"), (self.aluno_a3, "PRESENTE")],
            data="2026-04-06", quantidade=2,
        ))
        aula_id = _json(resp)[0]["id"]
        self._login(self.login_coord_a)
        resp = self.client.post(f"/api/coordenacao/registros-aula/{aula_id}", json={"status": "validada"})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.assertEqual(_json(resp)[0]["status"], "validada")
        self._login(self.cpf_prof_a)
        detalhe, _ = _json(self.client.get(f"/api/registros-aula/{aula_id}"))
        self.assertEqual(detalhe["status"], "validada")

    def test_08_coordenador_de_outra_escola_nao_valida(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload(
            [(self.aluno_a, "PRESENTE"), (self.aluno_a2, "PRESENTE"), (self.aluno_a3, "PRESENTE")],
            data="2026-04-07", quantidade=1,
        ))
        aula_id = _json(resp)[0]["id"]
        self._login(self.login_coord_b)
        resp = self.client.post(f"/api/coordenacao/registros-aula/{aula_id}", json={"status": "validada"})
        self._assert_403(resp)

    def test_09_invalidacao_exige_motivo_e_professor_ve(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload(
            [(self.aluno_a, "PRESENTE"), (self.aluno_a2, "PRESENTE"), (self.aluno_a3, "PRESENTE")],
            data="2026-04-08", quantidade=4, conteudo="Aula a invalidar",
        ))
        aula_id = _json(resp)[0]["id"]
        self._login(self.login_coord_a)
        resp = self.client.post(f"/api/coordenacao/registros-aula/{aula_id}", json={"status": "invalidada"})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(
            f"/api/coordenacao/registros-aula/{aula_id}",
            json={"status": "invalidada", "motivo": "Conteúdo incompatível com a habilidade."},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._login(self.cpf_prof_a)
        detalhe, _ = _json(self.client.get(f"/api/registros-aula/{aula_id}"))
        self.assertEqual(detalhe["status"], "invalidada")
        self.assertIn("incompatível", (detalhe["motivoInvalidacao"] or "").lower())

    def test_10_invalidada_nao_conta_validada_conta_carga(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload(
            [(self.aluno_a, "PRESENTE"), (self.aluno_a2, "PRESENTE"), (self.aluno_a3, "PRESENTE")],
            data="2026-04-09", quantidade=5, conteudo="Carga invalidada",
        ))
        inv_id = _json(resp)[0]["id"]
        resp = self.client.post(self._url(self.turma_a), json=self._payload(
            [(self.aluno_a, "PRESENTE"), (self.aluno_a2, "PRESENTE"), (self.aluno_a3, "PRESENTE")],
            data="2026-04-11", quantidade=7, conteudo="Carga validada",
        ))
        val_id = _json(resp)[0]["id"]
        self._login(self.login_coord_a)
        self.client.post(f"/api/coordenacao/registros-aula/{inv_id}", json={
            "status": "invalidada", "motivo": "Fora do plano.",
        })
        self.client.post(f"/api/coordenacao/registros-aula/{val_id}", json={"status": "validada"})
        self._login(self.inep_a)
        acomp, _ = _json(self.client.get("/api/acompanhamento-pedagogico"))
        item = next(i for i in acomp["itens"] if i["turmaId"] == self.turma_a and i["disciplinaId"] == self.mat)
        self.assertGreaterEqual(item["aulasInvalidadas"], 5)
        self.assertGreaterEqual(item["aulasValidadas"], 7)
        self.assertEqual(item["cargaPrevista"], 40)
        self.assertIsNotNone(item["percentualCumprido"])

    def test_11_frequencia_gera_total_de_faltas_apos_validacao(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload(
            [(self.aluno_a, "FALTA"), (self.aluno_a2, "PRESENTE"), (self.aluno_a3, "PRESENTE")],
            data="2026-04-12", quantidade=1, conteudo="Falta oficial",
        ))
        aula_id = _json(resp)[0]["id"]
        mon_antes, _ = _json(self.client.get(f"/api/monitoramento/turma/{self.turma_a}/disciplina/{self.mat}"))
        faltas_antes = next(x for x in mon_antes if x["id"] == self.aluno_a)["resumo"]["faltas"]
        self._login(self.login_coord_a)
        self.client.post(f"/api/coordenacao/registros-aula/{aula_id}", json={"status": "validada"})
        self._login(self.cpf_prof_a)
        mon, _ = _json(self.client.get(f"/api/monitoramento/turma/{self.turma_a}/disciplina/{self.mat}"))
        faltas = next(x for x in mon if x["id"] == self.aluno_a)["resumo"]["faltas"]
        self.assertGreaterEqual(faltas, faltas_antes + 1)

    def test_12_aluno_inativo_nao_entra_na_chamada(self):
        self._login(self.cpf_prof_a)
        data, _ = _json(self.client.get(self._url(self.turma_a)))
        ids = [a["id"] for a in data["alunos"]]
        self.assertNotIn(self.aluno_inativo, ids)
        resp = self.client.post(self._url(self.turma_a), json=self._payload(
            [(self.aluno_inativo, "FALTA")], data="2026-04-13",
        ))
        self.assertEqual(resp.status_code, 400)

    def test_13_gestor_ve_indicadores_da_propria_escola(self):
        self._login(self.inep_a)
        dash, err = _json(self.client.get("/api/dashboard"))
        self.assertIsNone(err)
        self.assertEqual(dash["painel"], "escola")
        self.assertIn("acompanhamentoPedagogico", dash)
        ids_turma = {i["turmaId"] for i in dash["acompanhamentoPedagogico"]["itens"]}
        self.assertIn(self.turma_a, ids_turma)
        self.assertNotIn(self.turma_b, ids_turma)
        acomp, _ = _json(self.client.get("/api/acompanhamento-pedagogico"))
        self.assertEqual(acomp["escopo"], "escola")
        self.assertEqual(acomp["escolaId"], self.escola_a)

    def test_14_gestor_nao_ve_outra_escola(self):
        self._login(self.inep_a)
        resp = self.client.get(f"/api/acompanhamento-pedagogico?escola_id={self.escola_b}")
        self._assert_403(resp)
        resp = self.client.get(f"/api/escolas/{self.escola_b}/painel")
        self._assert_403(resp)

    def test_15_secretaria_ve_rede(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        dash, err = _json(self.client.get("/api/dashboard"))
        self.assertIsNone(err)
        self.assertEqual(dash["painel"], "municipal")
        self.assertIn("escolas", dash["acompanhamentoPedagogico"])
        ids = {e["escolaId"] for e in dash["acompanhamentoPedagogico"]["escolas"]}
        self.assertIn(self.escola_a, ids)
        self.assertIn(self.escola_b, ids)
        acomp, _ = _json(self.client.get("/api/acompanhamento-pedagogico"))
        self.assertEqual(acomp["escopo"], "rede")
        escola, _ = _json(self.client.get(f"/api/acompanhamento-pedagogico?escola_id={self.escola_b}"))
        self.assertEqual(escola["escopo"], "escola")
        self.assertEqual(escola["escolaId"], self.escola_b)

    def test_16_professor_nao_ve_painel_gerencial(self):
        self._login(self.cpf_prof_a)
        dash, _ = _json(self.client.get("/api/dashboard"))
        self.assertEqual(dash["painel"], "professor")
        self.assertNotIn("acompanhamentoPedagogico", dash)
        self._assert_403(self.client.get("/api/acompanhamento-pedagogico"))
        self._assert_403(self.client.get(f"/api/escolas/{self.escola_a}/painel"))

    def test_17_coordenador_nao_ve_grafico_geral_de_gestao(self):
        self._login(self.login_coord_a)
        dash, _ = _json(self.client.get("/api/dashboard"))
        self.assertEqual(dash["painel"], "coordenador")
        self.assertIn("registrosAula", dash)
        self.assertNotIn("acompanhamentoPedagogico", dash)
        self._assert_403(self.client.get("/api/acompanhamento-pedagogico"))

    def test_18_professor_nao_emite_boletim_declaracoes(self):
        self._login(self.cpf_prof_a)
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/boletim.pdf"))
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/declaracao/cursando.pdf"))
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/comprovante-matricula.pdf"))

    def test_19_professor_emite_rendimento_da_propria_turma(self):
        self._login(self.cpf_prof_a)
        resp = self.client.get(f"/api/turmas/{self.turma_a}/documentos/rendimento/{self.mat}.pdf")
        self.assertEqual(resp.status_code, 200)
        self._assert_403(self.client.get(f"/api/turmas/{self.turma_b}/documentos/rendimento/{self.mat}.pdf"))

    def test_20_professor_ei_acessa_parecer_proprio(self):
        self._login(self.cpf_prof_ei)
        resp = self.client.get(f"/api/alunos/{self.aluno_ei}/documentos/parecer.pdf")
        self.assertEqual(resp.status_code, 200)
        self._assert_403(self.client.get(f"/api/alunos/{self.aluno_a}/documentos/parecer.pdf"))

    def test_21_coordenador_nao_edita_conteudo_nem_frequencia(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url(self.turma_a), json=self._payload(
            [(self.aluno_a, "PRESENTE"), (self.aluno_a2, "PRESENTE"), (self.aluno_a3, "PRESENTE")],
            data="2026-04-14", conteudo="Original intocado",
        ))
        aula_id = _json(resp)[0]["id"]
        original = _json(resp)[0]["conteudo"]
        self._login(self.login_coord_a)
        self.client.post(f"/api/coordenacao/registros-aula/{aula_id}", json={
            "status": "validada",
            "conteudo": "tentativa de edição",
            "lancamentos": [{"aluno_id": self.aluno_a, "situacao": "FALTA"}],
        })
        detalhe, _ = _json(self.client.get(f"/api/registros-aula/{aula_id}"))
        self.assertEqual(detalhe["conteudo"], original)
        self.assertTrue(all(f["situacao"] == "PRESENTE" for f in detalhe["frequencia"]))

    def test_22_schema_nao_destroi_tabelas_antigas(self):
        from src import create_app, db
        from src.models import Planejamento, RegistroAula, RegistroFalta, RegistroFrequencia, ensure_schema
        with self.app.app_context():
            n_plan = Planejamento.query.count()
            n_faltas = RegistroFalta.query.count()
            n_freq = RegistroFrequencia.query.count()
            n_aula = RegistroAula.query.count()
            db.create_all()
            ensure_schema()
            self.assertEqual(Planejamento.query.count(), n_plan)
            self.assertEqual(RegistroFalta.query.count(), n_faltas)
            self.assertEqual(RegistroFrequencia.query.count(), n_freq)
            self.assertEqual(RegistroAula.query.count(), n_aula)
        app2 = create_app()
        self.assertIsNotNone(app2)


if __name__ == "__main__":
    unittest.main()
