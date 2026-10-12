"""cria matriz curricular por vigencia

Revision ID: f8c20b3e4a91
Revises: e7a42c9d1b80
Create Date: 2026-09-03 16:00:00.000000

Matriz curricular municipal com vigência (2026+). Não altera snapshots
acadêmicos. Se as tabelas já existirem (create_all em testes), não recria.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "f8c20b3e4a91"
down_revision = "e7a42c9d1b80"
branch_labels = None
depends_on = None


def _tem_tabela(nome):
    return inspect(op.get_bind()).has_table(nome)


def upgrade():
    if not _tem_tabela("matriz_curricular"):
        op.create_table(
            "matriz_curricular",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("nome", sa.String(length=160), nullable=False),
            sa.Column("ano_inicio", sa.Integer(), nullable=False),
            sa.Column("ano_fim", sa.Integer(), nullable=True),
            sa.Column("etapa", sa.String(length=40), nullable=False),
            sa.Column("carga_horaria_semanal", sa.Integer(), nullable=True),
            sa.Column("dias_letivos", sa.Integer(), nullable=True),
            sa.Column("carga_horaria_anual", sa.Integer(), nullable=True),
            sa.Column("ativa", sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "nome", "ano_inicio", "etapa", name="uq_matriz_nome_inicio_etapa",
            ),
        )
        op.create_index(
            "ix_matriz_vigencia",
            "matriz_curricular",
            ["ano_inicio", "ano_fim", "etapa"],
        )
    if not _tem_tabela("matriz_componente"):
        op.create_table(
            "matriz_componente",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("matriz_id", sa.Integer(), nullable=False),
            sa.Column("nome", sa.String(length=160), nullable=False),
            sa.Column("categoria", sa.String(length=40), nullable=False),
            sa.Column("area", sa.String(length=80), nullable=True),
            sa.Column("ano_serie", sa.Integer(), nullable=False),
            sa.Column("aulas_semanais", sa.Integer(), nullable=True),
            sa.Column("carga_horaria_anual", sa.Integer(), nullable=True),
            sa.Column("ordem", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(["matriz_id"], ["matriz_curricular.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "matriz_id", "ano_serie", "nome", name="uq_matriz_comp_serie_nome",
            ),
        )
        op.create_index(
            "ix_matriz_comp_matriz",
            "matriz_componente",
            ["matriz_id"],
        )


def downgrade():
    if _tem_tabela("matriz_componente"):
        op.drop_table("matriz_componente")
    if _tem_tabela("matriz_curricular"):
        op.drop_table("matriz_curricular")
