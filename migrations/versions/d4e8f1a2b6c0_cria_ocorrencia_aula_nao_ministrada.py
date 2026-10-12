"""cria ocorrencia_aula_nao_ministrada

Revision ID: d4e8f1a2b6c0
Revises: c3a91d7e5b04
Create Date: 2026-09-04 18:50:00.000000

Aula prevista não ministrada (falta docente). Não é RegistroAula.
Se a tabela já existir (create_all em testes), não recria.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect, text


revision = "d4e8f1a2b6c0"
down_revision = "c3a91d7e5b04"
branch_labels = None
depends_on = None

TABELA = "ocorrencia_aula_nao_ministrada"
INDICE_ATIVA = "uq_ocorrencia_nao_ministrada_horario_data_ativa"
_WHERE_ATIVA = text("status = 'ATIVA'")


def _tem_tabela(nome):
    return inspect(op.get_bind()).has_table(nome)


def _tem_indice(tabela, nome):
    insp = inspect(op.get_bind())
    if not insp.has_table(tabela):
        return False
    return nome in {i["name"] for i in insp.get_indexes(tabela) if i.get("name")}


def upgrade():
    if _tem_tabela(TABELA):
        return
    op.create_table(
        TABELA,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("escola_id", sa.Integer(), nullable=False),
        sa.Column("turma_id", sa.Integer(), nullable=False),
        sa.Column("disciplina_id", sa.Integer(), nullable=True),
        sa.Column("professor_id", sa.Integer(), nullable=False),
        sa.Column("alocacao_id", sa.Integer(), nullable=False),
        sa.Column("horario_alocacao_id", sa.Integer(), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column("quantidade_aulas", sa.Integer(), nullable=False),
        sa.Column("motivo", sa.String(length=40), nullable=False),
        sa.Column("observacao", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("registrado_por_id", sa.Integer(), nullable=False),
        sa.Column("registrado_em", sa.DateTime(), nullable=False),
        sa.Column("cancelado_por_id", sa.Integer(), nullable=True),
        sa.Column("cancelado_em", sa.DateTime(), nullable=True),
        sa.Column("motivo_cancelamento", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["alocacao_id"], ["alocacao.id"]),
        sa.ForeignKeyConstraint(["disciplina_id"], ["disciplina.id"]),
        sa.ForeignKeyConstraint(["escola_id"], ["escola.id"]),
        sa.ForeignKeyConstraint(["horario_alocacao_id"], ["horario_alocacao.id"]),
        sa.ForeignKeyConstraint(["professor_id"], ["professor.id"]),
        sa.ForeignKeyConstraint(["turma_id"], ["turma.id"]),
        sa.ForeignKeyConstraint(["cancelado_por_id"], ["usuario.id"]),
        sa.ForeignKeyConstraint(["registrado_por_id"], ["usuario.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ocorrencia_nao_min_escola_data", TABELA, ["escola_id", "data"])
    op.create_index("ix_ocorrencia_nao_min_professor", TABELA, ["professor_id"])
    op.create_index("ix_ocorrencia_nao_min_status", TABELA, ["status"])
    if not _tem_indice(TABELA, INDICE_ATIVA):
        op.create_index(
            INDICE_ATIVA,
            TABELA,
            ["horario_alocacao_id", "data"],
            unique=True,
            sqlite_where=_WHERE_ATIVA,
            postgresql_where=_WHERE_ATIVA,
        )


def downgrade():
    if not _tem_tabela(TABELA):
        return
    if _tem_indice(TABELA, INDICE_ATIVA):
        op.drop_index(INDICE_ATIVA, table_name=TABELA)
    op.drop_index("ix_ocorrencia_nao_min_status", table_name=TABELA)
    op.drop_index("ix_ocorrencia_nao_min_professor", table_name=TABELA)
    op.drop_index("ix_ocorrencia_nao_min_escola_data", table_name=TABELA)
    op.drop_table(TABELA)
