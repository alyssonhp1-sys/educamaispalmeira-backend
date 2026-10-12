import os
import tempfile
import unittest
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


class ConfiguracaoAcademicaTest(unittest.TestCase):
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
        cls._montar_rede()

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
            raise RuntimeError("Falha no login de fixture: %s %s" % (login, _json(resp)[1]))

    @classmethod
    def _montar_rede(cls):
        client = cls.app.test_client()
        cls._login_client(client, "admin", SENHA_DEMO_ADMIN)
        cls.inep = "81000001"
        cls.cpf_prof = "81011111111"
        cls.login_coord = "coordCfg810"
        cls.login_sec = "secretaria810"

        resp = client.post("/api/escolas", json={
            "nome": "Escola Config 810",
            "inep": cls.inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_gestor = senha_temporaria(resp)

        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep)
        cls.disc_id = dash["disciplinas"][0]["id"] if "disciplinas" in dash else None

        from src.models import Disciplina, Usuario
        from src.passwords import hash_senha
        from src import db
        with cls.app.app_context():
            if cls.disc_id is None:
                cls.disc_id = Disciplina.query.filter_by(nome="Matemática").first().id
            db.session.add(Usuario(
                login=cls.login_sec,
                senha_hash=hash_senha(SENHA_PESSOAL),
                papel="secretaria",
                nome="Secretaria Municipal",
                primeiro_acesso=False,
            ))
            db.session.commit()

        resp = client.post("/api/professores", json={
            "nome": "Prof Config", "cpf": cls.cpf_prof, "escola_id": cls.escola_id,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_prof = senha_temporaria(resp)

        resp = client.post(f"/api/escolas/{cls.escola_id}/coordenadores", json={
            "login": cls.login_coord, "nome": "Coord Config",
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_coord = senha_temporaria(resp)
        concluir_primeiro_acesso(client, cls.inep, temp_gestor)
        concluir_primeiro_acesso(client, cls.login_coord, temp_coord)
        concluir_primeiro_acesso(client, cls.cpf_prof, temp_prof)

        cls._login_client(client, cls.inep, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano Config", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_id = dash["turmas"][0]["id"]
        cls.prof_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof)
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Media Seis Cinco",
            "cpf": "81033333333",
            "turma_id": cls.turma_id,
            "nome_mae": "Mãe Config",
        })
        cls.aluno_id = _json(resp)[0]["id"]
        resp = client.post("/api/alocacoes", json={
            "professor_id": cls.prof_id,
            "turma_id": cls.turma_id,
            "disciplina_id": cls.disc_id,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])

        cls._login_client(client, cls.cpf_prof, SENHA_PESSOAL)
        lancamentos = [
            {"aluno_id": cls.aluno_id, "valor": 6.5, "tipo": "nota", "bimestre": cod}
            for cod in (11, 12, 21, 22, 31, 32, 41, 42)
        ]
        resp = client.post(
            f"/api/turmas/{cls.turma_id}/disciplinas/{cls.disc_id}/notas",
            json=lancamentos,
        )
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _put_media(self, valor, senha=SENHA_DEMO_ADMIN):
        return self.client.put("/api/configuracao-academica", json={
            "mediaMinima": valor,
            "senhaConfirmacao": senha,
        })

    def _resumo_aluno(self):
        data, erro = _json(self.client.get(
            f"/api/monitoramento/turma/{self.turma_id}/disciplina/{self.disc_id}"
        ))
        self.assertIsNone(erro)
        return next(a for a in data if a["id"] == self.aluno_id)["resumo"]

    def test_01_banco_sem_configuracao_media_6(self):
        from src import db
        from src.models import ConfiguracaoAcademica
        from src.notas import garantir_configuracao_padrao, media_minima_vigente
        with self.app.app_context():
            ConfiguracaoAcademica.query.delete()
            db.session.commit()
            self.assertIsNone(ConfiguracaoAcademica.query.first())
            self.assertEqual(media_minima_vigente(), 6.0)
            r = calcular_de_notas(_mapa(6.5, 6.5))
            self.assertEqual(r["ma"], 6.5)
            self.assertEqual(r["situacao"], "APROVADO")
            self.assertEqual(r["media_minima"], 6.0)
            garantir_configuracao_padrao()

    def test_02_configuracao_pode_ser_criada(self):
        from src import db
        from src.models import ConfiguracaoAcademica
        with self.app.app_context():
            ConfiguracaoAcademica.query.delete()
            db.session.commit()
            self.assertIsNone(ConfiguracaoAcademica.query.first())
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.get("/api/configuracao-academica")
        data, erro = _json(resp)
        self.assertEqual(resp.status_code, 200, erro)
        self.assertEqual(data["media_minima"], 6.0)
        with self.app.app_context():
            self.assertIsNotNone(ConfiguracaoAcademica.query.first())

    def test_03_configuracao_persiste(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._put_media(7.0)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _erro = _json(self.client.get("/api/configuracao-academica"))
        self.assertEqual(data["media_minima"], 7.0)
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _erro = _json(self.client.get("/api/configuracao-academica"))
        self.assertEqual(data["media_minima"], 7.0)
        self._put_media(6.0)

    def test_04_dirigente_pode_consultar(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.get("/api/configuracao-academica")
        data, erro = _json(resp)
        self.assertEqual(resp.status_code, 200, erro)
        self.assertIn("media_minima", data)
        self.assertIn("mediaMinima", data)

    def test_05_dirigente_pode_alterar(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._put_media(7.0)
        data, erro = _json(resp)
        self.assertEqual(resp.status_code, 200, erro)
        self.assertEqual(data["media_minima"], 7.0)
        self._put_media(6.0)

    def test_05b_secretaria_pode_alterar(self):
        self._login(self.login_sec, SENHA_PESSOAL)
        resp = self.client.put("/api/configuracao-academica", json={
            "mediaMinima": 6.5,
            "senhaConfirmacao": SENHA_PESSOAL,
        })
        data, erro = _json(resp)
        self.assertEqual(resp.status_code, 200, erro)
        self.assertEqual(data["media_minima"], 6.5)
        self._login("admin", SENHA_DEMO_ADMIN)
        self._put_media(6.0)

    def test_06_gestor_nao_pode_alterar(self):
        self._login(self.inep, SENHA_PESSOAL)
        resp = self.client.put("/api/configuracao-academica", json={
            "mediaMinima": 8.0,
            "senhaConfirmacao": SENHA_PESSOAL,
        })
        self.assertEqual(resp.status_code, 403)
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _erro = _json(self.client.get("/api/configuracao-academica"))
        self.assertNotEqual(data["media_minima"], 8.0)

    def test_07_coordenador_nao_pode_alterar(self):
        self._login(self.login_coord, SENHA_PESSOAL)
        resp = self.client.put("/api/configuracao-academica", json={
            "mediaMinima": 8.0,
            "senhaConfirmacao": SENHA_PESSOAL,
        })
        self.assertEqual(resp.status_code, 403)

    def test_08_professor_nao_pode_alterar(self):
        self._login(self.cpf_prof, SENHA_PESSOAL)
        resp = self.client.put("/api/configuracao-academica", json={
            "mediaMinima": 8.0,
            "senhaConfirmacao": SENHA_PESSOAL,
        })
        self.assertEqual(resp.status_code, 403)

    def test_09_valor_negativo_rejeitado(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._put_media(-1)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("0 a 10", (_json(resp)[1] or "").lower())

    def test_10_valor_acima_de_dez_rejeitado(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._put_media(10.1)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("0 a 10", (_json(resp)[1] or "").lower())

    def test_11_valor_sete_aceito(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._put_media(7.0)
        data, erro = _json(resp)
        self.assertEqual(resp.status_code, 200, erro)
        self.assertEqual(data["media_minima"], 7.0)
        self._put_media(6.0)

    def test_12_calculo_com_configuracao_6(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        self.assertEqual(self._put_media(6.0).status_code, 200)
        esperado = calcular_de_notas(_mapa(6.5, 6.5), media_minima=6.0)
        self.assertEqual(esperado["situacao"], "APROVADO")
        self.assertEqual(esperado["ma"], 6.5)
        resumo = self._resumo_aluno()
        self.assertEqual(resumo["ma"], 6.5)
        self.assertEqual(resumo["situacao"], "APROVADO")
        self.assertEqual(resumo["media_minima"], 6.0)

    def test_13_mesmo_calculo_apos_alterar_para_7(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        self.assertEqual(self._put_media(6.0).status_code, 200)
        antes = self._resumo_aluno()
        self.assertEqual(antes["situacao"], "APROVADO")
        self.assertEqual(self._put_media(7.0).status_code, 200)
        esperado = calcular_de_notas(_mapa(6.5, 6.5), media_minima=7.0)
        self.assertEqual(esperado["situacao"], "EM RECUPERAÇÃO")
        depois = self._resumo_aluno()
        self.assertEqual(depois["ma"], 6.5)
        self.assertEqual(depois["situacao"], "EM RECUPERAÇÃO")
        self.assertEqual(depois["media_minima"], 7.0)
        self._put_media(6.0)

    def test_14_boletim_usa_configuracao(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        self.assertEqual(self._put_media(6.0).status_code, 200)
        boletim = self.client.get(f"/api/alunos/{self.aluno_id}/documentos/boletim.pdf")
        self.assertEqual(boletim.status_code, 200)
        html = texto_pdf(boletim)
        self.assertIn("APROVADO", html)
        self.assertEqual(self._put_media(7.0).status_code, 200)
        boletim = self.client.get(f"/api/alunos/{self.aluno_id}/documentos/boletim.pdf")
        html = texto_pdf(boletim)
        self.assertIn("EM RECUPERAÇÃO", html)
        self.assertNotIn("APROVADO", html)
        self._put_media(6.0)

    def test_15_relatorio_usa_mesmo_resultado(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        self.assertEqual(self._put_media(6.0).status_code, 200)
        rel = self.client.get(f"/api/turmas/{self.turma_id}/documentos/rendimento/{self.disc_id}.pdf")
        self.assertEqual(rel.status_code, 200)
        html = texto_pdf(rel)
        self.assertIn("APROVADO", html)
        self.assertEqual(self._put_media(7.0).status_code, 200)
        rel = self.client.get(f"/api/turmas/{self.turma_id}/documentos/rendimento/{self.disc_id}.pdf")
        html = "".join(texto_pdf(rel).split())
        self.assertIn("EMRECUPERAÇÃO", html)
        self._put_media(6.0)

    def test_16_create_app_nao_apaga_configuracao(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        self.assertEqual(self._put_media(7.0).status_code, 200)
        from src import create_app, db
        from src.models import ConfiguracaoAcademica, ensure_schema
        from src.notas import garantir_configuracao_padrao, media_minima_vigente
        with self.app.app_context():
            db.create_all()
            ensure_schema()
            garantir_configuracao_padrao()
            self.assertEqual(media_minima_vigente(), 7.0)
            self.assertEqual(ConfiguracaoAcademica.query.count(), 1)
        app2 = create_app()
        app2.testing = True
        c = app2.test_client()
        self._login_client(c, "admin", SENHA_DEMO_ADMIN)
        data, erro = _json(c.get("/api/configuracao-academica"))
        self.assertIsNone(erro)
        self.assertEqual(data["media_minima"], 7.0)
        self._login("admin", SENHA_DEMO_ADMIN)
        self._put_media(6.0)

    def test_17_app_continua_iniciando(self):
        self.assertIsNotNone(self.app)
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.get("/api/me")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = self.client.get("/api/dashboard")
        data, erro = _json(dash)
        self.assertEqual(dash.status_code, 200, erro)
        self.assertEqual(data["painel"], "municipal")
        self.assertIn("configuracaoAcademica", data)
        self.assertIn("mediaMinima", data["configuracaoAcademica"])


if __name__ == "__main__":
    unittest.main()
