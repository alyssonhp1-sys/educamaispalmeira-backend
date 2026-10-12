"""Auditoria de duplicidades acadêmicas. Somente leitura.

Não corrige, não apaga, não escolhe vencedor e não altera matricula_id.
Saída usa IDs e chaves técnicas — sem nome, CPF ou outros PII.
"""
from sqlalchemy import text

MSG_INTEGRIDADE_DUPLICADA = (
    "O banco recusou um lançamento duplicado da mesma chave acadêmica. "
    "Nenhum dado extra foi gravado nesta operação."
)

MSG_MIGRATION_DUPLICIDADE = (
    "Migration abortada: duplicidade incompatível em %s (%s grupo(s)). "
    "Nenhum índice UNIQUE foi criado e nenhum registro foi alterado. "
    "Execute: python -m flask educamais auditar-integridade. "
    "Correção de dados é decisão operacional separada."
)

# Índices UNIQUE parciais deste bloco (nomes estáveis).
INDICE_NOTA = "uq_registro_nota_matricula_disc_bim"
INDICE_FALTA = "uq_registro_falta_matricula_disc_bim"
INDICE_FREQUENCIA = "uq_registro_frequencia_matricula_aula"
INDICE_PARECER = "uq_relatorio_descritivo_matricula_eixo_bim"
INDICE_HISTORICO = "uq_historico_anual_matricula"

INDICES_INTEGRIDADE = (
    INDICE_NOTA,
    INDICE_FALTA,
    INDICE_FREQUENCIA,
    INDICE_PARECER,
    INDICE_HISTORICO,
)

# Checagens que a migration exige antes de criar UNIQUE.
# GROUP BY no SQL trata NULL de disciplina/eixo como iguais (auditoria).
SQL_INCOMPATIVEIS = (
    (
        "registro_nota",
        INDICE_NOTA,
        "SELECT matricula_id, disciplina_id, bimestre, COUNT(*) AS n "
        "FROM registro_nota WHERE matricula_id IS NOT NULL "
        "GROUP BY matricula_id, disciplina_id, bimestre HAVING COUNT(*) > 1",
        ("matricula_id", "disciplina_id", "bimestre"),
    ),
    (
        "registro_falta",
        INDICE_FALTA,
        "SELECT matricula_id, disciplina_id, bimestre, COUNT(*) AS n "
        "FROM registro_falta WHERE matricula_id IS NOT NULL "
        "GROUP BY matricula_id, disciplina_id, bimestre HAVING COUNT(*) > 1",
        ("matricula_id", "disciplina_id", "bimestre"),
    ),
    (
        "registro_frequencia",
        INDICE_FREQUENCIA,
        "SELECT matricula_id, registro_aula_id, COUNT(*) AS n "
        "FROM registro_frequencia "
        "WHERE matricula_id IS NOT NULL AND registro_aula_id IS NOT NULL "
        "GROUP BY matricula_id, registro_aula_id HAVING COUNT(*) > 1",
        ("matricula_id", "registro_aula_id"),
    ),
    (
        "relatorio_descritivo",
        INDICE_PARECER,
        "SELECT matricula_id, eixo, bimestre, COUNT(*) AS n "
        "FROM relatorio_descritivo WHERE matricula_id IS NOT NULL "
        "GROUP BY matricula_id, eixo, bimestre HAVING COUNT(*) > 1",
        ("matricula_id", "eixo", "bimestre"),
    ),
    (
        "historico_anual",
        INDICE_HISTORICO,
        "SELECT matricula_id, COUNT(*) AS n FROM historico_anual "
        "WHERE matricula_id IS NOT NULL "
        "GROUP BY matricula_id HAVING COUNT(*) > 1",
        ("matricula_id",),
    ),
)

SQL_MATRICULA_DUPLICADA = (
    "SELECT aluno_id, turma_id, COUNT(*) AS n FROM matricula "
    "GROUP BY aluno_id, turma_id HAVING COUNT(*) > 1"
)

SQL_NULL_MATRICULA = (
    ("registro_nota", "SELECT COUNT(*) FROM registro_nota WHERE matricula_id IS NULL"),
    ("registro_falta", "SELECT COUNT(*) FROM registro_falta WHERE matricula_id IS NULL"),
    ("registro_frequencia", "SELECT COUNT(*) FROM registro_frequencia WHERE matricula_id IS NULL"),
    ("relatorio_descritivo", "SELECT COUNT(*) FROM relatorio_descritivo WHERE matricula_id IS NULL"),
    ("historico_anual", "SELECT COUNT(*) FROM historico_anual WHERE matricula_id IS NULL"),
)

COLUNAS_ID = {
    "registro_nota": ("matricula_id", "disciplina_id", "bimestre"),
    "registro_falta": ("matricula_id", "disciplina_id", "bimestre"),
    "registro_frequencia": ("matricula_id", "registro_aula_id"),
    "relatorio_descritivo": ("matricula_id", "eixo", "bimestre"),
    "historico_anual": ("matricula_id",),
    "matricula": ("aluno_id", "turma_id"),
}


