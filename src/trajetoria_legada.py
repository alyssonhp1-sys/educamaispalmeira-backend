"""Cadastro administrativo de trajetória escolar anterior ao Educa+.

Não grava HistoricoAnual. Não aplica matriz vigente. Conceito permanece texto.
"""
from __future__ import annotations

from datetime import datetime

from flask import current_app, has_app_context
from sqlalchemy.exc import IntegrityError

from src import db
from src.auditoria import (
    TRAJETORIA_LEGADA_ALTERADA,
    TRAJETORIA_LEGADA_CRIADA,
    TRAJETORIA_LEGADA_REMOVIDA,
    registrar_auditoria,
)
from src.config import ANO_INICIO_HISTORICO_AUTOMATICO_PADRAO
from src.matriz_curricular import etapa_da_serie, inferir_serie, normalizar_nome_componente
from src.models import (
    CATEGORIAS_MATRIZ,
    Disciplina,
    HistoricoAnual,
    Matricula,
    MatrizComponente,
    ORIGEM_TRAJETORIA_LEGADA,
    ORIGENS_TRAJETORIA_LEGADA,
    TrajetoriaComponenteLegado,
    TrajetoriaEscolarLegada,
    Turma,
)

MSG_ANO_AUTOMATICO = (
    "Não é permitido cadastrar trajetória anterior para o ano letivo %s. "
    "A partir de %s os dados acadêmicos vêm automaticamente do Educa+."
)
MSG_DUPLICIDADE = (
    "Já existe trajetória anterior cadastrada para este aluno neste ano e série."
)
MSG_CONFLITO_EDUCAMAIS = (
    "Já existe histórico do Educa+ para este aluno neste ano e série. "
    "O registro do sistema tem prioridade e não pode ser substituído por legado."
)
MSG_SERIE = "Informe a série/ano (1º ao 9º) conforme o documento de origem."
MSG_ANO = "Informe o ano letivo anterior à adoção do Educa+."
MSG_COMPONENTE_NOME = "Informe o nome do componente curricular."
MSG_ESTABELECIMENTO = "Informe o estabelecimento de ensino."
MSG_MUNICIPIO = "Informe o município."
MSG_UF = "Informe a UF."
MSG_CATEGORIA = "Categoria curricular inválida."
MSG_ORIGEM = "Origem do registro legado inválida."

# Facilitador de digitação. Não é a CH total do ano e não reescreve a matriz.
CH_COMPONENTE_MIN = 40
CH_COMPONENTE_MAX = 450
CARGAS_HORARIAS_COMPONENTE_SUGERIDAS = tuple(list(range(40, 441, 20)) + [450])


