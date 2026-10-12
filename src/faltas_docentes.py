"""Ocorrência de aula prevista não ministrada (falta docente).

Não cria RegistroAula. Ausência de diário não vira falta automaticamente.
Identidade da aula prevista: (horario_alocacao_id, data). HorarioAlocacao é
o padrão semanal (um registro por dia da semana, com quantidade_aulas do dia).
"""
from calendar import monthrange
from datetime import date, datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from src import db
from src.calendario import (
    ano_letivo_da_turma,
    dia_conta_como_letivo,
    indice_dias,
    periodo_da_data,
)
from src.diario import STATUS_INVALIDADA, STATUS_PENDENTE, STATUS_VALIDADA
from src.frequencia import parse_data_chamada
from src.models import (
    Alocacao,
    Disciplina,
    Escola,
    HorarioAlocacao,
    MOTIVO_FALTA_PROFESSOR,
    OcorrenciaAulaNaoMinistrada,
    Professor,
    RegistroAula,
    STATUS_OCORRENCIA_ATIVA,
    STATUS_OCORRENCIA_CANCELADA,
    Turma,
)

MSG_AULA_NAO_MINISTRADA = (
    "Esta aula foi registrada pela gestão como não ministrada. Procure a "
    "coordenação caso seja necessário corrigir a ocorrência."
)
MSG_JA_POSSUI_REGISTRO = "Esta aula já possui registro."
MSG_JA_EXISTE_OCORRENCIA = "Já existe uma ocorrência ativa para esta aula."
MSG_DIA_NAO_LETIVO = "Não é possível registrar falta docente em dia não letivo."
MSG_AULA_PREVISTA_OBRIGATORIA = "Selecione uma aula prevista no horário da escola."
MSG_MOTIVO_CANCELAMENTO = "Informe o motivo do cancelamento."
MSG_OCORRENCIA_JA_CANCELADA = "Esta ocorrência já está cancelada."
MSG_DATA_FORA_ANO = "A data está fora do ano letivo da turma."
ROTULO_MOTIVO = {
    MOTIVO_FALTA_PROFESSOR: "Falta do professor",
}


def _texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def _int_id(valor):
    if valor in (None, "", 0, "0"):
        return None
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def ocorrencia_ativa_para_alocacao(alocacao_id, data):
    if not alocacao_id or not data:
        return None
    return (
        OcorrenciaAulaNaoMinistrada.query.filter_by(
            alocacao_id=alocacao_id,
            data=data,
            status=STATUS_OCORRENCIA_ATIVA,
        ).first()
    )


def ocorrencia_ativa_para_horario(horario_id, data):
    if not horario_id or not data:
        return None
    return (
        OcorrenciaAulaNaoMinistrada.query.filter_by(
            horario_alocacao_id=horario_id,
            data=data,
            status=STATUS_OCORRENCIA_ATIVA,
        ).first()
    )


def registro_aula_valido(alocacao_id, data):
    """RegistroAula pendente ou validada no mesmo dia da alocação. Invalidada não bloqueia."""
    if not alocacao_id or not data:
        return None
    return (
        RegistroAula.query.filter(
            RegistroAula.alocacao_id == alocacao_id,
            RegistroAula.data == data,
            RegistroAula.status.in_((STATUS_PENDENTE, STATUS_VALIDADA)),
        ).first()
    )


def _dia_calendario_tipo(turma, data):
    ano = ano_letivo_da_turma(turma)
    if not ano:
        return None, None
    indice = indice_dias(ano)
    if not indice:
        return ano, None
    return ano, indice.get(data)


def _validar_calendario(turma, data):
    ano, tipo = _dia_calendario_tipo(turma, data)
    if ano is not None and (data < ano.data_inicio or data > ano.data_fim):
        return MSG_DATA_FORA_ANO
    if tipo is None:
        return None
    if not dia_conta_como_letivo(tipo):
        return MSG_DIA_NAO_LETIVO
    return None


