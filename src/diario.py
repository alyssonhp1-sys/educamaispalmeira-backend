"""Registro de aula + frequência do diário de classe.

Planejamento e RegistroFalta permanecem intactos. Este módulo só cria o fluxo novo.
"""
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from src import db
from src.frequencia import (
    FALTA,
    PRESENTE,
    alunos_ativos_da_turma,
    alocacao_do_professor,
    aplicar_frequencia,
    disciplina_armazenada,
    normalizar_bimestre,
    normalizar_situacao,
    parse_data_chamada,
    resolver_matricula_da_chamada,
)
from src.matriculas import MSG_SEM_MATRICULA_CURSANDO
from src.models import RegistroAula, Turma
from src.integridade import MSG_INTEGRIDADE_DUPLICADA

STATUS_PENDENTE = "pendente"
STATUS_VALIDADA = "validada"
STATUS_INVALIDADA = "invalidada"
STATUS_AULA = frozenset({STATUS_PENDENTE, STATUS_VALIDADA, STATUS_INVALIDADA})


def normalizar_quantidade_aulas(valor):
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return n


def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def query_registros(turma_id, disciplina_id):
    q = RegistroAula.query.filter_by(turma_id=turma_id)
    if disciplina_id is None:
        q = q.filter(RegistroAula.disciplina_id.is_(None))
    else:
        q = q.filter_by(disciplina_id=disciplina_id)
    return q.order_by(RegistroAula.data.desc(), RegistroAula.id.desc())


def query_registros_escola(escola_id):
    return (
        RegistroAula.query.join(Turma, RegistroAula.turma_id == Turma.id)
        .filter(Turma.escola_id == escola_id)
        .order_by(RegistroAula.data.desc(), RegistroAula.id.desc())
    )


def query_registros_professor(professor_id):
    return RegistroAula.query.filter_by(professor_id=professor_id).order_by(
        RegistroAula.data.desc(), RegistroAula.id.desc()
    )


def alunos_formulario(turma):
    alunos = []
    for aluno in sorted(alunos_ativos_da_turma(turma), key=lambda a: (a.nome or "").lower()):
        alunos.append({"id": aluno.id, "nome": aluno.nome, "situacao": PRESENTE})
    presentes = len(alunos)
    return alunos, presentes, 0


def _contagem_frequencia(itens):
    faltosos = sum(1 for a in itens if (a.get("situacao") or "").upper() == FALTA)
    presentes = len(itens) - faltosos
    return presentes, faltosos


def payload_registro(aula, incluir_frequencia=False):
    freq = []
    if incluir_frequencia:
        for r in sorted(aula.frequencias, key=lambda x: (x.aluno.nome if x.aluno else "").lower()):
            freq.append({
                "alunoId": r.aluno_id,
                "nome": r.aluno.nome if r.aluno else None,
                "situacao": r.situacao,
            })
        presentes, faltosos = _contagem_frequencia(freq)
    else:
        situacoes = [r.situacao for r in (aula.frequencias or [])]
        faltosos = sum(1 for s in situacoes if (s or "").upper() == FALTA)
        presentes = len(situacoes) - faltosos if situacoes else None
        if not situacoes:
            faltosos = None
    return {
        "id": aula.id,
        "professorId": aula.professor_id,
        "professorNome": aula.professor.nome if aula.professor else None,
        "turmaId": aula.turma_id,
        "turmaNome": aula.turma.nome if aula.turma else None,
        "disciplinaId": aula.disciplina_id,
        "disciplinaNome": (
            aula.disciplina.nome if aula.disciplina
            else ("Educação Infantil" if aula.turma and aula.turma.etapa_ensino == "infantil" else None)
        ),
        "alocacaoId": aula.alocacao_id,
        "data": aula.data.isoformat() if aula.data else None,
        "bimestre": aula.bimestre,
        "quantidadeAulas": aula.quantidade_aulas,
        "conteudo": aula.conteudo,
        "metodologia": aula.metodologia,
        "observacao": aula.observacao,
        "status": aula.status,
        "coordenadorId": aula.coordenador_id,
        "coordenadorNome": aula.coordenador.nome if aula.coordenador else None,
        "dataValidacao": aula.data_validacao.isoformat() if aula.data_validacao else None,
        "motivoInvalidacao": aula.motivo_invalidacao,
        "criadoEm": aula.criado_em.isoformat() if aula.criado_em else None,
        "frequencia": freq if incluir_frequencia else None,
        "presentes": presentes,
        "faltosos": faltosos,
    }


