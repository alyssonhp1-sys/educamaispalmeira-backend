from flask import Blueprint
from flask_login import login_required, current_user
from src import db
from src.http import ok, err
from src import authz
from src.notas import media_minima_vigente
from src.alunos import ocupantes_ativos
from src.models import (
    Escola, Professor, Usuario, Aluno, Aviso, Turma, Planejamento,
    Disciplina, alunos_da_escola,
)
from src.indicadores import (
    listar_alunos_em_risco, listar_diarios_pendentes, resumo_escola, resumo_rede,
    acompanhamento_escola, acompanhamento_rede,
)
from src.faltas_docentes import resumo_faltas_docentes
from src.diario import payload_registro, query_registros_escola, query_registros_professor

spa_bp = Blueprint("spa", __name__)


def user_payload(user):
    return {
        "id": user.id,
        "login": user.login,
        "nome": user.nome,
        "papel": user.papel,
        "primeiroAcesso": user.primeiro_acesso,
        "escolaId": user.escola_id,
        "escolaNome": user.escola.nome if user.escola else None,
        "professorNome": user.professor.nome if getattr(user, "professor", None) else None,
    }


def turma_payload(turma, incluir_alunos=False):
    item = {
        "id": turma.id,
        "nome": turma.nome,
        "anoLetivo": turma.ano_letivo,
        "status": turma.status,
        "etapaEnsino": turma.etapa_ensino,
        "escolaId": turma.escola_id,
        "escolaNome": turma.escola.nome if turma.escola else None,
        "qtdAlunos": ocupantes_ativos(turma),
        "capacidade": turma.capacidade,
        "tipoJornada": turma.tipo_jornada,
        "cargaHorariaDiaria": turma.carga_horaria_diaria,
        "cargaHorariaAnual": turma.carga_horaria_anual,
        "alocacoes": [
            {
                "id": a.id,
                "professorId": a.professor_id,
                "professorNome": a.professor.nome if a.professor else None,
                "disciplinaId": a.disciplina_id,
                "disciplinaNome": a.disciplina.nome if a.disciplina else None,
                "cargaHorariaPrevista": a.carga_horaria_prevista,
                "aulasSemanais": a.aulas_semanais,
                "duracaoAulaMinutos": a.duracao_aula_minutos,
                "grade": [
                    {"diaSemana": h.dia_semana, "quantidadeAulas": h.quantidade_aulas}
                    for h in sorted(a.horarios or [], key=lambda x: x.dia_semana)
                ],
            }
            for a in turma.alocacoes
        ],
    }
    if incluir_alunos:
        item["alunos"] = [
            aluno_payload(a) for a in turma.alunos
            if (a.situacao or "").lower() != "inativo"
        ]
    return item


def aluno_payload(aluno):
    return {
        "id": aluno.id,
        "nome": aluno.nome,
        "cpf": aluno.cpf,
        "situacao": aluno.situacao,
        "turmaId": aluno.turma_id,
        "turmaNome": aluno.turma.nome if aluno.turma else None,
        "etapaEnsino": aluno.turma.etapa_ensino if aluno.turma else None,
        "turmaStatus": aluno.turma.status if aluno.turma else None,
        "escolaId": aluno.escola_id,
        "dataNascimento": aluno.data_nascimento.isoformat() if aluno.data_nascimento else None,
        "localNascimento": aluno.local_nascimento,
        "endereco": aluno.endereco,
        "telefone": aluno.telefone,
        "nomeMae": aluno.nome_mae,
        "profMae": aluno.prof_mae,
        "nomePai": aluno.nome_pai,
        "profPai": aluno.prof_pai,
        "temAlergia": aluno.tem_alergia,
        "temDeficiencia": aluno.tem_deficiencia,
        "rotaTransporte": aluno.rota_transporte,
        "nisBolsa": aluno.nis_bolsa,
    }


def aviso_payload(aviso):
    return {
        "id": aviso.id,
        "titulo": aviso.titulo,
        "conteudo": aviso.conteudo,
        "dataCriacao": aviso.data_criacao.isoformat() if aviso.data_criacao else None,
        "autorId": aviso.autor_id,
    }


def plan_payload(plan):
    tem_arquivo = bool(plan.arquivo)
    nome_exibicao = (plan.arquivo_original or "").strip() or None
    return {
        "id": plan.id,
        "professorId": plan.professor_id,
        "professorNome": plan.professor.nome if plan.professor else None,
        "turmaId": plan.turma_id,
        "turmaNome": plan.turma.nome if plan.turma else None,
        "dataEnvio": plan.data_envio.strftime("%d/%m") if plan.data_envio else None,
        "conteudo": plan.conteudo,
        "arquivo": (nome_exibicao or "anexo") if tem_arquivo else None,
        "feedback": plan.feedback_coordenacao,
        "status": plan.status,
        "arquivado": plan.arquivado,
    }


@spa_bp.route("/api/me")
@login_required
def api_me():
    return ok(user_payload(current_user))


