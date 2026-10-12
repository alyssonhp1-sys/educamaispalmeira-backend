"""Consulta administrativa somente leitura do AuditLog.

Não registra eventos, não altera linhas e não aceita ordenação arbitrária.
"""
from __future__ import annotations

import json
from datetime import datetime, time

from sqlalchemy.orm import joinedload

from src.auditoria import sanitizar_dados
from src.models import AuditLog, Escola

PER_PAGE_PADRAO = 25
PER_PAGE_MAX = 100

ENTIDADES_CONHECIDAS = (
    "Alocacao",
    "Aluno",
    "AnoLetivo",
    "ConfiguracaoAcademica",
    "DiaCalendario",
    "Disciplina",
    "Escola",
    "Matricula",
    "PeriodoLetivo",
    "RegistroAula",
    "RegistroFalta",
    "RegistroFrequencia",
    "RegistroNota",
    "RelatorioDescritivo",
    "TrajetoriaEscolarLegada",
    "Turma",
    "Usuario",
)


def acoes_conhecidas():
    """Constantes de ação do módulo central. Sem DISTINCT na tabela."""
    import src.auditoria as modulo

    valores = []
    for nome in dir(modulo):
        if not nome.isupper() or nome.startswith("_"):
            continue
        valor = getattr(modulo, nome)
        if isinstance(valor, str) and nome == valor:
            valores.append(valor)
    return tuple(sorted(valores))


def rotulo_acao(codigo):
    texto = (codigo or "").replace("_", " ").strip().lower()
    if not texto:
        return codigo or ""
    return texto[0].upper() + texto[1:]


def sanitizar_saida(dados):
    """Sanitização defensiva na leitura. AuditLog antigo não é confiável."""
    if dados is None:
        return None
    if isinstance(dados, str):
        bruto = dados.strip()
        if not bruto:
            return None
        try:
            dados = json.loads(bruto)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    try:
        return sanitizar_dados(dados)
    except TypeError:
        return None


def _int_opcional(bruto, nome, minimo=None):
    if bruto is None or str(bruto).strip() == "":
        return None, None
    try:
        valor = int(str(bruto).strip())
    except (TypeError, ValueError):
        return None, "Filtro inválido: %s." % nome
    if minimo is not None and valor < minimo:
        return None, "Filtro inválido: %s." % nome
    return valor, None


def _data_filtro(bruto, nome):
    if bruto is None or str(bruto).strip() == "":
        return None, None
    texto = str(bruto).strip()
    try:
        if len(texto) == 10:
            return datetime.strptime(texto, "%Y-%m-%d").date(), None
        dt = datetime.fromisoformat(texto.replace("Z", ""))
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt, None
    except (TypeError, ValueError):
        return None, "Data inválida: %s." % nome


def _como_datetime(valor, fim_do_dia=False):
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor
    if fim_do_dia:
        return datetime.combine(valor, time.max)
    return datetime.combine(valor, time.min)


def _paginacao(args):
    page, erro = _int_opcional(args.get("page"), "page", minimo=1)
    if erro:
        return None, None, erro
    if page is None:
        page = 1
    per_page, erro = _int_opcional(args.get("per_page"), "per_page", minimo=1)
    if erro:
        return None, None, erro
    if per_page is None:
        per_page = PER_PAGE_PADRAO
    if per_page > PER_PAGE_MAX:
        per_page = PER_PAGE_MAX
    return page, per_page, None


def nome_usuario_log(log):
    uid = log.usuario_id
    if uid is None:
        return None
    usuario = log.usuario
    if usuario is None:
        return "Usuário removido (#%s)" % uid
    return usuario.nome or usuario.login


def papel_usuario_log(log):
    usuario = log.usuario
    if log.usuario_id is None or usuario is None:
        return None
    return usuario.papel


def nome_escola_log(log):
    eid = log.escola_id
    escola = log.escola
    if escola is not None:
        return escola.nome
    if eid is None:
        return None
    return "Escola removida (#%s)" % eid


def serializar_log(log, incluir_ip):
    item = {
        "id": log.id,
        "data_hora": log.data_hora.isoformat() if log.data_hora else None,
        "acao": log.acao,
        "acao_rotulo": rotulo_acao(log.acao),
        "entidade": log.entidade,
        "entidade_id": log.entidade_id,
        "usuario_id": log.usuario_id,
        "usuario_nome": nome_usuario_log(log),
        "usuario_papel": papel_usuario_log(log),
        "escola_id": log.escola_id,
        "escola_nome": nome_escola_log(log),
        "request_id": log.request_id,
        "dados_anteriores": sanitizar_saida(log.dados_anteriores),
        "dados_novos": sanitizar_saida(log.dados_novos),
    }
    if incluir_ip:
        item["ip"] = log.ip
    return item


