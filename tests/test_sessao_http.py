"""Sessão, cookies, headers HTTP e endurecimento de autenticação (Bloco 4)."""
import io
import logging
import os
import shutil
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from src.config import (
    CHAVE_SECRET_DEV_TESTE,
    DURACAO_SESSAO,
    MAX_UPLOAD_BYTES,
    SESSION_COOKIE_NAME_VALOR,
    SESSION_COOKIE_SAMESITE_VALOR,
    ConfiguracaoInsegura,
)
from src.http import HEADER_REQUEST_ID, MENSAGEM_ERRO_INTERNO
from src.routes.auth import MSG_CREDENCIAL_INVALIDA
from tests.support import (
    SENHA_DEMO_ADMIN,
    SENHA_PESSOAL,
    apontar_banco_teste,
    concluir_primeiro_acesso,
    json_data,
    preparar_ambiente_teste,
    senha_temporaria,
    criar_app_migrada,
)


def _json(resp):
    return json_data(resp)


def _flags_cookie(header):
    partes = [p.strip() for p in (header or "").split(";") if p.strip()]
    flags = {}
    for parte in partes[1:]:
        if "=" in parte:
            chave, valor = parte.split("=", 1)
            flags[chave.strip().lower()] = valor.strip()
        else:
            flags[parte.lower()] = True
    return flags


def _cookie_sessao(resp):
    for item in resp.headers.getlist("Set-Cookie"):
        nome = item.split("=", 1)[0].strip().lower()
        if nome == SESSION_COOKIE_NAME_VALOR or nome == "session":
            return item
    return ""


def _assert_headers_seguranca(test, resp, hsts=False):
    test.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
    test.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
    test.assertEqual(resp.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
    politica = resp.headers.get("Permissions-Policy") or ""
    test.assertIn("camera=()", politica)
    test.assertIn("microphone=()", politica)
    test.assertIn("geolocation=()", politica)
    test.assertNotEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")
    test.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))
    csp = resp.headers.get("Content-Security-Policy") or ""
    test.assertIn("default-src 'self'", csp)
    test.assertIn("frame-ancestors 'none'", csp)
    test.assertNotIn("script-src *", csp)
    test.assertNotIn("script-src '*'", csp)
    if hsts:
        test.assertIn("max-age=", resp.headers.get("Strict-Transport-Security") or "")
    else:
        test.assertIsNone(resp.headers.get("Strict-Transport-Security"))
    rid = resp.headers.get(HEADER_REQUEST_ID)
    test.assertTrue(rid)
    uuid.UUID(rid)


class SessaoCookiesHeadersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cls._db_path = tmp.name
        cls._upload_dir = tempfile.mkdtemp(prefix="educamais_sessao_")
        cls._old_upload = os.environ.get("EDUCAMAIS_UPLOAD_FOLDER")
        os.environ["EDUCAMAIS_UPLOAD_FOLDER"] = cls._upload_dir
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
        if cls._old_upload is None:
            os.environ.pop("EDUCAMAIS_UPLOAD_FOLDER", None)
        else:
            os.environ["EDUCAMAIS_UPLOAD_FOLDER"] = cls._old_upload
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass
        shutil.rmtree(cls._upload_dir, ignore_errors=True)

    def setUp(self):
        self.client = self.app.test_client()
        with self.app.app_context():
            from src.passwords import resetar_falhas_login_para_testes
            resetar_falhas_login_para_testes()

    def _login_admin(self):
        resp = self.client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        return resp

    def test_01_cookie_httponly(self):
        self.assertTrue(self.app.config["SESSION_COOKIE_HTTPONLY"])
        resp = self._login_admin()
        flags = _flags_cookie(_cookie_sessao(resp))
        self.assertTrue(flags.get("httponly"))

    def test_02_cookie_samesite_lax(self):
        self.assertEqual(self.app.config["SESSION_COOKIE_SAMESITE"], SESSION_COOKIE_SAMESITE_VALOR)
        resp = self._login_admin()
        flags = _flags_cookie(_cookie_sessao(resp))
        self.assertEqual((flags.get("samesite") or "").lower(), "lax")

    def test_03_dev_teste_opera_sem_cookie_secure(self):
        self.assertFalse(self.app.config["SESSION_COOKIE_SECURE"])
        resp = self._login_admin()
        flags = _flags_cookie(_cookie_sessao(resp))
        self.assertFalse(flags.get("secure"))

    def test_04_sessao_tem_duracao_finita(self):
        self.assertEqual(self.app.config["PERMANENT_SESSION_LIFETIME"], DURACAO_SESSAO)
        self.assertEqual(DURACAO_SESSAO, timedelta(hours=8))
        self.assertLess(DURACAO_SESSAO, timedelta(days=30))
        resp = self._login_admin()
        flags = _flags_cookie(_cookie_sessao(resp))
        self.assertTrue(flags.get("expires") or flags.get("max-age"))
        if flags.get("max-age"):
            self.assertEqual(int(flags["max-age"]), int(DURACAO_SESSAO.total_seconds()))

    def test_05_logout_invalida_sessao(self):
        self._login_admin()
        resp = self.client.post("/api/auth/logout")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        me = self.client.get("/api/me")
        self.assertEqual(me.status_code, 401)
        dash = self.client.get("/api/dashboard")
        self.assertEqual(dash.status_code, 401)

    def test_06_login_nao_enumera_usuario(self):
        inexistente = self.client.post(
            "/api/auth/login", json={"login": "nao-existe-bloco4", "senha": "qualquer"}
        )
        errada = self.client.post(
            "/api/auth/login", json={"login": "admin", "senha": "senha-errada-bloco4"}
        )
        self.assertEqual(inexistente.status_code, errada.status_code)
        self.assertEqual(inexistente.status_code, 400)
        self.assertEqual(_json(inexistente)[1], _json(errada)[1])
        self.assertEqual(_json(inexistente)[1], MSG_CREDENCIAL_INVALIDA)
        self.assertNotIn("não encontrado", (_json(inexistente)[1] or "").lower())
        self.assertNotIn("senha incorreta", (_json(errada)[1] or "").lower())

    def test_07_headers_na_resposta_200(self):
        resp = self._login_admin()
        _assert_headers_seguranca(self, resp)
        me = self.client.get("/api/me")
        self.assertEqual(me.status_code, 200)
        _assert_headers_seguranca(self, me)

    def test_08_headers_no_401(self):
        resp = self.client.get("/api/me")
        self.assertEqual(resp.status_code, 401)
        _assert_headers_seguranca(self, resp)

    def test_09_headers_no_403(self):
        SessaoCookiesHeadersTest._seq += 1
        inep = f"6400{SessaoCookiesHeadersTest._seq:04d}"
        self._login_admin()
        criacao = self.client.post("/api/escolas", json={
            "nome": "Escola Bloco4 %s" % inep,
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(criacao.status_code, 200, _json(criacao)[1])
        self.client.post("/api/auth/logout")
        login = self.client.post("/api/auth/login", json={
            "login": inep, "senha": senha_temporaria(criacao),
        })
        self.assertEqual(login.status_code, 200, _json(login)[1])
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma bloqueada", "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 403)
        _assert_headers_seguranca(self, resp)

    def test_10_headers_no_404(self):
        self._login_admin()
        resp = self.client.get("/api/registros-aula/999999")
        self.assertEqual(resp.status_code, 404)
        _assert_headers_seguranca(self, resp)

    def test_11_headers_no_413(self):
        antigo = self.app.config["MAX_CONTENT_LENGTH"]
        self.app.config["MAX_CONTENT_LENGTH"] = 64
        try:
            resp = self.client.post(
                "/api/auth/login",
                data=b"x" * 400,
                content_type="application/json",
            )
            self.assertEqual(resp.status_code, 413)
            _assert_headers_seguranca(self, resp)
        finally:
            self.app.config["MAX_CONTENT_LENGTH"] = antigo

    def test_12_api_autenticada_usa_no_store(self):
        self._login_admin()
        me = self.client.get("/api/me")
        self.assertEqual(me.status_code, 200)
        cache = (me.headers.get("Cache-Control") or "").lower()
        self.assertIn("no-store", cache)
        dash = self.client.get("/api/dashboard")
        self.assertEqual(dash.status_code, 200)
        self.assertIn("no-store", (dash.headers.get("Cache-Control") or "").lower())

    def test_13_assets_estaticos_continuam_acessiveis(self):
        resp = self.client.get("/static/img/logo2.png")
        self.assertEqual(resp.status_code, 200)
        _assert_headers_seguranca(self, resp)
        self.assertIsNone(resp.headers.get("Strict-Transport-Security"))
        cache = (resp.headers.get("Cache-Control") or "").lower()
        self.assertNotIn("no-store", cache)
        bloqueado = self.client.get("/static/uploads/qualquer.pdf")
        self.assertEqual(bloqueado.status_code, 404)

    def test_14_login_e_primeiro_acesso_seguem_funcionando(self):
        SessaoCookiesHeadersTest._seq += 1
        inep = f"6401{SessaoCookiesHeadersTest._seq:04d}"
        self._login_admin()
        criacao = self.client.post("/api/escolas", json={
            "nome": "Escola Primeiro %s" % inep,
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(criacao.status_code, 200, _json(criacao)[1])
        temp = senha_temporaria(criacao)
        self.client.post("/api/auth/logout")
        user = concluir_primeiro_acesso(self.client, inep, temp)
        self.assertFalse(user["primeiroAcesso"])
        me = self.client.get("/api/me")
        self.assertEqual(me.status_code, 200)
        self.assertFalse(_json(me)[0]["primeiroAcesso"])

    def test_15_upload_download_continuam_funcionando(self):
        SessaoCookiesHeadersTest._seq += 1
        inep = f"6402{SessaoCookiesHeadersTest._seq:04d}"
        cpf = f"6402{SessaoCookiesHeadersTest._seq:07d}"
        self._login_admin()
        escola = self.client.post("/api/escolas", json={
            "nome": "Escola Upload %s" % inep,
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(escola.status_code, 200, _json(escola)[1])
        concluir_primeiro_acesso(self.client, inep, senha_temporaria(escola))
        self.client.post("/api/turmas", json={
            "nome": "5º Ano B4", "ano_letivo": 2026, "etapa": "fundamental",
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = dash["turmas"][0]["id"]
        disc_id = dash["disciplinas"][0]["id"]
        escola_id = dash["escola"]["id"]
        prof = self.client.post("/api/professores", json={
            "nome": "Prof B4", "cpf": cpf, "escola_id": escola_id,
        })
        self.assertEqual(prof.status_code, 200, _json(prof)[1])
        concluir_primeiro_acesso(self.client, cpf, senha_temporaria(prof))
        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"login": inep, "senha": SENHA_PESSOAL})
        dash = _json(self.client.get("/api/dashboard"))[0]
        prof_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cpf)
        aloc = self.client.post("/api/alocacoes", json={
            "professor_id": prof_id, "turma_id": turma_id, "disciplina_id": disc_id,
        })
        self.assertEqual(aloc.status_code, 200, _json(aloc)[1])
        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"login": cpf, "senha": SENHA_PESSOAL})
        pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        envio = self.client.post("/api/planejamento/enviar", data={
            "turma_id": str(turma_id),
            "conteudo": "Plano bloco 4",
            "arquivo": (io.BytesIO(pdf), "plano.pdf", "application/pdf"),
        })
        self.assertEqual(envio.status_code, 200, _json(envio)[1])
        with self.app.app_context():
            from src.models import Planejamento
            plano = Planejamento.query.filter_by(conteudo="Plano bloco 4").order_by(Planejamento.id.desc()).first()
            self.assertIsNotNone(plano)
            plano_id = plano.id
        download = self.client.get("/api/planejamentos/%s/arquivo" % plano_id)
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.data.startswith(b"%PDF"))
        cache = (download.headers.get("Cache-Control") or "").lower()
        self.assertIn("no-store", cache)
        _assert_headers_seguranca(self, download)
        self.assertEqual(self.app.config["MAX_CONTENT_LENGTH"], MAX_UPLOAD_BYTES)

    def test_16_cors_aberto_nao_existe(self):
        resp = self.client.get("/api/me")
        self.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))
        self._login_admin()
        resp = self.client.get("/api/dashboard")
        self.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))

    def test_17_nenhuma_senha_impressa_no_login(self):
        registros = io.StringIO()
        handler = logging.StreamHandler(registros)
        handler.setLevel(logging.DEBUG)
        raiz = logging.getLogger()
        raiz.addHandler(handler)
        try:
            with patch("builtins.print") as mocked_print:
                self.client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
                self.client.post("/api/auth/login", json={"login": "admin", "senha": "secreta-nao-logar"})
                self.client.post("/api/auth/login", json={"login": "fantasma", "senha": "outra-secreta"})
                texto_print = " ".join(str(c) for c in mocked_print.call_args_list)
        finally:
            raiz.removeHandler(handler)
        saida = registros.getvalue() + texto_print
        self.assertNotIn(SENHA_DEMO_ADMIN, saida)
        self.assertNotIn("secreta-nao-logar", saida)
        self.assertNotIn("outra-secreta", saida)
        self.assertNotIn(CHAVE_SECRET_DEV_TESTE, saida)

    def test_18_frontend_trata_401_sem_loop_de_login(self):
        texto = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
        self.assertIn("educamais:sessao-expirada", texto)
        self.assertIn("/api/auth/login", texto)
        self.assertIn("/api/me", texto)
        self.assertIn("/api/auth/definir-senha", texto)
        app_js = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
        self.assertIn("educamais:sessao-expirada", app_js)

    def test_19_remember_me_nao_e_ativado(self):
        resp = self._login_admin()
        juntos = " ".join(resp.headers.getlist("Set-Cookie")).lower()
        self.assertNotIn("remember", juntos)
        self.assertFalse(self.app.config.get("REMEMBER_COOKIE_SECURE"))


class ProducaoSessaoHttpTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        from src.config import Config
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
        from tests.support import apontar_banco_teste
        apontar_banco_teste("sqlite:///" + Path(self._db_path).resolve().as_posix())

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def _criar_app_producao(self):
        from src import create_app
        return create_app()

    def _flags_runtime_producao(self):
        from flask import Flask
        from src.config import aplicar_configuracao_runtime
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-producao-de-teste-nao-reutilizar"
        os.environ["EDUCAMAIS_DATABASE_URI"] = "postgresql+psycopg://u:p@localhost/educamais"
        os.environ.pop("EDUCAMAIS_DEMO", None)
        app = Flask("flags-prod")
        aplicar_configuracao_runtime(app)
        return app

    def test_01_producao_usa_cookie_secure(self):
        os.environ["EDUCAMAIS_DEBUG"] = "1"
        os.environ["FLASK_ENV"] = "development"
        os.environ["FLASK_DEBUG"] = "1"
        app = self._flags_runtime_producao()
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])
        self.assertTrue(app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertEqual(app.config["SESSION_COOKIE_SAMESITE"], "Lax")
        self.assertEqual(app.config["SESSION_COOKIE_NAME"], SESSION_COOKIE_NAME_VALOR)
        self.assertFalse(app.config["DEBUG"])
        self.assertFalse(app.config["TESTING"])
        self.assertFalse(app.config["EDUCAMAIS_DEMO"])

        preparar_ambiente_teste(demo=True)
        apontar_banco_teste("sqlite:///" + Path(self._db_path).resolve().as_posix())
        from src import create_app
        app_login = create_app()
        app_login.config["SESSION_COOKIE_SECURE"] = True
        app_login.config["SESSION_COOKIE_NAME"] = SESSION_COOKIE_NAME_VALOR
        with app_login.app_context():
            from src import db
            from src.models import Usuario
            from src.passwords import hash_senha
            if not Usuario.query.filter_by(login="admin").first():
                db.session.add(Usuario(
                    login="prod-bloco4",
                    senha_hash=hash_senha("senha-prod-teste"),
                    papel="dirigente",
                    nome="Prod",
                    primeiro_acesso=False,
                ))
                db.session.commit()
                login, senha = "prod-bloco4", "senha-prod-teste"
            else:
                login, senha = "admin", SENHA_DEMO_ADMIN
        client = app_login.test_client()
        resp = client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        flags = _flags_cookie(_cookie_sessao(resp))
        self.assertTrue(flags.get("secure"))
        self.assertTrue(flags.get("httponly"))
        self.assertEqual((flags.get("samesite") or "").lower(), "lax")

    def test_02_producao_sem_secret_key_falha(self):
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ.pop("EDUCAMAIS_SECRET_KEY", None)
        os.environ.pop("EDUCAMAIS_DEMO", None)
        with self.assertRaises(ConfiguracaoInsegura):
            self._criar_app_producao()

    def test_03_demo_impossivel_em_producao(self):
        from src.config import demo_habilitado
        os.environ["EDUCAMAIS_DEMO"] = "1"
        self.assertFalse(demo_habilitado("production"))
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-producao-de-teste-nao-reutilizar"
        with self.assertRaises(ConfiguracaoInsegura) as ctx:
            self._criar_app_producao()
        self.assertIn("PostgreSQL", str(ctx.exception))

    def test_04_500_producao_nao_expoe_traceback(self):
        preparar_ambiente_teste(demo=True)
        from tests.support import apontar_banco_teste
        apontar_banco_teste("sqlite:///" + Path(self._db_path).resolve().as_posix())
        from src import create_app
        app = create_app()
        app.config["DEBUG"] = False
        app.config["TESTING"] = False
        app.config["PROPAGATE_EXCEPTIONS"] = False
        self.assertFalse(app.config["DEBUG"])
        client = app.test_client()
        self.assertEqual(
            client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN}).status_code,
            200,
        )
        with patch("src.routes.spa.user_payload", side_effect=RuntimeError(
            "TRACE-SECRETO C:\\Users\\segredo\\app.py SELECT * FROM usuario"
        )):
            resp = client.get("/api/me")
        self.assertEqual(resp.status_code, 500)
        corpo = resp.get_data(as_text=True)
        self.assertNotIn("TRACE-SECRETO", corpo)
        self.assertNotIn("C:\\Users\\segredo", corpo)
        self.assertNotIn("SELECT * FROM", corpo)
        self.assertNotIn("Traceback", corpo)
        _, error = _json(resp)
        self.assertEqual(error, MENSAGEM_ERRO_INTERNO)
        _assert_headers_seguranca(self, resp)


if __name__ == "__main__":
    unittest.main()
