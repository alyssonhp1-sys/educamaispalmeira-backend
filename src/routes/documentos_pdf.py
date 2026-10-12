"""Rotas de documentos oficiais em PDF."""
from flask import Blueprint
from flask_login import current_user, login_required

from src import authz, db
from src.complemento_transferencia import (
    ComplementoTransferenciaErro,
    montar_formulario,
    salvar as salvar_complemento,
)
from src.documentos_pdf import (
    DocumentoErro,
    emitir_boletim_historico_pdf,
    emitir_boletim_pdf,
    emitir_comprovante_pdf,
    emitir_declaracao_pdf,
    emitir_historico_pdf,
    emitir_parecer_historico_pdf,
    emitir_parecer_pdf,
    emitir_rendimento_pdf,
    resposta_pdf,
)
from src.historico import escola_id_para_acl, obter_historico_anual
from src.historico_escolar import DocumentoErro
from src.http import err, ok, read_json_dict
from src.models import Aluno, Disciplina, Turma

docs_pdf_bp = Blueprint("docs_pdf", __name__)


def _pdf_ou_erro(fn, *args):
    try:
        pdf, nome = fn(*args)
    except DocumentoErro as exc:
        return err(exc.message, exc.status)
    return resposta_pdf(pdf, nome)


def _aluno_admin(aluno_id):
    aluno = db.session.get(Aluno, aluno_id)
    if not aluno:
        return None, err("Aluno não encontrado", 404)
    if not authz.pode_emitir_documento_administrativo(current_user, aluno):
        return None, authz.recusa()
    return aluno, None


@docs_pdf_bp.route("/api/alunos/<int:aluno_id>/documentos/historico.pdf")
@login_required
def pdf_historico(aluno_id):
    aluno = db.session.get(Aluno, aluno_id)
    if not aluno:
        return err("Aluno não encontrado", 404)
    if not authz.pode_emitir_historico_escolar(current_user, aluno):
        return authz.recusa()
    return _pdf_ou_erro(emitir_historico_pdf, aluno, False)


def _aluno_historico(aluno_id):
    aluno = db.session.get(Aluno, aluno_id)
    if not aluno:
        return None, err("Aluno não encontrado", 404)
    if not authz.pode_emitir_historico_escolar(current_user, aluno):
        return None, authz.recusa()
    return aluno, None


@docs_pdf_bp.route("/api/alunos/<int:aluno_id>/documentos/historico-transferencia.pdf")
@login_required
def pdf_historico_transferencia(aluno_id):
    aluno, falha = _aluno_historico(aluno_id)
    if falha:
        return falha
    return _pdf_ou_erro(emitir_historico_pdf, aluno, True)


@docs_pdf_bp.route(
    "/api/alunos/<int:aluno_id>/documentos/historico-transferencia/complemento",
    methods=["GET"],
)
@login_required
def obter_complemento_transferencia(aluno_id):
    aluno, falha = _aluno_historico(aluno_id)
    if falha:
        return falha
    try:
        return ok(montar_formulario(aluno))
    except DocumentoErro as exc:
        return err(exc.message, exc.status)


@docs_pdf_bp.route(
    "/api/alunos/<int:aluno_id>/documentos/historico-transferencia/complemento",
    methods=["PUT"],
)
@login_required
def gravar_complemento_transferencia(aluno_id):
    aluno, falha = _aluno_historico(aluno_id)
    if falha:
        return falha
    dados = read_json_dict()
    if dados is None:
        return err("JSON inválido ou ausente")
    try:
        payload = salvar_complemento(aluno, dados, authz.escola_do_aluno(aluno))
        db.session.commit()
    except DocumentoErro as exc:
        db.session.rollback()
        return err(exc.message, exc.status)
    except ComplementoTransferenciaErro as exc:
        db.session.rollback()
        return err(exc.message, exc.status)
    return ok(payload)


@docs_pdf_bp.route("/api/alunos/<int:aluno_id>/documentos/boletim.pdf")
@login_required
def pdf_boletim(aluno_id):
    aluno, falha = _aluno_admin(aluno_id)
    if falha:
        return falha
    return _pdf_ou_erro(emitir_boletim_pdf, aluno)


@docs_pdf_bp.route("/api/alunos/<int:aluno_id>/documentos/declaracao/<tipo>.pdf")
@login_required
def pdf_declaracao(aluno_id, tipo):
    aluno, falha = _aluno_admin(aluno_id)
    if falha:
        return falha
    return _pdf_ou_erro(emitir_declaracao_pdf, aluno, tipo)


@docs_pdf_bp.route("/api/alunos/<int:aluno_id>/documentos/parecer.pdf")
@login_required
def pdf_parecer(aluno_id):
    aluno = db.session.get(Aluno, aluno_id)
    if not aluno:
        return err("Aluno não encontrado", 404)
    if not authz.pode_emitir_parecer(current_user, aluno):
        return authz.recusa()
    return _pdf_ou_erro(emitir_parecer_pdf, aluno)


@docs_pdf_bp.route("/api/alunos/<int:aluno_id>/documentos/comprovante-matricula.pdf")
@login_required
def pdf_comprovante(aluno_id):
    aluno, falha = _aluno_admin(aluno_id)
    if falha:
        return falha
    return _pdf_ou_erro(emitir_comprovante_pdf, aluno)


@docs_pdf_bp.route("/api/historicos/<int:historico_id>/boletim.pdf")
@login_required
def pdf_boletim_historico(historico_id):
    hist = obter_historico_anual(historico_id)
    if hist is None:
        return err("Histórico não encontrado", 404)
    escola_id = escola_id_para_acl(hist)
    if not authz.pode_emitir_boletim_historico(current_user, escola_id):
        return authz.recusa()
    return _pdf_ou_erro(emitir_boletim_historico_pdf, hist)


@docs_pdf_bp.route("/api/historicos/<int:historico_id>/parecer.pdf")
@login_required
def pdf_parecer_historico(historico_id):
    hist = obter_historico_anual(historico_id)
    if hist is None:
        return err("Histórico não encontrado", 404)
    escola_id = escola_id_para_acl(hist)
    if not authz.pode_emitir_parecer_historico(current_user, escola_id):
        return authz.recusa()
    return _pdf_ou_erro(emitir_parecer_historico_pdf, hist)


@docs_pdf_bp.route("/api/turmas/<int:turma_id>/documentos/rendimento/<int:disciplina_id>.pdf")
@login_required
def pdf_rendimento(turma_id, disciplina_id):
    turma = db.session.get(Turma, turma_id)
    if not turma:
        return err("Turma não encontrada", 404)
    disciplina = db.session.get(Disciplina, disciplina_id)
    if not disciplina:
        return err("Disciplina não encontrada", 404)
    if not turma.escola:
        return err("A escola desta turma não foi encontrada.", 404)
    if not authz.pode_relatorio(current_user, turma, disciplina_id):
        return authz.recusa()
    return _pdf_ou_erro(emitir_rendimento_pdf, turma, disciplina)
