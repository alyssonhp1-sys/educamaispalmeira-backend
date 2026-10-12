"""Calendário letivo, grade semanal e previsão de aulas.

Cálculos de previsão e cumprimento ficam no backend. Alterar o calendário
não apaga RegistroAula nem outros registros pedagógicos.
"""
from datetime import date, timedelta

from src import db
from src.alunos import parse_data
from src.models import (
    Alocacao,
    AnoLetivo,
    DiaCalendario,
    HorarioAlocacao,
    NOMES_PERIODO_PADRAO,
    PeriodoLetivo,
    RegistroAula,
    TIPOS_DIA_CALENDARIO,
    TIPOS_DIA_LETIVO,
    TIPOS_JORNADA,
    Turma,
)

STATUS_VALIDADA = "validada"
STATUS_INVALIDADA = "invalidada"
STATUS_PENDENTE = "pendente"


def parse_data_iso(bruto):
    if isinstance(bruto, date):
        return bruto
    return parse_data(bruto)


def _int_opcional(valor, nome, minimo=1):
    if valor is None or str(valor).strip() == "":
        return None, None
    try:
        n = int(valor)
    except (TypeError, ValueError):
        return None, "%s inválid(a)." % nome
    if n < minimo:
        return None, "%s deve ser maior ou igual a %s." % (nome, minimo)
    return n, None


def _float_opcional(valor, nome, minimo=0):
    if valor is None or str(valor).strip() == "":
        return None, None
    try:
        n = float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None, "%s inválid(a)." % nome
    if n <= minimo:
        return None, "%s deve ser maior que %s." % (nome, minimo)
    return n, None


def percentual(numerador, denominador):
    if not denominador:
        return None
    return round(100.0 * float(numerador) / float(denominador), 1)


def ano_letivo_ativo():
    return AnoLetivo.query.filter_by(ativo=True).order_by(AnoLetivo.ano.desc()).first()


def ano_letivo_da_turma(turma):
    if not turma:
        return ano_letivo_ativo()
    encontrado = AnoLetivo.query.filter_by(ano=turma.ano_letivo).first()
    if encontrado:
        return encontrado
    return ano_letivo_ativo()


def _desativar_outros(exceto_id=None):
    from src.auditoria import ANO_LETIVO_ALTERADO, registrar_alteracao, snapshot_ano_letivo
    q = AnoLetivo.query.filter_by(ativo=True)
    if exceto_id is not None:
        q = q.filter(AnoLetivo.id != exceto_id)
    for outro in q.all():
        anterior = snapshot_ano_letivo(outro)
        outro.ativo = False
        registrar_alteracao(
            ANO_LETIVO_ALTERADO,
            "AnoLetivo",
            entidade_id=outro.id,
            anterior=anterior,
            novo=snapshot_ano_letivo(outro),
            identidade={"ano_letivo_id": outro.id, "ano": outro.ano},
        )


def validar_intervalo(inicio, fim, rotulo="Período"):
    if not inicio or not fim:
        return "Informe data de início e data de fim."
    if fim < inicio:
        return "%s: a data fim não pode ser anterior ao início." % rotulo
    return None


def _periodos_sobrepostos(periodos):
    ordenados = sorted(periodos, key=lambda p: (p["data_inicio"], p["numero"]))
    for i, atual in enumerate(ordenados):
        for outro in ordenados[i + 1:]:
            if atual["data_inicio"] <= outro["data_fim"] and outro["data_inicio"] <= atual["data_fim"]:
                return "Períodos sobrepostos: %s e %s." % (atual["nome"], outro["nome"])
    return None


def payload_periodo(p):
    return {
        "id": p.id,
        "anoLetivoId": p.ano_letivo_id,
        "numero": p.numero,
        "nome": p.nome,
        "dataInicio": p.data_inicio.isoformat() if p.data_inicio else None,
        "dataFim": p.data_fim.isoformat() if p.data_fim else None,
    }


def payload_dia(d):
    return {
        "id": d.id,
        "anoLetivoId": d.ano_letivo_id,
        "data": d.data.isoformat() if d.data else None,
        "tipo": d.tipo,
        "descricao": d.descricao,
        "contaComoLetivo": d.tipo in TIPOS_DIA_LETIVO,
        "diaSemana": d.data.weekday() if d.data else None,
    }