def _estado_aula_prevista(aloc, data, horario_id):
    occ = ocorrencia_ativa_para_horario(horario_id, data)
    if occ:
        return "nao_ministrada", occ
    aulas = RegistroAula.query.filter_by(alocacao_id=aloc.id, data=data).all()
    if not aulas:
        return "sem_registro", None
    if any((a.status or "") == STATUS_VALIDADA for a in aulas):
        return "validada", next(a for a in aulas if a.status == STATUS_VALIDADA)
    if any((a.status or "") == STATUS_PENDENTE for a in aulas):
        return "pendente", next(a for a in aulas if a.status == STATUS_PENDENTE)
    return "registrada", aulas[0]


def payload_ocorrencia(occ):
    if not occ:
        return None
    horario = occ.horario
    return {
        "id": occ.id,
        "escolaId": occ.escola_id,
        "escolaNome": occ.escola.nome if occ.escola else None,
        "turmaId": occ.turma_id,
        "turmaNome": occ.turma.nome if occ.turma else None,
        "disciplinaId": occ.disciplina_id,
        "disciplinaNome": (
            occ.disciplina.nome if occ.disciplina
            else ("Educação Infantil" if occ.turma and occ.turma.etapa_ensino == "infantil" else None)
        ),
        "professorId": occ.professor_id,
        "professorNome": occ.professor.nome if occ.professor else None,
        "alocacaoId": occ.alocacao_id,
        "horarioAlocacaoId": occ.horario_alocacao_id,
        "diaSemana": horario.dia_semana if horario else None,
        "data": occ.data.isoformat() if occ.data else None,
        "quantidadeAulas": occ.quantidade_aulas,
        "motivo": occ.motivo,
        "motivoRotulo": ROTULO_MOTIVO.get(occ.motivo, occ.motivo),
        "observacao": occ.observacao,
        "status": occ.status,
        "registradoEm": occ.registrado_em.isoformat() if occ.registrado_em else None,
        "canceladoEm": occ.cancelado_em.isoformat() if occ.cancelado_em else None,
        "motivoCancelamento": occ.motivo_cancelamento,
    }


def payload_aula_prevista(horario, data):
    aloc = horario.alocacao
    turma = aloc.turma if aloc else None
    estado, extra = _estado_aula_prevista(aloc, data, horario.id)
    invalidada = (
        estado == "registrada"
        and extra is not None
        and (extra.status or "") == STATUS_INVALIDADA
    )
    pode_registrar = estado == "sem_registro" or invalidada
    return {
        "horarioAlocacaoId": horario.id,
        "alocacaoId": aloc.id if aloc else None,
        "escolaId": turma.escola_id if turma else None,
        "turmaId": aloc.turma_id if aloc else None,
        "turmaNome": turma.nome if turma else None,
        "etapaEnsino": turma.etapa_ensino if turma else None,
        "disciplinaId": aloc.disciplina_id if aloc else None,
        "disciplinaNome": (
            aloc.disciplina.nome if aloc and aloc.disciplina
            else ("Educação Infantil" if turma and turma.etapa_ensino == "infantil" else None)
        ),
        "professorId": aloc.professor_id if aloc else None,
        "professorNome": aloc.professor.nome if aloc and aloc.professor else None,
        "diaSemana": horario.dia_semana,
        "data": data.isoformat() if data else None,
        "quantidadeAulas": horario.quantidade_aulas,
        "estado": estado,
        "podeRegistrarFalta": pode_registrar,
        "registroAulaId": extra.id if estado in ("pendente", "validada", "registrada") and extra is not None else None,
        "ocorrenciaId": extra.id if estado == "nao_ministrada" and extra is not None else None,
    }


