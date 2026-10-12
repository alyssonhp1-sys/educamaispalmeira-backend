"""Rotas de trajetória escolar legada (anterior ao Educa+)."""
from flask import Blueprint
from flask_login import current_user, login_required

from src import authz, db
from src.http import err, ok, read_json_dict
from src.models import Aluno
from src.trajetoria_legada import (
    TrajetoriaLegadaErro,
    atualizar,
    criar,
    excluir,
    obter,
    payload_listagem,
    serializar_trajetoria,
)

trajetoria_bp = Blueprint("trajetoria_legada", __name__)


def _escola_ctx(aluno):
    return authz.escola_do_aluno(aluno)


def _aluno_gestao(aluno_id):
    aluno = db.session.get(Aluno, aluno_id)
    if not aluno:
        return None, err("Aluno não encontrado", 404)
    if not authz.pode_gerir_trajetoria_legada(current_user, aluno):
        return None, authz.recusa()
    return aluno, None


@trajetoria_bp.route("/api/alunos/<int:aluno_id>/trajetoria-legada", methods=["GET"])
@login_required
def listar_trajetoria(aluno_id):
    aluno, falha = _aluno_gestao(aluno_id)
    if falha:
        return falha
    return ok(payload_listagem(aluno.id))


@trajetoria_bp.route("/api/alunos/<int:aluno_id>/trajetoria-legada", methods=["POST"])
@login_required
def criar_trajetoria(aluno_id):
    aluno, falha = _aluno_gestao(aluno_id)
    if falha:
        return falha
    dados = read_json_dict()
    if dados is None:
        return err("JSON inválido ou ausente")
    try:
        reg = criar(aluno, dados, _escola_ctx(aluno))
        db.session.commit()
    except TrajetoriaLegadaErro as exc:
        db.session.rollback()
        return err(exc.message, exc.status)
    return ok(serializar_trajetoria(reg))


@trajetoria_bp.route("/api/trajetoria-legada/<int:registro_id>", methods=["GET"])
@login_required
def obter_trajetoria(registro_id):
    reg = obter(registro_id)
    if reg is None:
        return err("Registro de trajetória anterior não encontrado", 404)
    aluno = db.session.get(Aluno, reg.aluno_id)
    if aluno is None:
        return err("Aluno não encontrado", 404)
    if not authz.pode_gerir_trajetoria_legada(current_user, aluno):
        return authz.recusa()
    return ok(serializar_trajetoria(reg))


@trajetoria_bp.route("/api/trajetoria-legada/<int:registro_id>", methods=["PUT"])
@login_required
def atualizar_trajetoria(registro_id):
    reg = obter(registro_id)
    if reg is None:
        return err("Registro de trajetória anterior não encontrado", 404)
    aluno = db.session.get(Aluno, reg.aluno_id)
    if aluno is None:
        return err("Aluno não encontrado", 404)
    if not authz.pode_gerir_trajetoria_legada(current_user, aluno):
        return authz.recusa()
    dados = read_json_dict()
    if dados is None:
        return err("JSON inválido ou ausente")
    try:
        atualizar(reg, dados, _escola_ctx(aluno))
        db.session.commit()
    except TrajetoriaLegadaErro as exc:
        db.session.rollback()
        return err(exc.message, exc.status)
    return ok(serializar_trajetoria(reg))


@trajetoria_bp.route("/api/trajetoria-legada/<int:registro_id>", methods=["DELETE"])
@login_required
def excluir_trajetoria(registro_id):
    reg = obter(registro_id)
    if reg is None:
        return err("Registro de trajetória anterior não encontrado", 404)
    aluno = db.session.get(Aluno, reg.aluno_id)
    if aluno is None:
        return err("Aluno não encontrado", 404)
    if not authz.pode_gerir_trajetoria_legada(current_user, aluno):
        return authz.recusa()
    try:
        excluir(reg, _escola_ctx(aluno))
        db.session.commit()
    except TrajetoriaLegadaErro as exc:
        db.session.rollback()
        return err(exc.message, exc.status)
    return ok({"mensagem": "Trajetória anterior removida", "id": registro_id})