def payload_ano(ano, incluir_dias=False, mes=None, ano_mes=None):
    if not ano:
        return None
    dias_payload = None
    if incluir_dias:
        q = DiaCalendario.query.filter_by(ano_letivo_id=ano.id)
        if mes and ano_mes:
            inicio = date(int(ano_mes), int(mes), 1)
            if int(mes) == 12:
                fim = date(int(ano_mes) + 1, 1, 1) - timedelta(days=1)
            else:
                fim = date(int(ano_mes), int(mes) + 1, 1) - timedelta(days=1)
            q = q.filter(DiaCalendario.data >= inicio, DiaCalendario.data <= fim)
        dias_payload = [payload_dia(d) for d in q.order_by(DiaCalendario.data).all()]
    periodos = (
        PeriodoLetivo.query.filter_by(ano_letivo_id=ano.id)
        .order_by(PeriodoLetivo.numero)
        .all()
    )
    return {
        "id": ano.id,
        "ano": ano.ano,
        "dataInicio": ano.data_inicio.isoformat() if ano.data_inicio else None,
        "dataFim": ano.data_fim.isoformat() if ano.data_fim else None,
        "diasLetivosPrevistos": ano.dias_letivos_previstos,
        "ativo": bool(ano.ativo),
        "criadoEm": ano.criado_em.isoformat() if ano.criado_em else None,
        "atualizadoEm": ano.atualizado_em.isoformat() if ano.atualizado_em else None,
        "periodos": [payload_periodo(p) for p in periodos],
        "dias": dias_payload,
    }


def atualizar_contagem_dias_letivos(ano):
    n = DiaCalendario.query.filter(
        DiaCalendario.ano_letivo_id == ano.id,
        DiaCalendario.tipo.in_(tuple(TIPOS_DIA_LETIVO)),
    ).count()
    ano.dias_letivos_previstos = n
    return n


def criar_ano_letivo(dados):
    ano_num, err = _int_opcional(dados.get("ano"), "Ano", minimo=2000)
    if err:
        return None, err
    if ano_num is None:
        return None, "Informe o ano letivo."
    if AnoLetivo.query.filter_by(ano=ano_num).first():
        return None, "Já existe um ano letivo para %s." % ano_num
    inicio = parse_data_iso(dados.get("data_inicio") or dados.get("dataInicio"))
    fim = parse_data_iso(dados.get("data_fim") or dados.get("dataFim"))
    msg = validar_intervalo(inicio, fim, "Ano letivo")
    if msg:
        return None, msg
    ativo = bool(dados.get("ativo", True))
    dias_prev, err = _int_opcional(
        dados.get("dias_letivos_previstos", dados.get("diasLetivosPrevistos")),
        "Dias letivos previstos",
        minimo=0,
    )
    if err:
        return None, err
    if ativo:
        _desativar_outros()
    ano = AnoLetivo(
        ano=ano_num,
        data_inicio=inicio,
        data_fim=fim,
        dias_letivos_previstos=dias_prev,
        ativo=ativo,
    )
    db.session.add(ano)
    db.session.flush()
    from src.auditoria import ANO_LETIVO_CRIADO, registrar_auditoria, snapshot_ano_letivo
    registrar_auditoria(
        ANO_LETIVO_CRIADO,
        "AnoLetivo",
        entidade_id=ano.id,
        dados_novos=snapshot_ano_letivo(ano),
    )
    periodos = dados.get("periodos")
    if periodos:
        msg = definir_periodos(ano, periodos, persistir=False)
        if msg:
            db.session.rollback()
            return None, msg
    if dados.get("gerar_calendario") or dados.get("gerarCalendario"):
        gerar_calendario_inicial(ano, persistir=False)
    db.session.commit()
    return ano, None


