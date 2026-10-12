import os
from datetime import timedelta


_VERDADEIRO = {"1", "true", "yes", "on"}

# Chave APENAS para desenvolvimento e testes. Nunca é usada em produção.
CHAVE_SECRET_DEV_TESTE = "educamais-somente-dev-teste-nao-usar-em-producao"

# Sessão administrativa: 8 horas, renovada a cada requisição (padrão Flask).
DURACAO_SESSAO = timedelta(hours=8)
SESSION_COOKIE_SAMESITE_VALOR = "Lax"
SESSION_COOKIE_NAME_VALOR = "educamais_sessao"
SECRET_KEY_PRODUCAO_MINIMO = 32
LOGIN_MAX_TENTATIVAS_PADRAO = 5
LOGIN_BLOQUEIO_SEGUNDOS_PADRAO = 300
PROXY_COUNT_PADRAO = 1
ANO_INICIO_HISTORICO_AUTOMATICO_PADRAO = 2026

# Valores que nunca podem ser SECRET_KEY em produção (mesmo se exportados).
CHAVES_PROIBIDAS_PRODUCAO = frozenset({
    CHAVE_SECRET_DEV_TESTE,
    "changeme",
    "secret",
    "secret-key",
    "secret_key",
    "dev",
    "development",
    "teste",
    "test",
    "password",
    "educamais",
    "educamais-secret",
})


class ConfiguracaoInsegura(RuntimeError):
    """Falta variável obrigatória para o ambiente pedido."""


def _raiz_projeto():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def flag_env(nome, padrao=False):
    bruto = os.environ.get(nome)
    if bruto is None or str(bruto).strip() == "":
        return padrao
    return str(bruto).strip().lower() in _VERDADEIRO


def resolver_ambiente():
    """production | development | testing. Sem valor, assume production (fail-closed)."""
    v = (os.environ.get("EDUCAMAIS_ENV") or "").strip().lower()
    if v in ("production", "prod"):
        return "production"
    if v in ("testing", "test"):
        return "testing"
    if v in ("development", "dev"):
        return "development"
    return "production"


def demo_habilitado(ambiente=None):
    """Demo só com EDUCAMAIS_DEMO explícito e fora de produção."""
    ambiente = ambiente or resolver_ambiente()
    if ambiente == "production":
        return False
    return flag_env("EDUCAMAIS_DEMO", padrao=False)


def ano_inicio_historico_automatico():
    """Primeiro ano letivo cujos dados acadêmicos vêm do Educa+ (não de legado)."""
    return inteiro_env(
        "EDUCAMAIS_ANO_INICIO_HISTORICO_AUTOMATICO",
        ANO_INICIO_HISTORICO_AUTOMATICO_PADRAO,
        minimo=1990,
        maximo=2100,
    )


def inteiro_env(nome, padrao, minimo=1, maximo=86400):
    bruto = os.environ.get(nome)
    if bruto is None or str(bruto).strip() == "":
        return padrao
    try:
        n = int(str(bruto).strip())
    except ValueError:
        raise ConfiguracaoInsegura("%s deve ser um inteiro." % nome)
    if n < minimo or n > maximo:
        raise ConfiguracaoInsegura(
            "%s deve estar entre %s e %s." % (nome, minimo, maximo)
        )
    return n


def chave_producao_aceitavel(chave):
    texto = (chave or "").strip()
    if len(texto) < SECRET_KEY_PRODUCAO_MINIMO:
        return False
    if texto in CHAVES_PROIBIDAS_PRODUCAO:
        return False
    if texto.casefold() in {c.casefold() for c in CHAVES_PROIBIDAS_PRODUCAO}:
        return False
    if "nao-usar-em-producao" in texto.casefold():
        return False
    return True


