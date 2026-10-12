"""adiciona constraints de integridade academica

Revision ID: c4e91f7a2b08
Revises: 6c0839c174ab
Create Date: 2026-08-31 08:40:00.000000

UNIQUE parciais comprovadas no modelo operacional.

Não altera tabelas, colunas nem dados. Não deduplica.
Se houver duplicidade incompatível, aborta ANTES de criar qualquer índice.

sqlite_where e postgresql_where carregam a mesma condição: o dialect emite
o índice parcial adequado em SQLite e em PostgreSQL vazio.
"""
from alembic import op
import sqlalchemy as sa


revision = "c4e91f7a2b08"
down_revision = "6c0839c174ab"
branch_labels = None
depends_on = None

_WHERE_MATRICULA = sa.text("matricula_id IS NOT NULL")
_WHERE_MATRICULA_AULA = sa.text(
    "matricula_id IS NOT NULL AND registro_aula_id IS NOT NULL"
)

INDICES = (
    (
        "uq_registro_nota_matricula_disc_bim",
        "registro_nota",
        ["matricula_id", "disciplina_id", "bimestre"],
        _WHERE_MATRICULA,
    ),
    (
        "uq_registro_falta_matricula_disc_bim",
        "registro_falta",
        ["matricula_id", "disciplina_id", "bimestre"],
        _WHERE_MATRICULA,
    ),
    (
        "uq_registro_frequencia_matricula_aula",
        "registro_frequencia",
        ["matricula_id", "registro_aula_id"],
        _WHERE_MATRICULA_AULA,
    ),
    (
        "uq_relatorio_descritivo_matricula_eixo_bim",
        "relatorio_descritivo",
        ["matricula_id", "eixo", "bimestre"],
        _WHERE_MATRICULA,
    ),
    (
        "uq_historico_anual_matricula",
        "historico_anual",
        ["matricula_id"],
        _WHERE_MATRICULA,
    ),
)


def upgrade():
    bind = op.get_bind()
    from src.integridade import exigir_sem_duplicidade_incompativel

    exigir_sem_duplicidade_incompativel(bind)
    for nome, tabela, colunas, where in INDICES:
        op.create_index(
            nome,
            tabela,
            colunas,
            unique=True,
            sqlite_where=where,
            postgresql_where=where,
        )


def downgrade():
    for nome, tabela, _colunas, _where in reversed(INDICES):
        op.drop_index(nome, table_name=tabela)