def criar_registro_aula(user, turma, disciplina_id, dados):
    professor = getattr(user, "professor", None)
    if not professor:
        return None, "Somente o professor alocado registra aula.", 403

    data = parse_data_chamada(dados.get("data"))
    bimestre = normalizar_bimestre(dados.get("bimestre"))
    quantidade = normalizar_quantidade_aulas(dados.get("quantidade_aulas", dados.get("quantidadeAulas")))
    conteudo = _texto(dados.get("conteudo"))
    metodologia = _texto(dados.get("metodologia"))
    observacao = _texto(dados.get("observacao")) or None

    if not data:
        return None, "Informe a data da aula (AAAA-MM-DD).", 400
    if not bimestre:
        from src.calendario import periodo_da_data
        info = periodo_da_data(data)
        if info.get("periodo"):
            bimestre = info["periodo"]["numero"]
    if not bimestre:
        return None, "Informe o bimestre (1 a 4).", 400
    if quantidade is None:
        return None, "A quantidade de aulas deve ser maior que zero.", 400
    infantil = turma.etapa_ensino == "infantil"
    if not conteudo:
        if infantil:
            return None, "Informe as experiências/atividades desenvolvidas.", 400
        return None, "Informe o conteúdo ministrado.", 400
    if not metodologia:
        if infantil:
            return None, "Informe as estratégias/metodologia.", 400
        return None, "Informe a metodologia.", 400

    disc_store = disciplina_armazenada(turma, disciplina_id)
    aloc = alocacao_do_professor(professor.id, turma, disc_store)
    if not aloc:
        return None, "Professor somente registra aula na própria alocação.", 403

    from src.faltas_docentes import MSG_AULA_NAO_MINISTRADA, ocorrencia_ativa_para_alocacao
    if ocorrencia_ativa_para_alocacao(aloc.id, data):
        return None, MSG_AULA_NAO_MINISTRADA, 409

    ativos = {a.id: a for a in alunos_ativos_da_turma(turma)}
    mapa = {}
    lancamentos = dados.get("lancamentos") or []
    if lancamentos and not isinstance(lancamentos, list):
        return None, "Envie a lista de alunos da frequência.", 400
    for item in lancamentos:
        if not isinstance(item, dict):
            return None, "Cada lançamento deve ser um objeto JSON.", 400
        try:
            aluno_id = int(item.get("aluno_id", item.get("alunoId")))
        except (TypeError, ValueError):
            return None, "Aluno inválido na frequência.", 400
        if aluno_id not in ativos:
            return None, "Aluno inativo ou fora desta turma não entra na chamada.", 400
        situacao = normalizar_situacao(item.get("situacao"))
        if not situacao:
            return None, "Situação inválida. Use PRESENTE ou FALTA.", 400
        mapa[aluno_id] = situacao

    pares = []
    for aluno in sorted(ativos.values(), key=lambda a: (a.nome or "").lower()):
        situacao = mapa.get(aluno.id, PRESENTE)
        mat = resolver_matricula_da_chamada(aluno.id, turma.id)
        if mat is None:
            db.session.rollback()
            return None, MSG_SEM_MATRICULA_CURSANDO, 400
        pares.append((aluno, mat, situacao))

    try:
        aula = RegistroAula(
            professor_id=professor.id,
            turma_id=turma.id,
            disciplina_id=disc_store,
            alocacao_id=aloc.id,
            data=data,
            bimestre=bimestre,
            quantidade_aulas=quantidade,
            conteudo=conteudo,
            metodologia=metodologia,
            observacao=observacao,
            status=STATUS_PENDENTE,
        )
        db.session.add(aula)
        db.session.flush()
        if ocorrencia_ativa_para_alocacao(aloc.id, data):
            db.session.rollback()
            return None, MSG_AULA_NAO_MINISTRADA, 409
        for aluno, mat, situacao in pares:
            aplicar_frequencia(
                mat,
                disc_store,
                data,
                bimestre,
                situacao,
                professor_id=professor.id,
                alocacao_id=aloc.id,
                registro_aula_id=aula.id,
                aluno_id=aluno.id,
                turma_id=turma.id,
            )
        n_faltas = sum(1 for _a, _m, sit in pares if sit == FALTA)
        from src.auditoria import (
            REGISTRO_AULA_CRIADO,
            iso_data,
            metadados_texto,
            registrar_auditoria,
        )
        registrar_auditoria(
            REGISTRO_AULA_CRIADO,
            "RegistroAula",
            entidade_id=aula.id,
            escola_id=turma.escola_id,
            dados_anteriores=None,
            dados_novos={
                "turma_id": turma.id,
                "disciplina_id": disc_store,
                "data": iso_data(data),
                "bimestre": bimestre,
                "quantidade_aulas": quantidade,
                "status": STATUS_PENDENTE,
                "n_frequencias": len(pares),
                "n_faltas": n_faltas,
                "n_presentes": len(pares) - n_faltas,
                "conteudo": metadados_texto(conteudo),
                "metodologia": metadados_texto(metodologia),
            },
        )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return None, str(exc), 400
    except IntegrityError:
        db.session.rollback()
        return None, MSG_INTEGRIDADE_DUPLICADA, 409
    except Exception:
        db.session.rollback()
        raise
    return aula, None, 200


