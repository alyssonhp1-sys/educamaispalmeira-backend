"""cria trajetoria escolar legada

Revision ID: b7e13c8a4d02
Revises: f8c20b3e4a91
Create Date: 2026-09-03 21:40:00.000000

Trajetória anterior à adoção do Educa+. Não altera HistoricoAnual.
Se as tabelas já existirem (create_all em testes), não recria.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "b7e13c8a4d02"
down_revision = "f8c20b3e4a91"
branch_labels = None
depends_on = None


def _tem_tabela(nome):
    return inspect(op.get_bind()).has_table(nome)


def upgrade():
    if not _tem_tabela("trajetoria_escolar_legada"):
        op.create_table(
            "trajetoria_escolar_legada",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("aluno_id", sa.Integer(), nullable=False),
            sa.Column("ano_letivo", sa.Integer(), nullable=False),
            sa.Column("serie", sa.Integer(), nullable=False),
            sa.Column("etapa", sa.String(length=40), nullable=True),
            sa.Column("estabelecimento", sa.String(length=160), nullable=True),
            sa.Column("municipio", sa.String(length=100), nullable=True),
            sa.Column("uf", sa.String(length=2), nullable=True),
            sa.Column("resultado", sa.String(length=80), nullable=True),
            sa.Column("carga_horaria_total", sa.Integer(), nullable=True),
            sa.Column("observacao", sa.Text(), nullable=True),
            sa.Column("origem", sa.String(length=40), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("atualizado_em", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["aluno_id"], ["aluno.id"], name="fk_trajetoria_legada_aluno_id",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "aluno_id", "ano_letivo", "serie",
                name="uq_trajetoria_legada_aluno_ano_serie",
            ),
        )
        op.create_index(
            "ix_trajetoria_legada_aluno",
            "trajetoria_escolar_legada",
            ["aluno_id"],
        )
    if not _tem_tabela("trajetoria_componente_legado"):
        op.create_table(
            "trajetoria_componente_legado",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("trajetoria_id", sa.Integer(), nullable=False),
            sa.Column("nome", sa.String(length=160), nullable=False),
            sa.Column("carga_horaria", sa.Integer(), nullable=True),
            sa.Column("nota_conceito", sa.String(length=40), nullable=True),
            sa.Column("faltas", sa.Integer(), nullable=True),
            sa.Column("frequencia", sa.String(length=40), nullable=True),
            sa.Column("categoria", sa.String(length=40), nullable=True),
            sa.Column("ordem", sa.Integer(), nullable=False),
            sa.ForeignKeyConstraint(
                ["trajetoria_id"],
                ["trajetoria_escolar_legada.id"],
                name="fk_trajetoria_comp_legado_trajetoria_id",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_trajetoria_comp_legado",
            "trajetoria_componente_legado",
            ["trajetoria_id"],
        )


def downgrade():
    if _tem_tabela("trajetoria_componente_legado"):
        op.drop_table("trajetoria_componente_legado")
    if _tem_tabela("trajetoria_escolar_legada"):
        op.drop_table("trajetoria_escolar_legada")
