import os
import tempfile
import unittest
from pathlib import Path

from src.config import CHAVE_SECRET_DEV_TESTE, ConfiguracaoInsegura
from src.passwords import SENHA_DEMO_ADMIN, verificar_senha
from tests.support import (
    json_data,
    preparar_ambiente_teste,
    senha_temporaria,
    criar_app_migrada,
)


def _json(resp):
    return json_data(resp)


class SegurancaConfiguracaoTest(unittest.TestCase):
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

    def _app(self):
        from src import create_app
        app = create_app()
        app.testing = True
        return app

    def test_01_producao_sem_secret_key_falha(self):
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ.pop("EDUCAMAIS_SECRET_KEY", None)
        os.environ.pop("EDUCAMAIS_DEMO", None)
        with self.assertRaises(ConfiguracaoInsegura):
            self._app()

    def test_02_producao_com_secret_key_inicializa(self):
        from flask import Flask
        from src.config import aplicar_configuracao_runtime
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-producao-de-teste-nao-reutilizar"
        os.environ["EDUCAMAIS_DATABASE_URI"] = "postgresql+psycopg://u:p@localhost/educamais"
        os.environ.pop("EDUCAMAIS_DEMO", None)
        app = Flask("cfg-prod")
        aplicar_configuracao_runtime(app)
        self.assertEqual(app.config["SECRET_KEY"], "chave-producao-de-teste-nao-reutilizar")
        self.assertFalse(app.config["DEBUG"])
        self.assertFalse(app.config["EDUCAMAIS_DEMO"])
        self.assertTrue(app.config["SESSION_COOKIE_SECURE"])

    def test_02b_producao_sqlite_e_recusada(self):
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-producao-de-teste-nao-reutilizar"
        os.environ.pop("EDUCAMAIS_DEMO", None)
        with self.assertRaises(ConfiguracaoInsegura) as ctx:
            self._app()
        self.assertIn("PostgreSQL", str(ctx.exception))

    def test_03_modo_padrao_nao_cria_admin_demo(self):
        os.environ["EDUCAMAIS_ENV"] = "testing"
        os.environ["EDUCAMAIS_SECRET_KEY"] = CHAVE_SECRET_DEV_TESTE
        os.environ.pop("EDUCAMAIS_DEMO", None)
        app = self._app()
        with app.app_context():
            from src.models import Usuario
            self.assertIsNone(Usuario.query.filter_by(login="admin").first())

    def test_04_modo_demo_explicito_cria_admin(self):
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = CHAVE_SECRET_DEV_TESTE
        os.environ["EDUCAMAIS_DEMO"] = "1"
        app = criar_app_migrada()
        app.testing = True
        with app.app_context():
            from src.models import Usuario
            admin = Usuario.query.filter_by(login="admin").first()
            self.assertIsNotNone(admin)
            self.assertTrue(verificar_senha(admin.senha_hash, SENHA_DEMO_ADMIN))

    def test_05_duas_contas_recebem_senhas_diferentes(self):
        preparar_ambiente_teste(demo=True)
        app = self._app()
        client = app.test_client()
        client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        r1 = client.post("/api/escolas", json={
            "nome": "Escola Temp A", "inep": "77000001",
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        r2 = client.post("/api/escolas", json={
            "nome": "Escola Temp B", "inep": "77000002",
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        self.assertEqual(r1.status_code, 200, _json(r1)[1])
        self.assertEqual(r2.status_code, 200, _json(r2)[1])
        s1 = senha_temporaria(r1)
        s2 = senha_temporaria(r2)
        self.assertTrue(s1)
        self.assertTrue(s2)
        self.assertNotEqual(s1, s2)
        self.assertGreaterEqual(len(s1), 12)
        self.assertGreaterEqual(len(s2), 12)

    def test_06_senha_temporaria_nao_fica_em_texto_puro(self):
        preparar_ambiente_teste(demo=True)
        app = self._app()
        client = app.test_client()
        client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        resp = client.post("/api/escolas", json={
            "nome": "Escola Hash", "inep": "77000003",
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        temp = senha_temporaria(resp)
        with app.app_context():
            from src.models import Usuario
            usuario = Usuario.query.filter_by(login="77000003").first()
            self.assertIsNotNone(usuario.senha_hash)
            self.assertNotEqual(usuario.senha_hash, temp)
            self.assertNotIn(temp, usuario.senha_hash)
            self.assertTrue(verificar_senha(usuario.senha_hash, temp))

    def test_07_primeiro_acesso_obrigatorio(self):
        preparar_ambiente_teste(demo=True)
        app = self._app()
        client = app.test_client()
        client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        resp = client.post("/api/escolas", json={
            "nome": "Escola Primeiro", "inep": "77000004",
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        temp = senha_temporaria(resp)
        client.post("/api/auth/logout")
        login = client.post("/api/auth/login", json={"login": "77000004", "senha": temp})
        self.assertEqual(login.status_code, 200, _json(login)[1])
        self.assertTrue(_json(login)[0]["primeiroAcesso"])
        bloqueado = client.post("/api/turmas", json={"nome": "X", "ano_letivo": 2026, "etapa": "fundamental"})
        self.assertEqual(bloqueado.status_code, 403)

    def test_08_senha_temporaria_nao_aparece_em_consulta(self):
        preparar_ambiente_teste(demo=True)
        app = self._app()
        client = app.test_client()
        client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        resp = client.post("/api/escolas", json={
            "nome": "Escola Consulta", "inep": "77000005",
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        temp = senha_temporaria(resp)
        dash = _json(client.get("/api/dashboard"))[0]
        texto = str(dash)
        self.assertNotIn(temp, texto)
        self.assertFalse(any("senhaTemporaria" in (g or {}) for g in dash.get("gestores", [])))
        me = _json(client.get("/api/me"))[0]
        self.assertNotIn("senhaTemporaria", me or {})
        self.assertNotIn("senha_temporaria", me or {})

    def test_09_producao_nao_recebe_senha_universal(self):
        from src.config import demo_habilitado
        os.environ["EDUCAMAIS_DEMO"] = "1"
        self.assertFalse(demo_habilitado("production"))
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-producao-de-teste-nao-reutilizar"
        with self.assertRaises(ConfiguracaoInsegura):
            self._app()

    def test_10_teste_funciona_sem_segredo_de_producao(self):
        os.environ["EDUCAMAIS_ENV"] = "testing"
        os.environ.pop("EDUCAMAIS_SECRET_KEY", None)
        os.environ["EDUCAMAIS_DEMO"] = "1"
        app = self._app()
        self.assertEqual(app.config["SECRET_KEY"], CHAVE_SECRET_DEV_TESTE)
        client = app.test_client()
        resp = client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_11_uri_prefere_educamais_database_uri(self):
        from src.config import Config, resolver_uri_banco
        uri = resolver_uri_banco("sqlite:///instance/via_uri.db", Config.ROOT_DIR)
        self.assertTrue(uri.replace("\\", "/").endswith("/instance/via_uri.db"))

    def test_12_url_e_alias_quando_uri_ausente(self):
        os.environ.pop("EDUCAMAIS_DATABASE_URI", None)
        os.environ["EDUCAMAIS_DATABASE_URL"] = "sqlite:///instance/via_url.db"
        from src.config import Config, resolver_uri_banco
        uri = resolver_uri_banco(None, Config.ROOT_DIR)
        self.assertTrue(uri.replace("\\", "/").endswith("/instance/via_url.db"))

    def test_13_postgres_normaliza_para_psycopg(self):
        from src.config import resolver_uri_banco
        uri = resolver_uri_banco("postgresql://user:pass@localhost/educamais")
        self.assertEqual(uri, "postgresql+psycopg://user:pass@localhost/educamais")

    def test_14_postgres_herokulike_normaliza(self):
        from src.config import resolver_uri_banco
        uri = resolver_uri_banco("postgres://user:pass@localhost:5432/educamais")
        self.assertEqual(uri, "postgresql+psycopg://user:pass@localhost:5432/educamais")

    def test_15_mascarar_uri_oculta_senha(self):
        from src.config import mascarar_uri_banco
        original = "postgresql+psycopg://user:segredo@localhost:5432/educamais"
        mascarada = mascarar_uri_banco(original)
        self.assertNotIn("segredo", mascarada)
        self.assertIn("localhost", mascarada)
        self.assertIn("educamais", mascarada)
        self.assertNotEqual(mascarada, original)

    def test_16_producao_sem_uri_falha(self):
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-producao-de-teste-nao-reutilizar"
        os.environ.pop("EDUCAMAIS_DEMO", None)
        os.environ.pop("EDUCAMAIS_DATABASE_URI", None)
        os.environ.pop("EDUCAMAIS_DATABASE_URL", None)
        with self.assertRaises(ConfiguracaoInsegura) as ctx:
            self._app()
        self.assertIn("EDUCAMAIS_DATABASE_URI", str(ctx.exception))

    def test_17_engine_options_sqlite_nao_vao_ao_postgres(self):
        from src.config import resolver_opcoes_engine
        sqlite_opts = resolver_opcoes_engine("sqlite:///instance/x.db")
        pg_opts = resolver_opcoes_engine(
            "postgresql+psycopg://usuario:senha@localhost:5432/educamais"
        )
        self.assertIn("check_same_thread", sqlite_opts.get("connect_args", {}))
        self.assertEqual(pg_opts, {})
        self.assertNotIn("timeout", pg_opts.get("connect_args", {}))


if __name__ == "__main__":
    unittest.main()