def aplicar_escopo(query, user, escola_id_filtro):
    """ACL de rede/escola. Tentativa explícita de outra escola: 403.

    Gestor nunca vê escola_id NULL (eventos de rede).
    """
    from src import authz

    if authz.municipal(user):
        if escola_id_filtro is not None:
            query = query.filter(AuditLog.escola_id == escola_id_filtro)
        return query, None
    if authz.gestor(user) and user.escola_id:
        if escola_id_filtro is not None and int(escola_id_filtro) != int(user.escola_id):
            return None, "escola_alheia"
        query = query.filter(AuditLog.escola_id == user.escola_id)
        return query, None
    return None, "negado"


def aplicar_filtros(query, args):
    acao = (args.get("acao") or "").strip()
    if acao:
        query = query.filter(AuditLog.acao == acao)

    entidade = (args.get("entidade") or "").strip()
    if entidade:
        query = query.filter(AuditLog.entidade == entidade)

    entidade_id, erro = _int_opcional(args.get("entidade_id"), "entidade_id")
    if erro:
        return None, erro
    if entidade_id is not None:
        query = query.filter(AuditLog.entidade_id == entidade_id)

    usuario_id, erro = _int_opcional(args.get("usuario_id"), "usuario_id", minimo=1)
    if erro:
        return None, erro
    if usuario_id is not None:
        query = query.filter(AuditLog.usuario_id == usuario_id)

    request_id = (args.get("request_id") or "").strip()
    if request_id:
        query = query.filter(AuditLog.request_id == request_id)

    data_inicial, erro = _data_filtro(args.get("data_inicial"), "data inicial")
    if erro:
        return None, erro
    data_final, erro = _data_filtro(args.get("data_final"), "data final")
    if erro:
        return None, erro

    inicio = _como_datetime(data_inicial, fim_do_dia=False)
    fim = _como_datetime(data_final, fim_do_dia=True)
    if inicio is not None and fim is not None and inicio > fim:
        return None, "intervalo"
    if inicio is not None:
        query = query.filter(AuditLog.data_hora >= inicio)
    if fim is not None:
        query = query.filter(AuditLog.data_hora <= fim)
    return query, None


def listar_logs(user, args):
    """Monta listagem paginada. Retorna (payload, codigo_erro).

    codigo_erro: None | 400 | 403 com mensagem em payload str quando erro.
    """
    from src import authz

    if not authz.pode_consultar_auditoria(user):
        return "Acesso negado", 403

    page, per_page, erro = _paginacao(args)
    if erro:
        return erro, 400

    escola_id, erro = _int_opcional(args.get("escola_id"), "escola_id", minimo=1)
    if erro:
        return erro, 400

    query = AuditLog.query
    query, recusa = aplicar_escopo(query, user, escola_id)
    if recusa == "escola_alheia":
        return "Acesso negado", 403
    if recusa == "negado" or query is None:
        return "Acesso negado", 403

    query, erro = aplicar_filtros(query, args)
    if erro == "intervalo":
        return "Intervalo de datas inválido.", 400
    if erro:
        return erro, 400

    total = query.count()
    pages = (total + per_page - 1) // per_page if total else 0
    itens = (
        query.options(
            joinedload(AuditLog.usuario),
            joinedload(AuditLog.escola),
        )
        .order_by(AuditLog.data_hora.desc(), AuditLog.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    incluir_ip = authz.pode_ver_ip_auditoria(user)
    return {
        "items": [serializar_log(log, incluir_ip) for log in itens],
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
    }, None


def opcoes_filtros(user):
    from src import authz

    if not authz.pode_consultar_auditoria(user):
        return "Acesso negado", 403
    payload = {
        "acoes": [{"valor": a, "rotulo": rotulo_acao(a)} for a in acoes_conhecidas()],
        "entidades": list(ENTIDADES_CONHECIDAS),
        "escopo": "rede" if authz.municipal(user) else "escola",
        "exibe_ip": authz.pode_ver_ip_auditoria(user),
    }
    if authz.municipal(user):
        escolas = Escola.query.order_by(Escola.nome).all()
        payload["escolas"] = [{"id": e.id, "nome": e.nome} for e in escolas]
    return payload, None
