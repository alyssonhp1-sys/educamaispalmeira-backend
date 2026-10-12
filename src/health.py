"""Health check mínimo. Sem autenticação, sem segredos, sem stack trace."""
from flask import current_app, jsonify
from sqlalchemy import text

from src.http import garantir_request_id, ok

MSG_BANCO = "Banco de dados indisponível"


def resposta_health():
    from src import db
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        current_app.logger.error(
            "Health check: banco indisponível request_id=%s",
            garantir_request_id(),
        )
        corpo = jsonify({
            "data": {"status": "unhealthy"},
            "error": MSG_BANCO,
        })
        return corpo, 503
    return ok({"status": "ok"})