def atualizar_ano_letivo(ano, dados):
    from src.auditoria import ANO_LETIVO_ALTERADO, registrar_alteracao, snapshot_ano_letivo
    anterior = snapshot_ano_letivo(ano)
    if "ano" in dados and dados.get("ano") not in (None, ""):
        ano_num, err = _int_opcional(dados.get("ano"), "Ano", minimo=2000)
        if err:
            return err
        outro = AnoLetivo.query.filter(AnoLetivo.ano == ano_num, AnoLetivo.id != ano.id).first()
        if outro:
            return "Já existe um ano letivo para %s." % ano_num
        ano.ano = ano_num
    if "data_inicio" in dados or "dataInicio" in dados:
        inicio = parse_data_iso(dados.get("data_inicio", dados.get("dataInicio")))
        if not inicio:
            return "Data de início inválida."
        ano.data_inicio = inicio
    if "data_fim" in dados or "dataFim" in dados:
        fim = parse_data_iso(dados.get("data_fim", dados.get("dataFim")))
        if not fim:
            return "Data de fim inválida."
        ano.data_fim = fim
    msg = validar_intervalo(ano.data_inicio, ano.data_fim, "Ano letivo")
    if msg:
        return msg
    if "dias_letivos_previstos" in dados or "diasLetivosPrevistos" in dados:
        dias_prev, err = _int_opcional(
            dados.get("dias_letivos_previstos", dados.get("diasLetivosPrevistos")),
            "Dias letivos previstos",
            minimo=0,
        )
        if err:
            return err
        ano.dias_letivos_previstos = dias_prev
    if "ativo" in dados:
        ativo = bool(dados.get("ativo"))
        if ativo:
            _desativar_outros(exceto_id=ano.id)
        ano.ativo = ativo
    periodos = dados.get("periodos")
    if periodos is not None:
        msg = definir_periodos(ano, periodos, persistir=False)
        if msg:
            return msg
    registrar_alteracao(
        ANO_LETIVO_ALTERADO,
        "AnoLetivo",
        entidade_id=ano.id,
        anterior=anterior,
        novo=snapshot_ano_letivo(ano),
        identidade={"ano_letivo_id": ano.id, "ano": ano.ano},
    )
    db.session.commit()
    return None


def definir_periodos(ano, lista, persistir=True):
    if not isinstance(lista, list) or not lista:
        return "Informe os períodos/bimestres."
    normalizados = []
    for item in lista:
        if not isinstance(item, dict):
            return "Cada período deve ser um objeto JSON."
        numero, err = _int_opcional(item.get("numero"), "Número do período", minimo=1)
        if err or numero is None:
            return err or "Informe o número do período."
        if numero > 4:
            return "O número do período deve ser de 1 a 4."
        inicio = parse_data_iso(item.get("data_inicio") or item.get("dataInicio"))
        fim = parse_data_iso(item.get("data_fim") or item.get("dataFim"))
        msg = validar_intervalo(inicio, fim, item.get("nome") or ("Período %s" % numero))
        if msg:
            return msg
        if inicio < ano.data_inicio or fim > ano.data_fim:
            return "O período %s está fora do ano letivo." % numero
        nome = (item.get("nome") or NOMES_PERIODO_PADRAO.get(numero) or ("%sº Bimestre" % numero)).strip()
        normalizados.append({
            "numero": numero,
            "nome": nome,
            "data_inicio": inicio,
            "data_fim": fim,
        })
    numeros = [p["numero"] for p in normalizados]
    if len(numeros) != len(set(numeros)):
        return "Há períodos com o mesmo número."
    msg = _periodos_sobrepostos(normalizados)
    if msg:
        return msg
    existentes = {p.numero: p for p in PeriodoLetivo.query.filter_by(ano_letivo_id=ano.id).all()}
    usados = set()
    from src.auditoria import (
        PERIODO_LETIVO_ALTERADO,
        PERIODO_LETIVO_CRIADO,
        PERIODO_LETIVO_REMOVIDO,
        registrar_alteracao,
        registrar_auditoria,
        snapshot_periodo,
    )
    for item in normalizados:
        usados.add(item["numero"])
        atual = existentes.get(item["numero"])
        if atual:
            anterior = snapshot_periodo(atual)
            atual.nome = item["nome"]
            atual.data_inicio = item["data_inicio"]
            atual.data_fim = item["data_fim"]
            registrar_alteracao(
                PERIODO_LETIVO_ALTERADO,
                "PeriodoLetivo",
                entidade_id=atual.id,
                anterior=anterior,
                novo=snapshot_periodo(atual),
                identidade={
                    "periodo_id": atual.id,
                    "ano_letivo_id": ano.id,
                    "numero": atual.numero,
                },
            )
        else:
            novo_periodo = PeriodoLetivo(
                ano_letivo_id=ano.id,
                numero=item["numero"],
                nome=item["nome"],
                data_inicio=item["data_inicio"],
                data_fim=item["data_fim"],
            )
            db.session.add(novo_periodo)
            db.session.flush()
            registrar_auditoria(
                PERIODO_LETIVO_CRIADO,
                "PeriodoLetivo",
                entidade_id=novo_periodo.id,
                dados_novos=snapshot_periodo(novo_periodo),
            )
    for numero, atual in existentes.items():
        if numero not in usados:
            registrar_auditoria(
                PERIODO_LETIVO_REMOVIDO,
                "PeriodoLetivo",
                entidade_id=atual.id,
                dados_anteriores=snapshot_periodo(atual),
            )
            db.session.delete(atual)
    if persistir:
        db.session.commit()
    else:
        db.session.flush()
    return None


