"""Migrations Alembic: banco novo, baseline e isolamento da suíte."""
import hashlib
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.config import Config, ConfiguracaoInsegura
from src.migracoes import (
    TABELAS_CONTAGEM,
    TABELAS_ESSENCIAIS,
    alinhar_schema_legado,
    revisao_atual,
    revisao_head,
    REVISION_BASELINE,
    REVISION_CONSTRAINTS,
    REVISION_HEAD,
)
from src.integridade import INDICES_INTEGRIDADE
from tests.support import criar_app_migrada, preparar_ambiente_teste


TABELAS_PREEXISTENTES = (
    "aluno",
    "turma",
    "registro_nota",
    "registro_falta",
    "registro_aula",
    "registro_frequencia",
    "relatorio_descritivo",
    "historico_anual",
    "usuario",
)

_FP_ORIGINAL = None
_FP_BACKUPS = None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _fingerprint(path):
    st = path.stat()
    return {"size": st.st_size, "sha256": _sha256(path)}


def setUpModule():
    global _FP_ORIGINAL, _FP_BACKUPS
    raiz = Path(Config.ROOT_DIR) / "instance"
    orig = raiz / "educamais.db"
    _FP_ORIGINAL = _fingerprint(orig) if orig.exists() else None
    _FP_BACKUPS = {
        str(p): _fingerprint(p)
        for p in sorted(raiz.glob("backup_pre_alembic_*.sqlite"))
    }


def tearDownModule():
    raiz = Path(Config.ROOT_DIR) / "instance"
    orig = raiz / "educamais.db"
    if _FP_ORIGINAL is not None:
        atual = _fingerprint(orig)
        if atual != _FP_ORIGINAL:
            raise AssertionError(
                "instance/educamais.db foi modificado durante os testes de migration."
            )
    for nome, fp in (_FP_BACKUPS or {}).items():
        p = Path(nome)
        if not p.exists() or _fingerprint(p) != fp:
            raise AssertionError("Backup %s foi modificado." % nome)


def _sqlite_counts(path):
    con = sqlite3.connect(path)
    try:
        nomes = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        out = {}
        for t in TABELAS_CONTAGEM:
            if t in nomes:
                out[t] = con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            else:
                out[t] = None
        return out, nomes
    finally:
        con.close()


def _tuples(path, sql):
    con = sqlite3.connect(path)
    try:
        return [tuple(r) for r in con.execute(sql)]
    finally:
        con.close()


