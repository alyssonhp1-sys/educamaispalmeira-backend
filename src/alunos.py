"""Regras cadastrais do aluno (RF02 / RF03).

Situação acadêmica (APROVADO / REPROVADO / EM RECUPERAÇÃO) permanece em src.notas.
Aqui só a situação cadastral: ativo, inativo, transferido.
"""
from datetime import datetime

from src.models import Aluno

SITUACAO_ATIVO = "ativo"
SITUACAO_INATIVO = "inativo"
SITUACAO_TRANSFERIDO = "transferido"
MSG_ALUNO_INATIVADO = (
    "Aluno inativado. O registro, o CPF, as notas e o histórico foram preservados."
)
# "cursando" é o valor legado equivalente a ativo.
SITUACOES_ATIVAS = frozenset({SITUACAO_ATIVO, "cursando"})


def normalizar_cpf(valor):
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def cpf_valido(valor):
    return len(normalizar_cpf(valor)) == 11


def texto(valor):
    if valor is None:
        return ""
    return str(valor).strip()


def tem_responsavel(dados, aluno=None):
    mae = dados.get("nome_mae")
    pai = dados.get("nome_pai")
    if mae is None and aluno is not None:
        mae = aluno.nome_mae
    if pai is None and aluno is not None:
        pai = aluno.nome_pai
    return bool(texto(mae) or texto(pai))


def aluno_esta_ativo(aluno):
    return (aluno.situacao or "").lower() in SITUACOES_ATIVAS


def inativar_aluno(aluno):
    """Inativa o cadastro sem apagar notas, faltas, frequência ou histórico.

    Fecha a matrícula vigente (vínculo) como inativa; Aluno.situacao é cadastral.
    """
    from src.auditoria import ALUNO_INATIVADO, registrar_auditoria
    from src.matriculas import VINCULO_INATIVO, encerrar_vinculo_vigente, matriculas_cursando

    vigentes = list(matriculas_cursando(aluno))
    sit_anterior = aluno.situacao
    turma_anterior = aluno.turma_id
    ja_inativo = (sit_anterior or "").lower() == SITUACAO_INATIVO
    if ja_inativo and not vigentes and turma_anterior is None:
        return aluno

    matricula_ids = [m.id for m in vigentes]
    situacao_vinculo_anterior = vigentes[0].situacao if vigentes else None
    encerrar_vinculo_vigente(aluno, VINCULO_INATIVO)
    aluno.situacao = SITUACAO_INATIVO
    aluno.turma_id = None
    registrar_auditoria(
        ALUNO_INATIVADO,
        "Aluno",
        entidade_id=aluno.id,
        escola_id=aluno.escola_id,
        dados_anteriores={
            "aluno_id": aluno.id,
            "matricula_ids": matricula_ids,
            "turma_id": turma_anterior,
            "situacao": sit_anterior,
            "situacao_vinculo": situacao_vinculo_anterior,
        },
        dados_novos={
            "aluno_id": aluno.id,
            "matricula_ids": matricula_ids,
            "turma_id": None,
            "situacao": SITUACAO_INATIVO,
            "situacao_vinculo": VINCULO_INATIVO,
        },
    )
    return aluno


def ocupantes_ativos(turma):
    """Ocupação atual: matrículas cursando (fonte do vínculo na transição)."""
    from src.matriculas import contar_matriculas_cursando
    return contar_matriculas_cursando(turma)


def turma_lotada(turma):
    if turma.capacidade is None:
        return False
    return ocupantes_ativos(turma) >= int(turma.capacidade)


def parse_capacidade(bruto):
    if bruto is None or str(bruto).strip() == "":
        return None, None
    try:
        valor = int(float(str(bruto).replace(",", ".")))
    except (TypeError, ValueError):
        return None, "Capacidade inválida."
    if valor < 1:
        return None, "Capacidade deve ser um número positivo, ou vazia para ilimitada."
    return valor, None


def parse_data(bruto):
    if not bruto:
        return None
    try:
        return datetime.strptime(str(bruto)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def aluno_com_cpf(cpf_norm, ignorar_id=None):
    q = Aluno.query.filter_by(cpf=cpf_norm)
    if ignorar_id is not None:
        q = q.filter(Aluno.id != ignorar_id)
    encontrado = q.first()
    if encontrado:
        return encontrado
    for outro in Aluno.query.all():
        if ignorar_id is not None and outro.id == ignorar_id:
            continue
        if normalizar_cpf(outro.cpf) == cpf_norm:
            return outro
    return None


def aplicar_ficha(aluno, dados):
    aluno.nome = texto(dados.get("nome"))
    aluno.cpf = normalizar_cpf(dados.get("cpf"))
    if "data_nascimento" in dados or aluno.data_nascimento is None:
        dn = parse_data(dados.get("data_nascimento"))
        if dados.get("data_nascimento"):
            aluno.data_nascimento = dn
    for origem, destino in (
        ("local_nascimento", "local_nascimento"),
        ("endereco", "endereco"),
        ("telefone", "telefone"),
        ("nome_mae", "nome_mae"),
        ("prof_mae", "prof_mae"),
        ("nome_pai", "nome_pai"),
        ("prof_pai", "prof_pai"),
        ("rota_transporte", "rota_transporte"),
        ("nis_bolsa", "nis_bolsa"),
    ):
        if origem in dados:
            setattr(aluno, destino, texto(dados.get(origem)) or None)
    if "tem_alergia" in dados or "alergia_detalhe" in dados:
        aluno.tem_alergia = texto(dados.get("tem_alergia") or dados.get("alergia_detalhe")) or None
    if "tem_deficiencia" in dados or "deficiencia_detalhe" in dados:
        aluno.tem_deficiencia = texto(dados.get("tem_deficiencia") or dados.get("deficiencia_detalhe")) or None
