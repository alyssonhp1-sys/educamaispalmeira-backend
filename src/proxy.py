"""Confiança em reverse proxy. Desligada por padrão (não lê X-Forwarded-For)."""
from werkzeug.middleware.proxy_fix import ProxyFix

from src.config import ConfiguracaoInsegura, flag_env, inteiro_env, PROXY_COUNT_PADRAO


def aplicar_proxy_fix(app):
    """ProxyFix só com EDUCAMAIS_PROXY_FIX explícito. Não habilitar automaticamente."""
    if not flag_env("EDUCAMAIS_PROXY_FIX", padrao=False):
        app.config["EDUCAMAIS_PROXY_FIX"] = False
        return False
    n = inteiro_env("EDUCAMAIS_TRUSTED_PROXY_COUNT", PROXY_COUNT_PADRAO, 1, 10)
    if n < 1:
        raise ConfiguracaoInsegura(
            "EDUCAMAIS_TRUSTED_PROXY_COUNT deve ser >= 1 quando ProxyFix está ativo."
        )
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=n,
        x_proto=n,
        x_host=n,
        x_prefix=n,
    )
    app.config["EDUCAMAIS_PROXY_FIX"] = True
    app.config["EDUCAMAIS_TRUSTED_PROXY_COUNT"] = n
    return True
