"""Helpers da suíte. Credenciais fixas só em modo demo/teste explícito."""
import os

from src.config import CHAVE_SECRET_DEV_TESTE
from src.passwords import SENHA_DEMO_ADMIN

SENHA_PESSOAL = "teste1234"


def preparar_ambiente_teste(demo=True):
    os.environ["EDUCAMAIS_ENV"] = "testing"
    os.environ["EDUCAMAIS_SECRET_KEY"] = CHAVE_SECRET_DEV_TESTE
    os.environ.pop("EDUCAMAIS_DEBUG", None)
    os.environ.pop("EDUCAMAIS_PROXY_FIX", None)
    os.environ.pop("EDUCAMAIS_TRUSTED_PROXY_COUNT", None)
    os.environ.pop("EDUCAMAIS_LOGIN_MAX_TENTATIVAS", None)
    os.environ.pop("EDUCAMAIS_LOGIN_BLOQUEIO_SEGUNDOS", None)
    if demo:
        os.environ["EDUCAMAIS_DEMO"] = "1"
    else:
        os.environ.pop("EDUCAMAIS_DEMO", None)


def apontar_banco_teste(uri):
    """Aponta Config e EDUCAMAIS_DATABASE_URI para o banco do teste (sem senha em log)."""
    from src.config import Config, resolver_opcoes_engine
    Config.SQLALCHEMY_DATABASE_URI = uri
    Config.SQLALCHEMY_ENGINE_OPTIONS = resolver_opcoes_engine(uri)
    os.environ["EDUCAMAIS_DATABASE_URI"] = uri


def criar_app_migrada():
    """Sobe a app pelo caminho comercial: flask db upgrade, sem create_all.

    Usa EDUCAMAIS_ENV já definido (production/development). Depois do upgrade,
    create_app() exige alembic_version e aplica bootstrap permitido pelo ambiente.
    A app temporária do upgrade é encerrada (session + engine) para não deixar
    conexão psycopg/SQLite aberta quando a factory cria a app definitiva.
    """
    from flask_migrate import upgrade
    from src import create_app, db
    app = create_app(preparar_schema=False)
    with app.app_context():
        upgrade()
        db.session.remove()
        db.engine.dispose()
    return create_app()


def texto_pdf(resp):
    """Texto de um PDF oficial ou corpo textual de uma resposta JSON."""
    from io import BytesIO
    data = resp.get_data()
    tipo = (getattr(resp, "content_type", None) or "").lower()
    if "pdf" in tipo or data.startswith(b"%PDF"):
        from pypdf import PdfReader
        leitor = PdfReader(BytesIO(data))
        return "\n".join((pagina.extract_text() or "") for pagina in leitor.pages)
    return resp.get_data(as_text=True)


def json_data(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


def senha_temporaria(resp):
    data, _ = json_data(resp)
    if not data:
        return None
    return data.get("senhaTemporaria") or data.get("senha_temporaria")


def obter_csrf(client):
    resp = client.get("/api/csrf-token")
    data, error = json_data(resp)
    if resp.status_code != 200 or not data:
        raise RuntimeError("Falha ao obter CSRF: %s" % error)
    return data.get("csrfToken")


def concluir_primeiro_acesso(client, login, senha_temp, nova=SENHA_PESSOAL):
    resp = client.post("/api/auth/login", json={"login": login, "senha": senha_temp})
    if resp.status_code != 200:
        raise RuntimeError("Falha no login de primeiro acesso: %s %s" % (login, json_data(resp)[1]))
    resp = client.post("/api/auth/definir-senha", json={"senha": nova, "confirmar": nova})
    if resp.status_code != 200:
        raise RuntimeError("Falha ao definir senha: %s %s" % (login, json_data(resp)[1]))
    return json_data(resp)[0]


def ativar_conta_criada(client, login, resp_criacao, nova=SENHA_PESSOAL):
    temp = senha_temporaria(resp_criacao)
    if not temp:
        raise RuntimeError("Resposta de criação sem senha temporária: %s" % json_data(resp_criacao))
    return concluir_primeiro_acesso(client, login, temp, nova=nova)