class DuplicidadeIncompativel(RuntimeError):
    """Há grupos duplicados que impedem o UNIQUE parcial."""


def _executar(bind, sql, params=None):
    if params is None:
        return list(bind.execute(text(sql)))
    return list(bind.execute(text(sql), params))


def _bind(bind=None):
    if bind is not None:
        return bind
    from src import db
    return db.session


def _inspecionavel(bind):
    if hasattr(bind, "get_bind"):
        try:
            got = bind.get_bind()
            if got is not None:
                return got
        except Exception:
            pass
    return bind


def _tabela_existe(bind, nome):
    from sqlalchemy import inspect
    return inspect(_inspecionavel(bind)).has_table(nome)


def _ids_do_grupo(bind, tabela, chaves, row):
    colunas = COLUNAS_ID.get(tabela)
    if not colunas:
        return []
    partes = []
    params = {}
    for i, chave in enumerate(chaves):
        valor = row[i]
        if valor is None:
            partes.append("%s IS NULL" % chave)
        else:
            partes.append("%s = :p%s" % (chave, i))
            params["p%s" % i] = valor
    sql = "SELECT id FROM %s WHERE %s ORDER BY id" % (tabela, " AND ".join(partes))
    try:
        return [r[0] for r in _executar(bind, sql, params)]
    except Exception:
        return []


def _grupos(bind, tabela, sql, chaves):
    if not _tabela_existe(bind, tabela):
        return []
    grupos = []
    for row in _executar(bind, sql):
        n = int(row[-1])
        chave = {chaves[i]: row[i] for i in range(len(chaves))}
        grupos.append({
            "tabela": tabela,
            "chave": chave,
            "n": n,
            "ids": _ids_do_grupo(bind, tabela, chaves, row),
        })
    return grupos


def duplicidades_incompativeis(bind=None):
    """Grupos que a migration UNIQUE recusaria. Sem PII."""
    bind = _bind(bind)
    encontrados = []
    for tabela, indice, sql, chaves in SQL_INCOMPATIVEIS:
        for g in _grupos(bind, tabela, sql, chaves):
            g["indice"] = indice
            encontrados.append(g)
    for g in _grupos(
        bind, "matricula", SQL_MATRICULA_DUPLICADA, ("aluno_id", "turma_id"),
    ):
        g["indice"] = "uq_matricula_aluno_turma"
        encontrados.append(g)
    return encontrados


def contagens_null_matricula(bind=None):
    bind = _bind(bind)
    out = {}
    for tabela, sql in SQL_NULL_MATRICULA:
        if not _tabela_existe(bind, tabela):
            out[tabela] = None
            continue
        out[tabela] = int(_executar(bind, sql)[0][0])
    return out


def auditar_integridade(bind=None):
    """Relatório completo, somente leitura."""
    bind = _bind(bind)
    incompativeis = duplicidades_incompativeis(bind)
    nulos = contagens_null_matricula(bind)
    historico_null = nulos.get("historico_anual") or 0
    return {
        "incompativeis": incompativeis,
        "nulos_matricula": nulos,
        "historico_null_permitido": historico_null,
        "somente_leitura": True,
    }


def exigir_sem_duplicidade_incompativel(bind=None):
    """Abortar migration se UNIQUE falharia. Não altera dados."""
    grupos = duplicidades_incompativeis(bind)
    if not grupos:
        return
    por_tabela = {}
    for g in grupos:
        por_tabela.setdefault(g["tabela"], 0)
        por_tabela[g["tabela"]] += 1
    rotulo = ", ".join("%s=%s" % item for item in sorted(por_tabela.items()))
    raise DuplicidadeIncompativel(MSG_MIGRATION_DUPLICIDADE % (rotulo, len(grupos)))


def formatar_relatorio(relatorio):
    linhas = [
        "Auditoria de integridade (somente leitura; sem PII).",
        "Nada foi corrigido, apagado ou associado.",
    ]
    incomp = relatorio.get("incompativeis") or []
    if not incomp:
        linhas.append("Duplicidades incompatíveis com UNIQUE moderno: 0.")
    else:
        linhas.append("Duplicidades incompatíveis: %s grupo(s)." % len(incomp))
        for g in incomp:
            ids = ",".join(str(i) for i in g.get("ids") or [])
            chave = " ".join("%s=%s" % (k, v) for k, v in g["chave"].items())
            linhas.append(
                "  %s n=%s %s ids=%s" % (g["tabela"], g["n"], chave, ids or "-")
            )
    linhas.append("Registros com matricula_id NULL (legado permitido):")
    for tabela, n in (relatorio.get("nulos_matricula") or {}).items():
        linhas.append("  %s: %s" % (tabela, n if n is not None else "(tabela ausente)"))
    return "\n".join(linhas)
