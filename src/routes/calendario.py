"""Rotas do calendário letivo, períodos e matriz de cargas."""
from flask import Blueprint, request
from flask_login import login_required, current_user

from src import authz, db
from src.alunos import parse_data
from src.calendario import (
    atualizar_ano_letivo,
    criar_ano_letivo,
    cumprimento_alocacao,
    definir_dia,
    definir_periodos,
    gerar_calendario_inicial,
    payload_ano,
    payload_dia,
    periodo_da_data,
)
from src.http import err, ok, read_json_dict
from src.models import Alocacao, AnoLetivo, DiaCalendario, Disciplina, Turma

cal_bp = Blueprint("calendario", __name__)


def _ano_ou_404(ano_id):
    ano = db.session.get(AnoLetivo, ano_id)
    if not ano:
        return None, err("Ano letivo não encontrado", 404)
    return ano, None


@cal_bp.route("/api/anos-letivos", methods=["GET"])
@login_required
def api_listar_anos_letivos():
    if not authz.pode_consultar_calendario(current_user):
        return authz.recusa()
    anos = AnoLetivo.query.order_by(AnoLetivo.ano.desc()).all()
    return ok({"anos": [payload_ano(a) for a in anos], "ativo": payload_ano(next((a for a in anos if a.ativo), None))})


@cal_bp.route("/api/anos-letivos/ativo", methods=["GET"])
@login_required
def api_ano_ativo():
    if not authz.pode_consultar_calendario(current_user):
        return authz.recusa()
    ano = AnoLetivo.query.filter_by(ativo=True).order_by(AnoLetivo.ano.desc()).first()
    if not ano:
        return ok({"ano": None, "motivo": "sem_ano_ativo"})
    return ok(payload_ano(ano, incluir_dias=False))


@cal_bp.route("/api/anos-letivos", methods=["POST"])
@login_required
def api_criar_ano_letivo():
    if not authz.pode_alterar_calendario(current_user):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err("JSON inválido ou ausente")
    ano, mensagem = criar_ano_letivo(d)
    if mensagem:
        return err(mensagem)
    return ok({**payload_ano(ano, incluir_dias=False), "mensagem": "Ano letivo criado."})


@cal_bp.route("/api/anos-letivos/<int:ano_id>", methods=["GET"])
@login_required
def api_get_ano_letivo(ano_id):
    if not authz.pode_consultar_calendario(current_user):
        return authz.recusa()
    ano, resp = _ano_ou_404(ano_id)
    if resp:
        return resp
    mes = request.args.get("mes", type=int)
    ano_mes = request.args.get("ano", type=int) or (ano.ano if ano else None)
    incluir = request.args.get("dias", "1") not in ("0", "false", "nao")
    return ok(payload_ano(ano, incluir_dias=incluir, mes=mes, ano_mes=ano_mes))


@cal_bp.route("/api/anos-letivos/<int:ano_id>", methods=["PUT"])
@login_required
def api_put_ano_letivo(ano_id):
    if not authz.pode_alterar_calendario(current_user):
        return authz.recusa()
    ano, resp = _ano_ou_404(ano_id)
    if resp:
        return resp
    d = read_json_dict()
    if d is None:
        return err("JSON inválido ou ausente")
    mensagem = atualizar_ano_letivo(ano, d)
    if mensagem:
        return err(mensagem)
    return ok({**payload_ano(ano), "mensagem": "Ano letivo atualizado."})


@cal_bp.route("/api/anos-letivos/<int:ano_id>/periodos", methods=["PUT"])
@login_required
def api_put_periodos(ano_id):
    if not authz.pode_alterar_calendario(current_user):
        return authz.recusa()
    ano, resp = _ano_ou_404(ano_id)
    if resp:
        return resp
    d = read_json_dict()
    if d is None:
        return err("JSON inválido ou ausente")
    lista = d.get("periodos") if isinstance(d.get("periodos"), list) else d.get("bimestres")
    if lista is None and isinstance(d.get("items"), list):
        lista = d.get("items")
    if lista is None and "numero" in d:
        lista = [d]
    mensagem = definir_periodos(ano, lista or [])
    if mensagem:
        return err(mensagem)
    return ok({**payload_ano(ano), "mensagem": "Períodos atualizados."})


@cal_bp.route("/api/anos-letivos/<int:ano_id>/gerar-calendario", methods=["POST"])
@login_required
def api_gerar_calendario(ano_id):
    if not authz.pode_alterar_calendario(current_user):
        return authz.recusa()
    ano, resp = _ano_ou_404(ano_id)
    if resp:
        return resp
    criados = gerar_calendario_inicial(ano)
    return ok({
        **payload_ano(ano, incluir_dias=False),
        "diasGerados": criados,
        "mensagem": "Calendário inicial gerado. Dias já configurados foram preservados.",
    })


