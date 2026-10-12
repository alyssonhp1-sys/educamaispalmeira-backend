"""Rotas somente leitura da consulta administrativa de AuditLog."""
from flask import Blueprint, request
from flask_login import current_user, login_required

from src.consulta_auditoria import listar_logs, opcoes_filtros
from src.http import err, ok

aud_bp = Blueprint("consulta_auditoria", __name__)


@aud_bp.route("/api/auditoria", methods=["GET"])
@login_required
def api_listar_auditoria():
    payload, codigo = listar_logs(current_user, request.args)
    if codigo:
        return err(payload, codigo)
    return ok(payload)


@aud_bp.route("/api/auditoria/filtros", methods=["GET"])
@login_required
def api_filtros_auditoria():
    payload, codigo = opcoes_filtros(current_user)
    if codigo:
        return err(payload, codigo)
    return ok(payload)
