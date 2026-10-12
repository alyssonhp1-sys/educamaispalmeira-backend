import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.notas import calcular_de_notas
from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria, texto_pdf


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


def _mapa(n1, n2, rp=None, rs1=None, rs2=None, pf=None):
    dados = {
        11: n1, 12: n2,
        21: n1, 22: n2,
        31: n1, 32: n2,
        41: n1, 42: n2,
    }
    if rp is not None:
        dados[13] = dados[23] = dados[33] = dados[43] = rp
    if rs1 is not None:
        dados[51] = rs1
    if rs2 is not None:
        dados[52] = rs2
    if pf is not None:
        dados[99] = pf
    return dados


def _lancamentos(aluno_id, n1, n2, pf=None):
    payload = [
        {"aluno_id": aluno_id, "valor": n1 if i % 2 == 0 else n2, "tipo": "nota", "bimestre": bim}
        for i, bim in enumerate((11, 12, 21, 22, 31, 32, 41, 42))
    ]
    if pf is not None:
        payload.append({"aluno_id": aluno_id, "valor": pf, "tipo": "nota", "bimestre": 99})
    return payload


class RegraAcademicaUnificadaTest(unittest.TestCase):
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
        RegraAcademicaUnificadaTest._seq += 1
        return RegraAcademicaUnificadaTest._seq

    def _rede(self, infantil=False):
        n = self._proximo()
        inep = f"8300{n:04d}"
        cpf_prof = f"8301{n:07d}"[:11]
        cpf_aluno = f"8302{n:07d}"[:11]
        etapa = "infantil" if infantil else "fundamental"
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola Unif {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Unif", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        self.client.post("/api/turmas", json={
            "nome": "Pré Unif" if infantil else "5º Ano Unif",
            "ano_letivo": 2026, "etapa": etapa,
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = dash["turmas"][0]["id"]
        disc_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Matemática")
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": "Ana" if infantil else "João Unif",
            "cpf": cpf_aluno,
            "turma_id": turma_id,
            "nome_mae": "Mãe Unif",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aluno_id = _json(resp)[0]["id"]
        aloc = {"professor_id": prof_id, "turma_id": turma_id}
        if not infantil:
            aloc["disciplina_id"] = disc_id
        self.assertEqual(self.client.post("/api/alocacoes", json=aloc).status_code, 200)
        concluir_primeiro_acesso(self.client, cpf_prof, temp_prof)
        return {
            "inep": inep,
            "cpf_prof": cpf_prof,
            "turma_id": turma_id,
            "disc_id": disc_id,
            "aluno_id": aluno_id,
        }

    def _resumo_api(self, turma_id, disc_id, aluno_id):
        data, erro = _json(self.client.get(
            f"/api/monitoramento/turma/{turma_id}/disciplina/{disc_id}"
        ))
        self.assertIsNone(erro)
        return next(a for a in data if a["id"] == aluno_id)

    def test_01_endpoint_fornece_media_minima_vigente(self):
        self._definir_media(7.0)
        self._login("admin", SENHA_DEMO_ADMIN)
        data, erro = _json(self.client.get("/api/configuracao-academica"))
        self.assertIsNone(erro)
        self.assertEqual(data["media_minima"], 7.0)
        self.assertEqual(data["mediaMinima"], 7.0)

    def test_02_media_6_mantem_aprovado_65(self):
        esperado = calcular_de_notas(_mapa(6.5, 6.5), media_minima=6.0)
        self.assertEqual(esperado["situacao"], "APROVADO")
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=_lancamentos(rede["aluno_id"], 6.5, 6.5),
        ).status_code, 200)
        item = self._resumo_api(rede["turma_id"], rede["disc_id"], rede["aluno_id"])
        self.assertEqual(item["resumo"]["situacao"], esperado["situacao"])
        self.assertEqual(item["resumo"]["ma"], esperado["ma"])
        self.assertEqual(item["resumo"]["media_minima"], 6.0)

    def test_03_media_7_altera_resultado_oficial(self):
        esperado6 = calcular_de_notas(_mapa(6.5, 6.5), media_minima=6.0)
        esperado7 = calcular_de_notas(_mapa(6.5, 6.5), media_minima=7.0)
        self.assertEqual(esperado6["situacao"], "APROVADO")
        self.assertEqual(esperado7["situacao"], "EM RECUPERAÇÃO")
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=_lancamentos(rede["aluno_id"], 6.5, 6.5),
        ).status_code, 200)
        self._definir_media(7.0)
        item = self._resumo_api(rede["turma_id"], rede["disc_id"], rede["aluno_id"])
        self.assertEqual(item["resumo"]["situacao"], esperado7["situacao"])
        self.assertEqual(item["resumo"]["media_minima"], 7.0)
        self.assertEqual(item["resumo"]["precisa_pf"], esperado7["precisa_pf"])

    def test_04_template_legado_usa_configuracao_real(self):
        html = (
            Path(__file__).resolve().parents[1] / "src" / "templates" / "turma.html"
        ).read_text(encoding="utf-8")
        self.assertIn("media_minima", html)
        self.assertIn("corteAcademico", html)
        self.assertIn("/api/configuracao-academica", html)
        self.assertIn("aplicarResumoOficial", html)
        self.assertIn("d.resumo", html)
        self.assertNotIn("mp < 6", html)
        self.assertNotIn("ap < 6", html)
        self.assertNotIn("ma >= 6", html)

    def test_05_api_e_painel_usam_configuracao_real(self):
        rede = self._rede()
        self._definir_media(7.0)
        self._login(rede["inep"])
        painel, erro = _json(self.client.get(f"/api/turmas/{rede['turma_id']}/painel"))
        self.assertIsNone(erro)
        self.assertEqual(painel["mediaMinima"], 7.0)
        self._login(rede["cpf_prof"])
        self.assertEqual(self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=_lancamentos(rede["aluno_id"], 6.5, 6.5),
        ).status_code, 200)
        item = self._resumo_api(rede["turma_id"], rede["disc_id"], rede["aluno_id"])
        self.assertEqual(item["resumo"]["situacao"], "EM RECUPERAÇÃO")
        self.assertEqual(item["resumo"]["media_minima"], 7.0)

    def test_06_recuperacao_igual_ao_motor(self):
        esperado = calcular_de_notas(_mapa(5, 5), media_minima=6.0)
        self.assertEqual(esperado["situacao"], "EM RECUPERAÇÃO")
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=_lancamentos(rede["aluno_id"], 5, 5),
        ).status_code, 200)
        item = self._resumo_api(rede["turma_id"], rede["disc_id"], rede["aluno_id"])
        self.assertEqual(item["resumo"]["situacao"], esperado["situacao"])
        self.assertEqual(item["resumo"]["ma"], esperado["ma"])
        self.assertTrue(item["resumo"]["precisa_pf"])

    def test_07_prova_final_igual_ao_motor(self):
        esperado = calcular_de_notas(_mapa(5, 5, pf=8), media_minima=6.0)
        self.assertEqual(esperado["situacao"], "APROVADO")
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=_lancamentos(rede["aluno_id"], 5, 5, pf=8),
        ).status_code, 200)
        item = self._resumo_api(rede["turma_id"], rede["disc_id"], rede["aluno_id"])
        self.assertEqual(item["resumo"]["situacao"], esperado["situacao"])
        self.assertEqual(item["resumo"]["ma"], esperado["ma"])
        self.assertEqual(item["resumo"]["final"], 8)
        self.assertTrue(item["resumo"]["precisa_pf"])

    def test_08_transferido_nao_vira_aprovado(self):
        esperado = calcular_de_notas(_mapa(7, 7), transferido=True, media_minima=6.0)
        self.assertEqual(esperado["situacao"], "TRANSFERIDO")
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=_lancamentos(rede["aluno_id"], 7, 7),
        ).status_code, 200)
        with self.app.app_context():
            from src import db
            from src.models import Aluno
            aluno = db.session.get(Aluno, rede["aluno_id"])
            aluno.situacao = "transferido"
            db.session.commit()
        item = self._resumo_api(rede["turma_id"], rede["disc_id"], rede["aluno_id"])
        self.assertEqual(item["situacao"], "transferido")
        self.assertEqual(item["resumo"]["situacao"], "TRANSFERIDO")
        self.assertNotEqual(item["resumo"]["situacao"], "APROVADO")

    def test_09_ei_nao_usa_media_minima(self):
        rede = self._rede(infantil=True)
        self._login(rede["cpf_prof"])
        data, erro = _json(self.client.get(
            f"/api/monitoramento/turma/{rede['turma_id']}/disciplina/0"
        ))
        self.assertIsNone(erro)
        item = next(a for a in data if a["id"] == rede["aluno_id"])
        self.assertNotIn("resumo", item)
        self.assertNotIn("APROVADO", json.dumps(item))
        self.assertNotIn("REPROVADO", json.dumps(item))
        self.assertNotIn("EM RECUPERAÇÃO", json.dumps(item))
        self.assertIn("pareceres", item)

    def test_10_mudanca_6_para_7_refletida_operacionalmente(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=_lancamentos(rede["aluno_id"], 6.5, 6.5),
        ).status_code, 200)
        antes = self._resumo_api(rede["turma_id"], rede["disc_id"], rede["aluno_id"])
        self.assertEqual(antes["resumo"]["situacao"], "APROVADO")
        self._definir_media(7.0)
        depois = self._resumo_api(rede["turma_id"], rede["disc_id"], rede["aluno_id"])
        self.assertEqual(depois["resumo"]["situacao"], "EM RECUPERAÇÃO")
        self._login(rede["inep"])
        boletim = self.client.get(f"/api/alunos/{rede['aluno_id']}/documentos/boletim.pdf")
        self.assertEqual(boletim.status_code, 200)
        html = texto_pdf(boletim)
        self.assertIn("EM RECUPERAÇÃO", html)
        self.assertNotIn("APROVADO", html)

    def test_11_snapshot_fechado_nao_muda(self):
        self._definir_media(6.0)
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['disc_id']}/notas",
            json=_lancamentos(rede["aluno_id"], 7, 7),
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(
            self.client.post(f"/api/turmas/{rede['turma_id']}/encerrar").status_code, 200,
        )
        with self.app.app_context():
            from src.historico import carregar_dados_fechamento
            from src.models import HistoricoAnual, Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_id"],
            ).one()
            hist = HistoricoAnual.query.filter_by(matricula_id=mat.id).one()
            json_antes = hist.dados_fechamento
            dados = carregar_dados_fechamento(hist)
            self.assertEqual(dados["criterios_academicos"]["media_minima"], 6.0)
        self._definir_media(7.0)
        with self.app.app_context():
            from src.historico import carregar_dados_fechamento
            from src.models import HistoricoAnual, Matricula
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_id"],
            ).one()
            hist = HistoricoAnual.query.filter_by(matricula_id=mat.id).one()
            self.assertEqual(hist.dados_fechamento, json_antes)
            dados = carregar_dados_fechamento(hist)
            self.assertEqual(dados["criterios_academicos"]["media_minima"], 6.0)

    def test_12_arquivos_operacionais_sem_corte_6_fixo(self):
        raiz = Path(__file__).resolve().parents[1]
        turma_jsx = (raiz / "frontend" / "src" / "pages" / "Turma.jsx").read_text(encoding="utf-8")
        grade_jsx = (raiz / "frontend" / "src" / "components" / "GradeNotas.jsx").read_text(encoding="utf-8")
        notas_js = (raiz / "frontend" / "src" / "notas.js").read_text(encoding="utf-8")
        turma_html = (raiz / "src" / "templates" / "turma.html").read_text(encoding="utf-8")
        self.assertNotIn("MEDIA_MINIMA_PADRAO", turma_jsx)
        self.assertNotIn("MEDIA_MINIMA_PADRAO", grade_jsx)
        self.assertNotIn("mp < 6", turma_html)
        self.assertNotIn("ap < 6", turma_html)
        self.assertNotIn("ma >= 6", turma_html)
        self.assertNotIn("mediaMinima == null ? MEDIA_MINIMA_PADRAO", notas_js)
        self.assertIn("corteOficial", notas_js)
        self.assertIn("fromOficial", grade_jsx)
        self.assertIn("dirty", turma_jsx)
