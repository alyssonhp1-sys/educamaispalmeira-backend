"""Complemento manual do ano corrente para o histórico de transferência.

Não grava HistoricoAnual, RegistroNota nem RegistroFalta.
Dado oficial existente tem precedência e não é substituído.
"""
from __future__ import annotations

from src import db
from src.auditoria import (
    COMPLEMENTO_TRANSFERENCIA_ALTERADO,
    COMPLEMENTO_TRANSFERENCIA_CRIADO,
    COMPLEMENTO_TRANSFERENCIA_REMOVIDO,
    registrar_auditoria,
)
from src.historico_escolar import (
    MSG_SEM_TRANSFERENCIA,
    DocumentoErro,
    _ano_em_curso,
    _matricula_fundamental_aberta,
    _secao_transferencia,
)
from src.matriz_curricular import chave_componente
from src.models import ComplementoTransferencia, ComplementoTransferenciaPeriodo


class ComplementoTransferenciaErro(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status


def _contexto(aluno):
    if aluno is None:
        raise DocumentoErro("Aluno não encontrado", 404)
    aberta = _matricula_fundamental_aberta(aluno)
    aberto = _ano_em_curso(aberta) if aberta is not None else None
    status = (aberto.get("turma_status") or "").lower() if aberto else ""
    if aberto is None or aberta is None or status == "concluida":
        raise DocumentoErro(MSG_SEM_TRANSFERENCIA, 400)
    return aberta, aberto


def _registro(matricula_id):
    return ComplementoTransferencia.query.filter_by(matricula_id=matricula_id).first()


def _mapa(matricula_id):
    reg = _registro(matricula_id)
    if reg is None:
        return {}
    return {(item.nome_chave, item.periodo): item for item in reg.periodos}


def mesclar_complemento(matricula, secao):
    """Preenche só célula ausente. Período futuro e valor oficial permanecem."""
    if not secao or matricula is None:
        return secao
    mapa = _mapa(matricula.id)
    if not mapa:
        return secao
    componentes = []
    for comp in secao.get("componentes") or []:
        chave = chave_componente(comp.get("nome"))
        periodos = []
        for periodo in comp.get("periodos") or []:
            item = dict(periodo)
            if not item.get("futuro"):
                manual = mapa.get((chave, item.get("numero")))
                if manual is not None:
                    if item.get("nota") is None and manual.nota is not None:
                        item["nota"] = manual.nota
                    if item.get("faltas") is None and manual.faltas is not None:
                        item["faltas"] = manual.faltas
            periodos.append(item)
        novo = dict(comp)
        novo["periodos"] = periodos
        componentes.append(novo)
    saida = dict(secao)
    saida["componentes"] = componentes
    return saida


def _celula_completa(periodo):
    if periodo.get("futuro"):
        return True
    return periodo.get("nota") is not None and periodo.get("faltas") is not None


def montar_formulario(aluno):
    aberta, aberto = _contexto(aluno)
    oficial = _secao_transferencia(aberta, aberto, aplicar_manual=False)
    mapa = _mapa(aberta.id)
    reg = _registro(aberta.id)
    componentes = []
    completo = True
    for comp in oficial.get("componentes") or []:
        chave = chave_componente(comp.get("nome"))
        periodos = []
        for periodo in comp.get("periodos") or []:
            numero = periodo.get("numero")
            futuro = bool(periodo.get("futuro"))
            manual = mapa.get((chave, numero))
            nota_oficial = periodo.get("nota")
            faltas_oficiais = periodo.get("faltas")
            nota_manual = None if manual is None else manual.nota
            faltas_manual = None if manual is None else manual.faltas
            if futuro:
                nota_efetiva = None
                faltas_efetivas = None
            else:
                nota_efetiva = nota_oficial if nota_oficial is not None else nota_manual
                faltas_efetivas = (
                    faltas_oficiais if faltas_oficiais is not None else faltas_manual
                )
            if not _celula_completa({
                "futuro": futuro,
                "nota": nota_efetiva,
                "faltas": faltas_efetivas,
            }):
                completo = False
            periodos.append({
                "numero": numero,
                "nome": periodo.get("nome"),
                "futuro": futuro,
                "nota_oficial": nota_oficial,
                "faltas_oficiais": faltas_oficiais,
                "nota_complementar": nota_manual if nota_oficial is None and not futuro else None,
                "faltas_complementares": (
                    faltas_manual if faltas_oficiais is None and not futuro else None
                ),
                "editavel_nota": (not futuro) and nota_oficial is None,
                "editavel_faltas": (not futuro) and faltas_oficiais is None,
            })
        componentes.append({
            "nome": comp.get("nome"),
            "disciplina_id": comp.get("disciplina_id"),
            "periodos": periodos,
        })
    if not componentes:
        completo = True
    escola_id = aberta.turma.escola_id if aberta.turma is not None else None
    return {
        "aluno_id": aluno.id,
        "matricula_id": aberta.id,
        "escola_id": escola_id,
        "ano_letivo": aberto.get("ano_letivo"),
        "turma_nome": aberto.get("turma_nome"),
        "completo": completo,
        "complemento_id": reg.id if reg is not None else None,
        "periodos": [
            {"numero": p.get("numero"), "nome": p.get("nome"), "futuro": bool(p.get("futuro"))}
            for p in (oficial.get("periodos") or [])
        ],
        "componentes": componentes,
    }


def _parse_nota(valor):
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        raise ComplementoTransferenciaErro("Informe a nota com um número de 0 a 10.")
    if isinstance(valor, (int, float)):
        numero = float(valor)
    else:
        texto = str(valor).strip().replace(",", ".")
        if not texto:
            return None
        try:
            numero = float(texto)
        except ValueError:
            raise ComplementoTransferenciaErro("Informe a nota com um número de 0 a 10.")
    if numero < 0 or numero > 10:
        raise ComplementoTransferenciaErro("Informe a nota com um número de 0 a 10.")
    return round(numero, 2)


def _parse_faltas(valor):
    if valor is None or valor == "":
        return None
    if isinstance(valor, bool):
        raise ComplementoTransferenciaErro("Informe as faltas com um número inteiro.")
    try:
        if isinstance(valor, str):
            texto = valor.strip()
            if not texto:
                return None
            if not texto.isdigit():
                raise ValueError
            numero = int(texto)
        else:
            numero = int(valor)
            if isinstance(valor, float) and not valor.is_integer():
                raise ValueError
    except (TypeError, ValueError):
        raise ComplementoTransferenciaErro("Informe as faltas com um número inteiro.")
    if numero < 0 or numero > 366:
        raise ComplementoTransferenciaErro("Informe as faltas com um número inteiro.")
    return numero


def _indice_oficial(formulario):
    indice = {}
    for comp in formulario.get("componentes") or []:
        chave = chave_componente(comp.get("nome"))
        for periodo in comp.get("periodos") or []:
            indice[(chave, periodo.get("numero"))] = periodo
    return indice


def _auditar(acao, reg, aluno_id, escola_id, ano_letivo):
    registrar_auditoria(
        acao,
        "ComplementoTransferencia",
        entidade_id=reg.id if reg is not None else None,
        escola_id=escola_id,
        dados_anteriores=None,
        dados_novos={
            "aluno_id": aluno_id,
            "escola_id": escola_id,
            "ano_letivo": ano_letivo,
            "complemento_id": reg.id if reg is not None else None,
            "tipo": "complemento_transferencia",
        },
    )


def salvar(aluno, dados, escola_id):
    if not isinstance(dados, dict):
        raise ComplementoTransferenciaErro("JSON inválido ou ausente.")
    formulario = montar_formulario(aluno)
    aberta, _aberto = _contexto(aluno)
    oficial = _indice_oficial(formulario)
    itens = dados.get("itens")
    if not isinstance(itens, list):
        raise ComplementoTransferenciaErro("Informe os períodos a complementar.")

    desejados = {}
    for bruto in itens:
        if not isinstance(bruto, dict):
            raise ComplementoTransferenciaErro("Período complementar inválido.")
        nome = (bruto.get("nome") or "").strip()
        if not nome:
            raise ComplementoTransferenciaErro("Informe o componente curricular.")
        chave = chave_componente(nome)
        try:
            periodo = int(bruto.get("periodo"))
        except (TypeError, ValueError):
            raise ComplementoTransferenciaErro("Informe o período oficial.")
        celula = oficial.get((chave, periodo))
        if celula is None:
            raise ComplementoTransferenciaErro(
                "O componente ou o período não pertence à transferência deste aluno.",
            )
        if celula.get("futuro"):
            continue
        nota = _parse_nota(bruto.get("nota")) if celula.get("editavel_nota") else None
        faltas = _parse_faltas(bruto.get("faltas")) if celula.get("editavel_faltas") else None
        if not celula.get("editavel_nota"):
            nota = None
        if not celula.get("editavel_faltas"):
            faltas = None
        if nota is None and faltas is None:
            desejados.pop((chave, periodo), None)
            continue
        disciplina_id = None
        for comp in formulario.get("componentes") or []:
            if chave_componente(comp.get("nome")) == chave:
                disciplina_id = comp.get("disciplina_id")
                nome = comp.get("nome") or nome
                break
        desejados[(chave, periodo)] = {
            "nome": nome,
            "disciplina_id": disciplina_id,
            "periodo": periodo,
            "nota": nota,
            "faltas": faltas,
        }

    reg = _registro(aberta.id)
    existia = reg is not None
    if not desejados:
        if reg is not None:
            reg_id = reg.id
            ano = reg.ano_letivo
            db.session.delete(reg)
            db.session.flush()
            registrar_auditoria(
                COMPLEMENTO_TRANSFERENCIA_REMOVIDO,
                "ComplementoTransferencia",
                entidade_id=reg_id,
                escola_id=escola_id,
                dados_anteriores=None,
                dados_novos={
                    "aluno_id": aluno.id,
                    "escola_id": escola_id,
                    "ano_letivo": ano,
                    "complemento_id": reg_id,
                    "tipo": "complemento_transferencia",
                },
            )
        return montar_formulario(aluno)

    if reg is None:
        reg = ComplementoTransferencia(
            aluno_id=aluno.id,
            matricula_id=aberta.id,
            escola_id=escola_id,
            ano_letivo=formulario.get("ano_letivo"),
        )
        db.session.add(reg)
        db.session.flush()
    else:
        reg.escola_id = escola_id
        reg.ano_letivo = formulario.get("ano_letivo")

    atuais = {(item.nome_chave, item.periodo): item for item in list(reg.periodos)}
    for chave_periodo, item in list(atuais.items()):
        if chave_periodo not in desejados:
            db.session.delete(item)
    for chave_periodo, dados_item in desejados.items():
        item = atuais.get(chave_periodo)
        if item is None:
            item = ComplementoTransferenciaPeriodo(
                complemento_id=reg.id,
                nome_chave=chave_periodo[0],
                periodo=dados_item["periodo"],
            )
            db.session.add(item)
        item.nome_componente = dados_item["nome"]
        item.disciplina_id = dados_item["disciplina_id"]
        item.nota = dados_item["nota"]
        item.faltas = dados_item["faltas"]
    db.session.flush()
    _auditar(
        COMPLEMENTO_TRANSFERENCIA_ALTERADO if existia else COMPLEMENTO_TRANSFERENCIA_CRIADO,
        reg,
        aluno.id,
        escola_id,
        formulario.get("ano_letivo"),
    )
    return montar_formulario(aluno)
