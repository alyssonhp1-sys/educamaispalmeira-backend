"""Estágio 4: infraestrutura AuditLog (append-only), sanitização e request_id."""
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import inspect, text

from src.auditoria import sanitizar_dados
from src.config import resolver_uri_teste_postgresql
from src.http import HEADER_REQUEST_ID
from src.migracoes import REVISION_CONSTRAINTS, REVISION_HEAD
from tests.support import SENHA_DEMO_ADMIN, preparar_ambiente_teste


URI_PG = resolver_uri_teste_postgresql()


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


def _como_dict(valor):
    if valor is None:
        return None
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        return json.loads(valor)
    return dict(valor)


class SanitizacaoAuditoriaTest(unittest.TestCase):
    def test_e_remove_senha(self):
        out = sanitizar_dados({"nome": "Ana", "senha": "nao-gravar"})
        self.assertEqual(out, {"nome": "Ana"})
        self.assertNotIn("senha", out)

    def test_f_remove_password_hash(self):
        out = sanitizar_dados({
            "login": "gestor",
            "password_hash": "hash",
            "Password_Hash": "outro",
        })
        self.assertEqual(out["login"], "gestor")
        self.assertNotIn("password_hash", out)
        self.assertNotIn("Password_Hash", out)

    def test_g_sanitizacao_recursiva(self):
        out = sanitizar_dados({
            "aluno": {"id": 3, "senha": "x", "nome": "João"},
            "meta": [{"token": "abc", "bimestre": 1}],
        })
        self.assertEqual(out["aluno"]["nome"], "João")
        self.assertEqual(out["aluno"]["id"], 3)
        self.assertNotIn("senha", out["aluno"])
        self.assertEqual(out["meta"][0]["bimestre"], 1)
        self.assertNotIn("token", out["meta"][0])

    def test_h_token_secret_secret_key_removidos(self):
        out = sanitizar_dados({
            "token": "t",
            "secret": "s",
            "secret_key": "k",
            "SECRET_KEY": "K",
            "nota": 8.0,
        })
        self.assertEqual(out, {"nota": 8.0})

    def test_i_dados_comuns_preservados(self):
        out = sanitizar_dados({
            "valor": 7.5,
            "bimestre": 11,
            "situacao": "aprovado",
            "ativo": True,
            "obs": None,
        })
        self.assertEqual(out["valor"], 7.5)
        self.assertEqual(out["bimestre"], 11)
        self.assertEqual(out["situacao"], "aprovado")
        self.assertTrue(out["ativo"])
        self.assertIsNone(out["obs"])

    def test_case_insensitive_e_camelcase(self):
        out = sanitizar_dados({
            "SENHA": "a",
            "Password": "b",
            "senhaHash": "c",
            "passwordHash": "d",
            "ok": 1,
        })
        self.assertEqual(out, {"ok": 1})

    def test_recusa_objeto_nao_json(self):
        class Modelo:
            senha_hash = "x"

        with self.assertRaises(TypeError):
            sanitizar_dados({"item": Modelo()})

    def test_y_preserva_secretaria(self):
        out = sanitizar_dados({
            "secretaria": "valor legítimo",
            "papel": "secretaria",
            "ok": 1,
        })
        self.assertEqual(out["secretaria"], "valor legítimo")
        self.assertEqual(out["papel"], "secretaria")
        self.assertEqual(out["ok"], 1)

    def test_z_remove_client_secret_access_token_password_hash(self):
        out = sanitizar_dados({
            "client_secret": "nao-gravar",
            "access_token": "nao-gravar",
            "password_hash": "nao-gravar",
            "AccessToken": "nao-gravar",
            "clientSecret": "nao-gravar",
            "secretaria": "valor legítimo",
            "senha_temporaria_gerada": True,
            "senha": "nao-gravar",
            "ok": True,
        })
        self.assertEqual(out["secretaria"], "valor legítimo")
        self.assertTrue(out["ok"])
        self.assertTrue(out["senha_temporaria_gerada"])
        self.assertNotIn("senha", out)
        self.assertNotIn("client_secret", out)
        self.assertNotIn("access_token", out)
        self.assertNotIn("password_hash", out)
        self.assertNotIn("AccessToken", out)
        self.assertNotIn("clientSecret", out)