def resolver_secret_key(ambiente=None):
    ambiente = ambiente or resolver_ambiente()
    chave = (os.environ.get("EDUCAMAIS_SECRET_KEY") or "").strip()
    if ambiente == "production":
        if not chave:
            raise ConfiguracaoInsegura(
                "EDUCAMAIS_SECRET_KEY não está definida. "
                "Defina essa variável de ambiente antes de iniciar em produção."
            )
        if not chave_producao_aceitavel(chave):
            raise ConfiguracaoInsegura(
                "EDUCAMAIS_SECRET_KEY é inválida para produção "
                "(curta, de exemplo ou marcada só para desenvolvimento)."
            )
        return chave
    return chave or CHAVE_SECRET_DEV_TESTE


def aplicar_configuracao_runtime(app):
    ambiente = resolver_ambiente()
    app.config["EDUCAMAIS_ENV"] = ambiente
    app.config["SECRET_KEY"] = resolver_secret_key(ambiente)
    app.config["EDUCAMAIS_DEMO"] = demo_habilitado(ambiente)
    # Produção relê a URI do ambiente (PostgreSQL obrigatório).
    # Development/testing: from_object(Config) já trouxe o valor (testes isolam
    # o arquivo patchando Config; apontar_banco_teste atualiza Config e o env).
    if ambiente == "production":
        app.config["SQLALCHEMY_DATABASE_URI"] = resolver_uri_banco()
    app.config["UPLOAD_FOLDER"] = resolver_upload_folder()
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    app.config["MAX_UPLOAD_BYTES"] = MAX_UPLOAD_BYTES
    app.config["LOGIN_MAX_TENTATIVAS"] = inteiro_env(
        "EDUCAMAIS_LOGIN_MAX_TENTATIVAS", LOGIN_MAX_TENTATIVAS_PADRAO, 1, 50,
    )
    app.config["LOGIN_BLOQUEIO_SEGUNDOS"] = inteiro_env(
        "EDUCAMAIS_LOGIN_BLOQUEIO_SEGUNDOS", LOGIN_BLOQUEIO_SEGUNDOS_PADRAO, 30, 86400,
    )
    app.config["EDUCAMAIS_PROXY_FIX"] = flag_env("EDUCAMAIS_PROXY_FIX", padrao=False)
    app.config["EDUCAMAIS_TRUSTED_PROXY_COUNT"] = inteiro_env(
        "EDUCAMAIS_TRUSTED_PROXY_COUNT", PROXY_COUNT_PADRAO, 1, 10,
    )
    app.config["EDUCAMAIS_ANO_INICIO_HISTORICO_AUTOMATICO"] = ano_inicio_historico_automatico()

    app.config["SESSION_COOKIE_NAME"] = SESSION_COOKIE_NAME_VALOR
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = SESSION_COOKIE_SAMESITE_VALOR
    app.config["PERMANENT_SESSION_LIFETIME"] = DURACAO_SESSAO
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = SESSION_COOKIE_SAMESITE_VALOR
    app.config["REMEMBER_COOKIE_NAME"] = "educamais_remember"

    if ambiente == "production":
        app.config["DEBUG"] = False
        app.config["TESTING"] = False
        app.config["PROPAGATE_EXCEPTIONS"] = False
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["REMEMBER_COOKIE_SECURE"] = True
        app.config["PREFERRED_URL_SCHEME"] = "https"
        _recusar_producao_insegura(app)
        _exigir_pastas_gravaveis_producao(app)
    else:
        app.config["DEBUG"] = flag_env("EDUCAMAIS_DEBUG", padrao=False)
        app.config["SESSION_COOKIE_SECURE"] = False
        app.config["REMEMBER_COOKIE_SECURE"] = False


