"""Logging operacional. Não é AuditLog: saúde, erro e infraestrutura."""
import logging
import os
import re
from logging.handlers import RotatingFileHandler

from src.config import mascarar_uri_banco, resolver_uri_banco

LOGGER_NOME = "educamais"
MAX_LOG_BYTES_PADRAO = 5 * 1024 * 1024
BACKUP_COUNT_PADRAO = 5

_URI_COM_SENHA = re.compile(
    r"(postgres(?:ql)?(?:\+\w+)?://[^:/?#]+:)([^@/]+)(@)",
    re.IGNORECASE,
)
_PADROES_REDACT = (
    (re.compile(r"(secret[_-]?key\s*[=:]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(password\s*[=:]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(senha\s*[=:]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(csrf[_-]?token\s*[=:]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(authorization\s*[=:]\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(cookie:\s*)\S+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(sessionid\s*[=:]\s*)\S+", re.IGNORECASE), r"\1***"),
)


def redigir_segredos(texto):
    if not texto:
        return texto
    saida = _URI_COM_SENHA.sub(r"\1***\3", str(texto))
    for padrao, subst in _PADROES_REDACT:
        saida = padrao.sub(subst, saida)
    return saida


class FiltroSegredos(logging.Filter):
    def filter(self, record):
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        record.msg = redigir_segredos(msg)
        record.args = ()
        return True


def _nivel():
    bruto = (os.environ.get("EDUCAMAIS_LOG_LEVEL") or "INFO").strip().upper()
    return getattr(logging, bruto, logging.INFO)


def configurar_logging(app):
    """Configura o logger da aplicação. Arquivo de rotação é opcional."""
    logger = logging.getLogger(LOGGER_NOME)
    logger.setLevel(_nivel())
    logger.propagate = True
    if not any(isinstance(f, FiltroSegredos) for f in logger.filters):
        logger.addFilter(FiltroSegredos())

    arquivo = (os.environ.get("EDUCAMAIS_LOG_FILE") or "").strip()
    if arquivo:
        pasta = os.path.dirname(os.path.abspath(arquivo))
        if pasta:
            os.makedirs(pasta, exist_ok=True)
        handler = RotatingFileHandler(
            arquivo,
            maxBytes=int(os.environ.get("EDUCAMAIS_LOG_MAX_BYTES") or MAX_LOG_BYTES_PADRAO),
            backupCount=int(os.environ.get("EDUCAMAIS_LOG_BACKUP_COUNT") or BACKUP_COUNT_PADRAO),
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        ))
        handler.addFilter(FiltroSegredos())
        ja = any(
            isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == handler.baseFilename
            for h in logger.handlers
        )
        if not ja:
            logger.addHandler(handler)

    app.logger.setLevel(logger.level)
    if not any(isinstance(f, FiltroSegredos) for f in app.logger.filters):
        app.logger.addFilter(FiltroSegredos())
    for handler in logger.handlers:
        if handler not in app.logger.handlers:
            app.logger.addHandler(handler)
    return logger


def log_startup(app):
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or resolver_uri_banco()
    app.logger.info(
        "Educa+ iniciado env=%s demo=%s db=%s",
        app.config.get("EDUCAMAIS_ENV"),
        bool(app.config.get("EDUCAMAIS_DEMO")),
        mascarar_uri_banco(uri),
    )