class AuditLogAppTest(unittest.TestCase):
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

    def _contar_audit_outra_conexao(self):
        from src import db
        with db.engine.connect() as conn:
            return conn.execute(text("SELECT COUNT(*) FROM audit_log")).scalar()

    def test_a_criacao_audit_log(self):
        from src import db
        from src.auditoria import registrar_auditoria
        from src.models import AuditLog
        with self.app.app_context():
            antes = AuditLog.query.count()
            log = registrar_auditoria(
                "criar",
                "Disciplina",
                entidade_id=9,
                escola_id=None,
                dados_novos={"nome": "Artes Audit"},
            )
            db.session.commit()
            self.assertIsNotNone(log.id)
            self.assertEqual(AuditLog.query.count(), antes + 1)
            gravado = db.session.get(AuditLog, log.id)
            self.assertEqual(gravado.acao, "criar")
            self.assertEqual(gravado.entidade, "Disciplina")
            self.assertEqual(gravado.entidade_id, 9)

    def test_b_usuario_id_nullable_sistema(self):
        from src import db
        from src.auditoria import registrar_auditoria
        from src.models import AuditLog
        with self.app.app_context():
            log = registrar_auditoria("backfill", "Matricula", entidade_id=1)
            db.session.commit()
            self.assertIsNone(log.usuario_id)
            self.assertIsNone(db.session.get(AuditLog, log.id).usuario_id)

    def test_c_escola_id_nullable(self):
        from src import db
        from src.auditoria import registrar_auditoria
        from src.models import AuditLog
        with self.app.app_context():
            log = registrar_auditoria("alterar", "ConfiguracaoAcademica", escola_id=None)
            db.session.commit()
            self.assertIsNone(db.session.get(AuditLog, log.id).escola_id)

    def test_d_json_anterior_novo_preservado(self):
        from src import db
        from src.auditoria import registrar_auditoria
        from src.models import AuditLog
        anterior = {"valor": 5.0, "bimestre": 11, "ativo": False}
        novo = {"valor": 8.5, "bimestre": 11, "ativo": True, "obs": None}
        with self.app.app_context():
            log = registrar_auditoria(
                "atualizar",
                "RegistroNota",
                entidade_id=44,
                dados_anteriores=anterior,
                dados_novos=novo,
            )
            db.session.commit()
            gravado = db.session.get(AuditLog, log.id)
            self.assertEqual(_como_dict(gravado.dados_anteriores), anterior)
            self.assertEqual(_como_dict(gravado.dados_novos)["valor"], 8.5)
            self.assertTrue(_como_dict(gravado.dados_novos)["ativo"])
            self.assertIsNone(_como_dict(gravado.dados_novos)["obs"])

    def test_e_a_h_sanitizacao_na_gravacao(self):
        from src import db
        from src.auditoria import registrar_auditoria
        from src.models import AuditLog
        with self.app.app_context():
            log = registrar_auditoria(
                "atualizar",
                "Usuario",
                entidade_id=1,
                dados_anteriores={"login": "a", "senha": "velha", "password_hash": "h1"},
                dados_novos={
                    "login": "a",
                    "senha": "nova",
                    "nested": {"token": "t", "secret": "s", "papel": "gestor"},
                },
            )
            db.session.commit()
            ant = _como_dict(log.dados_anteriores)
            novo = _como_dict(log.dados_novos)
            self.assertEqual(ant, {"login": "a"})
            self.assertNotIn("senha", json.dumps(ant))
            self.assertNotIn("password_hash", json.dumps(ant))
            self.assertEqual(novo["login"], "a")
            self.assertEqual(novo["nested"]["papel"], "gestor")
            blob = json.dumps(novo)
            self.assertNotIn("senha", blob)
            self.assertNotIn("token", blob)
            self.assertNotIn("secret", blob)

    def test_j_timestamp_utc(self):
        from src import db
        from src.auditoria import registrar_auditoria
        from src.models import AuditLog
        with self.app.app_context():
            antes = datetime.utcnow()
            log = registrar_auditoria("teste_utc", "AuditLog")
            db.session.commit()
            depois = datetime.utcnow()
            gravado = db.session.get(AuditLog, log.id)
            self.assertIsNotNone(gravado.data_hora)
            self.assertLessEqual(antes - timedelta(seconds=2), gravado.data_hora)
            self.assertLessEqual(gravado.data_hora, depois + timedelta(seconds=2))
            local = datetime.now()
            utcnow = datetime.utcnow()
            if abs((local - utcnow).total_seconds()) > 90:
                delta_local = abs((gravado.data_hora - local).total_seconds())
                delta_utc = abs((gravado.data_hora - utcnow).total_seconds())
                self.assertLess(delta_utc, delta_local)

    def test_k_request_id_criado_na_request(self):
        resp = self.client.get("/api/me")
        rid = resp.headers.get(HEADER_REQUEST_ID)
        self.assertTrue(rid)
        uuid.UUID(rid)
        resp2 = self.client.get("/api/me")
        rid2 = resp2.headers.get(HEADER_REQUEST_ID)
        self.assertNotEqual(rid, rid2)
        with self.app.test_client() as client:
            client.get("/api/me")
            from flask import g
            from src import db
            from src.auditoria import registrar_auditoria
            from src.models import AuditLog
            rid_g = g.request_id
            log = registrar_auditoria("consulta", "Usuario")
            db.session.commit()
            self.assertEqual(log.request_id, rid_g)
            self.assertEqual(db.session.get(AuditLog, log.id).request_id, rid_g)

    def test_l_ip_remote_addr(self):
        with self.app.test_client() as client:
            client.get(
                "/api/me",
                environ_base={"REMOTE_ADDR": "203.0.113.40"},
            )
            from src import db
            from src.auditoria import registrar_auditoria
            log = registrar_auditoria("consulta", "Usuario")
            db.session.commit()
            self.assertEqual(log.ip, "203.0.113.40")

    def test_m_x_forwarded_for_nao_substitui_remote_addr(self):
        with self.app.test_client() as client:
            client.get(
                "/api/me",
                headers={"X-Forwarded-For": "198.51.100.7, 192.0.2.1"},
                environ_base={"REMOTE_ADDR": "203.0.113.9"},
            )
            from src import db
            from src.auditoria import registrar_auditoria
            log = registrar_auditoria("consulta", "Usuario")
            db.session.commit()
            self.assertEqual(log.ip, "203.0.113.9")
            self.assertNotEqual(log.ip, "198.51.100.7")

    def test_usuario_autenticado_preenchido(self):
        with self.app.test_client() as client:
            resp = client.post(
                "/api/auth/login",
                json={"login": "admin", "senha": SENHA_DEMO_ADMIN},
            )
            self.assertEqual(resp.status_code, 200, _json(resp)[1])
            from src import db
            from src.auditoria import registrar_auditoria
            from src.models import Usuario
            admin_id = Usuario.query.filter_by(login="admin").one().id
            log = registrar_auditoria("teste_login", "Usuario", entidade_id=admin_id)
            db.session.commit()
            self.assertEqual(log.usuario_id, admin_id)

    def test_n_mesma_transacao_commit(self):
        from src import db
        from src.auditoria import registrar_auditoria
        from src.models import AuditLog, Disciplina
        nome = "Disc Audit Commit %s" % uuid.uuid4().hex[:12]
        with self.app.app_context():
            n_log = self._contar_audit_outra_conexao()
            db.session.add(Disciplina(nome=nome))
            registrar_auditoria("criar", "Disciplina", dados_novos={"nome": nome})
            self.assertEqual(self._contar_audit_outra_conexao(), n_log)
            db.session.commit()
            self.assertEqual(self._contar_audit_outra_conexao(), n_log + 1)
            self.assertEqual(Disciplina.query.filter_by(nome=nome).count(), 1)
            self.assertEqual(
                AuditLog.query.filter_by(acao="criar", entidade="Disciplina").count(),
                AuditLog.query.filter(
                    AuditLog.acao == "criar", AuditLog.entidade == "Disciplina"
                ).count(),
            )

    def test_o_rollback_remove_alteracao_e_audit_log(self):
        from src import db
        from src.auditoria import registrar_auditoria
        from src.models import AuditLog, Disciplina
        nome = "Disc Audit Rollback %s" % uuid.uuid4().hex[:12]
        with self.app.app_context():
            n_disc = Disciplina.query.count()
            n_log = AuditLog.query.count()
            db.session.add(Disciplina(nome=nome))
            registrar_auditoria("criar", "Disciplina", dados_novos={"nome": nome})
            db.session.rollback()
            self.assertEqual(Disciplina.query.count(), n_disc)
            self.assertEqual(AuditLog.query.count(), n_log)
            self.assertIsNone(Disciplina.query.filter_by(nome=nome).first())
            self.assertEqual(self._contar_audit_outra_conexao(), n_log)

    def test_p_sem_rota_edicao_exclusao_audit_log(self):
        from werkzeug.middleware.proxy_fix import ProxyFix
        self.assertNotIsInstance(self.app.wsgi_app, ProxyFix)
        achados = []
        for rule in self.app.url_map.iter_rules():
            texto = "%s %s" % (rule.endpoint or "", rule.rule or "")
            baixo = texto.lower()
            if "audit" in baixo or "auditoria" in baixo:
                achados.append((rule.rule, sorted(rule.methods or [])))
        permitidas = {"/api/auditoria", "/api/auditoria/filtros"}
        for regra, metodos in achados:
            self.assertIn(regra, permitidas, regra)
            self.assertIn("GET", metodos)
            self.assertFalse(set(metodos) & {"POST", "PUT", "PATCH", "DELETE"}, regra)
        self.assertEqual({r for r, _ in achados}, permitidas)
        self.client.post(
            "/api/auth/login",
            json={"login": "admin", "senha": SENHA_DEMO_ADMIN},
        )
        for metodo, path in (
            ("POST", "/api/auditoria"),
            ("PUT", "/api/audit-log/1"),
            ("PATCH", "/api/audit-log/1"),
            ("DELETE", "/api/audit-log/1"),
            ("PUT", "/api/auditoria"),
            ("PUT", "/api/auditoria/1"),
            ("PATCH", "/api/auditoria/1"),
            ("DELETE", "/api/auditoria/1"),
            ("DELETE", "/api/auditoria"),
        ):
            resp = self.client.open(path, method=metodo)
            self.assertNotEqual(resp.status_code, 200, path)
            self.assertLess(resp.status_code, 500, path)
        resp = self.client.delete("/api/excluir/audit_log/1")
        data, err = _json(resp)
        self.assertNotEqual(resp.status_code, 200)
        self.assertTrue(err)
        resp = self.client.put("/api/editar/audit_log/1", json={"acao": "x"})
        self.assertNotEqual(resp.status_code, 200)

    def test_fora_de_request_funciona(self):
        from src import db
        from src.auditoria import registrar_auditoria
        with self.app.app_context():
            log = registrar_auditoria("sistema", "AnoLetivo", entidade_id=None)
            db.session.commit()
            self.assertIsNone(log.usuario_id)
            self.assertIsNone(log.request_id)
            self.assertIsNone(log.ip)


class AuditLogMigrationSqliteTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
        from src.config import Config
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + Path(self._db_path).resolve().as_posix()
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-audit-migration"
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

    def test_q_upgrade_sqlite_cria_audit_log(self):
        from flask_migrate import upgrade
        from src import create_app, db
        from src.migracoes import revisao_atual, revisao_head

        app = create_app(preparar_schema=False)
        with app.app_context():
            upgrade()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            self.assertEqual(revisao_atual(), revisao_head())
            insp = inspect(db.engine)
            self.assertIn("audit_log", insp.get_table_names())
            cols = {c["name"] for c in insp.get_columns("audit_log")}
            for nome in (
                "id", "usuario_id", "acao", "entidade", "entidade_id",
                "escola_id", "data_hora", "request_id", "ip",
                "dados_anteriores", "dados_novos",
            ):
                self.assertIn(nome, cols, nome)
            idx = {i["name"] for i in insp.get_indexes("audit_log") if i.get("name")}
            self.assertIn("ix_audit_log_usuario_id", idx)
            self.assertIn("ix_audit_log_escola_id", idx)
            self.assertIn("ix_audit_log_data_hora", idx)
            self.assertIn("ix_audit_log_entidade_id", idx)

    def test_r_downgrade_upgrade_audit_log(self):
        from flask_migrate import downgrade, upgrade
        from src import create_app, db
        from src.migracoes import revisao_atual

        app = create_app(preparar_schema=False)
        with app.app_context():
            upgrade()
            self.assertIn("audit_log", inspect(db.engine).get_table_names())
            downgrade(revision=REVISION_CONSTRAINTS)
            self.assertEqual(revisao_atual(), REVISION_CONSTRAINTS)
            inspect(db.engine).clear_cache()
            self.assertNotIn("audit_log", inspect(db.engine).get_table_names())
            upgrade()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            inspect(db.engine).clear_cache()
            self.assertIn("audit_log", inspect(db.engine).get_table_names())

    def test_s_autogenerate_sem_drift(self):
        from alembic.autogenerate import compare_metadata
        from alembic.runtime.migration import MigrationContext
        from flask_migrate import upgrade
        from src import create_app, db
        from src.migracoes import revisao_atual

        app = create_app(preparar_schema=False)
        with app.app_context():
            upgrade()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            with db.engine.connect() as conn:
                ctx = MigrationContext.configure(
                    conn, opts={"render_as_batch": True},
                )
                diffs = compare_metadata(ctx, db.metadata)
            self.assertEqual(diffs, [], diffs)


