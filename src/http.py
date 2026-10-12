import uuid

from flask import current_app, g, has_app_context, has_request_context, jsonify, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

MENSAGEM_ERRO_INTERNO = "Ocorreu um erro interno. Tente novamente."
HEADER_REQUEST_ID = "X-Request-ID"

HEADERS_SEGURANCA = (
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
)

# Permite CDN de fontes/ícones já usados pelo index.html e templates legados.
# Não usa script-src * nem objeto/plugin.
CSP_VALOR = (
    "default-src 'self'; "
    "script-src 'self' https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com "
    "https://fonts.googleapis.com https://cdn.jsdelivr.net; "
    "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
HSTS_VALOR = "max-age=31536000; includeSubDomains"

PREFIXOS_SEM_CACHE = (
    "/api/",
    "/documentos/",
    "/relatorio/",
    "/coordenacao/",
)

PREFIXOS_API_JSON_404 = (
    "api/",
    "documentos/",
    "relatorio/",
    "coordenacao/",
    "avisos/",
    "planejamento/",
)

MENSAGENS_HTTP = {
    400: "Requisição inválida.",
    401: "Não autenticado",
    403: "Acesso negado",
    404: "Não encontrado",
    405: "Método não permitido",
    409: "Conflito",
    413: "O arquivo excede o tamanho máximo permitido (10 MB).",
    429: "Muitas tentativas. Tente novamente em alguns minutos.",
}


def ok(data=None, status=200):
    return jsonify({"data": data, "error": None}), status


def err(message, status=400):
    return jsonify({"data": None, "error": message}), status


def garantir_request_id():
    """UUID por request, em flask.g. Não reutiliza valor enviado pelo cliente."""
    if not has_request_context():
        return None
    rid = getattr(g, "request_id", None)
    if rid:
        return rid
    rid = str(uuid.uuid4())
    g.request_id = rid
    return rid


def ip_remoto():
    """IP da conexão imediata. X-Forwarded-For só vale se ProxyFix estiver ativo."""
    if not has_request_context():
        return None
    addr = request.remote_addr
    if not addr:
        return None
    return str(addr)[:45]


def aplicar_headers_seguranca(response):
    """Aplica headers em qualquer status (200, 401, 403, 404, 413, 500, …)."""
    for nome, valor in HEADERS_SEGURANCA:
        response.headers[nome] = valor
    response.headers["Content-Security-Policy"] = CSP_VALOR
    if has_app_context():
        env = current_app.config.get("EDUCAMAIS_ENV")
        if env == "production" and current_app.config.get("SESSION_COOKIE_SECURE"):
            response.headers["Strict-Transport-Security"] = HSTS_VALOR
    path = request.path or ""
    if path.startswith(PREFIXOS_SEM_CACHE):
        response.headers["Cache-Control"] = "no-store"
    rid = garantir_request_id()
    if rid:
        response.headers[HEADER_REQUEST_ID] = rid
    return response


def read_json_dict():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None
    return payload


def resposta_http_exception(exc):
    codigo = int(getattr(exc, "code", None) or 500)
    if codigo == 500:
        return err(MENSAGEM_ERRO_INTERNO, 500)
    if codigo == 413:
        return err(MENSAGENS_HTTP[413], 413)
    descricao = (getattr(exc, "description", None) or "").strip()
    padrao = MENSAGENS_HTTP.get(codigo, "Erro na requisição.")
    if codigo in (400, 409) and descricao and len(descricao) < 200:
        mensagem = descricao
    else:
        mensagem = padrao
    return err(mensagem, codigo)


def registrar_handlers_erro(app):
    @app.errorhandler(RequestEntityTooLarge)
    @app.errorhandler(413)
    def _payload_grande(_e):
        return err(MENSAGENS_HTTP[413], 413)

    @app.errorhandler(HTTPException)
    def _http(exc):
        return resposta_http_exception(exc)

    @app.errorhandler(500)
    def _erro_interno(_e):
        return err(MENSAGEM_ERRO_INTERNO, 500)

    @app.errorhandler(Exception)
    def _nao_tratada(exc):
        if isinstance(exc, HTTPException):
            return resposta_http_exception(exc)
        if app.debug or app.config.get("TESTING"):
            raise exc
        app.logger.error(
            "Erro interno request_id=%s path=%s",
            garantir_request_id(),
            request.path if has_request_context() else None,
        )
        return err(MENSAGEM_ERRO_INTERNO, 500)