@cal_bp.route("/api/anos-letivos/<int:ano_id>/dias", methods=["GET"])
@login_required
def api_listar_dias(ano_id):
    if not authz.pode_consultar_calendario(current_user):
        return authz.recusa()
    ano, resp = _ano_ou_404(ano_id)
    if resp:
        return resp
    mes = request.args.get("mes", type=int)
    ano_mes = request.args.get("ano", type=int) or ano.ano
    return ok(payload_ano(ano, incluir_dias=True, mes=mes, ano_mes=ano_mes))


@cal_bp.route("/api/anos-letivos/<int:ano_id>/dias", methods=["PUT"])
@login_required
def api_put_dias(ano_id):
    if not authz.pode_alterar_calendario(current_user):
        return authz.recusa()
    ano, resp = _ano_ou_404(ano_id)
    if resp:
        return resp
    d = read_json_dict()
    if d is None:
        return err("JSON inválido ou ausente")
    itens = d.get("dias") if isinstance(d.get("dias"), list) else None
    if itens is None:
        itens = [d]
    atualizados = []
    criados = 0
    alterados = 0
    unico_manual = None
    from src.auditoria import (
        DIA_CALENDARIO_ALTERADO,
        auditar_calendario_lote,
        diff_campos,
        registrar_auditoria,
        snapshot_dia_calendario,
    )
    for item in itens:
        if not isinstance(item, dict):
            return err("Cada dia deve ser um objeto JSON.")
        data = parse_data(item.get("data"))
        if not data:
            return err("Informe a data (AAAA-MM-DD).")
        existente = DiaCalendario.query.filter_by(ano_letivo_id=ano.id, data=data).first()
        anterior = snapshot_dia_calendario(existente)
        dia, mensagem = definir_dia(ano, data, item.get("tipo"), item.get("descricao"), persistir=False)
        if mensagem:
            db.session.rollback()
            return err(mensagem)
        novo = snapshot_dia_calendario(dia)
        mudou = anterior is None or diff_campos(anterior, novo) != (None, None)
        if mudou:
            if anterior is None:
                criados += 1
            else:
                alterados += 1
            unico_manual = (anterior, novo, dia)
        atualizados.append(payload_dia(dia))
    if len(itens) == 1 and unico_manual is not None:
        anterior, novo, dia = unico_manual
        registrar_auditoria(
            DIA_CALENDARIO_ALTERADO,
            "DiaCalendario",
            entidade_id=dia.id,
            dados_anteriores=anterior,
            dados_novos=novo,
        )
    elif len(itens) > 1:
        auditar_calendario_lote(
            ano,
            quantidade_criados=criados,
            quantidade_alterados=alterados,
            geracao=False,
        )
    db.session.commit()
    return ok({
        "dias": atualizados,
        "diasLetivosPrevistos": ano.dias_letivos_previstos,
        "mensagem": "Calendário atualizado. Registros de aula não foram alterados.",
    })


@cal_bp.route("/api/calendario/periodo", methods=["GET"])
@login_required
def api_periodo_da_data():
    if not authz.pode_consultar_calendario(current_user):
        return authz.recusa()
    data = parse_data(request.args.get("data"))
    if not data:
        return err("Informe a data (AAAA-MM-DD).")
    ano_id = request.args.get("ano_letivo_id", type=int)
    ano = db.session.get(AnoLetivo, ano_id) if ano_id else None
    return ok(periodo_da_data(data, ano=ano))


@cal_bp.route("/api/alocacoes/<int:aloc_id>", methods=["GET"])
@login_required
def api_get_alocacao(aloc_id):
    aloc = db.session.get(Alocacao, aloc_id)
    if not aloc or not aloc.turma:
        return err("Alocação não encontrada", 404)
    if not authz.pode_ver_turma(current_user, aloc.turma):
        return authz.recusa()
    data_ref = parse_data(request.args.get("data"))
    return ok(cumprimento_alocacao(aloc, data_ref=data_ref))


