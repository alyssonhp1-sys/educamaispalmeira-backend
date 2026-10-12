"""Schema versionado (Alembic) e ponte explícita para SQLite legado.

Startup comercial NÃO chama create_all, ALTER TABLE nem backfill.
Testes isolados (EDUCAMAIS_ENV=testing) ainda usam create_all + ensure_schema.
"""
from __future__ import annotations

import os
import sys

from flask import current_app

from src import db
from src.config import ConfiguracaoInsegura

REVISION_BASELINE = "6c0839c174ab"
REVISION_CONSTRAINTS = "c4e91f7a2b08"
REVISION_AUDIT_LOG = "d8f31a6b4c20"
REVISION_LOCKOUT = "e7a42c9d1b80"
REVISION_MATRIZ = "f8c20b3e4a91"
REVISION_TRAJETORIA_LEGADA = "b7e13c8a4d02"
REVISION_SITUACAO_COMPONENTE_LEGADO = "c3a91d7e5b04"
REVISION_FALTA_DOCENTE = "d4e8f1a2b6c0"
REVISION_COMPLEMENTO_TRANSFERENCIA = "e5b91c2a7d10"
REVISION_HEAD = REVISION_COMPLEMENTO_TRANSFERENCIA

MSG_COMANDO_SQLITE_LEGADO = "Comando exclusivo para banco SQLite legado."

TABELAS_ESSENCIAIS = (
    "usuario",
    "escola",
    "professor",
    "turma",
    "disciplina",
    "alocacao",
    "aluno",
    "matricula",
    "registro_nota",
    "registro_falta",
    "registro_aula",
    "registro_frequencia",
    "relatorio_descritivo",
    "historico_anual",
    "aviso",
    "planejamento",
    "ano_letivo",
    "periodo_letivo",
    "dia_calendario",
    "horario_alocacao",
    "configuracao_academica",
    "audit_log",
    "tentativa_login",
    "matriz_curricular",
    "matriz_componente",
    "trajetoria_escolar_legada",
    "trajetoria_componente_legado",
    "ocorrencia_aula_nao_ministrada",
    "complemento_transferencia",
    "complemento_transferencia_periodo",
)

TABELAS_CONTAGEM = (
    "aluno",
    "turma",
    "matricula",
    "registro_nota",
    "registro_falta",
    "registro_aula",
    "registro_frequencia",
    "relatorio_descritivo",
    "historico_anual",
    "usuario",
)


def comando_flask_admin():
    """True para `flask db ...` e `flask educamais ...` (não para `flask run`).

    `python -m flask` deixa argv[0] em flask/__main__.py (não o executável flask).
    """
    args = [a.lower() for a in sys.argv]
    tem_flask = False
    for a in sys.argv:
        base = os.path.basename(a).lower()
        norm = a.replace("\\", "/").lower()
        if base in ("flask", "flask.exe"):
            tem_flask = True
            break
        if "flask" in norm and norm.endswith("__main__.py"):
            tem_flask = True
            break
    if "-m" in args:
        i = args.index("-m")
        if i + 1 < len(args) and args[i + 1] == "flask":
            tem_flask = True
    if not tem_flask:
        return False
    return "db" in args or "educamais" in args


def diretorio_migrations():
    from src.config import Config
    return os.path.join(Config.ROOT_DIR, "migrations")


def revisao_atual():
    from alembic.runtime.migration import MigrationContext
    with db.engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        return ctx.get_current_revision()


def revisao_head():
    from alembic.script import ScriptDirectory
    ext = current_app.extensions.get("migrate")
    if ext is None:
        raise ConfiguracaoInsegura("Flask-Migrate não está inicializado.")
    pasta = diretorio_migrations()
    if not os.path.isdir(pasta):
        raise ConfiguracaoInsegura(
            "Diretório migrations/ ausente. Banco novo: flask db upgrade "
            "(após flask db init neste repositório). "
            "Nunca execute migration em produção sem backup."
        )
    config = ext.migrate.get_config()
    script = ScriptDirectory.from_config(config)
    return script.get_current_head()


def dialecto_atual(bind=None):
    """Nome do dialect SQLAlchemy (sqlite, postgresql, ...)."""
    if bind is None:
        bind = db.engine
    dialect = getattr(bind, "dialect", None)
    if dialect is not None and getattr(dialect, "name", None):
        return dialect.name
    if hasattr(bind, "get_bind"):
        try:
            got = bind.get_bind()
            if got is not None:
                return got.dialect.name
        except Exception:
            pass
    engine = getattr(bind, "engine", None)
    if engine is not None:
        return engine.dialect.name
    return bind.dialect.name