def avaliar_registro_aula(aula, user, dados):
    status = _texto(dados.get("status") or dados.get("acao")).lower()
    if status in ("validar", "aprovar"):
        status = STATUS_VALIDADA
    if status in ("invalidar", "reprovar"):
        status = STATUS_INVALIDADA
    if status not in (STATUS_VALIDADA, STATUS_INVALIDADA):
        return None, "Informe o status: validada ou invalidada.", 400

    motivo = None
    if status == STATUS_INVALIDADA:
        motivo = _texto(dados.get("motivo") or dados.get("motivo_invalidacao") or dados.get("motivoInvalidacao"))
        if not motivo:
            return None, "Informe o motivo obrigatório para invalidar a aula.", 400

    agora = datetime.utcnow()
    status_anterior = aula.status
    aula.coordenador_id = user.id
    aula.data_validacao = agora
    aula.atualizado_em = agora
    if status == STATUS_INVALIDADA:
        aula.motivo_invalidacao = motivo
        aula.status = STATUS_INVALIDADA
    else:
        aula.motivo_invalidacao = None
        aula.status = STATUS_VALIDADA
    if status != status_anterior:
        from src.auditoria import (
            REGISTRO_AULA_INVALIDADO,
            REGISTRO_AULA_VALIDADO,
            registrar_auditoria,
        )
        acao = REGISTRO_AULA_INVALIDADO if status == STATUS_INVALIDADA else REGISTRO_AULA_VALIDADO
        dados_novos = {
            "registro_aula_id": aula.id,
            "turma_id": aula.turma_id,
            "disciplina_id": aula.disciplina_id,
            "status_anterior": status_anterior,
            "status_novo": status,
        }
        if status == STATUS_INVALIDADA:
            dados_novos["motivo_informado"] = bool(motivo)
            dados_novos["tamanho_motivo"] = len(motivo or "")
        registrar_auditoria(
            acao,
            "RegistroAula",
            entidade_id=aula.id,
            escola_id=aula.turma.escola_id if aula.turma is not None else None,
            dados_anteriores={
                "registro_aula_id": aula.id,
                "status": status_anterior,
            },
            dados_novos=dados_novos,
        )
    db.session.commit()
    return aula, None, 200