def listar_aulas_previstas(escola_id, data):
    weekday = data.weekday()
    horarios = (
        HorarioAlocacao.query.join(Alocacao, HorarioAlocacao.alocacao_id == Alocacao.id)
        .join(Turma, Alocacao.turma_id == Turma.id)
        .filter(
            Turma.escola_id == escola_id,
            Turma.status == "ativa",
            HorarioAlocacao.dia_semana == weekday,
        )
        .order_by(Turma.nome, HorarioAlocacao.id)
        .all()
    )
    itens = []
    dia_bloqueado = None
    for h in horarios:
        turma = h.alocacao.turma if h.alocacao else None
        if not turma:
            continue
        msg_cal = _validar_calendario(turma, data)
        if msg_cal == MSG_DIA_NAO_LETIVO:
            dia_bloqueado = msg_cal
            continue
        if msg_cal == MSG_DATA_FORA_ANO:
            continue
        itens.append(payload_aula_prevista(h, data))
    return {
        "data": data.isoformat(),
        "diaSemana": weekday,
        "diaNaoLetivo": bool(dia_bloqueado),
        "mensagemCalendario": dia_bloqueado,
        "aulas": itens,
    }


def criar_ocorrencia(user, dados, escola_id):
    data = parse_data_chamada(dados.get("data"))
    horario_id = _int_id(dados.get("horario_alocacao_id") or dados.get("horarioAlocacaoId"))
    observacao = _texto(dados.get("observacao")) or None
    if not data:
        return None, "Informe a data da aula prevista.", 400
    if not horario_id:
        return None, MSG_AULA_PREVISTA_OBRIGATORIA, 400

    horario = db.session.get(HorarioAlocacao, horario_id)
    if not horario or not horario.alocacao or not horario.alocacao.turma:
        return None, MSG_AULA_PREVISTA_OBRIGATORIA, 400
    aloc = horario.alocacao
    turma = aloc.turma
    if turma.escola_id != escola_id:
        return None, "Acesso negado", 403
    if horario.dia_semana != data.weekday():
        return None, "A aula selecionada não está prevista para esta data.", 400

    msg_cal = _validar_calendario(turma, data)
    if msg_cal:
        return None, msg_cal, 400

    aula = registro_aula_valido(aloc.id, data)
    if aula is not None:
        return None, MSG_JA_POSSUI_REGISTRO, 409
    existente = ocorrencia_ativa_para_horario(horario.id, data)
    if existente is not None:
        return None, MSG_JA_EXISTE_OCORRENCIA, 409

    qtd = int(horario.quantidade_aulas or 0)
    if qtd <= 0:
        return None, "A aula prevista não possui quantidade de aulas configurada.", 400

    try:
        occ = OcorrenciaAulaNaoMinistrada(
            escola_id=turma.escola_id,
            turma_id=turma.id,
            disciplina_id=aloc.disciplina_id,
            professor_id=aloc.professor_id,
            alocacao_id=aloc.id,
            horario_alocacao_id=horario.id,
            data=data,
            quantidade_aulas=qtd,
            motivo=MOTIVO_FALTA_PROFESSOR,
            observacao=observacao,
            status=STATUS_OCORRENCIA_ATIVA,
            registrado_por_id=user.id,
            registrado_em=datetime.utcnow(),
        )
        db.session.add(occ)
        db.session.flush()
        if registro_aula_valido(aloc.id, data) is not None:
            db.session.rollback()
            return None, MSG_JA_POSSUI_REGISTRO, 409
        if (
            OcorrenciaAulaNaoMinistrada.query.filter(
                OcorrenciaAulaNaoMinistrada.horario_alocacao_id == horario.id,
                OcorrenciaAulaNaoMinistrada.data == data,
                OcorrenciaAulaNaoMinistrada.status == STATUS_OCORRENCIA_ATIVA,
                OcorrenciaAulaNaoMinistrada.id != occ.id,
            ).first()
        ):
            db.session.rollback()
            return None, MSG_JA_EXISTE_OCORRENCIA, 409
        from src.auditoria import FALTA_DOCENTE_REGISTRADA, iso_data, registrar_auditoria
        registrar_auditoria(
            FALTA_DOCENTE_REGISTRADA,
            "OcorrenciaAulaNaoMinistrada",
            entidade_id=occ.id,
            escola_id=turma.escola_id,
            dados_anteriores=None,
            dados_novos={
                "escola_id": turma.escola_id,
                "turma_id": turma.id,
                "disciplina_id": aloc.disciplina_id,
                "professor_id": aloc.professor_id,
                "alocacao_id": aloc.id,
                "horario_alocacao_id": horario.id,
                "data": iso_data(data),
                "quantidade_aulas": qtd,
                "acao": "criar",
            },
        )
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return None, MSG_JA_EXISTE_OCORRENCIA, 409
    except Exception:
        db.session.rollback()
        raise
    return occ, None, 200


