from flask import Flask, request, send_from_directory, redirect
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from flask_migrate import Migrate
from src.config import Config, aplicar_configuracao_runtime, demo_habilitado, resolver_ambiente
from src.http import (
    err,
    aplicar_headers_seguranca,
    garantir_request_id,
    registrar_handlers_erro,
    PREFIXOS_API_JSON_404,
)
import os

db = SQLAlchemy()
migrate = Migrate()
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = None
login_manager.login_message = "Faça login para acessar."
login_manager.login_message_category = "info"

from src.csrf import ClienteTesteCsrf, validar_csrf_se_preciso
from src.health import resposta_health
from src.logs import configurar_logging, log_startup
from src.proxy import aplicar_proxy_fix

_DISCIPLINAS_DEMO = [
    "Matemática", "Português", "História", "Geografia", "Ciências", "Artes", "Ed. Física",
]


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/") or request.is_json:
        return err("Não autenticado", 401)
    return redirect("/login")


def _bootstrap_demo():
    """Só com EDUCAMAIS_DEMO=1 fora de produção. Não redefine senha existente."""
    from src import models
    from src.passwords import SENHA_DEMO_ADMIN, hash_senha

    if models.Usuario.query.filter_by(login="admin").first():
        return
    db.session.add(models.Usuario(
        login="admin",
        senha_hash=hash_senha(SENHA_DEMO_ADMIN),
        papel="dirigente",
        nome="Administrador",
        primeiro_acesso=False,
    ))
    for d in _DISCIPLINAS_DEMO:
        if not models.Disciplina.query.filter_by(nome=d).first():
            db.session.add(models.Disciplina(nome=d))
    db.session.commit()


def _path_e_upload_estatico(path):
    """True só para /static/uploads e filhos. Não afeta /static/img nem outros assets."""
    p = (path or "").replace("\\", "/")
    while "//" in p:
        p = p.replace("//", "/")
    p = p.lower().rstrip("/")
    return p == "/static/uploads" or p.startswith("/static/uploads/")


def create_app(preparar_schema=None):
    """Factory da aplicação.

    preparar_schema=False: não cria/altera schema e não exige Alembic
    (usado por flask db, flask educamais e quem vai rodar upgrade em seguida).
    """
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.test_client_class = ClienteTesteCsrf
    app.config.from_object(Config)
    aplicar_configuracao_runtime(app)
    from src.config import resolver_opcoes_engine, uri_e_sqlite
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = resolver_opcoes_engine(uri)
    Config.init_app(app)
    aplicar_proxy_fix(app)
    configurar_logging(app)

    db.init_app(app)
    from src.migracoes import diretorio_migrations, register_cli
    migrate.init_app(
        app, db,
        directory=diretorio_migrations(),
        render_as_batch=uri_e_sqlite(uri),
    )
    bcrypt.init_app(app)
    login_manager.init_app(app)
    register_cli(app)
    registrar_handlers_erro(app)

    @app.before_request
    def _identificar_request():
        garantir_request_id()

    @app.before_request
    def _proteger_csrf():
        return validar_csrf_se_preciso()

    @app.after_request
    def _headers_seguranca(response):
        return aplicar_headers_seguranca(response)

    @app.before_request
    def _recusar_uploads_publicos():
        if _path_e_upload_estatico(request.path):
            return err("Arquivo não encontrado", 404)

    from src.routes.auth import auth_bp
    from src.routes.api import api_bp
    from src.routes.spa import spa_bp
    from src.routes.calendario import cal_bp
    from src.routes.auditoria import aud_bp
    from src.routes.documentos_pdf import docs_pdf_bp
    from src.routes.trajetoria_legada import trajetoria_bp
    from src.routes.faltas_docentes import faltas_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(spa_bp)
    app.register_blueprint(cal_bp)
    app.register_blueprint(aud_bp)
    app.register_blueprint(docs_pdf_bp)
    app.register_blueprint(trajetoria_bp)
    app.register_blueprint(faltas_bp)

    @app.route("/health", methods=["GET"])
    def health():
        return resposta_health()

    from src import models  # noqa: F401 — metadata visível ao Alembic
    from src.migracoes import aplicar_schema_teste, comando_flask_admin, exigir_schema_migrado

    pular_schema = preparar_schema is False or comando_flask_admin()
    with app.app_context():
        if not pular_schema:
            ambiente = resolver_ambiente()
            if ambiente == "testing":
                aplicar_schema_teste()
            else:
                exigir_schema_migrado()
            if demo_habilitado():
                _bootstrap_demo()
            from src.passwords import completar_senhas_iniciais_ausentes
            from src.notas import garantir_configuracao_padrao
            from src.matriz_curricular import garantir_matriz_2026
            completar_senhas_iniciais_ausentes()
            garantir_configuracao_padrao()
            garantir_matriz_2026()
        log_startup(app)

    dist = os.path.join(Config.ROOT_DIR, "frontend", "dist")

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_spa(path):
        normalizado = (path or "").replace("\\", "/").lstrip("/")
        if any(normalizado == p.rstrip("/") or normalizado.startswith(p) for p in PREFIXOS_API_JSON_404):
            return err("Não encontrado", 404)
        file_path = os.path.join(dist, path)
        if path and os.path.isfile(file_path):
            return send_from_directory(dist, path)
        index = os.path.join(dist, "index.html")
        if os.path.isfile(index):
            return send_from_directory(dist, "index.html")
        return (
            "Frontend ainda não foi compilado. Rode npm install && npm run build em /frontend "
            "ou use npm run dev na porta 5173.",
            503,
        )

    return app