def _exigir_pastas_gravaveis_producao(app):
    """Uploads e instance precisam ser graváveis. Backup externo não bloqueia o startup."""
    pastas = (
        ("instance", os.path.join(_raiz_projeto(), "instance")),
        ("uploads", app.config.get("UPLOAD_FOLDER") or resolver_upload_folder()),
    )
    for nome, pasta in pastas:
        try:
            os.makedirs(pasta, exist_ok=True)
            probe = os.path.join(pasta, ".educamais_write_probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
        except OSError as exc:
            raise ConfiguracaoInsegura(
                "Pasta %s não é gravável (%s)." % (nome, pasta)
            ) from exc


def _recusar_producao_insegura(app):
    """Fail-closed: produção não sobe com debug, demo, cookie inseguro ou TESTING."""
    if app.config.get("DEBUG"):
        raise ConfiguracaoInsegura("DEBUG não pode estar ativo em produção.")
    if app.config.get("TESTING"):
        raise ConfiguracaoInsegura("TESTING não pode estar ativo em produção.")
    if app.config.get("EDUCAMAIS_DEMO"):
        raise ConfiguracaoInsegura("Modo demo não é permitido em produção.")
    if app.config.get("SESSION_COOKIE_SECURE") is not True:
        raise ConfiguracaoInsegura(
            "SESSION_COOKIE_SECURE deve ser True em produção. "
            "Não inicie produção em HTTP sem TLS no proxy."
        )
    if not uri_banco_definida_no_ambiente():
        raise ConfiguracaoInsegura(
            "EDUCAMAIS_DATABASE_URI não está definida. "
            "Em produção o fallback silencioso para SQLite não é permitido. "
            "Defina EDUCAMAIS_DATABASE_URI (ou o alias EDUCAMAIS_DATABASE_URL)."
        )
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or resolver_uri_banco()
    if uri_e_sqlite(uri) or not uri_e_postgres(uri):
        raise ConfiguracaoInsegura(
            "Produção exige PostgreSQL. SQLite não é banco de produção. "
            "Defina EDUCAMAIS_DATABASE_URI com postgresql+psycopg://..."
        )


DRIVERS_POSTGRES_PARA_PSYCOPG = frozenset({
    "postgres",
    "postgresql",
    "postgresql+psycopg2",
})


def uri_e_sqlite(uri):
    return (uri or "").startswith("sqlite:")


def uri_e_postgres(uri):
    bruto = (uri or "").strip().lower()
    return bruto.startswith("postgres://") or bruto.startswith("postgresql")


def uri_banco_definida_no_ambiente():
    """True se EDUCAMAIS_DATABASE_URI ou o alias EDUCAMAIS_DATABASE_URL estiver definida."""
    if (os.environ.get("EDUCAMAIS_DATABASE_URI") or "").strip():
        return True
    if (os.environ.get("EDUCAMAIS_DATABASE_URL") or "").strip():
        return True
    return False


def resolver_uri_teste_postgresql():
    """URI do banco PostgreSQL descartável de testes. Não usa o SQLite real."""
    return (os.environ.get("EDUCAMAIS_TEST_DATABASE_URI") or "").strip() or None


def resolver_uri_ensaio_migracao():
    """URI do PostgreSQL descartável do ensaio SQLite→PostgreSQL.

    NÃO reutiliza EDUCAMAIS_TEST_DATABASE_URI nem educamais_pg_test.
    """
    return (os.environ.get("EDUCAMAIS_MIGRACAO_TEST_DATABASE_URI") or "").strip() or None


def mascarar_uri_banco(uri):
    """URI para log/relatório: senha omitida. Não usar para conectar."""
    if not uri:
        return uri
    try:
        from sqlalchemy.engine.url import make_url
        url = make_url(uri)
        return url.render_as_string(hide_password=True)
    except Exception:
        return "<uri-invalida>"


def normalizar_uri_banco(bruto):
    """Normaliza driver PostgreSQL para postgresql+psycopg via make_url (sem parse manual)."""
    from sqlalchemy.engine.url import make_url
    if bruto.startswith("postgres://"):
        bruto = "postgresql://" + bruto[len("postgres://"):]
    url = make_url(bruto)
    if url.drivername in DRIVERS_POSTGRES_PARA_PSYCOPG:
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def resolver_opcoes_engine(uri):
    """Opções do engine por dialect. Não passa connect_args de SQLite ao psycopg.

    PostgreSQL usa o pool padrão do SQLAlchemy (sem tuning neste bloco).
    """
    if uri_e_sqlite(uri):
        return {"connect_args": {"check_same_thread": False}}
    return {}


def resolver_sqlite_uri(bruto, raiz=None):
    """Monta URI SQLite. Caminhos relativos (ex.: instance/educamais_demo.db) ficam na raiz do projeto."""
    raiz = os.path.abspath(raiz or _raiz_projeto())

    def sqlite_file(path):
        return "sqlite:///" + os.path.abspath(path).replace("\\", "/")

    if not bruto:
        return sqlite_file(os.path.join(raiz, "instance", "educamais.db"))
    if not bruto.startswith("sqlite:///"):
        return bruto
    resto = bruto[len("sqlite:///"):]
    windows_abs = len(resto) >= 2 and resto[1] == ":"
    unix_abs = resto.startswith("/")
    if windows_abs or unix_abs:
        return "sqlite:///" + resto.replace("\\", "/")
    return sqlite_file(os.path.join(raiz, resto))


def resolver_uri_banco(bruto=None, raiz=None):
    """URI do banco.

    Precedência: argumento, EDUCAMAIS_DATABASE_URI, EDUCAMAIS_DATABASE_URL,
    fallback SQLite em instance/educamais.db (somente development/testing no startup).

    postgres:// e postgresql:// são normalizados para postgresql+psycopg://.
    """
    if bruto is None:
        bruto = (os.environ.get("EDUCAMAIS_DATABASE_URI") or "").strip() or None
        if bruto is None:
            bruto = (os.environ.get("EDUCAMAIS_DATABASE_URL") or "").strip() or None
    if not bruto:
        return resolver_sqlite_uri(None, raiz)
    if uri_e_sqlite(bruto):
        return resolver_sqlite_uri(bruto, raiz)
    return normalizar_uri_banco(bruto)


# Formatos administrativos do planejamento. Imagens (png/jpg) deixaram de ser aceitas:
# o fluxo real anexa documento pedagógico, não foto.
EXTENSOES_UPLOAD_PERMITIDAS = frozenset({"pdf", "doc", "docx", "xls", "xlsx", "odt", "ods"})
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def resolver_upload_folder(raiz=None):
    """Pasta privada de uploads. Relativa à raiz do projeto, sem caminho Windows hardcoded."""
    raiz = os.path.abspath(raiz or _raiz_projeto())
    bruto = (os.environ.get("EDUCAMAIS_UPLOAD_FOLDER") or "").strip()
    if not bruto:
        return os.path.join(raiz, "instance", "uploads")
    if os.path.isabs(bruto):
        return os.path.abspath(bruto)
    return os.path.abspath(os.path.join(raiz, bruto))


class Config:
    SECRET_KEY = None
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    ROOT_DIR = _raiz_projeto()

    # EDUCAMAIS_DATABASE_URI (ou alias EDUCAMAIS_DATABASE_URL).
    # Fallback SQLite em instance/educamais.db só no import e em development/testing.
    # Produção recusa fallback silencioso em aplicar_configuracao_runtime.
    SQLALCHEMY_DATABASE_URI = resolver_uri_banco(raiz=ROOT_DIR)
    SQLALCHEMY_ENGINE_OPTIONS = resolver_opcoes_engine(SQLALCHEMY_DATABASE_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = None
    MAX_CONTENT_LENGTH = MAX_UPLOAD_BYTES
    ALLOWED_EXTENSIONS = set(EXTENSOES_UPLOAD_PERMITIDAS)

    @staticmethod
    def init_app(app):
        os.makedirs(os.path.join(Config.ROOT_DIR, "instance"), exist_ok=True)
        pasta = app.config.get("UPLOAD_FOLDER") or resolver_upload_folder()
        app.config["UPLOAD_FOLDER"] = pasta
        os.makedirs(pasta, exist_ok=True)
