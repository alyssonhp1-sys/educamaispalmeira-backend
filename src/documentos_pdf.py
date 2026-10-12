"""Coleta e emissão de documentos oficiais em PDF.

PDFs autenticados em /api/alunos/.../documentos/.
"""
from __future__ import annotations

from flask import make_response, current_app

from src import db
from src.auditoria import (
    DOCUMENTO_BOLETIM_EMITIDO,
    DOCUMENTO_DECLARACAO_EMITIDA,
    DOCUMENTO_HISTORICO_EMITIDO,
    DOCUMENTO_HISTORICO_TRANSFERENCIA_EMITIDO,
    DOCUMENTO_MATRICULA_EMITIDA,
    DOCUMENTO_PARECER_EMITIDO,
    DOCUMENTO_RENDIMENTO_EMITIDO,
    registrar_auditoria,
)
from src.documentos_institucionais import slug_arquivo
from src.historico import (
    MSG_BOLETIM_EI,
    MSG_LEGADO_INSUFICIENTE,
    MSG_PARECER_NAO_EI,
    boletim_do_snapshot,
    carregar_dados_fechamento,
    escola_id_para_acl,
    historico_e_infantil,
    identidade_congelada,
    media_minima_do_snapshot,
    pareceres_do_snapshot,
    snapshot_suficiente_para_boletim,
    snapshot_suficiente_para_parecer,
)
from src.historico_escolar import DocumentoErro, montar_historico_escolar
from src.http import MENSAGEM_ERRO_INTERNO
from src.matriculas import matricula_cursando_na_turma, resolver_matricula_academica
from src.models import Alocacao, Disciplina, Usuario
from src.notas import calcular_notas_matricula, media_minima_vigente
from src.pdf_oficial import (
    montar_pdf_boletim,
    montar_pdf_declaracao,
    montar_pdf_historico,
    montar_pdf_matricula,
    montar_pdf_parecer,
    montar_pdf_rendimento,
)
from src.pareceres import pareceres_da_matricula


def _auditar_emissao(acao, aluno_id, escola_id, tipo):
    registrar_auditoria(
        acao,
        "Aluno",
        entidade_id=aluno_id,
        escola_id=escola_id,
        dados_anteriores=None,
        dados_novos={
            "aluno_id": aluno_id,
            "escola_id": escola_id,
            "tipo": tipo,
            "formato": "pdf",
        },
    )
    db.session.commit()