def gerar_calendario_inicial(ano, persistir=True):
    """Gera dias úteis (seg–sex) como LETIVO. Sábado/domingo entram como NAO_LETIVO.

    Não sobrescreve dias já configurados. Sábado letivo ou reposição podem ser
    marcados depois pela Secretaria.
    """
    existentes = {
        d.data for d in DiaCalendario.query.filter_by(ano_letivo_id=ano.id).all()
    }
    atual = ano.data_inicio
    criados = 0
    while atual <= ano.data_fim:
        if atual not in existentes:
            tipo = "LETIVO" if atual.weekday() < 5 else "NAO_LETIVO"
            db.session.add(DiaCalendario(
                ano_letivo_id=ano.id,
                data=atual,
                tipo=tipo,
            ))
            criados += 1
        atual += timedelta(days=1)
    db.session.flush()
    atualizar_contagem_dias_letivos(ano)
    from src.auditoria import auditar_calendario_lote
    auditar_calendario_lote(ano, quantidade_criados=criados, geracao=True)
    if persistir:
        db.session.commit()
    return criados


def definir_dia(ano, data, tipo, descricao=None, persistir=True):
    tipo = str(tipo or "").strip().upper()
    if tipo not in TIPOS_DIA_CALENDARIO:
        return None, "Tipo de dia inválido. Use LETIVO, FERIADO, RECESSO, NAO_LETIVO ou REPOSICAO."
    if data < ano.data_inicio or data > ano.data_fim:
        return None, "A data está fora do ano letivo."
    dia = DiaCalendario.query.filter_by(ano_letivo_id=ano.id, data=data).first()
    if dia:
        dia.tipo = tipo
        if descricao is not None:
            dia.descricao = (str(descricao).strip() or None)
    else:
        dia = DiaCalendario(
            ano_letivo_id=ano.id,
            data=data,
            tipo=tipo,
            descricao=(str(descricao).strip() or None) if descricao is not None else None,
        )
        db.session.add(dia)
    db.session.flush()
    atualizar_contagem_dias_letivos(ano)
    if persistir:
        db.session.commit()
    return dia, None


def periodo_da_data(data, ano=None):
    """Retorna o período que contém a data, ou estado controlado se não houver."""
    if not data:
        return {"periodo": None, "motivo": "data_invalida", "anoLetivoId": None}
    if ano is None:
        ano = AnoLetivo.query.filter(
            AnoLetivo.data_inicio <= data,
            AnoLetivo.data_fim >= data,
        ).first()
        if not ano:
            candidato = AnoLetivo.query.filter_by(ano=data.year).first() or ano_letivo_ativo()
            ano = candidato
    if not ano:
        return {"periodo": None, "motivo": "sem_ano_letivo", "anoLetivoId": None}
    p = PeriodoLetivo.query.filter(
        PeriodoLetivo.ano_letivo_id == ano.id,
        PeriodoLetivo.data_inicio <= data,
        PeriodoLetivo.data_fim >= data,
    ).first()
    if not p:
        return {
            "periodo": None,
            "motivo": "fora_de_periodo",
            "anoLetivoId": ano.id,
            "ano": ano.ano,
        }
    return {
        "periodo": payload_periodo(p),
        "motivo": None,
        "anoLetivoId": ano.id,
        "ano": ano.ano,
    }