def _alembic_rev(path):
    con = sqlite3.connect(path)
    try:
        nomes = {
            r[0]
            for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "alembic_version" not in nomes:
            return None
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _snapshot_academico(path):
    return {
        "notas": _tuples(
            path,
            "SELECT id, aluno_id, disciplina_id, bimestre, valor "
            "FROM registro_nota ORDER BY id",
        ),
        "faltas": _tuples(
            path,
            "SELECT id, aluno_id, disciplina_id, bimestre, quantidade "
            "FROM registro_falta ORDER BY id",
        ),
        "historicos": _tuples(
            path,
            "SELECT id, aluno_id, ano_letivo, dados_fechamento "
            "FROM historico_anual ORDER BY id",
        ),
        "aulas": _tuples(
            path,
            "SELECT id, professor_id, turma_id, disciplina_id, data, bimestre, "
            "quantidade_aulas, conteudo, status FROM registro_aula ORDER BY id",
        ),
        "frequencias": _tuples(
            path,
            "SELECT id, aluno_id, turma_id, disciplina_id, data, bimestre, "
            "situacao, registro_aula_id FROM registro_frequencia ORDER BY id",
        ),
        "pareceres": _tuples(
            path,
            "SELECT id, aluno_id, eixo, bimestre, avaliacao, observacao "
            "FROM relatorio_descritivo ORDER BY id",
        ),
    }


def _ids(path, tabela):
    return tuple(r[0] for r in _tuples(path, "SELECT id FROM %s ORDER BY id" % tabela))


def _pares_matricula(path, tabela):
    return _tuples(path, "SELECT id, matricula_id FROM %s ORDER BY id" % tabela)

class MigrationBancoNovoTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + Path(self._db_path).resolve().as_posix()
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-migration-teste"
        os.environ["EDUCAMAIS_DEMO"] = "0"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        Config.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def test_upgrade_banco_vazio_cria_schema_sem_create_all(self):
        from flask_migrate import upgrade
        from sqlalchemy import inspect
        from src import create_app, db

        app = create_app(preparar_schema=False)
        with app.app_context():
            insp = inspect(db.engine)
            self.assertNotIn("usuario", insp.get_table_names())
            self.assertNotIn("alembic_version", insp.get_table_names())
            upgrade()
            insp.clear_cache()
            tabelas = set(insp.get_table_names())
            for nome in TABELAS_ESSENCIAIS:
                self.assertIn(nome, tabelas, nome)
            self.assertIn("alembic_version", tabelas)
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            self.assertEqual(revisao_atual(), revisao_head())

            uniques_nota = inspect(db.engine).get_unique_constraints("registro_nota")
            uniques_falta = inspect(db.engine).get_unique_constraints("registro_falta")
            uniques_freq = inspect(db.engine).get_unique_constraints("registro_frequencia")
            uniques_rel = inspect(db.engine).get_unique_constraints("relatorio_descritivo")
            uniques_hist = inspect(db.engine).get_unique_constraints("historico_anual")
            self.assertEqual(uniques_nota, [])
            self.assertEqual(uniques_falta, [])
            self.assertEqual(uniques_freq, [])
            self.assertEqual(uniques_rel, [])
            self.assertEqual(uniques_hist, [])
            nomes_idx = {
                i["name"]
                for t in (
                    "registro_nota", "registro_falta", "registro_frequencia",
                    "relatorio_descritivo", "historico_anual",
                )
                for i in inspect(db.engine).get_indexes(t)
                if i.get("name")
            }
            for nome in INDICES_INTEGRIDADE:
                self.assertIn(nome, nomes_idx, nome)

        app2 = create_app()
        app2.testing = True
        with app2.app_context():
            from src.models import Usuario
            from src.notas import media_minima_vigente
            self.assertEqual(Usuario.query.count(), 0)
            self.assertEqual(media_minima_vigente(), 6.0)
            self.assertEqual(revisao_atual(), REVISION_HEAD)

    def test_downgrade_baseline_e_destrutivo_em_banco_descartavel(self):
        from flask_migrate import downgrade, upgrade
        from sqlalchemy import inspect
        from src import create_app, db

        app = create_app(preparar_schema=False)
        with app.app_context():
            upgrade()
            insp = inspect(db.engine)
            self.assertIn("usuario", insp.get_table_names())
            downgrade(revision="base")
            insp.clear_cache()
            tabelas = set(insp.get_table_names())
            self.assertNotIn("usuario", tabelas)
            self.assertNotIn("matricula", tabelas)
            self.assertIsNone(revisao_atual())

    def test_upgrade_downgrade_upgrade_constraints(self):
        from flask_migrate import downgrade, upgrade
        from sqlalchemy import inspect
        from src import create_app, db
        from src.models import (
            Aluno, Disciplina, Escola, HistoricoAnual, Matricula,
            RegistroFalta, RegistroNota, RelatorioDescritivo, Turma,
        )

        app = create_app(preparar_schema=False)
        with app.app_context():
            upgrade(revision=REVISION_BASELINE)
            self.assertEqual(revisao_atual(), REVISION_BASELINE)
            insp = inspect(db.engine)
            nomes_base = {i["name"] for i in insp.get_indexes("registro_nota") if i.get("name")}
            self.assertNotIn("uq_registro_nota_matricula_disc_bim", nomes_base)

            disc = Disciplina(nome="Português Constraints Ciclo")
            escola = Escola(
                nome="Escola Ciclo Cons", inep="79200001",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add_all([disc, escola])
            db.session.flush()
            turma = Turma(
                nome="5º Ano Ciclo", ano_letivo=2026, status="ativa",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            db.session.add(turma)
            db.session.flush()
            aluno = Aluno(
                nome="Aluno Ciclo", cpf="79200000001", situacao="ativo",
                turma_id=turma.id, escola_id=escola.id, nome_mae="Mãe Ciclo",
            )
            db.session.add(aluno)
            db.session.flush()
            mat = Matricula(
                aluno_id=aluno.id, turma_id=turma.id,
                data_inicio=date(2026, 2, 1), situacao="cursando",
            )
            db.session.add(mat)
            db.session.flush()
            db.session.add(RegistroNota(
                aluno_id=aluno.id, matricula_id=mat.id,
                disciplina_id=disc.id, bimestre=11, valor=8.0, tipo="bimestral",
            ))
            db.session.add(RegistroFalta(
                aluno_id=aluno.id, matricula_id=mat.id,
                disciplina_id=disc.id, bimestre=1, quantidade=2,
            ))
            db.session.add(RelatorioDescritivo(
                aluno_id=aluno.id, matricula_id=mat.id,
                eixo="Linguagem", bimestre=1, avaliacao="Em desenvolvimento",
            ))
            db.session.add(HistoricoAnual(
                ano_letivo=2025, aluno_id=aluno.id, matricula_id=None,
                escola_nome=escola.nome, turma_nome="legado",
                etapa="fundamental", dados_fechamento="{}",
            ))
            db.session.commit()
            n_notas = RegistroNota.query.count()
            n_faltas = RegistroFalta.query.count()
            n_hist = HistoricoAnual.query.count()
            n_parec = RelatorioDescritivo.query.count()
            nota_id = RegistroNota.query.one().id
            hist_null_id = HistoricoAnual.query.filter_by(matricula_id=None).one().id

            upgrade()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            insp.clear_cache()
            nomes_head = {i["name"] for i in insp.get_indexes("registro_nota") if i.get("name")}
            self.assertIn("uq_registro_nota_matricula_disc_bim", nomes_head)
            self.assertEqual(RegistroNota.query.count(), n_notas)
            self.assertEqual(RegistroFalta.query.count(), n_faltas)
            self.assertEqual(HistoricoAnual.query.count(), n_hist)
            self.assertEqual(RelatorioDescritivo.query.count(), n_parec)
            self.assertEqual(db.session.get(RegistroNota, nota_id).valor, 8.0)
            self.assertIsNone(db.session.get(HistoricoAnual, hist_null_id).matricula_id)

            downgrade(revision=REVISION_BASELINE)
            self.assertEqual(revisao_atual(), REVISION_BASELINE)
            insp.clear_cache()
            nomes_down = {i["name"] for i in insp.get_indexes("registro_nota") if i.get("name")}
            self.assertNotIn("uq_registro_nota_matricula_disc_bim", nomes_down)
            self.assertEqual(RegistroNota.query.count(), n_notas)
            self.assertEqual(HistoricoAnual.query.count(), n_hist)
            self.assertIsNone(db.session.get(HistoricoAnual, hist_null_id).matricula_id)

            upgrade()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            insp.clear_cache()
            nomes_up2 = {i["name"] for i in insp.get_indexes("registro_nota") if i.get("name")}
            self.assertIn("uq_registro_nota_matricula_disc_bim", nomes_up2)
            self.assertEqual(RegistroNota.query.count(), n_notas)
            self.assertEqual(db.session.get(RegistroNota, nota_id).valor, 8.0)

    def test_migration_aborta_se_duplicidade_incompativel(self):
        from flask_migrate import upgrade
        from src import create_app, db
        from src.models import Aluno, Disciplina, Escola, Matricula, RegistroNota, Turma

        app = create_app(preparar_schema=False)
        with app.app_context():
            upgrade(revision=REVISION_BASELINE)
            disc = Disciplina(nome="Português Dup Cons")
            escola = Escola(
                nome="Escola Dup Cons", inep="79300001",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add_all([disc, escola])
            db.session.flush()
            turma = Turma(
                nome="5º Ano Dup", ano_letivo=2026, status="ativa",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            db.session.add(turma)
            db.session.flush()
            aluno = Aluno(
                nome="Aluno Dup", cpf="79300000001", situacao="ativo",
                turma_id=turma.id, escola_id=escola.id, nome_mae="Mãe Dup",
            )
            db.session.add(aluno)
            db.session.flush()
            mat = Matricula(
                aluno_id=aluno.id, turma_id=turma.id,
                data_inicio=date(2026, 2, 1), situacao="cursando",
            )
            db.session.add(mat)
            db.session.flush()
            db.session.add(RegistroNota(
                aluno_id=aluno.id, matricula_id=mat.id,
                disciplina_id=disc.id, bimestre=11, valor=8.0, tipo="bimestral",
            ))
            db.session.add(RegistroNota(
                aluno_id=aluno.id, matricula_id=mat.id,
                disciplina_id=disc.id, bimestre=11, valor=3.0, tipo="bimestral",
            ))
            db.session.commit()
            n_notas = RegistroNota.query.count()
            with self.assertRaises(SystemExit) as ctx:
                upgrade()
            self.assertEqual(ctx.exception.code, 1)
            self.assertEqual(revisao_atual(), REVISION_BASELINE)
            self.assertEqual(RegistroNota.query.count(), n_notas)
            insp_nomes = {
                i["name"] for i in inspect_indexes(db.engine, "registro_nota")
            }
            self.assertNotIn("uq_registro_nota_matricula_disc_bim", insp_nomes)

    def test_autogenerate_sem_diff_apos_head(self):
        from flask_migrate import upgrade
        from alembic.autogenerate import compare_metadata
        from alembic.runtime.migration import MigrationContext
        from src import create_app, db

        app = create_app(preparar_schema=False)
        with app.app_context():
            upgrade()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            with db.engine.connect() as conn:
                ctx = MigrationContext.configure(
                    conn, opts={"render_as_batch": True},
                )
                diffs = compare_metadata(ctx, db.metadata)
            self.assertEqual(diffs, [], diffs)


def inspect_indexes(engine, table):
    from sqlalchemy import inspect
    return inspect(engine).get_indexes(table)


class MigrationStampTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + Path(self._db_path).resolve().as_posix()
        preparar_ambiente_teste(demo=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        Config.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def test_stamp_e_upgrade_preservam_dados_de_create_all(self):
        from flask_migrate import stamp, upgrade
        from src import create_app
        from src.models import Usuario

        app = create_app()
        with app.app_context():
            n_users = Usuario.query.count()
            self.assertGreaterEqual(n_users, 1)
            self.assertIsNone(revisao_atual())
            stamp(revision="head")
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            upgrade()
            self.assertEqual(Usuario.query.count(), n_users)
            self.assertEqual(revisao_atual(), REVISION_HEAD)


class MigrationBancoExistenteCopiaTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._src = Path(Config.ROOT_DIR) / "instance" / "educamais.db"
        self._copy = None
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        Config.SQLALCHEMY_DATABASE_URI = self._old_uri
        if self._copy and self._copy.exists():
            try:
                os.unlink(self._copy)
            except OSError:
                pass

    def _copiar_legado(self):
        if not self._src.exists():
            self.skipTest("instance/educamais.db ausente (sem banco persistente neste ambiente)")
        fd, dest = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.unlink(dest)
        shutil.copy2(self._src, dest)
        self._copy = Path(dest)
        return self._copy

    def _app_copia(self):
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + self._copy.resolve().as_posix()
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-migration-teste"
        os.environ["EDUCAMAIS_DEMO"] = "0"
        from src import create_app
        return create_app(preparar_schema=False)

    def _cli_alinhar(self, app):
        runner = app.test_cli_runner()
        result = runner.invoke(args=["educamais", "alinhar-schema-legado", "--confirmar"])
        self.assertEqual(result.exit_code, 0, result.output or result.exception)
        return result

    def test_cli_recusa_sem_confirmar(self):
        self._copiar_legado()
        app = self._app_copia()
        runner = app.test_cli_runner()
        result = runner.invoke(args=["educamais", "alinhar-schema-legado"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIsNone(_alembic_rev(self._copy))

    def test_copia_preserva_contagens_conteudo_e_e_idempotente(self):
        self._copiar_legado()
        antes, nomes_antes = _sqlite_counts(self._copy)
        academico_antes = _snapshot_academico(self._copy)
        self.assertIsNone(antes["matricula"])
        self.assertNotIn("alembic_version", nomes_antes)
        for tabela in TABELAS_PREEXISTENTES:
            self.assertIsNotNone(antes[tabela], tabela)

        app = self._app_copia()
        self._cli_alinhar(app)

        depois1, nomes1 = _sqlite_counts(self._copy)
        academico_depois = _snapshot_academico(self._copy)
        self.assertIn("alembic_version", nomes1)
        self.assertIn("matricula", nomes1)
        self.assertEqual(_alembic_rev(self._copy), REVISION_BASELINE)
        for tabela in TABELAS_PREEXISTENTES:
            self.assertEqual(depois1[tabela], antes[tabela], tabela)
        self.assertEqual(academico_depois, academico_antes)

        mats1 = _ids(self._copy, "matricula")
        notas_mid1 = _pares_matricula(self._copy, "registro_nota")
        faltas_mid1 = _pares_matricula(self._copy, "registro_falta")
        freq_mid1 = _pares_matricula(self._copy, "registro_frequencia")
        parec_mid1 = _pares_matricula(self._copy, "relatorio_descritivo")
        hist_mid1 = _pares_matricula(self._copy, "historico_anual")
        self.assertEqual(depois1["matricula"], len(mats1))

        self._cli_alinhar(app)

        depois2, _ = _sqlite_counts(self._copy)
        self.assertEqual(depois2, depois1)
        self.assertEqual(_ids(self._copy, "matricula"), mats1)
        self.assertEqual(_pares_matricula(self._copy, "registro_nota"), notas_mid1)
        self.assertEqual(_pares_matricula(self._copy, "registro_falta"), faltas_mid1)
        self.assertEqual(_pares_matricula(self._copy, "registro_frequencia"), freq_mid1)
        self.assertEqual(_pares_matricula(self._copy, "relatorio_descritivo"), parec_mid1)
        self.assertEqual(_pares_matricula(self._copy, "historico_anual"), hist_mid1)
        self.assertEqual(_snapshot_academico(self._copy), academico_antes)
        self.assertEqual(_alembic_rev(self._copy), REVISION_BASELINE)
        with app.app_context():
            self.assertEqual(revisao_atual(), REVISION_BASELINE)
            self.assertNotEqual(revisao_head(), REVISION_BASELINE)

            from src.integridade import auditar_integridade
            from flask_migrate import upgrade
            from sqlalchemy import inspect
            from src import db
            rel = auditar_integridade()
            self.assertTrue(rel["somente_leitura"])
            n_incomp = len(rel["incompativeis"])
            contagens_pre = {t: depois2[t] for t in TABELAS_PREEXISTENTES}
            if n_incomp:
                with self.assertRaises(Exception):
                    upgrade()
                self.assertEqual(revisao_atual(), REVISION_BASELINE)
            else:
                upgrade()
                self.assertEqual(revisao_atual(), REVISION_HEAD)
                insp = inspect(db.engine)
                nomes_idx = {
                    i["name"]
                    for t in (
                        "registro_nota", "registro_falta", "registro_frequencia",
                        "relatorio_descritivo", "historico_anual",
                    )
                    for i in insp.get_indexes(t)
                    if i.get("name")
                }
                for nome in INDICES_INTEGRIDADE:
                    self.assertIn(nome, nomes_idx, nome)
            depois3, _ = _sqlite_counts(self._copy)
            for tabela in TABELAS_PREEXISTENTES:
                self.assertEqual(depois3[tabela], contagens_pre[tabela], tabela)
            self.assertEqual(_snapshot_academico(self._copy), academico_antes)
            self.assertEqual(_ids(self._copy, "matricula"), mats1)

    def test_falha_no_alinhamento_nao_marca_head(self):
        self._copiar_legado()
        app = self._app_copia()
        with app.app_context():
            with patch("src.models.ensure_schema", side_effect=RuntimeError("falha simulada no alinhamento")):
                with patch("src.migracoes.marcar_baseline_alinhado") as stamp_fn:
                    with self.assertRaises(RuntimeError):
                        alinhar_schema_legado()
                    stamp_fn.assert_not_called()
            self.assertIsNone(revisao_atual())
        self.assertIsNone(_alembic_rev(self._copy))
        self.assertEqual(_fingerprint(self._src), _FP_ORIGINAL)

    def test_stamp_so_depois_do_ensure_com_sucesso(self):
        self._copiar_legado()
        app = self._app_copia()
        ordem = []
        from src import models as models_mod
        from src import migracoes as migracoes_mod
        ensure_real = models_mod.ensure_schema
        marcar_real = migracoes_mod.marcar_baseline_alinhado

        def ensure_com_ordem():
            ordem.append("ensure")
            return ensure_real()

        def marcar_com_ordem():
            ordem.append("stamp")
            self.assertIn("ensure", ordem)
            return marcar_real()

        with app.app_context():
            with patch.object(models_mod, "ensure_schema", side_effect=ensure_com_ordem):
                with patch.object(migracoes_mod, "marcar_baseline_alinhado", side_effect=marcar_com_ordem):
                    alinhar_schema_legado()
            self.assertEqual(ordem, ["ensure", "stamp"])
            self.assertEqual(revisao_atual(), REVISION_BASELINE)


class AlinharLegadoAmbiguoTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + Path(self._db_path).resolve().as_posix()
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-migration-teste"
        os.environ["EDUCAMAIS_DEMO"] = "0"

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        Config.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def test_alinhar_nao_adivinha_registro_com_duas_matriculas(self):
        from flask_migrate import upgrade
        from src import create_app, db
        from src.models import (
            Aluno, Disciplina, Escola, HistoricoAnual, Matricula,
            RegistroFalta, RegistroNota, RelatorioDescritivo, Turma,
        )

        app = create_app(preparar_schema=False)
        with app.app_context():
            upgrade()
            disc = Disciplina(nome="Português Ambiguo Alinhar")
            escola = Escola(
                nome="Escola Ambiguo Alinhar", inep="74100099",
                oferta_infantil=True, oferta_fundamental=True,
            )
            db.session.add_all([disc, escola])
            db.session.flush()
            t1 = Turma(
                nome="5º Ano Amb", ano_letivo=2026, status="concluida",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            t2 = Turma(
                nome="6º Ano Amb", ano_letivo=2026, status="ativa",
                etapa_ensino="fundamental", escola_id=escola.id,
            )
            db.session.add_all([t1, t2])
            db.session.flush()
            aluno = Aluno(
                nome="Aluno Ambiguo Alinhar", cpf="74100000099", situacao="ativo",
                turma_id=t2.id, escola_id=escola.id, nome_mae="Mãe Ambiguo",
            )
            db.session.add(aluno)
            db.session.flush()
            m1 = Matricula(
                aluno_id=aluno.id, turma_id=t1.id,
                data_inicio=date(2026, 2, 1), data_fim=date(2026, 6, 30),
                situacao="concluida",
            )
            m2 = Matricula(
                aluno_id=aluno.id, turma_id=t2.id,
                data_inicio=date(2026, 8, 1), situacao="cursando",
            )
            db.session.add_all([m1, m2])
            db.session.flush()
            nota = RegistroNota(
                aluno_id=aluno.id, disciplina_id=disc.id,
                bimestre=11, valor=5.5, tipo="bimestral",
            )
            falta = RegistroFalta(
                aluno_id=aluno.id, disciplina_id=disc.id,
                bimestre=1, quantidade=3,
            )
            parecer = RelatorioDescritivo(
                aluno_id=aluno.id, eixo="Linguagem", bimestre=1,
                avaliacao="Em desenvolvimento", observacao="legado",
            )
            hist = HistoricoAnual(
                aluno_id=aluno.id, ano_letivo=2026,
                escola_nome=escola.nome, turma_nome=None,
                etapa="fundamental", dados_fechamento='{"legado": true}',
                situacao_final="ativo",
            )
            db.session.add_all([nota, falta, parecer, hist])
            db.session.commit()
            aluno_id = aluno.id
            nota_id = nota.id
            falta_id = falta.id
            parecer_id = parecer.id
            hist_id = hist.id
            valor_nota = nota.valor
            qtd_falta = falta.quantidade
            json_hist = hist.dados_fechamento
            n_notas = RegistroNota.query.count()
            n_faltas = RegistroFalta.query.count()
            n_parec = RelatorioDescritivo.query.count()
            n_hist = HistoricoAnual.query.count()
            n_mats = Matricula.query.count()
            ids_mats = {m1.id, m2.id}

            alinhar_schema_legado()

            nota2 = db.session.get(RegistroNota, nota_id)
            falta2 = db.session.get(RegistroFalta, falta_id)
            parecer2 = db.session.get(RelatorioDescritivo, parecer_id)
            hist2 = db.session.get(HistoricoAnual, hist_id)
            aluno2 = db.session.get(Aluno, aluno_id)
            self.assertIsNone(nota2.matricula_id)
            self.assertIsNone(falta2.matricula_id)
            self.assertIsNone(parecer2.matricula_id)
            self.assertIsNone(hist2.matricula_id)
            self.assertEqual(nota2.valor, valor_nota)
            self.assertEqual(falta2.quantidade, qtd_falta)
            self.assertEqual(hist2.dados_fechamento, json_hist)
            self.assertEqual(RegistroNota.query.count(), n_notas)
            self.assertEqual(RegistroFalta.query.count(), n_faltas)
            self.assertEqual(RelatorioDescritivo.query.count(), n_parec)
            self.assertEqual(HistoricoAnual.query.count(), n_hist)
            self.assertEqual(Matricula.query.count(), n_mats)
            self.assertEqual(
                {m.id for m in Matricula.query.filter_by(aluno_id=aluno_id).all()},
                ids_mats,
            )
            self.assertEqual(aluno2.turma_id, t2.id)
            self.assertEqual(revisao_atual(), REVISION_HEAD)


class BancoOriginalIntocadoTest(unittest.TestCase):
    def test_original_e_backup_inalterados(self):
        orig = Path(Config.ROOT_DIR) / "instance" / "educamais.db"
        if not orig.exists():
            self.skipTest("instance/educamais.db ausente")
        self.assertIsNotNone(_FP_ORIGINAL)
        self.assertEqual(_fingerprint(orig), _FP_ORIGINAL)
        self.assertIsNone(_alembic_rev(orig))
        con = sqlite3.connect(orig)
        try:
            nomes = {
                r[0]
                for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            con.close()
        self.assertNotIn("alembic_version", nomes)
        self.assertNotIn("matricula", nomes)
        for nome, fp in (_FP_BACKUPS or {}).items():
            self.assertTrue(Path(nome).exists(), nome)
            self.assertEqual(_fingerprint(Path(nome)), fp)


class ProducaoSemMigracaoTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        from tests.support import apontar_banco_teste
        apontar_banco_teste("sqlite:///" + Path(self._db_path).resolve().as_posix())

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        Config.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def test_development_sem_alembic_falha(self):
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_DEMO"] = "0"
        from src import create_app
        with self.assertRaises(ConfiguracaoInsegura) as ctx:
            create_app()
        self.assertIn("flask db upgrade", str(ctx.exception).lower())

    def test_producao_migrada_sobe(self):
        os.environ["EDUCAMAIS_ENV"] = "production"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-producao-de-teste-nao-reutilizar"
        os.environ.pop("EDUCAMAIS_DEMO", None)
        from src import create_app
        with self.assertRaises(ConfiguracaoInsegura) as ctx:
            create_app()
        self.assertIn("PostgreSQL", str(ctx.exception))


class ComandoLegadoNaoSqliteTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        from tests.support import apontar_banco_teste
        apontar_banco_teste("sqlite:///" + Path(self._db_path).resolve().as_posix())
        preparar_ambiente_teste(demo=True)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        Config.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def test_alinhar_schema_legado_recusa_postgres(self):
        from src import create_app
        from src.migracoes import MSG_COMANDO_SQLITE_LEGADO
        app = create_app()
        runner = app.test_cli_runner()
        with patch("src.migracoes.dialecto_atual", return_value="postgresql"):
            result = runner.invoke(
                args=["educamais", "alinhar-schema-legado", "--confirmar"],
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn(MSG_COMANDO_SQLITE_LEGADO, result.output)

    def test_backfill_legado_recusa_postgres(self):
        from src import create_app
        from src.migracoes import MSG_COMANDO_SQLITE_LEGADO
        app = create_app()
        runner = app.test_cli_runner()
        with patch("src.migracoes.dialecto_atual", return_value="postgresql"):
            result = runner.invoke(
                args=["educamais", "backfill-legado", "--confirmar"],
            )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn(MSG_COMANDO_SQLITE_LEGADO, result.output)


if __name__ == "__main__":
    unittest.main()