def resposta_pdf(conteudo, filename):
    resp = make_response(conteudo)
    resp.headers["Content-Type"] = "application/pdf"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Content-Disposition"] = "attachment; filename=\"%s\"" % filename
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _gerar(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except DocumentoErro:
        raise
    except Exception:
        from flask import has_request_context, request
        from src.http import garantir_request_id
        current_app.logger.error(
            "Falha ao gerar PDF request_id=%s path=%s",
            garantir_request_id() if has_request_context() else None,
            request.path if has_request_context() else None,
        )
        raise DocumentoErro(MENSAGEM_ERRO_INTERNO, 500)


def _escola_ctx(aluno):
    escola = aluno.escola if aluno is not None else None
    if escola is None and aluno is not None and aluno.turma is not None:
        escola = aluno.turma.escola
    if escola is None:
        return {}
    return {"id": escola.id, "nome": escola.nome, "inep": escola.inep, "endereco": None}


def _nome_unico(nomes):
    limpos = []
    vistos = set()
    for nome in nomes:
        texto = (nome or "").strip()
        if not texto:
            continue
        chave = texto.casefold()
        if chave in vistos:
            continue
        vistos.add(chave)
        limpos.append(texto)
    if len(limpos) == 1:
        return limpos[0]
    return None


def professor_titular_da_turma(turma):
    """Nome só quando a turma tem exatamente um professor alocado. Não inventa titular."""
    if turma is None or getattr(turma, "id", None) is None:
        return None
    nomes = []
    for aloc in Alocacao.query.filter_by(turma_id=turma.id).all():
        if aloc.professor is not None:
            nomes.append(aloc.professor.nome)
    return _nome_unico(nomes)


def coordenacao_pedagogica_da_escola(escola_id):
    """Nome só quando a escola tem exatamente um coordenador pedagógico."""
    if not escola_id:
        return None
    nomes = [
        usuario.nome
        for usuario in Usuario.query.filter_by(
            escola_id=escola_id, papel="coordenador_pedagogico",
        ).all()
    ]
    return _nome_unico(nomes)


def _assinantes_parecer_atual(aluno):
    turma = aluno.turma if aluno is not None else None
    escola = _escola_ctx(aluno)
    return {
        "professor_titular": professor_titular_da_turma(turma),
        "coordenacao_pedagogica": coordenacao_pedagogica_da_escola(escola.get("id")),
    }


def emitir_historico_pdf(aluno, para_transferencia=False):
    payload = montar_historico_escolar(aluno, para_transferencia=para_transferencia)
    pdf = _gerar(montar_pdf_historico, payload)
    prefixo = "historico-transferencia" if para_transferencia else "historico-escolar"
    nome = slug_arquivo(payload.get("aluno", {}).get("nome"), prefixo, aluno.cpf)
    acao = (
        DOCUMENTO_HISTORICO_TRANSFERENCIA_EMITIDO
        if para_transferencia else DOCUMENTO_HISTORICO_EMITIDO
    )
    tipo = "historico_transferencia" if para_transferencia else "historico_escolar"
    _auditar_emissao(acao, aluno.id, _escola_ctx(aluno).get("id"), tipo)
    return pdf, nome


def _boletim_linhas_atuais(aluno):
    mat = resolver_matricula_academica(aluno.id)
    linhas = []
    if mat is None:
        return linhas
    disciplinas = Disciplina.query.order_by(Disciplina.nome.asc()).all()
    for disc in disciplinas:
        dados = calcular_notas_matricula(mat.id, disc.id)
        tem = any(v is not None for v in [dados["mb1"], dados["mb2"], dados["mb3"], dados["mb4"]])
        if tem or dados["situacao"] not in ["CURSANDO", "TRANSFERIDO"]:
            dados = dict(dados)
            dados["disciplina"] = disc.nome
            linhas.append(dados)
    return linhas


def emitir_boletim_pdf(aluno):
    if not aluno.turma:
        raise DocumentoErro(
            "Este aluno está sem turma. Enturme o aluno antes de emitir o boletim.",
            400,
        )
    if aluno.turma.etapa_ensino == "infantil":
        raise DocumentoErro(MSG_BOLETIM_EI, 400)
    ctx = {
        "titulo": "BOLETIM ESCOLAR",
        "aluno_nome": aluno.nome,
        "turma_nome": aluno.turma.nome,
        "ano_letivo": aluno.turma.ano_letivo,
        "escola": _escola_ctx(aluno),
        "media_minima": media_minima_vigente(),
        "linhas": _boletim_linhas_atuais(aluno),
    }
    pdf = _gerar(montar_pdf_boletim, ctx)
    nome = slug_arquivo(aluno.nome, "boletim", aluno.cpf)
    _auditar_emissao(DOCUMENTO_BOLETIM_EMITIDO, aluno.id, _escola_ctx(aluno).get("id"), "boletim")
    return pdf, nome


def emitir_boletim_historico_pdf(hist):
    dados = carregar_dados_fechamento(hist)
    if historico_e_infantil(hist, dados):
        raise DocumentoErro(MSG_BOLETIM_EI, 400)
    if not snapshot_suficiente_para_boletim(hist, dados):
        raise DocumentoErro(MSG_LEGADO_INSUFICIENTE, 400)
    ident = identidade_congelada(hist, dados)
    ctx = {
        "titulo": "BOLETIM ESCOLAR",
        "aluno_nome": ident.get("aluno_nome"),
        "turma_nome": ident.get("turma_nome"),
        "ano_letivo": ident.get("ano_letivo"),
        "escola": {
            "id": ident.get("escola_id"),
            "nome": ident.get("escola_nome"),
            "inep": ident.get("escola_inep"),
        },
        "media_minima": media_minima_do_snapshot(dados),
        "linhas": boletim_do_snapshot(dados),
        "referencia": (
            "Documento referente ao ano letivo de %s" % ident.get("ano_letivo")
            if ident.get("ano_letivo") is not None else ""
        ),
    }
    pdf = _gerar(montar_pdf_boletim, ctx)
    nome = slug_arquivo(ident.get("aluno_nome"), "boletim-historico")
    _auditar_emissao(
        DOCUMENTO_BOLETIM_EMITIDO,
        ident.get("aluno_id") or hist.aluno_id,
        escola_id_para_acl(hist),
        "boletim_historico",
    )
    return pdf, nome


def emitir_declaracao_pdf(aluno, tipo):
    if not aluno.turma:
        raise DocumentoErro(
            "Este aluno está sem turma. Enturme o aluno antes de emitir a declaração.",
            400,
        )
    escola = _escola_ctx(aluno)
    if tipo == "cursando":
        titulo = "DECLARAÇÃO DE MATRÍCULA"
        texto = (
            "Declaramos para os devidos fins que o(a) aluno(a) <b>%s</b> "
            "está regularmente matriculado(a) e frequentando o ano letivo de %s, "
            "na turma <b>%s</b> desta instituição de ensino."
        ) % (
            _xml(aluno.nome),
            aluno.turma.ano_letivo,
            _xml(aluno.turma.nome),
        )
    elif tipo == "conclusao":
        from src.historico import listar_historicos_anuais_do_aluno
        hists = listar_historicos_anuais_do_aluno(aluno.id)
        tem_fechamento = any(h.get("documento") == "boletim" for h in hists)
        if not tem_fechamento:
            raise DocumentoErro(
                "Não há encerramento de Ensino Fundamental registrado para emitir "
                "declaração de conclusão.",
                400,
            )
        titulo = "DECLARAÇÃO DE CONCLUSÃO"
        texto = (
            "Declaramos para os devidos fins que o(a) aluno(a) <b>%s</b> "
            "concluiu o <b>%s</b> referente ao ano letivo de %s, "
            "conforme registro de encerramento desta instituição."
        ) % (
            _xml(aluno.nome),
            _xml(aluno.turma.nome),
            aluno.turma.ano_letivo,
        )
    else:
        raise DocumentoErro("Tipo de declaração não suportado.", 400)
    pdf = _gerar(montar_pdf_declaracao, {
        "titulo": titulo, "texto": texto, "escola": escola,
    })
    prefixo = "declaracao-matricula" if tipo == "cursando" else "declaracao-conclusao"
    nome = slug_arquivo(aluno.nome, prefixo, aluno.cpf)
    _auditar_emissao(
        DOCUMENTO_DECLARACAO_EMITIDA, aluno.id, escola.get("id"), "declaracao_%s" % tipo,
    )
    return pdf, nome


def _xml(texto):
    return (
        str(texto or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def emitir_parecer_pdf(aluno):
    if not aluno.turma:
        raise DocumentoErro(
            "Este aluno está sem turma. Enturme o aluno antes de emitir o parecer.",
            400,
        )
    if aluno.turma.etapa_ensino != "infantil":
        raise DocumentoErro(MSG_PARECER_NAO_EI, 400)
    mat = matricula_cursando_na_turma(aluno.id, aluno.turma_id)
    if mat is None:
        mat = resolver_matricula_academica(aluno.id)
        if mat is not None and mat.turma_id != aluno.turma_id:
            mat = None
    rels = pareceres_da_matricula(mat) if mat is not None else []
    por_bim = {1: [], 2: [], 3: [], 4: []}
    for r in rels:
        if r.bimestre in por_bim:
            por_bim[r.bimestre].append({
                "eixo": r.eixo, "avaliacao": r.avaliacao, "observacao": r.observacao,
            })
    pdf = _gerar(montar_pdf_parecer, {
        "titulo": "RELATÓRIO DESCRITIVO DE DESENVOLVIMENTO",
        "aluno_nome": aluno.nome,
        "turma_nome": aluno.turma.nome,
        "ano_letivo": aluno.turma.ano_letivo,
        "escola": _escola_ctx(aluno),
        "pareceres": por_bim,
        **_assinantes_parecer_atual(aluno),
    })
    nome = slug_arquivo(aluno.nome, "parecer-educacao-infantil", aluno.cpf)
    _auditar_emissao(
        DOCUMENTO_PARECER_EMITIDO, aluno.id, _escola_ctx(aluno).get("id"), "parecer",
    )
    return pdf, nome


def emitir_parecer_historico_pdf(hist):
    dados = carregar_dados_fechamento(hist)
    if not historico_e_infantil(hist, dados):
        raise DocumentoErro(MSG_PARECER_NAO_EI, 400)
    if not snapshot_suficiente_para_parecer(hist, dados):
        raise DocumentoErro(MSG_LEGADO_INSUFICIENTE, 400)
    ident = identidade_congelada(hist, dados)
    pdf = _gerar(montar_pdf_parecer, {
        "titulo": "RELATÓRIO DESCRITIVO DE DESENVOLVIMENTO",
        "aluno_nome": ident.get("aluno_nome"),
        "turma_nome": ident.get("turma_nome"),
        "ano_letivo": ident.get("ano_letivo"),
        "escola": {
            "id": ident.get("escola_id"),
            "nome": ident.get("escola_nome"),
            "inep": ident.get("escola_inep"),
        },
        "pareceres": pareceres_do_snapshot(dados),
        "referencia": (
            "Documento referente ao ano letivo de %s" % ident.get("ano_letivo")
            if ident.get("ano_letivo") is not None else ""
        ),
    })
    nome = slug_arquivo(ident.get("aluno_nome"), "parecer-historico")
    _auditar_emissao(
        DOCUMENTO_PARECER_EMITIDO,
        ident.get("aluno_id") or hist.aluno_id,
        escola_id_para_acl(hist),
        "parecer_historico",
    )
    return pdf, nome


def emitir_comprovante_pdf(aluno):
    if not aluno.turma:
        raise DocumentoErro(
            "Este aluno está sem turma. Enturme o aluno antes de emitir a ficha de matrícula.",
            400,
        )
    turma_ano = "%s (%s)" % (aluno.turma.nome, aluno.turma.ano_letivo)
    pdf = _gerar(montar_pdf_matricula, {
        "escola": _escola_ctx(aluno),
        "aluno": {
            "nome": aluno.nome,
            "data_nascimento": aluno.data_nascimento,
            "local_nascimento": aluno.local_nascimento,
            "turma_ano": turma_ano,
            "endereco": aluno.endereco,
            "telefone": aluno.telefone,
            "nome_mae": aluno.nome_mae,
            "prof_mae": aluno.prof_mae,
            "nome_pai": aluno.nome_pai,
            "prof_pai": aluno.prof_pai,
            "tem_alergia": aluno.tem_alergia,
            "tem_deficiencia": aluno.tem_deficiencia,
            "rota_transporte": aluno.rota_transporte,
            "nis_bolsa": aluno.nis_bolsa,
        },
    })
    nome = slug_arquivo(aluno.nome, "ficha-matricula", aluno.cpf)
    _auditar_emissao(
        DOCUMENTO_MATRICULA_EMITIDA, aluno.id, _escola_ctx(aluno).get("id"), "ficha_matricula",
    )
    return pdf, nome


def emitir_rendimento_pdf(turma, disciplina):
    from src.matriculas import matriculas_da_turma

    escola = turma.escola
    alocacao = Alocacao.query.filter_by(
        turma_id=turma.id, disciplina_id=disciplina.id,
    ).first()
    nome_professor = (
        alocacao.professor.nome if alocacao and alocacao.professor else None
    )
    alunos = []
    if turma.etapa_ensino == "fundamental":
        for matricula in matriculas_da_turma(turma.id):
            aluno = matricula.aluno
            if aluno is None:
                continue
            dados = calcular_notas_matricula(matricula.id, disciplina.id)
            dados = dict(dados)
            dados["nome"] = aluno.nome
            alunos.append(dados)
    pdf = _gerar(montar_pdf_rendimento, {
        "escola": {
            "id": escola.id if escola else None,
            "nome": escola.nome if escola else None,
            "inep": escola.inep if escola else None,
        },
        "turma_nome": turma.nome,
        "disciplina_nome": disciplina.nome,
        "professor_nome": nome_professor,
        "alunos": alunos,
    })
    nome = slug_arquivo(
        "%s-%s" % (turma.nome, disciplina.nome), "ficha-rendimento",
    )
    registrar_auditoria(
        DOCUMENTO_RENDIMENTO_EMITIDO,
        "Turma",
        entidade_id=turma.id,
        escola_id=turma.escola_id,
        dados_anteriores=None,
        dados_novos={
            "turma_id": turma.id,
            "disciplina_id": disciplina.id,
            "escola_id": turma.escola_id,
            "tipo": "ficha_rendimento",
            "formato": "pdf",
        },
    )
    db.session.commit()
    return pdf, nome