def indice_dias(ano):
    if not ano:
        return {}
    return {
        d.data: d.tipo
        for d in DiaCalendario.query.filter_by(ano_letivo_id=ano.id).all()
    }


def indice_grades(alocacao_ids):
    mapa = {}
    if not alocacao_ids:
        return mapa
    for h in HorarioAlocacao.query.filter(HorarioAlocacao.alocacao_id.in_(alocacao_ids)).all():
        mapa.setdefault(h.alocacao_id, {})[h.dia_semana] = h.quantidade_aulas
    return mapa


def dia_conta_como_letivo(tipo):
    return tipo in TIPOS_DIA_LETIVO


def dias_letivos_ate(ano, data_ref, indice=None, data_inicio=None):
    """Datas letivas (LETIVO/REPOSICAO) de data_inicio até data_ref, inclusive."""
    if not ano or not data_ref:
        return None
    indice = indice if indice is not None else indice_dias(ano)
    if not indice:
        return None
    inicio = data_inicio or ano.data_inicio
    fim = min(data_ref, ano.data_fim)
    if fim < inicio:
        return []
    return sorted(
        d for d, tipo in indice.items()
        if dia_conta_como_letivo(tipo) and inicio <= d <= fim
    )


def aulas_previstas_alocacao(aloc, data_ref=None, ano=None, indice=None, grade=None):
    """Soma quantidade_aulas da grade nos dias LETIVO/REPOSICAO até data_ref.

    None = sem calendário ou sem grade (não há previsão). 0 = grade existe, mas
    nenhum dia letivo correspondente ocorreu até a data.
    """
    if not aloc:
        return None
    turma = aloc.turma
    ano = ano or ano_letivo_da_turma(turma)
    if not ano:
        return None
    data_ref = data_ref or date.today()
    grade = grade if grade is not None else {
        h.dia_semana: h.quantidade_aulas for h in (aloc.horarios or [])
    }
    if not grade:
        return None
    letivos = dias_letivos_ate(ano, data_ref, indice=indice)
    if letivos is None:
        return None
    total = 0
    for dia in letivos:
        qtd = grade.get(dia.weekday())
        if qtd:
            total += int(qtd)
    return total


def _aulas_por_status(aloc):
    q = RegistroAula.query.filter_by(alocacao_id=aloc.id)
    if not aloc.id:
        q = RegistroAula.query.filter_by(professor_id=aloc.professor_id, turma_id=aloc.turma_id)
        if aloc.disciplina_id in (None, 0):
            q = q.filter(RegistroAula.disciplina_id.is_(None))
        else:
            q = q.filter_by(disciplina_id=aloc.disciplina_id)
    aulas = q.all()
    lancadas = sum(a.quantidade_aulas or 0 for a in aulas)
    validadas = sum(a.quantidade_aulas or 0 for a in aulas if (a.status or "") == STATUS_VALIDADA)
    invalidadas = sum(a.quantidade_aulas or 0 for a in aulas if (a.status or "") == STATUS_INVALIDADA)
    pendentes = sum(a.quantidade_aulas or 0 for a in aulas if (a.status or "") == STATUS_PENDENTE)
    return lancadas, validadas, invalidadas, pendentes


def sugerir_carga_anual(aloc, ano=None, indice=None, grade=None):
    """Sugestão em aulas no ano. Não grava sozinha."""
    turma = aloc.turma if aloc else None
    ano = ano or ano_letivo_da_turma(turma)
    grade = grade if grade is not None else {
        h.dia_semana: h.quantidade_aulas for h in (aloc.horarios or [])
    } if aloc else {}
    if aloc and ano and grade:
        n = aulas_previstas_alocacao(aloc, ano.data_fim, ano=ano, indice=indice, grade=grade)
        if n:
            return n
    aulas_sem = aloc.aulas_semanais if aloc else None
    if aulas_sem and ano and ano.dias_letivos_previstos:
        return int(round(aulas_sem * ano.dias_letivos_previstos / 5.0))
    return None