def exigir_sqlite_legado():
    if dialecto_atual() != "sqlite":
        raise ConfiguracaoInsegura(MSG_COMANDO_SQLITE_LEGADO)


def exigir_schema_migrado():
    """Produção/development: falha se o banco não estiver na revisão Alembic atual.

    Não executa flask db upgrade. Não cria tabela. Não faz ALTER.
    """
    atual = revisao_atual()
    head = revisao_head()
    if atual is None:
        raise ConfiguracaoInsegura(
            "Banco de dados não está migrado (alembic_version ausente). "
            "Banco novo: flask db upgrade. "
            "SQLite já existente no schema atual: flask db stamp head. "
            "SQLite antigo (faltam tabelas/colunas do modelo atual): "
            "backup e depois flask educamais alinhar-schema-legado --confirmar. "
            "Nunca execute migration em produção sem backup."
        )
    if atual != head:
        raise ConfiguracaoInsegura(
            "Banco está na revisão Alembic %s; o código espera %s. "
            "Após backup, execute: flask db upgrade. "
            "O startup não aplica migrations automaticamente."
            % (atual, head)
        )


def aplicar_schema_teste():
    """Somente testes isolados: create_all + ensure_schema (legado, não produção)."""
    from src import models
    db.create_all()
    models.ensure_schema()


def marcar_baseline_alinhado():
    """Stamp do baseline estrutural após create_all + ensure.

    Revisões posteriores (constraints, etc.) entram com flask db upgrade.
    Não marca head se head já avançou além do baseline.
    """
    from flask_migrate import stamp

    atual = revisao_atual()
    head = revisao_head()
    if atual is None:
        stamp(revision=REVISION_BASELINE)
        return revisao_atual()
    if atual == REVISION_BASELINE:
        return atual
    if atual != head:
        raise ConfiguracaoInsegura(
            "Este banco já tem revisão Alembic %s (head=%s). "
            "Não será feito stamp automático. Use flask db upgrade após backup."
            % (atual, head)
        )
    return atual


def alinhar_schema_legado():
    """Ponte explícita para SQLite existente atrás do modelo.

    create_all só cria tabelas ausentes; ensure_schema só ADD COLUMN + backfill
    conservador; stamp marca o baseline (`6c0839c174ab`). Não faz DROP. Não é
    startup. UNIQUE deste bloco entra depois, com flask db upgrade.

    stamp do baseline só ocorre se create_all e ensure_schema concluírem sem
    exceção. Recusa PostgreSQL: o comando não é caminho de banco novo.
    """
    from src import models

    exigir_sqlite_legado()
    db.create_all()
    models.ensure_schema()
    return marcar_baseline_alinhado()


def executar_backfills_legado():
    """Só dados. Comando administrativo; não roda no startup. Só SQLite legado."""
    exigir_sqlite_legado()
    from src.models import executar_backfills_legado as _run
    _run()


