"""Proteção CSRF por token sincronizador na sessão Flask.

Mutações autenticadas e o login exigem o header X-CSRF-Token.
GET/HEAD/OPTIONS não exigem. O frontend obtém o token em GET /api/csrf-token.
"""
import hmac
import secrets

from flask import request, session
from flask.testing import FlaskClient
from flask_login import current_user

from src.http import err, ok

CSRF_SESSION_KEY = "_csrf_token"
CSRF_HEADER = "X-CSRF-Token"
CSRF_HEADER_ALT = "X-CSRFToken"
MSG_CSRF = "Token CSRF ausente ou inválido."
METODOS_MUTACAO = frozenset({"POST", "PUT", "PATCH", "DELETE"})
ROTAS_SEM_CSRF = frozenset({"/health", "/api/csrf-token"})


def gerar_token_csrf():
    return secrets.token_urlsafe(32)


def garantir_token_csrf():
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = gerar_token_csrf()
        session[CSRF_SESSION_KEY] = token
        session.modified = True
    return token


def _token_enviado():
    enviado = request.headers.get(CSRF_HEADER) or request.headers.get(CSRF_HEADER_ALT)
    if enviado:
        return enviado.strip()
    if request.form:
        campo = (request.form.get("csrf_token") or "").strip()
        if campo:
            return campo
    return ""


def _precisa_csrf():
    if request.method not in METODOS_MUTACAO:
        return False
    path = request.path or ""
    if path in ROTAS_SEM_CSRF or path.startswith("/static/"):
        return False
    if path == "/api/auth/login":
        return True
    return bool(getattr(current_user, "is_authenticated", False))


def validar_csrf_se_preciso():
    if not _precisa_csrf():
        return None
    esperado = session.get(CSRF_SESSION_KEY) or ""
    enviado = _token_enviado()
    if not esperado or not enviado:
        return err(MSG_CSRF, 400)
    if not hmac.compare_digest(str(esperado), str(enviado)):
        return err(MSG_CSRF, 400)
    return None


def resposta_token_csrf():
    return ok({"csrfToken": garantir_token_csrf()})


def _tem_header_csrf(headers):
    if not headers:
        return False
    if hasattr(headers, "keys"):
        for chave in headers.keys():
            if str(chave).lower() in ("x-csrf-token", "x-csrftoken"):
                return True
        return False
    try:
        for chave, _valor in headers:
            if str(chave).lower() in ("x-csrf-token", "x-csrftoken"):
                return True
    except (TypeError, ValueError):
        return False
    return False


def _com_header_csrf(headers, token):
    if headers is None:
        return {CSRF_HEADER: token}
    if isinstance(headers, dict):
        saida = dict(headers)
        saida[CSRF_HEADER] = token
        return saida
    from werkzeug.datastructures import Headers
    h = Headers(headers)
    h[CSRF_HEADER] = token
    return h


def _erro_csrf(resp):
    if resp is None or getattr(resp, "status_code", None) != 400:
        return False
    corpo = resp.get_json(silent=True) if hasattr(resp, "get_json") else None
    if not isinstance(corpo, dict):
        return False
    texto = str(corpo.get("error") or "")
    return "csrf" in texto.casefold()


class ClienteTesteCsrf(FlaskClient):
    """Cliente de teste que envia CSRF como o frontend, sem desligar a proteção."""

    def open(self, *args, buffered=False, follow_redirects=False, **kwargs):
        csrf = kwargs.pop("csrf", True)
        method = (kwargs.get("method") or "GET").upper()
        path = args[0] if args and isinstance(args[0], str) else kwargs.get("path", "")
        auto = bool(csrf) and method in METODOS_MUTACAO
        if auto and not _tem_header_csrf(kwargs.get("headers")):
            token = self._token_csrf()
            if token:
                kwargs["headers"] = _com_header_csrf(kwargs.get("headers"), token)
        resp = super().open(
            *args, buffered=buffered, follow_redirects=follow_redirects, **kwargs
        )
        if str(path).rstrip("/").endswith("/api/auth/logout") and method == "POST":
            self._csrf_token_cache = None
        if auto and csrf is True and _erro_csrf(resp):
            self._csrf_token_cache = None
            token = self._token_csrf(force=True)
            if token:
                kwargs["headers"] = _com_header_csrf(kwargs.get("headers"), token)
                resp = super().open(
                    *args, buffered=buffered, follow_redirects=follow_redirects, **kwargs
                )
        return resp

    def _token_csrf(self, force=False):
        cache = getattr(self, "_csrf_token_cache", None)
        if cache and not force:
            return cache
        resp = super().open("/api/csrf-token", method="GET")
        corpo = resp.get_json(silent=True) if hasattr(resp, "get_json") else None
        token = ""
        if isinstance(corpo, dict):
            token = str((corpo.get("data") or {}).get("csrfToken") or "")
        self._csrf_token_cache = token or None
        return token
