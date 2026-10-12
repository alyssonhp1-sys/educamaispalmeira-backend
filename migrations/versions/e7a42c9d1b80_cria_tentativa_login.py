"""cria tabela tentativa_login

Revision ID: e7a42c9d1b80
Revises: d8f31a6b4c20
Create Date: 2026-09-02 10:30:00.000000

Lockout persistente de login (Bloco 6). Não altera tabelas acadêmicas.

Se `tentativa_login` já existir (create_all em testes), não recria.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "e7a42c9d1b80"
down_revision = "d8f31a6b4c20"
branch_labels = None
depends_on = None

TABELA = "tentativa_login"


def _tem_tabela(nome):
    return inspect(op.get_bind()).has_table(nome)


def upgrade():
    if _tem_tabela(TABELA):
        return
    op.create_table(
        TABELA,
        sa.Column("login_normalizado", sa.String(length=80), nullable=False),
        sa.Column("falhas", sa.Integer(), nullable=False),
        sa.Column("bloqueado_ate", sa.DateTime(), nullable=True),
        sa.Column("atualizado_em", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("login_normalizado"),
    )


def downgrade():
    if not _tem_tabela(TABELA):
        return
    op.drop_table(TABELA)
