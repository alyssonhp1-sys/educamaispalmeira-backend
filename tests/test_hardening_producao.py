"""Hardening final: CSRF, lockout persistente, reset, proxy, health, papéis."""
import io
import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.middleware.proxy_fix import ProxyFix

from src.config import (
    CHAVE_SECRET_DEV_TESTE,
    SESSION_COOKIE_NAME_VALOR,
    ConfiguracaoInsegura,
    demo_habilitado,
)
from src.csrf import MSG_CSRF
from src.http import CSP_VALOR, HSTS_VALOR, MENSAGEM_ERRO_INTERNO
from src.migracoes import REVISION_AUDIT_LOG, REVISION_HEAD
from tests.support import (
    SENHA_DEMO_ADMIN,
    SENHA_PESSOAL,
    apontar_banco_teste,
    concluir_primeiro_acesso,
    json_data,
    obter_csrf,
    preparar_ambiente_teste,
    senha_temporaria,
)

URI_PG = os.environ.get("EDUCAMAIS_TEST_DATABASE_URI", "").strip() or None
CHAVE_PROD = "chave-producao-de-teste-nao-reutilizar"


def _json(resp):
    return json_data(resp)


class HardeningConfiguracaoTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        from src.config import Config
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
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

    def test_a_producao_sem_secret_key_falha(self):
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ.pop("EDUCAMAIS_SECRET_KEY", None)
        from src import create_app
        with self.assertRaises(ConfiguracaoInsegura):
            create_app()

    def test_a2_chave_de_dev_recusada_em_producao(self):
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ["EDUCAMAIS_SECRET_KEY"] = CHAVE_SECRET_DEV_TESTE
        os.environ["EDUCAMAIS_DATABASE_URI"] = "postgresql+psycopg://u:p@localhost/educamais"
        from src import create_app
        with self.assertRaises(ConfiguracaoInsegura) as ctx:
            create_app()
        self.assertIn("SECRET_KEY", str(ctx.exception))

    def test_b_debug_desligado_em_producao(self):
        from flask import Flask
        from src.config import aplicar_configuracao_runtime
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ["EDUCAMAIS_SECRET_KEY"] = CHAVE_PROD
        os.environ["EDUCAMAIS_DATABASE_URI"] = "postgresql+psycopg://u:p@localhost/educamais"
        os.environ["EDUCAMAIS_DEBUG"] = "1"
        os.environ.pop("EDUCAMAIS_DEMO", None)
        app = Flask("prod-debug")
        aplicar_configuracao_runtime(app)
        self.assertFalse(app.config["DEBUG"])
        self.assertFalse(app.config["TESTING"])
        self.assertFalse(demo_habilitado("production"))

    def test_sqlite_recusado_em_producao(self):
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ["EDUCAMAIS_SECRET_KEY"] = CHAVE_PROD
        from src import create_app
        with self.assertRaises(ConfiguracaoInsegura) as ctx:
            create_app()
        self.assertIn("PostgreSQL", str(ctx.exception))


class HardeningAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = os.environ.copy()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cls._db_path = tmp.name
        from src.config import Config
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        apontar_banco_teste("sqlite:///" + Path(cls._db_path).resolve().as_posix())
        from src import create_app
        cls.app = create_app()
        cls.app.testing = True
        cls._seq = 0

    @classmethod
    def tearDownClass(cls):
        os.environ.clear()
        os.environ.update(cls._env)
        preparar_ambiente_teste(demo=True)
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass

    def setUp(self):
        self.client = self.app.test_client()
        with self.app.app_context():
            from src.passwords import resetar_falhas_login_para_testes
            resetar_falhas_login_para_testes()

    def _login_admin(self):
        resp = self.client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        return resp

    def _proximo(self):
        HardeningAppTest._seq += 1
        return HardeningAppTest._seq

    def _criar_escola(self):
        n = self._proximo()
        inep = "6800%04d" % n
        self._login_admin()
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola Hard %s" % inep,
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        return inep, senha_temporaria(resp), _json(resp)[0]

    def test_c_cookie_httponly_samesite_nome(self):
        self.assertEqual(self.app.config["SESSION_COOKIE_NAME"], SESSION_COOKIE_NAME_VALOR)
        resp = self._login_admin()
        juntos = " ".join(resp.headers.getlist("Set-Cookie")).lower()
        self.assertIn(SESSION_COOKIE_NAME_VALOR, juntos)
        self.assertIn("httponly", juntos)
        self.assertIn("samesite=lax", juntos)

    def test_d_logout_invalida_sessao(self):
        self._login_admin()
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_e_lockout_apos_falhas(self):
        inep, temp, _ = self._criar_escola()
        concluir_primeiro_acesso(self.client, inep, temp)
        self.client.post("/api/auth/logout")
        for _ in range(5):
            resp = self.client.post("/api/auth/login", json={"login": inep, "senha": "errada"})
            self.assertIn(resp.status_code, (400, 403))
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": SENHA_PESSOAL})
        self.assertEqual(resp.status_code, 403)
        _, error = _json(resp)
        self.assertIn("bloqueado", error.lower())

    def test_f_lockout_persiste_entre_apps(self):
        inep, temp, _ = self._criar_escola()
        concluir_primeiro_acesso(self.client, inep, temp)
        self.client.post("/api/auth/logout")
        for _ in range(5):
            self.client.post("/api/auth/login", json={"login": inep, "senha": "errada"})
        from src import create_app
        app2 = create_app()
        app2.testing = True
        c2 = app2.test_client()
        resp = c2.post("/api/auth/login", json={"login": inep, "senha": SENHA_PESSOAL})
        self.assertEqual(resp.status_code, 403)

    def test_g_h_i_reset_administrativo_e_acl(self):
        inep, temp, dados = self._criar_escola()
        dash = _json(self.client.get("/api/dashboard"))[0]
        gestor_id = next(g["id"] for g in dash["gestores"] if g["login"] == inep)
        escola_id = dados["id"]
        concluir_primeiro_acesso(self.client, inep, temp)
        cpf = "6810%07d" % self._proximo()
        cpf = (cpf + "00000000000")[:11]
        prof = self.client.post("/api/professores", json={
            "nome": "Prof Reset", "cpf": cpf, "escola_id": escola_id,
        })
        self.assertEqual(prof.status_code, 200, _json(prof)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        usuario_prof = next(p["usuarioId"] for p in dash["professores"] if p["cpf"] == cpf)
        concluir_primeiro_acesso(self.client, cpf, senha_temporaria(prof))

        self._login_admin()
        reset = self.client.post("/api/usuarios/%s/resetar-senha" % usuario_prof)
        self.assertEqual(reset.status_code, 200, _json(reset)[1])
        nova = senha_temporaria(reset)
        self.assertTrue(nova)
        self.assertGreaterEqual(len(nova), 12)
        blob = str(_json(reset)[0])
        with self.app.app_context():
            from src import db
            from src.models import AuditLog, Usuario
            alvo = db.session.get(Usuario, usuario_prof)
            self.assertTrue(alvo.primeiro_acesso)
            self.assertNotIn(alvo.senha_hash, blob)
            logs = AuditLog.query.filter_by(acao="SENHA_RESETADA").all()
            self.assertTrue(logs)
            for log in logs:
                texto = str(log.dados_novos or "") + str(log.dados_anteriores or "")
                self.assertNotIn(nova, texto)
                self.assertNotIn("senha_hash", texto.casefold())

        self.client.post("/api/auth/logout")
        login_temp = self.client.post("/api/auth/login", json={"login": cpf, "senha": nova})
        self.assertEqual(login_temp.status_code, 200, _json(login_temp)[1])
        self.assertTrue(_json(login_temp)[0]["primeiroAcesso"])

        self.client.post("/api/auth/logout")
        concluir_primeiro_acesso(self.client, cpf, nova)
        resp = self.client.post("/api/usuarios/%s/resetar-senha" % gestor_id)
        self.assertEqual(resp.status_code, 403)

        self._login_admin()
        inep_b, temp_b, _dados_b = self._criar_escola()
        dash = _json(self.client.get("/api/dashboard"))[0]
        gestor_b = next(g["id"] for g in dash["gestores"] if g["login"] == inep_b)
        concluir_primeiro_acesso(self.client, inep_b, temp_b)
        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"login": inep, "senha": SENHA_PESSOAL})
        resp = self.client.post("/api/usuarios/%s/resetar-senha" % gestor_b)
        self.assertEqual(resp.status_code, 403)
        me = _json(self.client.get("/api/me"))[0]
        resp = self.client.post("/api/usuarios/%s/resetar-senha" % me["id"])
        self.assertEqual(resp.status_code, 403)

    def test_j_escalada_de_papel_recusada(self):
        inep, temp, dados = self._criar_escola()
        dash = _json(self.client.get("/api/dashboard"))[0]
        gestor_id = next(g["id"] for g in dash["gestores"] if g["login"] == inep)
        concluir_primeiro_acesso(self.client, inep, temp)
        resp = self.client.put("/api/editar/usuario/%s" % gestor_id, json={
            "login": inep,
            "nome": "Gestor",
            "papel": "dirigente",
            "escola_id": 99999,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Usuario
            u = db.session.get(Usuario, gestor_id)
            self.assertEqual(u.papel, "gestor_escolar")
            self.assertNotEqual(u.escola_id, 99999)
        resp = self.client.post("/api/escolas/%s/diretor" % dados["id"])
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola Forjada", "inep": "68999999",
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 403)

    def test_k_l_m_n_csrf(self):
        login_sem = self.client.post(
            "/api/auth/login",
            json={"login": "admin", "senha": SENHA_DEMO_ADMIN},
            csrf=False,
        )
        self.assertEqual(login_sem.status_code, 400)
        self.assertEqual(_json(login_sem)[1], MSG_CSRF)

        token_ruim = self.client.post(
            "/api/auth/login",
            json={"login": "admin", "senha": SENHA_DEMO_ADMIN},
            headers={"X-CSRF-Token": "token-invalido"},
            csrf=False,
        )
        self.assertEqual(token_ruim.status_code, 400)
        self.assertEqual(_json(token_ruim)[1], MSG_CSRF)

        token = obter_csrf(self.client)
        login_ok = self.client.post(
            "/api/auth/login",
            json={"login": "admin", "senha": SENHA_DEMO_ADMIN},
            headers={"X-CSRF-Token": token},
            csrf=False,
        )
        self.assertEqual(login_ok.status_code, 200, _json(login_ok)[1])

        me = self.client.get("/api/me", csrf=False)
        self.assertEqual(me.status_code, 200)
        dash = self.client.get("/api/dashboard", csrf=False)
        self.assertEqual(dash.status_code, 200)

        mut = self.client.post(
            "/api/disciplinas",
            json={"nome": "Disc CSRF %s" % self._proximo()},
            csrf=False,
        )
        self.assertEqual(mut.status_code, 400)
        self.assertEqual(_json(mut)[1], MSG_CSRF)

    def test_o_cors_nao_aberto(self):
        resp = self.client.get("/api/me", headers={"Origin": "https://evil.example"})
        self.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))
        self._login_admin()
        resp = self.client.get("/api/dashboard", headers={"Origin": "https://evil.example"})
        self.assertIsNone(resp.headers.get("Access-Control-Allow-Origin"))
        self.assertNotEqual(resp.headers.get("Access-Control-Allow-Origin"), "*")

    def test_p_xff_spoof_ignorado_por_padrao(self):
        self.assertNotIsInstance(self.app.wsgi_app, ProxyFix)
        self._login_admin()
        n = self._proximo()
        inep = "6820%04d" % n
        resp = self.client.post(
            "/api/escolas",
            json={
                "nome": "Escola XFF %s" % inep,
                "inep": inep,
                "oferta_infantil": True,
                "oferta_fundamental": True,
            },
            headers={"X-Forwarded-For": "9.9.9.9", "X-Real-IP": "8.8.8.8"},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import AuditLog
            log = AuditLog.query.order_by(AuditLog.id.desc()).first()
            self.assertIsNotNone(log.ip)
            self.assertNotEqual(log.ip, "9.9.9.9")
            self.assertNotEqual(log.ip, "8.8.8.8")

    def test_r_headers_csp(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(resp.headers.get("Content-Security-Policy"), CSP_VALOR)
        self.assertIsNone(resp.headers.get("Strict-Transport-Security"))
        self.app.config["EDUCAMAIS_ENV"] = "production"
        self.app.config["SESSION_COOKIE_SECURE"] = True
        try:
            prod = self.client.get("/health")
            self.assertEqual(prod.headers.get("Strict-Transport-Security"), HSTS_VALOR)
        finally:
            self.app.config["EDUCAMAIS_ENV"] = "testing"
            self.app.config["SESSION_COOKIE_SECURE"] = False

    def test_s_upload_traversal_nao_baixa(self):
        resp = self.client.get("/static/uploads/../config.py")
        self.assertEqual(resp.status_code, 404)
        self._login_admin()
        resp = self.client.get("/api/planejamentos/1/arquivo")
        self.assertIn(resp.status_code, (403, 404))

    def test_t_500_sem_traceback(self):
        self._login_admin()
        antigo_testing = self.app.config["TESTING"]
        antigo_debug = self.app.config["DEBUG"]
        self.app.config["TESTING"] = False
        self.app.config["DEBUG"] = False
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        try:
            with patch("src.routes.spa.user_payload", side_effect=RuntimeError(
                "TRACE-HARD C:\\interno\\app.py SELECT * FROM usuario"
            )):
                resp = self.client.get("/api/me")
            self.assertEqual(resp.status_code, 500)
            corpo = resp.get_data(as_text=True)
            self.assertNotIn("TRACE-HARD", corpo)
            self.assertNotIn("C:\\interno", corpo)
            self.assertNotIn("SELECT * FROM", corpo)
            self.assertNotIn("Traceback", corpo)
            self.assertEqual(_json(resp)[1], MENSAGEM_ERRO_INTERNO)
        finally:
            self.app.config["TESTING"] = antigo_testing
            self.app.config["DEBUG"] = antigo_debug

    def test_u_health_saudavel(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data, error = _json(resp)
        self.assertIsNone(error)
        self.assertEqual(data["status"], "ok")
        texto = resp.get_data(as_text=True)
        self.assertNotIn("SECRET", texto)
        self.assertNotIn("postgresql", texto.casefold())
        self.assertNotIn("senha", texto.casefold())

    def test_v_health_banco_indisponivel(self):
        from src import db
        with patch.object(db.session, "execute", side_effect=RuntimeError("down")):
            resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 503)
        data, error = _json(resp)
        self.assertEqual(data["status"], "unhealthy")
        self.assertIn("banco", error.lower())
        self.assertNotIn("Traceback", resp.get_data(as_text=True))

    def test_w_logs_sem_segredos(self):
        registros = io.StringIO()
        handler = logging.StreamHandler(registros)
        handler.setLevel(logging.DEBUG)
        logger = logging.getLogger("educamais")
        logger.addHandler(handler)
        try:
            token = obter_csrf(self.client)
            self.client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
            self.client.post("/api/auth/login", json={"login": "admin", "senha": "secreta-nao-logar"})
        finally:
            logger.removeHandler(handler)
        saida = registros.getvalue()
        self.assertNotIn(SENHA_DEMO_ADMIN, saida)
        self.assertNotIn("secreta-nao-logar", saida)
        self.assertNotIn(CHAVE_SECRET_DEV_TESTE, saida)
        if token:
            self.assertNotIn(token, saida)

    def test_sucesso_login_limpa_lockout(self):
        inep, temp, _ = self._criar_escola()
        concluir_primeiro_acesso(self.client, inep, temp)
        self.client.post("/api/auth/logout")
        self.client.post("/api/auth/login", json={"login": inep, "senha": "errada"})
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": SENHA_PESSOAL})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import TentativaLogin
            self.assertIsNone(db.session.get(TentativaLogin, inep.lower()))

    def test_frontend_envia_csrf(self):
        texto = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "api.js").read_text(encoding="utf-8")
        self.assertIn("X-CSRF-Token", texto)
        self.assertIn("/api/csrf-token", texto)


class HardeningProxyFixTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        from src.config import Config
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
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

    def test_q_proxyfix_so_explicito(self):
        from src import create_app
        app = create_app()
        self.assertFalse(app.config.get("EDUCAMAIS_PROXY_FIX"))
        self.assertNotIsInstance(app.wsgi_app, ProxyFix)

        os.environ["EDUCAMAIS_PROXY_FIX"] = "1"
        os.environ["EDUCAMAIS_TRUSTED_PROXY_COUNT"] = "1"
        app2 = create_app()
        self.assertTrue(app2.config.get("EDUCAMAIS_PROXY_FIX"))
        self.assertIsInstance(app2.wsgi_app, ProxyFix)
        client = app2.test_client()
        client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        n = "68300001"
        resp = client.post(
            "/api/escolas",
            json={
                "nome": "Escola Proxy",
                "inep": n,
                "oferta_infantil": True,
                "oferta_fundamental": True,
            },
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with app2.app_context():
            from src.models import AuditLog
            log = AuditLog.query.order_by(AuditLog.id.desc()).first()
            self.assertEqual(log.ip, "203.0.113.9")


class HardeningMigrationLockoutTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        from src.config import Config
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
        apontar_banco_teste("sqlite:///" + Path(self._db_path).resolve().as_posix())
        os.environ["EDUCAMAIS_ENV"] = "testing"
        os.environ["EDUCAMAIS_DEMO"] = "0"

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

    def test_upgrade_downgrade_upgrade_sqlite(self):
        from flask_migrate import downgrade, upgrade
        from sqlalchemy import inspect
        from src import create_app, db
        from src.migracoes import revisao_atual

        app = create_app(preparar_schema=False)
        with app.app_context():
            upgrade()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            self.assertIn("tentativa_login", inspect(db.engine).get_table_names())
            downgrade(revision=REVISION_AUDIT_LOG)
            inspect(db.engine).clear_cache()
            self.assertEqual(revisao_atual(), REVISION_AUDIT_LOG)
            self.assertNotIn("tentativa_login", inspect(db.engine).get_table_names())
            upgrade()
            inspect(db.engine).clear_cache()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            self.assertIn("tentativa_login", inspect(db.engine).get_table_names())


@unittest.skipUnless(
    URI_PG,
    "EDUCAMAIS_TEST_DATABASE_URI ausente; PostgreSQL de testes pulado.",
)
class HardeningLockoutPostgresTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        from src.config import Config
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-lockout-pg"
        os.environ["EDUCAMAIS_DEMO"] = "0"
        apontar_banco_teste(URI_PG)

    def tearDown(self):
        from flask_migrate import upgrade
        from src import create_app, db
        try:
            app = create_app(preparar_schema=False)
            with app.app_context():
                upgrade()
                db.session.remove()
                db.engine.dispose()
        finally:
            os.environ.clear()
            os.environ.update(self._env)
            preparar_ambiente_teste(demo=True)
            from src.config import Config
            Config.SQLALCHEMY_DATABASE_URI = self._old_uri

    def test_upgrade_downgrade_upgrade_postgresql(self):
        from flask_migrate import downgrade, upgrade
        from sqlalchemy import inspect
        from src import create_app, db
        from src.migracoes import revisao_atual

        app = create_app(preparar_schema=False)
        with app.app_context():
            self.assertEqual(db.engine.dialect.name, "postgresql")
            upgrade()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            self.assertIn("tentativa_login", inspect(db.engine).get_table_names())
            downgrade(revision=REVISION_AUDIT_LOG)
            inspect(db.engine).clear_cache()
            self.assertEqual(revisao_atual(), REVISION_AUDIT_LOG)
            self.assertNotIn("tentativa_login", inspect(db.engine).get_table_names())
            upgrade()
            inspect(db.engine).clear_cache()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            self.assertIn("tentativa_login", inspect(db.engine).get_table_names())


if __name__ == "__main__":
    unittest.main()