@spa_bp.route("/api/dashboard")
@login_required
def api_dashboard():
    if current_user.primeiro_acesso:
        return ok({"precisaDefinirSenha": True, "usuario": user_payload(current_user)})

    papel = current_user.papel

    if papel in ["dirigente", "secretaria"]:
        escolas = Escola.query.order_by(Escola.nome).all()
        professores = Professor.query.all()
        gestores = Usuario.query.filter_by(papel="gestor_escolar").all()
        coords = Usuario.query.filter_by(papel="coordenador_pedagogico").all()
        return ok({
            "precisaDefinirSenha": False,
            "usuario": user_payload(current_user),
            "painel": "municipal",
            "escolas": [
                {
                    "id": e.id,
                    "nome": e.nome,
                    "inep": e.inep,
                    "ofertaInfantil": e.oferta_infantil,
                    "ofertaFundamental": e.oferta_fundamental,
                }
                for e in escolas
            ],
            "professores": [
                {
                    "id": p.id,
                    "usuarioId": p.usuario_id,
                    "nome": p.nome,
                    "cpf": p.cpf,
                    "escolaNome": p.escola.nome if p.escola else None,
                }
                for p in professores
            ],
            "gestores": [
                {
                    "id": g.id,
                    "login": g.login,
                    "nome": g.nome,
                    "escolaNome": g.escola.nome if g.escola else None,
                }
                for g in gestores
            ],
            "coordenadores": [
                {
                    "id": c.id,
                    "login": c.login,
                    "nome": c.nome,
                    "escolaNome": c.escola.nome if c.escola else None,
                }
                for c in coords
            ],
            "totalAlunos": Aluno.query.count(),
            "indicadores": {**resumo_rede(), **resumo_faltas_docentes()},
            "acompanhamentoPedagogico": acompanhamento_rede(),
            "configuracaoAcademica": {
                "mediaMinima": media_minima_vigente(),
            },
        })

    if papel == "gestor_escolar":
        if not current_user.escola_id:
            return err("Escola não vinculada", 403)
        escola = current_user.escola
        if not escola:
            return err("Escola não encontrada", 404)
        todas = Turma.query.filter_by(escola_id=escola.id).all()
        professores = Professor.query.filter_by(escola_id=escola.id).all()
        disciplinas = Disciplina.query.all()
        alunos = alunos_da_escola(escola.id)
        avisos = Aviso.query.filter(
            (Aviso.escola_destino_id == escola.id) | (Aviso.escola_destino_id == None)
        ).order_by(Aviso.data_criacao.desc()).all()
        return ok({
            "precisaDefinirSenha": False,
            "usuario": user_payload(current_user),
            "painel": "escola",
            "escola": {
                "id": escola.id,
                "nome": escola.nome,
                "inep": escola.inep,
                "ofertaInfantil": escola.oferta_infantil,
                "ofertaFundamental": escola.oferta_fundamental,
            },
            "turmas": [turma_payload(t) for t in todas if t.status == "ativa"],
            "historico": [turma_payload(t) for t in todas if t.status == "concluida"],
            "professores": [
                {"id": p.id, "usuarioId": p.usuario_id, "nome": p.nome, "cpf": p.cpf}
                for p in professores
            ],
            "disciplinas": [{"id": d.id, "nome": d.nome} for d in disciplinas],
            "alunos": [aluno_payload(a) for a in alunos],
            "avisos": [aviso_payload(a) for a in avisos],
            "indicadores": {**resumo_escola(escola.id), **resumo_faltas_docentes(escola.id)},
            "acompanhamentoPedagogico": acompanhamento_escola(escola.id),
        })

    if papel == "coordenador_pedagogico":
        if not current_user.escola_id:
            return err("Escola não vinculada", 403)
        if not current_user.escola:
            return err("Escola não encontrada", 404)
        base = Planejamento.query.join(Turma).filter(Turma.escola_id == current_user.escola_id)
        ativ = base.filter(Planejamento.arquivado == False).order_by(Planejamento.data_envio.desc()).all()
        arq = base.filter(Planejamento.arquivado == True).order_by(Planejamento.data_envio.desc()).all()
        turmas = Turma.query.filter_by(escola_id=current_user.escola_id, status="ativa").all()
        registros = query_registros_escola(current_user.escola_id).all()
        return ok({
            "precisaDefinirSenha": False,
            "usuario": user_payload(current_user),
            "painel": "coordenador",
            "escola": {"id": current_user.escola.id, "nome": current_user.escola.nome},
            "planejamentos": [plan_payload(p) for p in ativ],
            "arquivados": [plan_payload(p) for p in arq],
            "registrosAula": [payload_registro(a) for a in registros],
            "turmas": [turma_payload(t) for t in turmas],
            "indicadores": {
                **resumo_escola(current_user.escola_id),
                **resumo_faltas_docentes(current_user.escola_id),
            },
            "alunosEmRisco": listar_alunos_em_risco(escola_id=current_user.escola_id),
            "diariosPendentes": listar_diarios_pendentes(escola_id=current_user.escola_id),
        })

    if papel == "professor":
        if not current_user.professor:
            return err("Professor não vinculado", 403)
        eid = current_user.professor.escola_id
        avisos = Aviso.query.filter(
            (Aviso.escola_destino_id == eid) | (Aviso.escola_destino_id == None)
        ).order_by(Aviso.data_criacao.desc()).all()
        base = Planejamento.query.filter_by(professor_id=current_user.professor.id)
        ativ = base.filter(Planejamento.arquivado == False).order_by(Planejamento.data_envio.desc()).all()
        arq = base.filter(Planejamento.arquivado == True).order_by(Planejamento.data_envio.desc()).all()
        aloc = [a for a in current_user.professor.alocacoes if a.turma and a.turma.status == "ativa"]
        return ok({
            "precisaDefinirSenha": False,
            "usuario": user_payload(current_user),
            "painel": "professor",
            "escolaNome": current_user.professor.escola.nome if current_user.professor.escola else None,
            "alocacoes": [
                {
                    "id": a.id,
                    "turmaId": a.turma_id,
                    "turmaNome": a.turma.nome if a.turma else None,
                    "disciplinaId": a.disciplina_id,
                    "disciplinaNome": a.disciplina.nome if a.disciplina else "Educação Infantil",
                    "etapaEnsino": a.turma.etapa_ensino if a.turma else None,
                    "cargaHorariaPrevista": a.carga_horaria_prevista,
                    "aulasSemanais": a.aulas_semanais,
                    "duracaoAulaMinutos": a.duracao_aula_minutos,
                }
                for a in aloc
            ],
            "planejamentos": [plan_payload(p) for p in ativ],
            "arquivados": [plan_payload(p) for p in arq],
            "registrosAula": [payload_registro(r) for r in query_registros_professor(current_user.professor.id).all()],
            "avisos": [aviso_payload(a) for a in avisos],
        })

    return err("Papel não reconhecido", 403)


