"""Infraestrutura central de AuditLog (append-only).

O chamador informa explicitamente acao, entidade e os recortes JSON
relevantes. Operações acadêmicas críticas registram via registrar_auditoria
na mesma sessão da alteração principal.

Não faz commit: o registro entra na sessão corrente para participar
da mesma transação da alteração principal.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from flask import has_request_context, request
from flask_login import current_user

from src import db
from src.http import garantir_request_id, ip_remoto


# Correspondência por chave inteira ou por componente (split em "_").
# "secretaria" NÃO é segredo: o componente é a palavra inteira "secret".
CHAVES_SENSIVEIS = frozenset({
    "senha",
    "password",
    "password_hash",
    "senha_hash",
    "token",
    "secret",
    "secret_key",
    "cookie",
    "session",
    "authorization",
    "access_token",
    "refresh_token",
    "client_secret",
    "csrf_token",
})

COMPONENTES_SENSIVEIS = frozenset({
    "senha",
    "password",
    "token",
    "secret",
    "cookie",
    "session",
    "authorization",
})

# Flags operacionais permitidos (não são o valor da senha).
CHAVES_PERMITIDAS = frozenset({
    "senha_temporaria_gerada",
})

NOTA_CRIADA = "NOTA_CRIADA"
NOTA_ALTERADA = "NOTA_ALTERADA"
NOTA_REMOVIDA = "NOTA_REMOVIDA"
FALTA_CRIADA = "FALTA_CRIADA"
FALTA_ALTERADA = "FALTA_ALTERADA"
FALTA_REMOVIDA = "FALTA_REMOVIDA"
FREQUENCIA_CRIADA = "FREQUENCIA_CRIADA"
FREQUENCIA_ALTERADA = "FREQUENCIA_ALTERADA"
REGISTRO_AULA_CRIADO = "REGISTRO_AULA_CRIADO"
REGISTRO_AULA_VALIDADO = "REGISTRO_AULA_VALIDADO"
REGISTRO_AULA_INVALIDADO = "REGISTRO_AULA_INVALIDADO"
FALTA_DOCENTE_REGISTRADA = "FALTA_DOCENTE_REGISTRADA"
FALTA_DOCENTE_CANCELADA = "FALTA_DOCENTE_CANCELADA"
MATRICULA_CRIADA = "MATRICULA_CRIADA"
ALUNO_ENTURMADO = "ALUNO_ENTURMADO"
ALUNO_REMATRICULADO = "ALUNO_REMATRICULADO"
ALUNO_INATIVADO = "ALUNO_INATIVADO"
RELATORIO_DESCRITIVO_CRIADO = "RELATORIO_DESCRITIVO_CRIADO"
RELATORIO_DESCRITIVO_ALTERADO = "RELATORIO_DESCRITIVO_ALTERADO"
RELATORIO_DESCRITIVO_REMOVIDO = "RELATORIO_DESCRITIVO_REMOVIDO"
TURMA_ENCERRADA = "TURMA_ENCERRADA"

USUARIO_CRIADO = "USUARIO_CRIADO"
USUARIO_ALTERADO = "USUARIO_ALTERADO"
USUARIO_REMOVIDO = "USUARIO_REMOVIDO"
SENHA_RESETADA = "SENHA_RESETADA"
LOGIN_BLOQUEADO = "LOGIN_BLOQUEADO"
CONFIGURACAO_ACADEMICA_ALTERADA = "CONFIGURACAO_ACADEMICA_ALTERADA"
ANO_LETIVO_CRIADO = "ANO_LETIVO_CRIADO"
ANO_LETIVO_ALTERADO = "ANO_LETIVO_ALTERADO"
PERIODO_LETIVO_CRIADO = "PERIODO_LETIVO_CRIADO"
PERIODO_LETIVO_ALTERADO = "PERIODO_LETIVO_ALTERADO"
PERIODO_LETIVO_REMOVIDO = "PERIODO_LETIVO_REMOVIDO"
DIA_CALENDARIO_ALTERADO = "DIA_CALENDARIO_ALTERADO"
CALENDARIO_GERADO = "CALENDARIO_GERADO"
CALENDARIO_ATUALIZADO = "CALENDARIO_ATUALIZADO"
ESCOLA_CRIADA = "ESCOLA_CRIADA"
ESCOLA_ALTERADA = "ESCOLA_ALTERADA"
ESCOLA_REMOVIDA = "ESCOLA_REMOVIDA"
TURMA_CRIADA = "TURMA_CRIADA"
TURMA_ALTERADA = "TURMA_ALTERADA"
TURMA_REMOVIDA = "TURMA_REMOVIDA"
DISCIPLINA_CRIADA = "DISCIPLINA_CRIADA"
DISCIPLINA_REMOVIDA = "DISCIPLINA_REMOVIDA"
ALOCACAO_CRIADA = "ALOCACAO_CRIADA"
ALOCACAO_ALTERADA = "ALOCACAO_ALTERADA"
DOCUMENTO_HISTORICO_EMITIDO = "DOCUMENTO_HISTORICO_EMITIDO"
DOCUMENTO_HISTORICO_TRANSFERENCIA_EMITIDO = "DOCUMENTO_HISTORICO_TRANSFERENCIA_EMITIDO"
DOCUMENTO_BOLETIM_EMITIDO = "DOCUMENTO_BOLETIM_EMITIDO"
DOCUMENTO_DECLARACAO_EMITIDA = "DOCUMENTO_DECLARACAO_EMITIDA"
DOCUMENTO_PARECER_EMITIDO = "DOCUMENTO_PARECER_EMITIDO"
DOCUMENTO_MATRICULA_EMITIDA = "DOCUMENTO_MATRICULA_EMITIDA"
DOCUMENTO_RENDIMENTO_EMITIDO = "DOCUMENTO_RENDIMENTO_EMITIDO"
TRAJETORIA_LEGADA_CRIADA = "TRAJETORIA_LEGADA_CRIADA"
TRAJETORIA_LEGADA_ALTERADA = "TRAJETORIA_LEGADA_ALTERADA"
TRAJETORIA_LEGADA_REMOVIDA = "TRAJETORIA_LEGADA_REMOVIDA"
COMPLEMENTO_TRANSFERENCIA_CRIADO = "COMPLEMENTO_TRANSFERENCIA_CRIADO"
COMPLEMENTO_TRANSFERENCIA_ALTERADO = "COMPLEMENTO_TRANSFERENCIA_ALTERADO"
COMPLEMENTO_TRANSFERENCIA_REMOVIDO = "COMPLEMENTO_TRANSFERENCIA_REMOVIDO"

# Código da célula do boletim (não é o bimestre 1–4 isolado).
CAMPOS_CELULA_NOTA = {
    11: "n1",
    12: "n2",
    13: "rp1",
    21: "n3",
    22: "n4",
    23: "rp2",
    31: "n5",
    32: "n6",
    33: "rp3",
    41: "n7",
    42: "n8",
    43: "rp4",
    51: "rs1",
    52: "rs2",
    99: "pf",
}

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")
_TIPOS_JSON_SIMPLES = (str, int, float, bool, type(None))


def _normalizar_chave(chave):
    texto = str(chave).strip().replace("-", "_")
    texto = _CAMEL.sub(r"\1_\2", texto)
    return texto.casefold()


def _chave_sensivel(chave):
    """True só para chaves realmente sensíveis.

    Usa a chave normalizada inteira e cada componente separado por "_".
    Não usa substring: "secretaria" permanece; "client_secret" some.
    """
    normalizada = _normalizar_chave(chave)
    if normalizada in CHAVES_PERMITIDAS:
        return False
    if normalizada in CHAVES_SENSIVEIS:
        return True
    for parte in normalizada.split("_"):
        if parte in COMPONENTES_SENSIVEIS:
            return True
    return False


def sanitizar_dados(dados):
    """Remove chaves sensíveis (case-insensitive) de dicts aninhados.

    Não serializa objetos ORM nem outros tipos não-JSON. O chamador
    deve passar apenas o recorte relevante (dict/list/valores simples).
    Preserva chaves legítimas como "secretaria" (não confundir com secret).
    """
    if dados is None:
        return None
    if isinstance(dados, dict):
        out = {}
        for chave, valor in dados.items():
            if _chave_sensivel(chave):
                continue
            out[chave] = sanitizar_dados(valor)
        return out
    if isinstance(dados, (list, tuple)):
        return [sanitizar_dados(item) for item in dados]
    if isinstance(dados, _TIPOS_JSON_SIMPLES):
        return dados
    raise TypeError(
        "Auditoria aceita dict, list ou valores JSON simples; "
        "não registre objetos inteiros automaticamente."
    )


def _usuario_autenticado_id():
    if not has_request_context():
        return None
    if not getattr(current_user, "is_authenticated", False):
        return None
    uid = getattr(current_user, "id", None)
    if uid is None:
        return None
    return int(uid)


def _request_id_atual():
    if not has_request_context():
        return None
    return garantir_request_id()


def _validar_json(payload):
    if payload is None:
        return None
    json.dumps(payload)
    return payload


def campo_celula_nota(bimestre):
    try:
        codigo = int(bimestre)
    except (TypeError, ValueError):
        return None
    return CAMPOS_CELULA_NOTA.get(codigo)


def valores_iguais(a, b):
    """Compara valores persistidos (nota/falta) sem ruído de tipo."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return a == b