def cancelar_ocorrencia(occ, user, dados):
    if occ.status == STATUS_OCORRENCIA_CANCELADA:
        return None, MSG_OCORRENCIA_JA_CANCELADA, 400
    motivo = _texto(
        dados.get("motivo_cancelamento")
        or dados.get("motivoCancelamento")
        or dados.get("motivo")
    )
    if not motivo:
        return None, MSG_MOTIVO_CANCELAMENTO, 400
    occ.status = STATUS_OCORRENCIA_CANCELADA
    occ.cancelado_por_id = user.id
    occ.cancelado_em = datetime.utcnow()
    occ.motivo_cancelamento = motivo
    from src.auditoria import FALTA_DOCENTE_CANCELADA, iso_data, registrar_auditoria
    registrar_auditoria(
        FALTA_DOCENTE_CANCELADA,
        "OcorrenciaAulaNaoMinistrada",
        entidade_id=occ.id,
        escola_id=occ.escola_id,
        dados_anteriores={"status": STATUS_OCORRENCIA_ATIVA},
        dados_novos={
            "escola_id": occ.escola_id,
            "turma_id": occ.turma_id,
            "disciplina_id": occ.disciplina_id,
            "professor_id": occ.professor_id,
            "data": iso_data(occ.data),
            "quantidade_aulas": occ.quantidade_aulas,
            "acao": "cancelar",
            "motivo_informado": True,
            "tamanho_motivo": len(motivo),
        },
    )
    db.session.commit()
    return occ, None, 200


def periodo_padrao(hoje=None):
    hoje = hoje or date.today()
    info = periodo_da_data(hoje)
    periodo = info.get("periodo") if info else None
    if periodo and periodo.get("dataInicio") and periodo.get("dataFim"):
        return periodo["dataInicio"], periodo["dataFim"]
    inicio = date(hoje.year, hoje.month, 1)
    fim = date(hoje.year, hoje.month, monthrange(hoje.year, hoje.month)[1])
    return inicio.isoformat(), fim.isoformat()


def _query_ocorrencias(escola_id=None, filtros=None):
    filtros = filtros or {}
    q = OcorrenciaAulaNaoMinistrada.query
    if escola_id is not None:
        q = q.filter(OcorrenciaAulaNaoMinistrada.escola_id == escola_id)
    elif filtros.get("escola_id"):
        q = q.filter(OcorrenciaAulaNaoMinistrada.escola_id == filtros["escola_id"])
    if filtros.get("professor_id"):
        q = q.filter(OcorrenciaAulaNaoMinistrada.professor_id == filtros["professor_id"])
    if filtros.get("turma_id"):
        q = q.filter(OcorrenciaAulaNaoMinistrada.turma_id == filtros["turma_id"])
    if filtros.get("disciplina_id"):
        q = q.filter(OcorrenciaAulaNaoMinistrada.disciplina_id == filtros["disciplina_id"])
    inicio = filtros.get("data_inicio")
    fim = filtros.get("data_fim")
    if inicio:
        q = q.filter(OcorrenciaAulaNaoMinistrada.data >= inicio)
    if fim:
        q = q.filter(OcorrenciaAulaNaoMinistrada.data <= fim)
    return q


