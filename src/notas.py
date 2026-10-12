"""Motor oficial de notas do Educa+ Palmeira.

Média mínima vigente vem da configuração acadêmica persistida.
Sem registro no banco, usa 6,0 (comportamento histórico).
Arredondamento: 1 casa decimal, metade para cima (6,25 → 6,3).
"""
from decimal import Decimal, ROUND_HALF_UP

from flask import has_app_context

from src import db
from src.models import (
    Aluno,
    RegistroNota,
    ConfiguracaoAcademica,
    MEDIA_MINIMA_PADRAO,
)


def arredondar(valor):
    if valor is None:
        return None
    return float(Decimal(str(valor)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def validar_nota_0_a_10(valor):
    """Retorna (float, None) se válido, ou (None, mensagem) se inválido."""
    if valor is None or str(valor).strip() == "":
        return None, "Nota inválida. Informe um número de 0 a 10."
    texto = str(valor).strip().replace(",", ".")
    try:
        numero = float(texto)
    except (TypeError, ValueError):
        return None, "Nota inválida. Informe um número de 0 a 10."
    if numero < 0 or numero > 10:
        return None, "Nota inválida. Informe um número de 0 a 10."
    return numero, None


def garantir_configuracao_padrao():
    """Garante uma linha de configuração. Não apaga nem redefine valor já persistido."""
    cfg = ConfiguracaoAcademica.query.order_by(ConfiguracaoAcademica.id).first()
    if cfg is None:
        cfg = ConfiguracaoAcademica(media_minima=MEDIA_MINIMA_PADRAO)
        db.session.add(cfg)
        db.session.commit()
    return cfg


def media_minima_vigente():
    if not has_app_context():
        return MEDIA_MINIMA_PADRAO
    cfg = ConfiguracaoAcademica.query.order_by(ConfiguracaoAcademica.id).first()
    if cfg is None or cfg.media_minima is None:
        return MEDIA_MINIMA_PADRAO
    return float(cfg.media_minima)


def _corte(media_minima):
    if media_minima is None:
        return media_minima_vigente()
    return float(media_minima)


def payload_configuracao(cfg=None):
    if cfg is None:
        media = media_minima_vigente()
    else:
        media = float(cfg.media_minima)
    return {"media_minima": media, "mediaMinima": media}


def _substitui_menor(a, b, recup):
    if recup is None or recup <= min(a, b):
        return (a + b) / 2
    if a < b:
        return (recup + b) / 2
    return (a + recup) / 2


def calc_bimestre(n1, n2, rp, media_minima=None):
    corte = _corte(media_minima)
    if n1 is None or n2 is None:
        return {"mb": None, "precisa_rp": False}
    media = (n1 + n2) / 2
    precisa = media < corte
    mb = _substitui_menor(n1, n2, rp) if precisa else media
    return {"mb": arredondar(mb), "precisa_rp": precisa}


def calc_semestre(m1, m2, rs, media_minima=None):
    corte = _corte(media_minima)
    if m1 is None or m2 is None:
        return {"ms": None, "precisa_rs": False}
    media = (m1 + m2) / 2
    precisa = media < corte
    ms = _substitui_menor(m1, m2, rs) if precisa else media
    return {"ms": arredondar(ms), "precisa_rs": precisa}


def calc_anual(ms1, ms2, pf, media_minima=None):
    corte = _corte(media_minima)
    if ms1 is None or ms2 is None:
        return {"ma": None, "situacao": "CURSANDO", "precisa_pf": False}
    ma = arredondar((ms1 + ms2) / 2)
    if ma < corte:
        if pf is not None:
            if pf > min(ms1, ms2):
                ma = _substitui_menor(ms1, ms2, pf)
            ma = arredondar(ma)
            situacao = "APROVADO" if ma >= corte else "REPROVADO"
            return {"ma": ma, "situacao": situacao, "precisa_pf": True}
        return {"ma": ma, "situacao": "EM RECUPERAÇÃO", "precisa_pf": True}
    return {"ma": ma, "situacao": "APROVADO", "precisa_pf": False}


def calcular_de_notas(notas_por_codigo, transferido=False, total_faltas=0, media_minima=None):
    """Calcula o boletim a partir de um mapa {codigo_bimestre: valor}."""
    corte = _corte(media_minima)
    get_val = notas_por_codigo.get

    b1 = calc_bimestre(get_val(11), get_val(12), get_val(13), corte)
    b2 = calc_bimestre(get_val(21), get_val(22), get_val(23), corte)
    b3 = calc_bimestre(get_val(31), get_val(32), get_val(33), corte)
    b4 = calc_bimestre(get_val(41), get_val(42), get_val(43), corte)
    s1 = calc_semestre(b1["mb"], b2["mb"], get_val(51), corte)
    s2 = calc_semestre(b3["mb"], b4["mb"], get_val(52), corte)
    an = calc_anual(s1["ms"], s2["ms"], get_val(99), corte)

    situacao = "TRANSFERIDO" if transferido else an["situacao"]

    return {
        "mb1": b1["mb"],
        "mb2": b2["mb"],
        "mb3": b3["mb"],
        "mb4": b4["mb"],
        "rs1": get_val(51),
        "rs2": get_val(52),
        "ms1": s1["ms"],
        "ms2": s2["ms"],
        "final": get_val(99),
        "ma": an["ma"],
        "situacao": situacao,
        "faltas": total_faltas,
        "media_minima": corte,
        "precisa_rp1": b1["precisa_rp"],
        "precisa_rp2": b2["precisa_rp"],
        "precisa_rp3": b3["precisa_rp"],
        "precisa_rp4": b4["precisa_rp"],
        "precisa_rs1": s1["precisa_rs"],
        "precisa_rs2": s2["precisa_rs"],
        "precisa_pf": an["precisa_pf"],
        "n1": get_val(11),
        "n2": get_val(12),
        "rp1": get_val(13),
        "n3": get_val(21),
        "n4": get_val(22),
        "rp2": get_val(23),
        "n5": get_val(31),
        "n6": get_val(32),
        "rp3": get_val(33),
        "n7": get_val(41),
        "n8": get_val(42),
        "rp4": get_val(43),
    }


def calcular_notas_matricula(matricula_id, disciplina_id):
    """Boletim daquela matrícula + disciplina. Não mistura outros anos."""
    from src.frequencia import total_faltas_efetivas
    from src.models import Matricula

    notas = RegistroNota.query.filter_by(
        matricula_id=matricula_id, disciplina_id=disciplina_id,
    ).all()
    mat = db.session.get(Matricula, matricula_id)
    if not notas and mat is not None:
        n_mats = Matricula.query.filter_by(aluno_id=mat.aluno_id).count()
        if n_mats == 1:
            notas = RegistroNota.query.filter_by(
                aluno_id=mat.aluno_id,
                disciplina_id=disciplina_id,
                matricula_id=None,
            ).all()
    mapa = {n.bimestre: n.valor for n in notas}
    aluno_obj = mat.aluno if mat is not None else None
    transferido = bool(aluno_obj and aluno_obj.situacao == "transferido")
    aluno_id = aluno_obj.id if aluno_obj is not None else None
    total_faltas = total_faltas_efetivas(
        aluno_id, disciplina_id, matricula_id=matricula_id,
    )
    return calcular_de_notas(mapa, transferido=transferido, total_faltas=total_faltas)


def calcular_notas_aluno(aluno_id, disciplina_id, matricula_id=None):
    """Compatibilidade: resolve a matrícula acadêmica atual quando não for informada."""
    from src.frequencia import total_faltas_efetivas
    from src.matriculas import resolver_matricula_academica

    if matricula_id is None:
        mat = resolver_matricula_academica(aluno_id)
        if mat is not None:
            matricula_id = mat.id
    if matricula_id is not None:
        return calcular_notas_matricula(matricula_id, disciplina_id)

    # Sem matrícula resolvida: só lançamentos ainda sem contexto (não mistura anos já associados).
    notas = RegistroNota.query.filter_by(
        aluno_id=aluno_id, disciplina_id=disciplina_id, matricula_id=None,
    ).all()
    mapa = {n.bimestre: n.valor for n in notas}
    aluno_obj = db.session.get(Aluno, aluno_id)
    transferido = bool(aluno_obj and aluno_obj.situacao == "transferido")
    total_faltas = total_faltas_efetivas(aluno_id, disciplina_id)
    return calcular_de_notas(mapa, transferido=transferido, total_faltas=total_faltas)


def _recorte_nota(matricula, disciplina_id, bimestre, valor, tipo, registro_id=None):
    from src.auditoria import campo_celula_nota

    return {
        "matricula_id": matricula.id,
        "disciplina_id": disciplina_id,
        "bimestre": int(bimestre),
        "campo": campo_celula_nota(bimestre),
        "tipo": tipo,
        "valor": valor,
        "registro_id": registro_id,
    }


def upsert_nota_matricula(matricula, disciplina_id, bimestre, valor, tipo="bimestral"):
    """Atualiza ou insere a célula (matrícula, disciplina, bimestre). Não apaga outras."""
    from src.auditoria import (
        NOTA_ALTERADA,
        NOTA_CRIADA,
        escola_id_da_matricula,
        registrar_auditoria,
        valores_iguais,
    )
    from src.matriculas import garantir_aluno_da_matricula

    garantir_aluno_da_matricula(matricula.aluno_id, matricula)
    existentes = RegistroNota.query.filter_by(
        matricula_id=matricula.id,
        disciplina_id=disciplina_id,
        bimestre=bimestre,
    ).all()
    if existentes:
        atual = existentes[0]
        valor_anterior = atual.valor
        tipo_anterior = atual.tipo
        for reg in existentes:
            garantir_aluno_da_matricula(reg.aluno_id, matricula)
            reg.valor = valor
            reg.tipo = tipo
            reg.aluno_id = matricula.aluno_id
        if valores_iguais(valor_anterior, valor) and (tipo_anterior or "") == (tipo or ""):
            return atual
        registrar_auditoria(
            NOTA_ALTERADA,
            "RegistroNota",
            entidade_id=atual.id,
            escola_id=escola_id_da_matricula(matricula),
            dados_anteriores=_recorte_nota(
                matricula, disciplina_id, bimestre, valor_anterior, tipo_anterior, atual.id,
            ),
            dados_novos=_recorte_nota(
                matricula, disciplina_id, bimestre, valor, tipo, atual.id,
            ),
        )
        return atual
    reg = RegistroNota(
        aluno_id=matricula.aluno_id,
        matricula_id=matricula.id,
        disciplina_id=disciplina_id,
        bimestre=bimestre,
        valor=valor,
        tipo=tipo,
    )
    db.session.add(reg)
    db.session.flush()
    registrar_auditoria(
        NOTA_CRIADA,
        "RegistroNota",
        entidade_id=reg.id,
        escola_id=escola_id_da_matricula(matricula),
        dados_anteriores=None,
        dados_novos=_recorte_nota(
            matricula, disciplina_id, bimestre, valor, tipo, reg.id,
        ),
    )
    return reg


CHAVES_RESUMO_MONITORAMENTO = (
    "mb1", "mb2", "mb3", "mb4", "ms1", "ms2", "ma", "situacao",
    "faltas", "media_minima", "rs1", "rs2", "final",
    "precisa_rp1", "precisa_rp2", "precisa_rp3", "precisa_rp4",
    "precisa_rs1", "precisa_rs2", "precisa_pf",
)


def payload_resumo_monitoramento(resumo):
    """Serializa o retorno do motor oficial. Sem recálculo."""
    return {chave: resumo.get(chave) for chave in CHAVES_RESUMO_MONITORAMENTO}


def remover_nota_matricula(matricula, disciplina_id, bimestre):
    """Remove só a célula (matrícula, disciplina, bimestre). Idempotente."""
    from src.auditoria import (
        NOTA_REMOVIDA,
        escola_id_da_matricula,
        registrar_auditoria,
    )
    from src.matriculas import garantir_aluno_da_matricula

    garantir_aluno_da_matricula(matricula.aluno_id, matricula)
    existentes = RegistroNota.query.filter_by(
        matricula_id=matricula.id,
        disciplina_id=disciplina_id,
        bimestre=bimestre,
    ).all()
    if not existentes:
        return
    atual = existentes[0]
    registrar_auditoria(
        NOTA_REMOVIDA,
        "RegistroNota",
        entidade_id=atual.id,
        escola_id=escola_id_da_matricula(matricula),
        dados_anteriores=_recorte_nota(
            matricula, disciplina_id, bimestre, atual.valor, atual.tipo, atual.id,
        ),
        dados_novos=_recorte_nota(
            matricula, disciplina_id, bimestre, None, atual.tipo, atual.id,
        ),
    )
    RegistroNota.query.filter_by(
        matricula_id=matricula.id,
        disciplina_id=disciplina_id,
        bimestre=bimestre,
    ).delete(synchronize_session=False)
