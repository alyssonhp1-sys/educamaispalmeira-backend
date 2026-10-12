"""adiciona situacao por componente legado

Revision ID: c3a91d7e5b04
Revises: b7e13c8a4d02
Create Date: 2026-09-03 23:40:00.000000

Situação textual do componente transcrito do documento de origem.
Não é enum. Não altera HistoricoAnual. Se a coluna já existir
(create_all em testes), não recria.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "c3a91d7e5b04"
down_revision = "b7e13c8a4d02"
branch_labels = None
depends_on = None


def _tem_coluna(tabela, coluna):
    insp = inspect(op.get_bind())
    if not insp.has_table(tabela):
        return False
    return coluna in {c["name"] for c in insp.get_columns(tabela)}


def upgrade():
    if not _tem_coluna("trajetoria_componente_legado", "situacao"):
        op.add_column(
            "trajetoria_componente_legado",
            sa.Column("situacao", sa.String(length=80), nullable=True),
        )


def downgrade():
    if _tem_coluna("trajetoria_componente_legado", "situacao"):
        with op.batch_alter_table("trajetoria_componente_legado") as batch:
            batch.drop_column("situacao")