def listar_ocorrencias(escola_id=None, filtros=None):
    filtros = filtros or {}
    q = _query_ocorrencias(escola_id=escola_id, filtros=filtros)
    status = filtros.get("status")
    if status and status != "TODAS":
        q = q.filter(OcorrenciaAulaNaoMinistrada.status == status)
    return [
        payload_ocorrencia(o)
        for o in q.order_by(
            OcorrenciaAulaNaoMinistrada.data.desc(),
            OcorrenciaAulaNaoMinistrada.id.desc(),
        ).all()
    ]


def parse_filtros(args):
    inicio = parse_data_chamada(args.get("data_inicio") or args.get("dataInicio"))
    fim = parse_data_chamada(args.get("data_fim") or args.get("dataFim"))
    if inicio is None and fim is None:
        di, df = periodo_padrao()
        inicio = parse_data_chamada(di)
        fim = parse_data_chamada(df)
    status = (_texto(args.get("status")) or STATUS_OCORRENCIA_ATIVA).upper()
    if status in ("TODAS", "TODOS", "ALL"):
        status = "TODAS"
    elif status not in (STATUS_OCORRENCIA_ATIVA, STATUS_OCORRENCIA_CANCELADA):
        status = STATUS_OCORRENCIA_ATIVA
    return {
        "data_inicio": inicio,
        "data_fim": fim,
        "escola_id": _int_id(args.get("escola_id") or args.get("escolaId")),
        "professor_id": _int_id(args.get("professor_id") or args.get("professorId")),
        "turma_id": _int_id(args.get("turma_id") or args.get("turmaId")),
        "disciplina_id": _int_id(args.get("disciplina_id") or args.get("disciplinaId")),
        "status": status,
    }


def _agregar(q):
    n = q.with_entities(func.count(OcorrenciaAulaNaoMinistrada.id)).scalar() or 0
    aulas = q.with_entities(
        func.coalesce(func.sum(OcorrenciaAulaNaoMinistrada.quantidade_aulas), 0)
    ).scalar() or 0
    return int(n), int(aulas)


def resumo_faltas_docentes(escola_id=None):
    q = OcorrenciaAulaNaoMinistrada.query.filter_by(status=STATUS_OCORRENCIA_ATIVA)
    if escola_id is not None:
        q = q.filter_by(escola_id=escola_id)
    n, aulas = _agregar(q)
    escolas = (
        q.with_entities(OcorrenciaAulaNaoMinistrada.escola_id)
        .distinct()
        .count()
    )
    professores = (
        q.with_entities(OcorrenciaAulaNaoMinistrada.professor_id)
        .distinct()
        .count()
    )
    return {
        "faltasDocentesAtivas": n,
        "aulasNaoMinistradas": aulas,
        "escolasComOcorrencias": escolas,
        "professoresEnvolvidos": professores,
    }


def _opcoes_filtro(escola_id=None):
    escolas_q = Escola.query.order_by(Escola.nome)
    if escola_id is not None:
        escolas_q = escolas_q.filter_by(id=escola_id)
    turmas_q = Turma.query.filter_by(status="ativa").order_by(Turma.nome)
    profs_q = Professor.query.order_by(Professor.nome)
    if escola_id is not None:
        turmas_q = turmas_q.filter_by(escola_id=escola_id)
        profs_q = profs_q.filter_by(escola_id=escola_id)
    return {
        "escolas": [{"id": e.id, "nome": e.nome} for e in escolas_q.all()],
        "turmas": [{"id": t.id, "nome": t.nome, "escolaId": t.escola_id} for t in turmas_q.all()],
        "professores": [
            {"id": p.id, "nome": p.nome, "escolaId": p.escola_id} for p in profs_q.all()
        ],
        "disciplinas": [{"id": d.id, "nome": d.nome} for d in Disciplina.query.order_by(Disciplina.nome).all()],
    }