@unittest.skipUnless(
    URI_PG,
    "Defina EDUCAMAIS_TEST_DATABASE_URI com PostgreSQL descartável (não produção/migração).",
)
class AuditLogMigrationPostgresTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        from src.config import Config
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        from tests.support import apontar_banco_teste
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-audit-pg"
        os.environ["EDUCAMAIS_DEMO"] = "0"
        apontar_banco_teste(URI_PG)

    def tearDown(self):
        from flask_migrate import upgrade
        from src import create_app
        try:
            app = create_app(preparar_schema=False)
            with app.app_context():
                upgrade()
                from src import db
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
        from src import create_app, db
        from src.migracoes import revisao_atual

        app = create_app(preparar_schema=False)
        with app.app_context():
            self.assertEqual(db.engine.dialect.name, "postgresql")
            upgrade()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            self.assertIn("audit_log", inspect(db.engine).get_table_names())
            downgrade(revision=REVISION_CONSTRAINTS)
            inspect(db.engine).clear_cache()
            self.assertEqual(revisao_atual(), REVISION_CONSTRAINTS)
            self.assertNotIn("audit_log", inspect(db.engine).get_table_names())
            upgrade()
            inspect(db.engine).clear_cache()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            self.assertIn("audit_log", inspect(db.engine).get_table_names())
            with db.engine.connect() as conn:
                ctx_opts = {}
                from alembic.runtime.migration import MigrationContext
                from alembic.autogenerate import compare_metadata
                ctx = MigrationContext.configure(conn, opts=ctx_opts)
                diffs = compare_metadata(ctx, db.metadata)
            self.assertEqual(diffs, [], diffs)
