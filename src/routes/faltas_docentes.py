"""Rotas de aula não ministrada (falta docente) e relatório gerencial."""
import csv
import io

from flask import Blueprint, Response, request
from flask_login import current_user, login_required

from src import authz, db
from src.faltas_docentes import (
    cancelar_ocorrencia,
    criar_ocorrencia,
    listar_aulas_previstas,
    listar_ocorrencias,
    linhas_csv_relatorio,
    parse_filtros,
    payload_ocorrencia,
    periodo_padrao,
    relatorio_faltas_docentes,
)
from src.frequencia import parse_data_chamada
from src.http import err, ok, read_json_dict
from src.models import OcorrenciaAulaNaoMinistrada

faltas_bp = Blueprint("faltas_docentes", __name__)


def _escola_escopo_escrita():
    if not authz.pode_registrar_falta_docente(current_user):
        return None, authz.recusa()
    return current_user.escola_id, None


def _escola_escopo_leitura(escola_id=None):
    if authz.municipal(current_user):
        return escola_id, None
    if not authz.pode_ver_faltas_docentes(current_user, current_user.escola_id):
        return None, authz.recusa()
    if escola_id is not None and escola_id != current_user.escola_id:
        return None, authz.recusa()
    return current_user.escola_id, None


@faltas_bp.route("/api/faltas-docentes/aulas-previstas", methods=["GET"])
@login_required
def api_aulas_previstas():
    escola_id, recusa = _escola_escopo_escrita()
    if recusa:
        return recusa
    data = parse_data_chamada(request.args.get("data"))
    if not data:
        return err("Informe a data (AAAA-MM-DD).")
    return ok(listar_aulas_previstas(escola_id, data))


@faltas_bp.route("/api/faltas-docentes", methods=["GET", "POST"])
@login_required
def api_faltas_docentes():
    if request.method == "POST":
        escola_id, recusa = _escola_escopo_escrita()
        if recusa:
            return recusa
        d = read_json_dict()
        if d is None:
            return err("JSON inválido ou ausente")
        occ, mensagem, status = criar_ocorrencia(current_user, d, escola_id)
        if mensagem:
            return err(mensagem, status)
        payload = payload_ocorrencia(occ)
        payload["mensagem"] = "Falta docente registrada."
        return ok(payload)

    escola_arg = request.args.get("escola_id", type=int)
    escola_id, recusa = _escola_escopo_leitura(escola_arg)
    if recusa:
        return recusa
    filtros = parse_filtros(request.args)
    if not authz.municipal(current_user):
        filtros["escola_id"] = escola_id
        escola_filtro = escola_id
    else:
        escola_filtro = filtros.get("escola_id")
    itens = listar_ocorrencias(escola_id=escola_filtro, filtros=filtros)
    di, df = periodo_padrao()
    inicio = filtros.get("data_inicio")
    fim = filtros.get("data_fim")
    return ok({
        "ocorrencias": itens,
        "periodoPadrao": {
            "dataInicio": inicio.isoformat() if inicio else di,
            "dataFim": fim.isoformat() if fim else df,
        },
    })


@faltas_bp.route("/api/faltas-docentes/<int:occ_id>/cancelar", methods=["POST"])
@login_required
def api_cancelar_falta_docente(occ_id):
    occ = db.session.get(OcorrenciaAulaNaoMinistrada, occ_id)
    if not occ:
        return err("Ocorrência não encontrada", 404)
    if not authz.pode_cancelar_falta_docente(current_user, occ):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err("JSON inválido ou ausente")
    occ, mensagem, status = cancelar_ocorrencia(occ, current_user, d)
    if mensagem:
        return err(mensagem, status)
    payload = payload_ocorrencia(occ)
    payload["mensagem"] = "Ocorrência cancelada."
    return ok(payload)


@faltas_bp.route("/api/faltas-docentes/relatorio", methods=["GET"])
@login_required
def api_relatorio_faltas_docentes():
    if not authz.pode_relatorio_faltas_docentes(current_user):
        return authz.recusa()
    escola_arg = request.args.get("escola_id", type=int)
    escola_id, recusa = _escola_escopo_leitura(escola_arg)
    if recusa:
        return recusa
    filtros = parse_filtros(request.args)
    if authz.municipal(current_user):
        payload = relatorio_faltas_docentes(escola_id=None, filtros=filtros)
        payload["escopo"] = "escola" if filtros.get("escola_id") else "rede"
    else:
        filtros["escola_id"] = escola_id
        payload = relatorio_faltas_docentes(escola_id=escola_id, filtros=filtros)
        payload["escopo"] = "escola"
    return ok(payload)


@faltas_bp.route("/api/faltas-docentes/relatorio.csv", methods=["GET"])
@login_required
def api_relatorio_faltas_csv():
    if not authz.pode_relatorio_faltas_docentes(current_user):
        return authz.recusa()
    escola_arg = request.args.get("escola_id", type=int)
    escola_id, recusa = _escola_escopo_leitura(escola_arg)
    if recusa:
        return recusa
    filtros = parse_filtros(request.args)
    if not authz.municipal(current_user):
        filtros["escola_id"] = escola_id
        payload = relatorio_faltas_docentes(escola_id=escola_id, filtros=filtros)
    else:
        payload = relatorio_faltas_docentes(escola_id=None, filtros=filtros)
    buf = io.StringIO()
    writer = csv.writer(buf)
    for linha in linhas_csv_relatorio(payload):
        writer.writerow(linha)
    return Response(
        "\ufeff" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=faltas-docentes.csv"},
    )