def metadados_texto(texto):
    """Metadados de texto sensível: existência, tamanho e hash. Sem o original."""
    if texto is None:
        return {"havia_conteudo": False, "tamanho_conteudo": 0, "hash_sha256": None}
    raw = str(texto)
    if raw == "":
        return {"havia_conteudo": False, "tamanho_conteudo": 0, "hash_sha256": None}
    return {
        "havia_conteudo": True,
        "tamanho_conteudo": len(raw),
        "hash_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    }


def escola_id_da_matricula(matricula):
    if matricula is None:
        return None
    turma = getattr(matricula, "turma", None)
    if turma is not None and getattr(turma, "escola_id", None) is not None:
        return turma.escola_id
    aluno = getattr(matricula, "aluno", None)
    if aluno is not None:
        return getattr(aluno, "escola_id", None)
    return None


def iso_data(valor):
    if valor is None:
        return None
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return str(valor)


def _valores_equivalentes(a, b):
    """Igualdade de recortes administrativos (inclui int/float e listas pequenas)."""
    if a is b:
        return True
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) is bool(b) and a == b
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return False
        return all(_valores_equivalentes(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return all(_valores_equivalentes(a[k], b[k]) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return a == b


def diff_campos(anterior, novo):
    """Retorna só chaves que mudaram. (None, None) = no-op."""
    anterior = anterior or {}
    novo = novo or {}
    ant_out, novo_out = {}, {}
    for chave in set(anterior) | set(novo):
        a = anterior.get(chave)
        b = novo.get(chave)
        if not _valores_equivalentes(a, b):
            ant_out[chave] = a
            novo_out[chave] = b
    if not ant_out and not novo_out:
        return None, None
    return ant_out, novo_out


def registrar_alteracao(
    acao,
    entidade,
    entidade_id=None,
    escola_id=None,
    anterior=None,
    novo=None,
    identidade=None,
):
    """Registra alteração real. No-op não gera AuditLog. Não commita."""
    identidade = identidade or {}
    ant_diff, novo_diff = diff_campos(anterior, novo)
    if ant_diff is None and novo_diff is None:
        return None
    dados_ant = {**identidade, **(ant_diff or {})}
    dados_novo = {**identidade, **(novo_diff or {})}
    return registrar_auditoria(
        acao,
        entidade,
        entidade_id=entidade_id,
        escola_id=escola_id,
        dados_anteriores=dados_ant,
        dados_novos=dados_novo,
    )


def snapshot_usuario(usuario):
    if usuario is None:
        return None
    return {
        "usuario_alvo_id": usuario.id,
        "login": usuario.login,
        "papel": usuario.papel,
        "escola_id": usuario.escola_id,
        "primeiro_acesso": bool(usuario.primeiro_acesso),
        "nome": usuario.nome,
    }


def snapshot_escola(escola):
    if escola is None:
        return None
    return {
        "escola_id": escola.id,
        "nome": escola.nome,
        "inep": escola.inep,
        "oferta_infantil": bool(escola.oferta_infantil),
        "oferta_fundamental": bool(escola.oferta_fundamental),
    }


def snapshot_turma(turma):
    if turma is None:
        return None
    return {
        "turma_id": turma.id,
        "escola_id": turma.escola_id,
        "nome": turma.nome,
        "ano_letivo": turma.ano_letivo,
        "etapa_ensino": turma.etapa_ensino,
        "status": turma.status,
        "capacidade": turma.capacidade,
        "tipo_jornada": turma.tipo_jornada,
        "carga_horaria_diaria": turma.carga_horaria_diaria,
        "carga_horaria_anual": turma.carga_horaria_anual,
    }


def snapshot_disciplina(disciplina):
    if disciplina is None:
        return None
    return {
        "disciplina_id": disciplina.id,
        "nome": disciplina.nome,
    }


def snapshot_grade(alocacao):
    if alocacao is None:
        return []
    from src.models import HorarioAlocacao

    if alocacao.id is None:
        itens = list(alocacao.horarios or [])
    else:
        itens = (
            HorarioAlocacao.query.filter_by(alocacao_id=alocacao.id)
            .order_by(HorarioAlocacao.dia_semana)
            .all()
        )
    return [
        {"dia_semana": h.dia_semana, "quantidade_aulas": h.quantidade_aulas}
        for h in sorted(itens, key=lambda x: x.dia_semana)
    ]


def snapshot_alocacao(alocacao):
    if alocacao is None:
        return None
    turma = getattr(alocacao, "turma", None)
    return {
        "alocacao_id": alocacao.id,
        "professor_id": alocacao.professor_id,
        "turma_id": alocacao.turma_id,
        "disciplina_id": alocacao.disciplina_id,
        "escola_id": turma.escola_id if turma is not None else None,
        "carga_horaria_prevista": alocacao.carga_horaria_prevista,
        "aulas_semanais": alocacao.aulas_semanais,
        "duracao_aula_minutos": alocacao.duracao_aula_minutos,
        "grade": snapshot_grade(alocacao),
    }


def snapshot_ano_letivo(ano):
    if ano is None:
        return None
    return {
        "ano_letivo_id": ano.id,
        "ano": ano.ano,
        "data_inicio": iso_data(ano.data_inicio),
        "data_fim": iso_data(ano.data_fim),
        "dias_letivos_previstos": ano.dias_letivos_previstos,
        "ativo": bool(ano.ativo),
    }


def snapshot_periodo(periodo):
    if periodo is None:
        return None
    return {
        "periodo_id": periodo.id,
        "ano_letivo_id": periodo.ano_letivo_id,
        "numero": periodo.numero,
        "nome": periodo.nome,
        "data_inicio": iso_data(periodo.data_inicio),
        "data_fim": iso_data(periodo.data_fim),
    }


def snapshot_dia_calendario(dia):
    if dia is None:
        return None
    return {
        "dia_id": dia.id,
        "ano_letivo_id": dia.ano_letivo_id,
        "data": iso_data(dia.data),
        "tipo": dia.tipo,
        "descricao": dia.descricao,
    }


def auditar_usuario_criado(usuario, senha_temporaria_gerada=False):
    """USUARIO_CRIADO. Nunca recebe o valor da senha."""
    db.session.flush()
    dados = snapshot_usuario(usuario)
    if senha_temporaria_gerada:
        dados["senha_temporaria_gerada"] = True
    return registrar_auditoria(
        USUARIO_CRIADO,
        "Usuario",
        entidade_id=usuario.id,
        escola_id=usuario.escola_id,
        dados_novos=dados,
    )


def auditar_senha_resetada(usuario):
    """SENHA_RESETADA. Nunca recebe o valor da senha nem o hash."""
    dados = snapshot_usuario(usuario)
    dados["senha_temporaria_gerada"] = True
    dados["primeiro_acesso"] = True
    return registrar_auditoria(
        SENHA_RESETADA,
        "Usuario",
        entidade_id=usuario.id,
        escola_id=usuario.escola_id,
        dados_novos=dados,
    )


def auditar_usuario_removido(usuario):
    if usuario is None or usuario.id is None:
        return None
    return registrar_auditoria(
        USUARIO_REMOVIDO,
        "Usuario",
        entidade_id=usuario.id,
        escola_id=usuario.escola_id,
        dados_anteriores=snapshot_usuario(usuario),
        dados_novos=None,
    )


def auditar_calendario_lote(ano, quantidade_criados=0, quantidade_alterados=0, geracao=False):
    """Um evento estrutural. Sem lista de dias."""
    if quantidade_criados <= 0 and quantidade_alterados <= 0:
        return None
    acao = CALENDARIO_GERADO if geracao else CALENDARIO_ATUALIZADO
    return registrar_auditoria(
        acao,
        "AnoLetivo",
        entidade_id=ano.id if ano is not None else None,
        dados_novos={
            "ano_letivo_id": ano.id if ano is not None else None,
            "ano_letivo": ano.ano if ano is not None else None,
            "quantidade_dias_criados": int(quantidade_criados),
            "quantidade_dias_alterados": int(quantidade_alterados),
            "data_inicio": iso_data(ano.data_inicio) if ano is not None else None,
            "data_fim": iso_data(ano.data_fim) if ano is not None else None,
        },
    )


def registrar_auditoria(
    acao,
    entidade,
    entidade_id=None,
    escola_id=None,
    dados_anteriores=None,
    dados_novos=None,
):
    """Inclui um AuditLog na sessão atual. Não commita.

    usuario_id vem do Flask-Login quando houver request autenticado;
    operações técnicas (sem request ou anônimas) gravam usuario_id NULL.
    """
    from src.models import AuditLog

    acao = (acao or "").strip()
    entidade = (entidade or "").strip()
    if not acao or not entidade:
        raise ValueError("acao e entidade são obrigatórios.")

    log = AuditLog(
        usuario_id=_usuario_autenticado_id(),
        acao=acao,
        entidade=entidade,
        entidade_id=entidade_id,
        escola_id=escola_id,
        data_hora=datetime.utcnow(),
        request_id=_request_id_atual(),
        ip=ip_remoto(),
        dados_anteriores=_validar_json(sanitizar_dados(dados_anteriores)),
        dados_novos=_validar_json(sanitizar_dados(dados_novos)),
    )
    db.session.add(log)
    return log