@cal_bp.route("/api/alocacoes/<int:aloc_id>", methods=["PUT"])
@login_required
def api_put_alocacao(aloc_id):
    from src.calendario import aplicar_campos_carga
    aloc = db.session.get(Alocacao, aloc_id)
    if not aloc or not aloc.turma:
        return err("Alocação não encontrada", 404)
    if not authz.pode_configurar_carga(current_user, turma=aloc.turma, aloc=aloc):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err("JSON inválido ou ausente")
    from src.auditoria import ALOCACAO_ALTERADA, registrar_alteracao, snapshot_alocacao
    anterior = snapshot_alocacao(aloc)
    if "professor_id" in d or "professorId" in d:
        from src.models import Professor
        pid = d.get("professor_id", d.get("professorId"))
        prof = db.session.get(Professor, pid)
        if not prof:
            return err("Professor não encontrado", 404)
        if not authz.pode_alocar(current_user, aloc.turma, prof):
            return authz.recusa()
        aloc.professor_id = prof.id
    mensagem = aplicar_campos_carga(aloc, d)
    if mensagem:
        return err(mensagem)
    db.session.flush()
    registrar_alteracao(
        ALOCACAO_ALTERADA,
        "Alocacao",
        entidade_id=aloc.id,
        escola_id=aloc.turma.escola_id if aloc.turma else None,
        anterior=anterior,
        novo=snapshot_alocacao(aloc),
        identidade={
            "alocacao_id": aloc.id,
            "professor_id": aloc.professor_id,
            "turma_id": aloc.turma_id,
            "disciplina_id": aloc.disciplina_id,
        },
    )
    db.session.commit()
    return ok({**cumprimento_alocacao(aloc), "mensagem": "Alocação atualizada."})


@cal_bp.route("/api/turmas/<int:turma_id>", methods=["PUT"])
@login_required
def api_put_turma(turma_id):
    from src.calendario import aplicar_jornada_turma
    turma = db.session.get(Turma, turma_id)
    if not turma:
        return err("Turma não encontrada", 404)
    if not authz.pode_editar_turma(current_user, turma):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err("JSON inválido ou ausente")
    from src.auditoria import TURMA_ALTERADA, registrar_alteracao, snapshot_turma
    anterior = snapshot_turma(turma)
    if d.get("nome"):
        turma.nome = d["nome"]
    if "capacidade" in d:
        from src.alunos import parse_capacidade
        cap, erro_cap = parse_capacidade(d.get("capacidade"))
        if erro_cap:
            return err(erro_cap)
        turma.capacidade = cap
    mensagem = aplicar_jornada_turma(turma, d)
    if mensagem:
        return err(mensagem)
    registrar_alteracao(
        TURMA_ALTERADA,
        "Turma",
        entidade_id=turma.id,
        escola_id=turma.escola_id,
        anterior=anterior,
        novo=snapshot_turma(turma),
        identidade={"turma_id": turma.id, "escola_id": turma.escola_id},
    )
    db.session.commit()
    return ok({"mensagem": "Turma atualizada.", "id": turma.id})


@cal_bp.route("/api/matriz-cargas", methods=["GET"])
@login_required
def api_matriz_cargas():
    if not (authz.municipal(current_user) or authz.gestor(current_user)):
        return authz.recusa()
    etapa = (request.args.get("etapa") or "").strip().lower() or None
    ano = request.args.get("ano_letivo", type=int) or request.args.get("ano", type=int)
    q = Alocacao.query.join(Turma)
    if authz.gestor(current_user):
        q = q.filter(Turma.escola_id == current_user.escola_id)
    if etapa:
        q = q.filter(Turma.etapa_ensino == etapa)
    if ano:
        q = q.filter(Turma.ano_letivo == ano)
    q = q.filter(Turma.status == "ativa")
    itens = []
    for aloc in q.order_by(Turma.nome).all():
        turma = aloc.turma
        itens.append({
            "alocacaoId": aloc.id,
            "escolaId": turma.escola_id if turma else None,
            "escolaNome": turma.escola.nome if turma and turma.escola else None,
            "turmaId": aloc.turma_id,
            "turmaNome": turma.nome if turma else None,
            "anoLetivo": turma.ano_letivo if turma else None,
            "etapaEnsino": turma.etapa_ensino if turma else None,
            "serie": turma.nome if turma else None,
            "professorId": aloc.professor_id,
            "professorNome": aloc.professor.nome if aloc.professor else None,
            "disciplinaId": aloc.disciplina_id,
            "disciplinaNome": aloc.disciplina.nome if aloc.disciplina else (
                "Educação Infantil" if turma and turma.etapa_ensino == "infantil" else None
            ),
            "cargaHorariaPrevista": aloc.carga_horaria_prevista,
            "aulasSemanais": aloc.aulas_semanais,
            "duracaoAulaMinutos": aloc.duracao_aula_minutos,
            "cargaHorariaNaoConfigurada": aloc.carga_horaria_prevista is None,
        })
    disciplinas = [{"id": d.id, "nome": d.nome} for d in Disciplina.query.order_by(Disciplina.nome).all()]
    return ok({"itens": itens, "disciplinas": disciplinas})
