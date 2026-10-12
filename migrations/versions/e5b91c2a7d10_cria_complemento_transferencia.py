"""cria complemento de transferência

Revision ID: e5b91c2a7d10
Revises: d4e8f1a2b6c0
Create Date: 2026-09-29 10:30:00.000000

Dados transcritos do ano corrente para emitir histórico de transferência.
Não é HistoricoAnual, nota oficial nem frequência oficial.
Se as tabelas já existirem (create_all em testes), não recria.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "e5b91c2a7d10"
down_revision = "d4e8f1a2b6c0"
branch_labels = None
depends_on = None

PAI = "complemento_transferencia"
FILHO = "complemento_transferencia_periodo"


def _tem_tabela(nome):
    return inspect(op.get_bind()).has_table(nome)


def upgrade():
    if not _tem_tabela(PAI):
        op.create_table(
            PAI,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("aluno_id", sa.Integer(), nullable=False),
            sa.Column("matricula_id", sa.Integer(), nullable=False),
            sa.Column("escola_id", sa.Integer(), nullable=True),
            sa.Column("ano_letivo", sa.Integer(), nullable=False),
            sa.Column("criado_em", sa.DateTime(), nullable=False),
            sa.Column("atualizado_em", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["aluno_id"], ["aluno.id"], name="fk_compl_transf_aluno_id"),
            sa.ForeignKeyConstraint(["escola_id"], ["escola.id"], name="fk_compl_transf_escola_id"),
            sa.ForeignKeyConstraint(
                ["matricula_id"], ["matricula.id"], name="fk_compl_transf_matricula_id",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("matricula_id", name="uq_compl_transf_matricula"),
        )
        op.create_index("ix_compl_transf_aluno", PAI, ["aluno_id"])
        op.create_index("ix_compl_transf_escola", PAI, ["escola_id"])
    if not _tem_tabela(FILHO):
        op.create_table(
            FILHO,
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("complemento_id", sa.Integer(), nullable=False),
            sa.Column("disciplina_id", sa.Integer(), nullable=True),
            sa.Column("nome_componente", sa.String(length=160), nullable=False),
            sa.Column("nome_chave", sa.String(length=160), nullable=False),
            sa.Column("periodo", sa.Integer(), nullable=False),
            sa.Column("nota", sa.Float(), nullable=True),
            sa.Column("faltas", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(
                ["complemento_id"],
                ["complemento_transferencia.id"],
                name="fk_compl_transf_per_complemento_id",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["disciplina_id"], ["disciplina.id"], name="fk_compl_transf_per_disciplina_id",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "complemento_id", "nome_chave", "periodo",
                name="uq_compl_transf_per_comp_periodo",
            ),
        )
        op.create_index("ix_compl_transf_per_complemento", FILHO, ["complemento_id"])


def downgrade():
    if _tem_tabela(FILHO):
        op.drop_table(FILHO)
    if _tem_tabela(PAI):
        op.drop_table(PAI)