def payload_grade(aloc):
    return [
        {"id": h.id, "diaSemana": h.dia_semana, "quantidadeAulas": h.quantidade_aulas}
        for h in sorted(aloc.horarios or [], key=lambda x: x.dia_semana)
    ]


def cumprimento_alocacao(aloc, data_ref=None, ano=None, indice=None, grade=None):
    turma = aloc.turma
    data_ref = data_ref or date.today()
    ano = ano or ano_letivo_da_turma(turma)
    grade = grade if grade is not None else {
        h.dia_semana: h.quantidade_aulas for h in (aloc.horarios or [])
    }
    lancadas, validadas, invalidadas, pendentes = _aulas_por_status(aloc)
    previstas = aloc.carga_horaria_prevista
    previstas_ate = aulas_previstas_alocacao(
        aloc, data_ref, ano=ano, indice=indice, grade=grade,
    )
    progresso_anual = percentual(validadas, previstas) if previstas else None
    cumprimento_hoje = percentual(validadas, previstas_ate)
    return {
        "alocacaoId": aloc.id,
        "professorId": aloc.professor_id,
        "professorNome": aloc.professor.nome if aloc.professor else None,
        "turmaId": aloc.turma_id,
        "turmaNome": turma.nome if turma else None,
        "etapaEnsino": turma.etapa_ensino if turma else None,
        "disciplinaId": aloc.disciplina_id,
        "disciplinaNome": (
            aloc.disciplina.nome if aloc.disciplina
            else ("Educação Infantil" if turma and turma.etapa_ensino == "infantil" else None)
        ),
        "cargaPrevista": previstas,
        "cargaHorariaNaoConfigurada": previstas is None,
        "aulasSemanais": aloc.aulas_semanais,
        "duracaoAulaMinutos": aloc.duracao_aula_minutos,
        "cargaSugerida": sugerir_carga_anual(aloc, ano=ano, indice=indice, grade=grade),
        "grade": payload_grade(aloc),
        "aulasLancadas": lancadas,
        "aulasValidadas": validadas,
        "aulasInvalidadas": invalidadas,
        "aulasPendentes": pendentes,
        "percentualCumprido": progresso_anual,
        "progressoAnual": progresso_anual,
        "aulasPrevistasAteHoje": previstas_ate,
        "percentualCumprimentoAteHoje": cumprimento_hoje,
        "semPrevisaoAteData": not previstas_ate,
    }


def aplicar_grade(aloc, itens, persistir=True):
    if itens is None:
        return None
    if not isinstance(itens, list):
        return "A grade semanal deve ser uma lista."
    normalizados = {}
    for item in itens:
        if not isinstance(item, dict):
            return "Cada item da grade deve ser um objeto JSON."
        dia, err = _int_opcional(item.get("dia_semana", item.get("diaSemana")), "Dia da semana", minimo=0)
        if err:
            return err
        if dia is None or dia > 6:
            return "Dia da semana inválido (0=segunda … 6=domingo)."
        qtd, err = _int_opcional(
            item.get("quantidade_aulas", item.get("quantidadeAulas")),
            "Quantidade de aulas",
            minimo=0,
        )
        if err:
            return err
        if not qtd:
            continue
        normalizados[dia] = qtd
    existentes = {h.dia_semana: h for h in list(aloc.horarios or [])}
    for dia, qtd in normalizados.items():
        atual = existentes.pop(dia, None)
        if atual:
            atual.quantidade_aulas = qtd
        else:
            db.session.add(HorarioAlocacao(
                alocacao_id=aloc.id,
                dia_semana=dia,
                quantidade_aulas=qtd,
            ))
    for restante in existentes.values():
        db.session.delete(restante)
    if aloc.aulas_semanais is None and normalizados:
        aloc.aulas_semanais = sum(normalizados.values())
    if persistir:
        db.session.commit()
    else:
        db.session.flush()
    return None