@spa_bp.route("/api/escolas/<int:escola_id>/painel")
@login_required
def api_monitorar(escola_id):
    if current_user.papel not in ["dirigente", "secretaria"]:
        return err("Acesso negado", 403)
    escola = db.session.get(Escola, escola_id)
    if not escola:
        return err("Escola não encontrada", 404)
    turmas = Turma.query.filter_by(escola_id=escola.id).all()
    professores = Professor.query.filter_by(escola_id=escola.id).all()
    disciplinas = Disciplina.query.all()
    return ok({
        "escola": {"id": escola.id, "nome": escola.nome, "inep": escola.inep},
        "turmas": [turma_payload(t) for t in turmas],
        "professores": [{"id": p.id, "nome": p.nome, "cpf": p.cpf} for p in professores],
        "disciplinas": [{"id": d.id, "nome": d.nome} for d in disciplinas],
        "totalAlunos": len(alunos_da_escola(escola.id)),
        "acompanhamentoPedagogico": acompanhamento_escola(escola.id),
    })


@spa_bp.route("/api/turmas/<int:turma_id>/painel")
@login_required
def api_turma_painel(turma_id):
    turma = db.session.get(Turma, turma_id)
    if not turma:
        return err("Turma não encontrada", 404)
    if not authz.pode_ver_turma(current_user, turma):
        return authz.recusa()

    pode_editar, is_coord, disciplinas = False, False, []
    if authz.municipal(current_user) or (
        current_user.papel in ["gestor_escolar", "coordenador_pedagogico"]
        and current_user.escola_id == turma.escola_id
    ):
        is_coord = True
        disciplinas = Disciplina.query.all()
        pode_editar = False
    elif authz.professor(current_user) and current_user.professor:
        aloc = [a for a in current_user.professor.alocacoes if a.turma_id == turma_id]
        pode_editar = turma.status != "concluida" and bool(aloc)
        disciplinas = [a.disciplina for a in aloc if a.disciplina]

    if turma.status == "concluida":
        pode_editar = False

    alocado_turma = authz.professor(current_user) and authz.alocado(current_user, turma.id)
    docs_admin = authz.municipal(current_user) or (
        authz.gestor(current_user) and current_user.escola_id == turma.escola_id
    )
    docs_parecer = False
    if turma.etapa_ensino == "infantil":
        if authz.municipal(current_user):
            docs_parecer = True
        elif current_user.papel in ("gestor_escolar", "coordenador_pedagogico"):
            docs_parecer = current_user.escola_id == turma.escola_id
        elif alocado_turma:
            docs_parecer = True

    return ok({
        "turma": turma_payload(turma, incluir_alunos=True),
        "podeEditar": pode_editar,
        "isCoord": is_coord,
        "papel": current_user.papel,
        "podeChamada": bool(alocado_turma and turma.status != "concluida"),
        "podeRegistrarAula": bool(alocado_turma and turma.status != "concluida"),
        "podeHistoricoAulas": True,
        "docsAdministrativos": bool(docs_admin),
        "docsParecer": bool(docs_parecer),
        "docsRendimento": turma.etapa_ensino != "infantil",
        "disciplinas": [{"id": d.id, "nome": d.nome} for d in disciplinas if d],
        "mediaMinima": media_minima_vigente(),
    })
