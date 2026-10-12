"""cria tabela audit_log

Revision ID: d8f31a6b4c20
Revises: c4e91f7a2b08
Create Date: 2026-09-01 09:30:00.000000

Infraestrutura de auditoria (Estágio 4). Não altera tabelas acadêmicas.

Se `audit_log` já existir (create_all no alinhamento de SQLite legado),
não recria tabela nem índices — o stamp do baseline + upgrade precisa
chegar ao head sem colidir.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "d8f31a6b4c20"
down_revision = "c4e91f7a2b08"
branch_labels = None
depends_on = None

TABELA = "audit_log"
INDICES = (
    ("ix_audit_log_usuario_id", ["usuario_id"]),
    ("ix_audit_log_escola_id", ["escola_id"]),
    ("ix_audit_log_data_hora", ["data_hora"]),
    ("ix_audit_log_entidade_id", ["entidade", "entidade_id"]),
)


def _tem_tabela(nome):
    return inspect(op.get_bind()).has_table(nome)


def upgrade():
    if _tem_tabela(TABELA):
        return
    op.create_table(
        TABELA,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("usuario_id", sa.Integer(), nullable=True),
        sa.Column("acao", sa.String(length=80), nullable=False),
        sa.Column("entidade", sa.String(length=80), nullable=False),
        sa.Column("entidade_id", sa.Integer(), nullable=True),
        sa.Column("escola_id", sa.Integer(), nullable=True),
        sa.Column("data_hora", sa.DateTime(), nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("dados_anteriores", sa.JSON(), nullable=True),
        sa.Column("dados_novos", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["usuario_id"], ["usuario.id"],
            name="fk_audit_log_usuario_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["escola_id"], ["escola.id"],
            name="fk_audit_log_escola_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for nome, colunas in INDICES:
        op.create_index(nome, TABELA, colunas, unique=False)


def downgrade():
    if not _tem_tabela(TABELA):
        return
    op.drop_table(TABELA)