def aplicar_campos_carga(aloc, dados):
    if "carga_horaria_prevista" in dados or "cargaHorariaPrevista" in dados:
        bruto = dados.get("carga_horaria_prevista", dados.get("cargaHorariaPrevista"))
        if bruto is None or str(bruto).strip() == "":
            aloc.carga_horaria_prevista = None
        else:
            carga, err = _int_opcional(bruto, "Carga horária prevista", minimo=1)
            if err:
                return err
            aloc.carga_horaria_prevista = carga
    if "aulas_semanais" in dados or "aulasSemanais" in dados:
        aulas, err = _int_opcional(
            dados.get("aulas_semanais", dados.get("aulasSemanais")),
            "Aulas semanais",
            minimo=1,
        )
        if err:
            return err
        aloc.aulas_semanais = aulas
    if "duracao_aula_minutos" in dados or "duracaoAulaMinutos" in dados:
        dur, err = _int_opcional(
            dados.get("duracao_aula_minutos", dados.get("duracaoAulaMinutos")),
            "Duração da aula",
            minimo=1,
        )
        if err:
            return err
        aloc.duracao_aula_minutos = dur
    grade = dados.get("grade") if "grade" in dados else dados.get("horarios")
    if grade is not None:
        msg = aplicar_grade(aloc, grade, persistir=False)
        if msg:
            return msg
    return None


def aplicar_jornada_turma(turma, dados):
    if "tipo_jornada" in dados or "tipoJornada" in dados:
        bruto = dados.get("tipo_jornada", dados.get("tipoJornada"))
        if bruto is None or str(bruto).strip() == "":
            turma.tipo_jornada = None
        else:
            tipo = str(bruto).strip().upper()
            if tipo not in TIPOS_JORNADA:
                return "Jornada inválida. Use PARCIAL ou INTEGRAL."
            turma.tipo_jornada = tipo
    if "carga_horaria_diaria" in dados or "cargaHorariaDiaria" in dados:
        val, err = _float_opcional(
            dados.get("carga_horaria_diaria", dados.get("cargaHorariaDiaria")),
            "Carga horária diária",
        )
        if err:
            return err
        turma.carga_horaria_diaria = val
    if "carga_horaria_anual" in dados or "cargaHorariaAnual" in dados:
        val, err = _float_opcional(
            dados.get("carga_horaria_anual", dados.get("cargaHorariaAnual")),
            "Carga horária anual",
        )
        if err:
            return err
        turma.carga_horaria_anual = val
    return None


def cumprimento_turma_ei(turma, data_ref=None, ano=None, indice=None):
    data_ref = data_ref or date.today()
    ano = ano or ano_letivo_da_turma(turma)
    letivos = dias_letivos_ate(ano, data_ref, indice=indice) if ano else None
    dias_ate = len(letivos) if letivos is not None else None
    q = RegistroAula.query.filter_by(turma_id=turma.id, status=STATUS_VALIDADA)
    datas_validadas = {a.data for a in q.all() if a.data and a.data <= data_ref}
    if ano:
        datas_validadas = {d for d in datas_validadas if d >= ano.data_inicio}
    dias_validados = len(datas_validadas)
    carga_diaria = turma.carga_horaria_diaria
    carga_ate = None
    if dias_ate is not None and carga_diaria:
        carga_ate = round(dias_ate * float(carga_diaria), 1)
    professores = sorted({
        a.professor.nome for a in (turma.alocacoes or []) if a.professor
    })
    return {
        "turmaId": turma.id,
        "turmaNome": turma.nome,
        "escolaId": turma.escola_id,
        "etapaEnsino": turma.etapa_ensino,
        "tipoJornada": turma.tipo_jornada,
        "cargaHorariaDiaria": turma.carga_horaria_diaria,
        "cargaHorariaAnual": turma.carga_horaria_anual,
        "professores": professores,
        "diasLetivosPrevistos": ano.dias_letivos_previstos if ano else None,
        "diasLetivosAteHoje": dias_ate,
        "diasComRegistroValidado": dias_validados,
        "percentualRegistros": percentual(dias_validados, dias_ate),
        "cargaPrevistaAteHoje": carga_ate,
        "semPrevisaoAteData": dias_ate is None,
    }