def register_cli(app):
    import click

    @app.cli.group("educamais")
    def educamais():
        """Comandos administrativos (schema, backup, ensaio de migração). Não executam no startup."""

    @educamais.command("alinhar-schema-legado")
    @click.option("--confirmar", is_flag=True, help="Obrigatório. Faça backup antes.")
    def alinhar(confirmar):
        if not confirmar:
            click.echo(
                "Recusa: informe --confirmar. Faça backup do SQLite antes. "
                "Este comando não substitui flask db upgrade em banco novo."
            )
            raise SystemExit(2)
        try:
            rev = alinhar_schema_legado()
        except ConfiguracaoInsegura as exc:
            click.echo(str(exc))
            raise SystemExit(2)
        click.echo("Schema legado alinhado e marcado no baseline Alembic (%s)." % rev)

    @educamais.command("backfill-legado")
    @click.option("--confirmar", is_flag=True, help="Obrigatório.")
    def backfill(confirmar):
        if not confirmar:
            click.echo("Recusa: informe --confirmar. Backfill não roda no startup.")
            raise SystemExit(2)
        try:
            executar_backfills_legado()
        except ConfiguracaoInsegura as exc:
            click.echo(str(exc))
            raise SystemExit(2)
        click.echo("Backfills conservadores concluídos (idempotentes).")

    @educamais.command("auditar-integridade")
    def auditar_integridade_cli():
        """Somente leitura: duplicidades técnicas por chave lógica. Não corrige."""
        from src.integridade import auditar_integridade, formatar_relatorio

        rel = auditar_integridade()
        click.echo(formatar_relatorio(rel))
        if rel.get("incompativeis"):
            raise SystemExit(1)

    @educamais.command("preparar-copia-sqlite")
    @click.option("--original", default=None, help="Padrão: instance/educamais.db (somente leitura).")
    @click.option("--copia", default=None, help="Padrão: instance/ensaio_migracao/educamais_copia.db")
    @click.option("--confirmar", is_flag=True, help="Obrigatório para criar a cópia.")
    @click.option("--substituir-copia", is_flag=True, help="Recria a cópia se já existir.")
    def preparar_copia_cli(original, copia, confirmar, substituir_copia):
        """Cria cópia de trabalho do SQLite. Não altera o original."""
        from src.migracao_sqlite_pg import preparar_copia_trabalho
        try:
            rel = preparar_copia_trabalho(
                original=original, copia=copia,
                confirmar=confirmar, substituir=substituir_copia,
            )
        except Exception as exc:
            click.echo(str(exc))
            raise SystemExit(2)
        orig = rel.get("original") or {}
        click.echo("Original: %s" % orig.get("caminho"))
        click.echo("SHA-256 original: %s" % orig.get("sha256"))
        click.echo("Tamanho original: %s" % orig.get("size"))
        cop = rel.get("copia") or {}
        click.echo("Cópia: %s" % cop.get("caminho"))
        click.echo("SHA-256 cópia: %s" % cop.get("sha256"))
        if not confirmar:
            click.echo(rel.get("aviso") or "Sem --confirmar: nenhuma cópia criada.")
            raise SystemExit(2)
        click.echo("Cópia criada. Original byte a byte intacto: %s" % (
            "sim" if rel.get("byte_a_byte_original") else "NAO"
        ))
        if rel.get("original_depois"):
            click.echo("SHA-256 original depois: %s" % rel["original_depois"].get("sha256"))

    @educamais.command("auditar-fonte-sqlite")
    @click.option("--original", default=None, help="Padrão: instance/educamais.db")
    @click.option("--copia", default=None, help="Opcional: caminho da cópia de trabalho")
    def auditar_fonte_cli(original, copia):
        """Auditoria somente leitura da fonte SQLite. Não corrige dados."""
        from src.migracao_sqlite_pg import auditar_fonte_sqlite, formatar_auditoria_fonte
        rel = auditar_fonte_sqlite(original=original, copia=copia)
        click.echo(formatar_auditoria_fonte(rel))
        if rel.get("inconsistencias"):
            raise SystemExit(1)

    @educamais.command("alinhar-copia-ensaio")
    @click.option("--copia", required=True, help="Cópia de trabalho. Recusa o SQLite original.")
    @click.option("--confirmar", is_flag=True, help="Obrigatório. Cria backup da cópia antes.")
    @click.option("--sem-upgrade-head", is_flag=True, help="Só baseline; não aplica flask db upgrade.")
    def alinhar_copia_cli(copia, confirmar, sem_upgrade_head):
        """Alinha a CÓPIA (nunca o original) com o procedimento legado existente."""
        from src.migracao_sqlite_pg import alinhar_copia_ensaio, formatar_auditoria_fonte
        if not confirmar:
            click.echo("Recusa: informe --confirmar. O original não será alterado.")
            raise SystemExit(2)
        try:
            rel = alinhar_copia_ensaio(
                copia, confirmar=True, ate_head=not sem_upgrade_head,
            )
        except Exception as exc:
            click.echo(str(exc))
            raise SystemExit(2)
        click.echo("Cópia alinhada: %s" % rel.get("copia"))
        click.echo("Backup da cópia: %s" % rel.get("backup"))
        alembic = rel.get("alembic") or {}
        click.echo("Alembic atual=%s head=%s" % (alembic.get("atual"), alembic.get("head")))
        if rel.get("depois"):
            click.echo(formatar_auditoria_fonte(rel["depois"]))

    @educamais.command("ensaiar-migracao-pg")
    @click.option("--fonte", required=True, help="Cópia SQLite. Recusa instance/educamais.db.")
    @click.option("--destino-uri", required=True, help="URI explícita. Sem padrão.")
    @click.option("--confirmar", is_flag=True, help="Obrigatório para alterar o destino.")
    @click.option("--limpar-destino", is_flag=True, help="TRUNCATE só em educamais_migracao_test.")
    @click.option("--schema-ja-preparado", is_flag=True, help="Não roda flask db upgrade no destino.")
    def ensaiar_migracao_cli(fonte, destino_uri, confirmar, limpar_destino, schema_ja_preparado):
        """Ensaio SQLite(cópia) -> PostgreSQL vazio. Não é cutover de produção."""
        from src.migracao_sqlite_pg import (
            DestinoInseguro,
            FonteInsegura,
            MigracaoAbortada,
            ensaiar_migracao,
            formatar_diagnostico_destino,
            formatar_validacao,
            mascarar_excecao,
            validar_destino_postgresql,
        )
        diag = validar_destino_postgresql(destino_uri, confirmar=confirmar)
        click.echo(formatar_diagnostico_destino(diag))
        if not confirmar:
            raise SystemExit(2)
        try:
            rel = ensaiar_migracao(
                fonte,
                destino_uri,
                confirmar=True,
                limpar_destino=limpar_destino,
                preparar_schema=not schema_ja_preparado,
            )
        except (DestinoInseguro, FonteInsegura, MigracaoAbortada) as exc:
            click.echo(str(exc))
            raise SystemExit(2)
        except Exception as exc:
            click.echo(mascarar_excecao(exc, destino_uri))
            raise SystemExit(2)
        imp = rel.get("importacao") or {}
        click.echo("Ordem de importação: %s" % " -> ".join(imp.get("ordem") or []))
        click.echo("Registros copiados:")
        for t, n in (imp.get("copiadas") or {}).items():
            click.echo("  %s: %s" % (t, n))
        seqs = imp.get("sequences") or []
        if seqs:
            click.echo("Sequences ajustadas: %s tabela(s)." % len(seqs))
        val = rel.get("validacao") or {}
        click.echo(formatar_validacao(val))
        if not val.get("ok"):
            raise SystemExit(1)

    @educamais.command("validar-migracao-pg")
    @click.option("--fonte", required=True)
    @click.option("--destino-uri", required=True, help="URI explícita. Sem padrão.")
    def validar_migracao_cli(fonte, destino_uri):
        """Compara fonte SQLite x destino PostgreSQL. Somente leitura no destino."""
        from src.migracao_sqlite_pg import (
            DestinoInseguro,
            formatar_validacao,
            mascarar_excecao,
            validar_equivalencia,
        )
        try:
            rel = validar_equivalencia(fonte, destino_uri)
        except DestinoInseguro as exc:
            click.echo(str(exc))
            raise SystemExit(2)
        except Exception as exc:
            click.echo(mascarar_excecao(exc, destino_uri))
            raise SystemExit(2)
        click.echo(formatar_validacao(rel))
        if not rel.get("ok"):
            raise SystemExit(1)

    @educamais.command("auditar-uploads-migracao")
    @click.option("--sqlite", default=None, help="SQLite para cruzar planejamento.arquivo (somente leitura).")
    def auditar_uploads_cli(sqlite):
        """Inventário de uploads. Não move, não renomeia, não apaga."""
        from src.migracao_sqlite_pg import auditar_uploads, formatar_auditoria_uploads
        from src.migracao_sqlite_pg import caminho_sqlite_original
        path = sqlite or str(caminho_sqlite_original())
        rel = auditar_uploads(sqlite_path=path)
        click.echo(formatar_auditoria_uploads(rel))

    from src.backup import register_cli_backup
    register_cli_backup(educamais)

    @educamais.command("preparar-demo")
    @click.option("--confirmar", is_flag=True, help="Obrigatório. Confirma a geração dos dados fictícios.")
    @click.option(
        "--destino",
        default=None,
        help="Arquivo SQLite (ex.: instance/educamais_demo.db). Sem este flag, usa o banco atual.",
    )
    @click.option("--substituir", is_flag=True, help="Apaga o arquivo --destino se já existir.")
    @click.option(
        "--permitir-banco-padrao",
        is_flag=True,
        help="Permite gravar em instance/educamais.db. Não use em base real.",
    )
    def preparar_demo_cli(confirmar, destino, substituir, permitir_banco_padrao):
        """Popula uma base FICTÍCIA de demonstração. Recusa produção. Não roda no startup."""
        from scripts.gerar_dados_demo import executar_preparar_demo, imprimir_credenciais

        credenciais, erro = executar_preparar_demo(
            confirmar=confirmar,
            destino=destino,
            substituir=substituir,
            permitir_padrao=permitir_banco_padrao,
        )
        if erro:
            click.echo(erro, err=True)
            raise SystemExit(2)
        imprimir_credenciais(credenciais)