def relatorio_faltas_docentes(escola_id=None, filtros=None):
    filtros = filtros or {}
    q_base = _query_ocorrencias(escola_id=escola_id, filtros=filtros)
    q_ativas = q_base.filter(OcorrenciaAulaNaoMinistrada.status == STATUS_OCORRENCIA_ATIVA)
    totais_n, totais_aulas = _agregar(q_ativas)
    escolas_n = q_ativas.with_entities(OcorrenciaAulaNaoMinistrada.escola_id).distinct().count()
    profs_n = q_ativas.with_entities(OcorrenciaAulaNaoMinistrada.professor_id).distinct().count()

    status = filtros.get("status") or STATUS_OCORRENCIA_ATIVA
    if status == "TODAS":
        q_lista = q_base
    else:
        q_lista = q_base.filter(OcorrenciaAulaNaoMinistrada.status == status)
    itens = [
        payload_ocorrencia(o)
        for o in q_lista.order_by(
            OcorrenciaAulaNaoMinistrada.data.desc(),
            OcorrenciaAulaNaoMinistrada.id.desc(),
        ).all()
    ]

    por_escola_map = {}
    for o in q_ativas.all():
        chave = o.escola_id
        item = por_escola_map.setdefault(chave, {
            "escolaId": o.escola_id,
            "escolaNome": o.escola.nome if o.escola else None,
            "ocorrencias": 0,
            "aulasNaoMinistradas": 0,
        })
        item["ocorrencias"] += 1
        item["aulasNaoMinistradas"] += int(o.quantidade_aulas or 0)
    por_escola = sorted(por_escola_map.values(), key=lambda x: (x["escolaNome"] or "").lower())

    por_professor = []
    escola_filtro = escola_id if escola_id is not None else filtros.get("escola_id")
    if escola_filtro is not None:
        por_prof_map = {}
        for o in q_ativas.filter(OcorrenciaAulaNaoMinistrada.escola_id == escola_filtro).all():
            chave = o.professor_id
            item = por_prof_map.setdefault(chave, {
                "professorId": o.professor_id,
                "professorNome": o.professor.nome if o.professor else None,
                "ocorrencias": 0,
                "aulasNaoMinistradas": 0,
            })
            item["ocorrencias"] += 1
            item["aulasNaoMinistradas"] += int(o.quantidade_aulas or 0)
        por_professor = sorted(por_prof_map.values(), key=lambda x: (x["professorNome"] or "").lower())

    di = filtros.get("data_inicio")
    df = filtros.get("data_fim")
    return {
        "periodoPadrao": {
            "dataInicio": di.isoformat() if di else None,
            "dataFim": df.isoformat() if df else None,
        },
        "indicadores": {
            "ocorrenciasAtivas": totais_n,
            "aulasNaoMinistradas": totais_aulas,
            "escolasComOcorrencias": escolas_n,
            "professoresEnvolvidos": profs_n,
        },
        "porEscola": por_escola,
        "porProfessor": por_professor,
        "itens": itens,
        "opcoes": _opcoes_filtro(escola_id if escola_id is not None else filtros.get("escola_id")),
    }


def linhas_csv_relatorio(payload):
    linhas = [[
        "Data", "Escola", "Professor", "Turma", "Disciplina",
        "Dia da semana", "Qtd. aulas", "Motivo", "Situação",
    ]]
    for i in payload.get("itens") or []:
        linhas.append([
            i.get("data") or "",
            i.get("escolaNome") or "",
            i.get("professorNome") or "",
            i.get("turmaNome") or "",
            i.get("disciplinaNome") or "",
            "" if i.get("diaSemana") is None else str(i.get("diaSemana")),
            i.get("quantidadeAulas") if i.get("quantidadeAulas") is not None else "",
            i.get("motivoRotulo") or i.get("motivo") or "",
            "Ativa" if i.get("status") == STATUS_OCORRENCIA_ATIVA else "Cancelada",
        ])
    return linhas