class TrajetoriaLegadaErro(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def ano_inicio_historico_automatico():
    """Um único ponto de leitura da adoção do Educa+ no histórico."""
    if has_app_context():
        valor = current_app.config.get("EDUCAMAIS_ANO_INICIO_HISTORICO_AUTOMATICO")
        if valor is not None:
            return int(valor)
    from src.config import ano_inicio_historico_automatico as _cfg
    return _cfg()


def periodo_e_automatico(ano_letivo):
    if ano_letivo is None:
        return False
    try:
        ano = int(ano_letivo)
    except (TypeError, ValueError):
        return False
    return ano >= ano_inicio_historico_automatico()


def _texto(valor, limite):
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    if len(texto) > limite:
        return texto[:limite]
    return texto


def _inteiro_opcional(valor, nome, minimo=0, maximo=99999):
    if valor is None or valor == "":
        return None
    try:
        n = int(valor)
    except (TypeError, ValueError):
        raise TrajetoriaLegadaErro("%s deve ser um número inteiro." % nome)
    if n < minimo or n > maximo:
        raise TrajetoriaLegadaErro(
            "%s deve estar entre %s e %s." % (nome, minimo, maximo)
        )
    return n


def _ano_letivo(valor):
    inicio = ano_inicio_historico_automatico()
    try:
        ano = int(valor)
    except (TypeError, ValueError):
        raise TrajetoriaLegadaErro(MSG_ANO)
    if ano < 1950 or ano > 2100:
        raise TrajetoriaLegadaErro(MSG_ANO)
    if ano >= inicio:
        raise TrajetoriaLegadaErro(MSG_ANO_AUTOMATICO % (ano, inicio))
    return ano


def _serie(valor):
    try:
        serie = int(valor)
    except (TypeError, ValueError):
        raise TrajetoriaLegadaErro(MSG_SERIE)
    if serie < 1 or serie > 9:
        raise TrajetoriaLegadaErro(MSG_SERIE)
    return serie


def _uf(valor):
    texto = _texto(valor, 2)
    if texto is None:
        raise TrajetoriaLegadaErro(MSG_UF)
    return texto.upper()


def _categoria(valor):
    texto = _texto(valor, 40)
    if texto is None:
        return None
    texto = texto.upper().replace(" ", "_")
    if texto not in CATEGORIAS_MATRIZ:
        raise TrajetoriaLegadaErro(MSG_CATEGORIA)
    return texto


def _origem(valor):
    texto = _texto(valor, 40) or ORIGEM_TRAJETORIA_LEGADA
    texto = texto.upper().replace(" ", "_")
    if texto in ("INFORMADO_PELA_ESCOLA", "INFORMADO", "DOCUMENTO_ANTERIOR"):
        texto = ORIGEM_TRAJETORIA_LEGADA
    if texto not in ORIGENS_TRAJETORIA_LEGADA:
        raise TrajetoriaLegadaErro(MSG_ORIGEM)
    return texto


def _componentes_de_payload(lista):
    if lista is None:
        return []
    if not isinstance(lista, list):
        raise TrajetoriaLegadaErro("Lista de componentes inválida.")
    saida = []
    for i, item in enumerate(lista):
        if not isinstance(item, dict):
            raise TrajetoriaLegadaErro("Componente inválido.")
        nome = _texto(item.get("nome") or item.get("componente"), 160)
        if not nome:
            raise TrajetoriaLegadaErro(MSG_COMPONENTE_NOME)
        saida.append({
            "nome": nome,
            "carga_horaria": _inteiro_opcional(
                item.get("carga_horaria") or item.get("cargaHoraria"),
                "Carga horária do componente",
            ),
            "nota_conceito": _texto(
                item.get("nota_conceito") or item.get("notaConceito")
                or item.get("nota") or item.get("media") or item.get("conceito"),
                40,
            ),
            "situacao": _texto(
                item.get("situacao") or item.get("situacao_componente")
                or item.get("situacaoComponente"),
                80,
            ),
            "faltas": _inteiro_opcional(item.get("faltas"), "Faltas"),
            "frequencia": _texto(
                item.get("frequencia") or item.get("frequência"), 40,
            ),
            "categoria": _categoria(item.get("categoria")),
            "ordem": i,
        })
    return saida


def metadados_auditoria(reg, escola_id):
    """Recorte mínimo. Sem notas, filiação, CPF ou histórico completo."""
    return {
        "aluno_id": reg.aluno_id,
        "ano_letivo": reg.ano_letivo,
        "serie": reg.serie,
        "escola_id": escola_id,
        "registro_legado_id": reg.id,
    }


def _auditar(acao, reg, escola_id, anteriores=None, novos=None):
    registrar_auditoria(
        acao,
        "TrajetoriaEscolarLegada",
        entidade_id=reg.id,
        escola_id=escola_id,
        dados_anteriores=anteriores,
        dados_novos=novos,
    )


def serializar_componente(comp):
    return {
        "id": comp.id,
        "nome": comp.nome,
        "carga_horaria": comp.carga_horaria,
        "nota_conceito": comp.nota_conceito,
        "situacao": comp.situacao,
        "faltas": comp.faltas,
        "frequencia": comp.frequencia,
        "categoria": comp.categoria,
        "ordem": comp.ordem,
    }


def serializar_trajetoria(reg):
    comps = sorted(reg.componentes or [], key=lambda c: (c.ordem or 0, c.id or 0))
    return {
        "id": reg.id,
        "aluno_id": reg.aluno_id,
        "ano_letivo": reg.ano_letivo,
        "serie": reg.serie,
        "etapa": reg.etapa,
        "estabelecimento": reg.estabelecimento,
        "municipio": reg.municipio,
        "uf": reg.uf,
        "resultado": reg.resultado,
        "carga_horaria_total": reg.carga_horaria_total,
        "observacao": reg.observacao,
        "origem": reg.origem,
        "componentes": [serializar_componente(c) for c in comps],
    }


def listar_do_aluno(aluno_id):
    registros = (
        TrajetoriaEscolarLegada.query.filter_by(aluno_id=aluno_id)
        .order_by(
            TrajetoriaEscolarLegada.ano_letivo.asc(),
            TrajetoriaEscolarLegada.serie.asc(),
            TrajetoriaEscolarLegada.id.asc(),
        )
        .all()
    )
    return [serializar_trajetoria(r) for r in registros]


def _rank_nome_exibicao(item):
    nome, (qtd, origem) = item
    titulo = any(c.isupper() for c in nome) and not nome.isupper()
    return (origem, 0 if titulo else 1, -qtd, nome.casefold())


def escolher_nome_exibicao(ocorrencias):
    """Uma grafia por componente. Matriz tem preferência sobre a disciplina operacional."""
    contagem = {}
    for origem, nome in ocorrencias:
        texto = str(nome or "").strip()
        if not texto:
            continue
        info = contagem.get(texto)
        if info is None:
            contagem[texto] = [1, origem]
        else:
            info[0] += 1
            if origem < info[1]:
                info[1] = origem
    if not contagem:
        return None
    return min(contagem.items(), key=_rank_nome_exibicao)[0]


def nomes_componentes_cadastrados():
    """Nomes já gravados em Disciplina e MatrizComponente.

    Duplicatas da mesma grafia normalizada viram uma opção. Não cria
    disciplina, não altera matriz e não inclui nomes só do histórico legado.
    """
    grupos = {}
    consultas = (
        (0, db.session.query(MatrizComponente.nome)),
        (1, db.session.query(Disciplina.nome)),
    )
    for origem, consulta in consultas:
        for (nome,) in consulta:
            chave = normalizar_nome_componente(nome)
            if not chave:
                continue
            grupos.setdefault(chave, []).append((origem, nome))
    nomes = []
    for ocorrencias in grupos.values():
        escolhido = escolher_nome_exibicao(ocorrencias)
        if escolhido:
            nomes.append(escolhido)
    nomes.sort(key=normalizar_nome_componente)
    return nomes


def cargas_componente_fora_da_sugestao():
    """CH de componente (40–450) que a matriz ou o legado já têm fora da grade de 20h.

    Ignora a CH total do ano e a CH anual da matriz como um todo.
    """
    sugeridas = set(CARGAS_HORARIAS_COMPONENTE_SUGERIDAS)
    achadas = set()
    consultas = (
        db.session.query(MatrizComponente.carga_horaria_anual),
        db.session.query(TrajetoriaComponenteLegado.carga_horaria),
    )
    for consulta in consultas:
        for (valor,) in consulta.distinct():
            if valor is None:
                continue
            try:
                n = int(valor)
            except (TypeError, ValueError):
                continue
            if CH_COMPONENTE_MIN <= n <= CH_COMPONENTE_MAX and n not in sugeridas:
                achadas.add(n)
    return sorted(achadas)


def payload_listagem(aluno_id):
    return {
        "ano_inicio_automatico": ano_inicio_historico_automatico(),
        "itens": listar_do_aluno(aluno_id),
        "componentes_cadastrados": nomes_componentes_cadastrados(),
        "cargas_horarias_sugeridas": list(CARGAS_HORARIAS_COMPONENTE_SUGERIDAS),
        "cargas_horarias_fora_da_sugestao": cargas_componente_fora_da_sugestao(),
    }


def obter(registro_id):
    return db.session.get(TrajetoriaEscolarLegada, registro_id)


def _historico_educamais_do_periodo(aluno_id, ano_letivo, serie):
    from src.historico import carregar_dados_fechamento, historico_e_infantil

    registros = HistoricoAnual.query.filter_by(
        aluno_id=aluno_id, ano_letivo=ano_letivo,
    ).all()
    for hist in registros:
        dados = carregar_dados_fechamento(hist)
        if historico_e_infantil(hist, dados):
            continue
        serie_hist = inferir_serie(hist.turma_nome)
        if serie_hist == serie or serie_hist is None:
            return True
    mats = (
        Matricula.query.join(Turma, Matricula.turma_id == Turma.id)
        .filter(Matricula.aluno_id == aluno_id, Turma.ano_letivo == ano_letivo)
        .all()
    )
    for mat in mats:
        turma = mat.turma
        if turma is None:
            continue
        if (turma.etapa_ensino or "") == "infantil":
            continue
        serie_t = inferir_serie(turma.nome)
        if serie_t == serie or serie_t is None:
            return True
    return False


def _duplicado(aluno_id, ano_letivo, serie, ignorar_id=None):
    q = TrajetoriaEscolarLegada.query.filter_by(
        aluno_id=aluno_id, ano_letivo=ano_letivo, serie=serie,
    )
    if ignorar_id is not None:
        q = q.filter(TrajetoriaEscolarLegada.id != ignorar_id)
    return q.first() is not None


def _validar_periodo(aluno_id, ano_letivo, serie, ignorar_id=None):
    if _duplicado(aluno_id, ano_letivo, serie, ignorar_id=ignorar_id):
        raise TrajetoriaLegadaErro(MSG_DUPLICIDADE)
    if _historico_educamais_do_periodo(aluno_id, ano_letivo, serie):
        raise TrajetoriaLegadaErro(MSG_CONFLITO_EDUCAMAIS)


def _aplicar_componentes(reg, componentes):
    reg.componentes.clear()
    db.session.flush()
    for item in componentes:
        reg.componentes.append(TrajetoriaComponenteLegado(
            nome=item["nome"],
            carga_horaria=item["carga_horaria"],
            nota_conceito=item["nota_conceito"],
            situacao=item["situacao"],
            faltas=item["faltas"],
            frequencia=item["frequencia"],
            categoria=item["categoria"],
            ordem=item["ordem"],
        ))


def _campos_cabecalho(dados):
    ano = _ano_letivo(dados.get("ano_letivo") or dados.get("anoLetivo"))
    serie = _serie(dados.get("serie") or dados.get("serieAno"))
    etapa = etapa_da_serie(serie)
    estabelecimento = _texto(
        dados.get("estabelecimento") or dados.get("escola"), 160,
    )
    municipio = _texto(dados.get("municipio"), 100)
    if not estabelecimento:
        raise TrajetoriaLegadaErro(MSG_ESTABELECIMENTO)
    if not municipio:
        raise TrajetoriaLegadaErro(MSG_MUNICIPIO)
    return {
        "ano_letivo": ano,
        "serie": serie,
        "etapa": etapa,
        "estabelecimento": estabelecimento,
        "municipio": municipio,
        "uf": _uf(dados.get("uf")),
        "resultado": _texto(
            dados.get("resultado") or dados.get("situacao"), 80,
        ),
        "carga_horaria_total": _inteiro_opcional(
            dados.get("carga_horaria_total") or dados.get("cargaHorariaTotal"),
            "Carga horária total",
        ),
        "observacao": _texto(dados.get("observacao") or dados.get("observações"), 4000),
        "origem": _origem(dados.get("origem")),
        "componentes": _componentes_de_payload(dados.get("componentes")),
    }


def criar(aluno, dados, escola_id):
    campos = _campos_cabecalho(dados or {})
    _validar_periodo(aluno.id, campos["ano_letivo"], campos["serie"])
    agora = datetime.utcnow()
    reg = TrajetoriaEscolarLegada(
        aluno_id=aluno.id,
        ano_letivo=campos["ano_letivo"],
        serie=campos["serie"],
        etapa=campos["etapa"],
        estabelecimento=campos["estabelecimento"],
        municipio=campos["municipio"],
        uf=campos["uf"],
        resultado=campos["resultado"],
        carga_horaria_total=campos["carga_horaria_total"],
        observacao=campos["observacao"],
        origem=campos["origem"],
        criado_em=agora,
        atualizado_em=agora,
    )
    db.session.add(reg)
    db.session.flush()
    _aplicar_componentes(reg, campos["componentes"])
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise TrajetoriaLegadaErro(MSG_DUPLICIDADE) from exc
    _auditar(
        TRAJETORIA_LEGADA_CRIADA, reg, escola_id,
        novos=metadados_auditoria(reg, escola_id),
    )
    return reg


def atualizar(reg, dados, escola_id):
    campos = _campos_cabecalho(dados or {})
    _validar_periodo(reg.aluno_id, campos["ano_letivo"], campos["serie"], ignorar_id=reg.id)
    anterior = metadados_auditoria(reg, escola_id)
    reg.ano_letivo = campos["ano_letivo"]
    reg.serie = campos["serie"]
    reg.etapa = campos["etapa"]
    reg.estabelecimento = campos["estabelecimento"]
    reg.municipio = campos["municipio"]
    reg.uf = campos["uf"]
    reg.resultado = campos["resultado"]
    reg.carga_horaria_total = campos["carga_horaria_total"]
    reg.observacao = campos["observacao"]
    reg.origem = campos["origem"]
    reg.atualizado_em = datetime.utcnow()
    _aplicar_componentes(reg, campos["componentes"])
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise TrajetoriaLegadaErro(MSG_DUPLICIDADE) from exc
    _auditar(
        TRAJETORIA_LEGADA_ALTERADA, reg, escola_id,
        anteriores=anterior,
        novos=metadados_auditoria(reg, escola_id),
    )
    return reg


def excluir(reg, escola_id):
    anterior = metadados_auditoria(reg, escola_id)
    _auditar(TRAJETORIA_LEGADA_REMOVIDA, reg, escola_id, anteriores=anterior)
    db.session.delete(reg)
    return anterior


# Reexporta a constante de configuração para testes que não passam pelo Flask.
ANO_INICIO_PADRAO = ANO_INICIO_HISTORICO_AUTOMATICO_PADRAO
