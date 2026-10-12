"""Ensaio SQLite -> PostgreSQL: proteções, cópia local e validação.

Não grava em instance/educamais.db.
Testes com PostgreSQL real exigem EDUCAMAIS_MIGRACAO_TEST_DATABASE_URI
apontando para educamais_migracao_test. Não reutilizam educamais_pg_test.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from src.config import Config, resolver_uri_ensaio_migracao, resolver_uri_teste_postgresql
from src.migracao_sqlite_pg import (
    BANCO_ENSAIO_PERMITIDO,
    DestinoInseguro,
    FonteInsegura,
    MigracaoAbortada,
    auditar_fonte_sqlite,
    auditar_uploads,
    caminho_sqlite_original,
    copiar_sqlite_arquivo,
    ensaiar_migracao,
    fingerprint_arquivo,
    formatar_diagnostico_destino,
    importar_dados,
    mapear_schema,
    ordem_importacao,
    preparar_copia_trabalho,
    proximo_setval,
    aplicar_migrations_destino,
    recusar_se_original,
    uri_sqlite_arquivo,
    validar_destino_postgresql,
    validar_equivalencia,
)
from tests.support import preparar_ambiente_teste, SENHA_PESSOAL


URI_PG_ENSAIO = resolver_uri_ensaio_migracao()

_FP_ORIGINAL = None


def _sha_orig():
    p = caminho_sqlite_original()
    return fingerprint_arquivo(p) if p.exists() else None


def setUpModule():
    global _FP_ORIGINAL
    _FP_ORIGINAL = _sha_orig()


def tearDownModule():
    atual = _sha_orig()
    if _FP_ORIGINAL is not None and atual != _FP_ORIGINAL:
        raise AssertionError(
            "instance/educamais.db foi modificado durante os testes de migração."
        )


def _sqlite_vazio():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _apagar(*paths):
    for p in paths:
        if not p:
            continue
        try:
            os.unlink(p)
        except OSError:
            pass


def _popular_fonte(app):
    from src import db
    from src.models import (
        Aluno, Alocacao, AnoLetivo, ConfiguracaoAcademica, DiaCalendario,
        Disciplina, Escola, HistoricoAnual, HorarioAlocacao, Matricula,
        PeriodoLetivo, Planejamento, Professor, RegistroAula, RegistroFalta,
        RegistroFrequencia, RegistroNota, RelatorioDescritivo, Turma, Usuario,
    )
    from src.passwords import hash_senha

    hash_admin = hash_senha(SENHA_PESSOAL)
    with app.app_context():
        escola = Escola(
            nome="Escola José de Alencar", inep="77000001",
            oferta_infantil=True, oferta_fundamental=True,
        )
        db.session.add(escola)
        db.session.flush()
        admin = Usuario(
            login="gestor.ensaio", nome="Gestor Ensaio",
            senha_hash=hash_admin, papel="gestor_escolar",
            primeiro_acesso=False, escola_id=escola.id,
        )
        db.session.add(admin)
        disc = Disciplina(nome="Português")
        db.session.add(disc)
        db.session.flush()
        prof_user = Usuario(
            login="88019998877", nome="Prof Ensaio",
            senha_hash=hash_admin, papel="professor",
            primeiro_acesso=False, escola_id=escola.id,
        )
        db.session.add(prof_user)
        db.session.flush()
        prof = Professor(
            nome="Prof Ensaio", cpf="88019998877",
            escola_id=escola.id, usuario_id=prof_user.id,
        )
        db.session.add(prof)
        turma_a = Turma(
            nome="5º Ano A", ano_letivo=2026, status="ativa",
            etapa_ensino="fundamental", escola_id=escola.id,
        )
        turma_b = Turma(
            nome="5º Ano B", ano_letivo=2026, status="ativa",
            etapa_ensino="fundamental", escola_id=escola.id,
        )
        turma_ei = Turma(
            nome="Pré A", ano_letivo=2026, status="ativa",
            etapa_ensino="infantil", escola_id=escola.id,
        )
        db.session.add_all([turma_a, turma_b, turma_ei])
        db.session.flush()
        aluno = Aluno(
            nome="José da Conceição", cpf="77010000001",
            escola_id=escola.id, turma_id=turma_b.id,
            data_nascimento=date(2014, 5, 20), situacao="ativo",
        )
        crianca = Aluno(
            nome="Ana Língua", cpf="77010000002",
            escola_id=escola.id, turma_id=turma_ei.id, situacao="ativo",
        )
        db.session.add_all([aluno, crianca])
        db.session.flush()
        mat_a = Matricula(
            aluno_id=aluno.id, turma_id=turma_a.id,
            data_inicio=date(2026, 2, 1), data_fim=date(2026, 6, 30),
            situacao="transferido",
        )
        mat_b = Matricula(
            aluno_id=aluno.id, turma_id=turma_b.id,
            data_inicio=date(2026, 7, 1), situacao="cursando",
        )
        mat_ei = Matricula(
            aluno_id=crianca.id, turma_id=turma_ei.id,
            data_inicio=date(2026, 2, 1), situacao="cursando",
        )
        db.session.add_all([mat_a, mat_b, mat_ei])
        db.session.flush()
        aloc = Alocacao(
            professor_id=prof.id, turma_id=turma_a.id, disciplina_id=disc.id,
            aulas_semanais=5,
        )
        db.session.add(aloc)
        db.session.flush()
        db.session.add(HorarioAlocacao(
            alocacao_id=aloc.id, dia_semana=0, quantidade_aulas=2,
        ))
        db.session.add(RegistroNota(
            aluno_id=aluno.id, matricula_id=mat_a.id, disciplina_id=disc.id,
            bimestre=11, valor=8.25, tipo="bimestral",
        ))
        db.session.add(RegistroNota(
            aluno_id=aluno.id, matricula_id=mat_b.id, disciplina_id=disc.id,
            bimestre=11, valor=5.5, tipo="bimestral",
        ))
        db.session.add(RegistroNota(
            aluno_id=aluno.id, matricula_id=None, disciplina_id=disc.id,
            bimestre=12, valor=7.0, tipo="bimestral",
        ))
        db.session.add(RegistroFalta(
            aluno_id=aluno.id, matricula_id=mat_a.id, disciplina_id=disc.id,
            bimestre=1, quantidade=3,
        ))
        aula = RegistroAula(
            professor_id=prof.id, turma_id=turma_a.id, disciplina_id=disc.id,
            alocacao_id=aloc.id, data=date(2026, 3, 10), bimestre=1,
            quantidade_aulas=1, conteudo="Sujeito e predicado",
            metodologia="Aula expositiva", status="pendente",
            criado_em=datetime(2026, 3, 10, 8, 30, 0),
            atualizado_em=datetime(2026, 3, 10, 8, 30, 0),
        )
        db.session.add(aula)
        db.session.flush()
        db.session.add(RegistroFrequencia(
            aluno_id=aluno.id, turma_id=turma_a.id, matricula_id=mat_a.id,
            disciplina_id=disc.id, professor_id=prof.id, alocacao_id=aloc.id,
            registro_aula_id=aula.id, data=date(2026, 3, 10), bimestre=1,
            situacao="FALTA",
        ))
        db.session.add(RelatorioDescritivo(
            aluno_id=crianca.id, matricula_id=mat_ei.id,
            eixo="Linguagem", bimestre=1, avaliacao="Conquistado",
            observacao="Criança desenvolveu linguagem oral.",
        ))
        db.session.add(HistoricoAnual(
            ano_letivo=2025, aluno_id=aluno.id, matricula_id=None,
            escola_nome="Escola José de Alencar", turma_nome="4º Ano",
            etapa="fundamental",
            dados_fechamento='{"notas":{"11":8.25},"obs":"José"}',
            situacao_final="ativo",
        ))
        ano = AnoLetivo(
            ano=2026, data_inicio=date(2026, 2, 1), data_fim=date(2026, 12, 15),
            ativo=True,
            criado_em=datetime(2026, 1, 10, 9, 0, 0),
            atualizado_em=datetime(2026, 1, 10, 9, 0, 0),
        )
        db.session.add(ano)
        db.session.flush()
        db.session.add(PeriodoLetivo(
            ano_letivo_id=ano.id, numero=1, nome="1º Bimestre",
            data_inicio=date(2026, 2, 1), data_fim=date(2026, 4, 30),
        ))
        db.session.add(DiaCalendario(
            ano_letivo_id=ano.id, data=date(2026, 2, 1), tipo="LETIVO",
            descricao="Início",
        ))
        db.session.add(Planejamento(
            professor_id=prof.id, turma_id=turma_a.id,
            conteudo="Plano de março", arquivo="anexo-ensaio.pdf",
            arquivo_original="plano.pdf", status="pendente", arquivado=False,
            data_envio=datetime(2026, 3, 1, 10, 0, 0),
        ))
        if ConfiguracaoAcademica.query.first() is None:
            db.session.add(ConfiguracaoAcademica(
                media_minima=6.0, atualizado_em=datetime(2026, 1, 1, 0, 0, 0),
            ))
        db.session.commit()
        ids = {
            "escola_id": escola.id,
            "usuario_id": admin.id,
            "senha_hash": hash_admin,
            "aluno_id": aluno.id,
            "crianca_id": crianca.id,
            "mat_a": mat_a.id,
            "mat_b": mat_b.id,
            "mat_ei": mat_ei.id,
            "disc_id": disc.id,
            "aula_id": aula.id,
            "turma_a": turma_a.id,
            "turma_b": turma_b.id,
        }
    return ids


def _preparar_dest_alembic(path):
    uri = uri_sqlite_arquivo(path)
    aplicar_migrations_destino(uri, confirmar=True, permitir_sqlite_teste=True)
    return uri


class ProtecaoDestinoTest(unittest.TestCase):
    def test_uri_ausente_e_recusada(self):
        diag = validar_destino_postgresql("", confirmar=True)
        self.assertFalse(diag["executar"])
        self.assertTrue(diag["recusas"])

    def test_sem_confirmar_nao_autoriza(self):
        uri = "postgresql+psycopg://u:SEGREDO@localhost:5432/%s" % BANCO_ENSAIO_PERMITIDO
        diag = validar_destino_postgresql(uri, confirmar=False)
        self.assertFalse(diag["executar"])
        texto = formatar_diagnostico_destino(diag)
        self.assertNotIn("SEGREDO", texto)
        self.assertIn("localhost", texto)
        self.assertIn(BANCO_ENSAIO_PERMITIDO, texto)

    def test_educamais_pg_test_e_recusado(self):
        uri = "postgresql+psycopg://u:x@localhost:5432/educamais_pg_test"
        diag = validar_destino_postgresql(uri, confirmar=True)
        self.assertFalse(diag["executar"])
        self.assertTrue(any("pg_test" in r or "recusado" in r.lower() for r in diag["recusas"]))

    def test_postgres_template_e_nome_ambiguo_recusados(self):
        for banco in ("postgres", "template0", "template1", "educamais"):
            uri = "postgresql+psycopg://u:x@localhost:5432/%s" % banco
            diag = validar_destino_postgresql(uri, confirmar=True)
            self.assertFalse(diag["executar"], banco)

    def test_host_remoto_e_recusado(self):
        uri = "postgresql+psycopg://u:x@8.8.8.8:5432/%s" % BANCO_ENSAIO_PERMITIDO
        diag = validar_destino_postgresql(uri, confirmar=True)
        self.assertFalse(diag["executar"])
        self.assertTrue(any("remoto" in r.lower() for r in diag["recusas"]))

    def test_localhost_permitido_com_confirmar(self):
        uri = "postgresql+psycopg://ensaio@127.0.0.1:5432/%s" % BANCO_ENSAIO_PERMITIDO
        diag = validar_destino_postgresql(uri, confirmar=True)
        self.assertEqual(diag["recusas"], [])
        self.assertTrue(diag["executar"])

    def test_uri_ensaio_nao_reutiliza_pg_test(self):
        uri_compat = "postgresql+psycopg://u:x@localhost:5432/educamais_pg_test"
        uri_migracao = (
            "postgresql+psycopg://educamais_test@localhost:5432/%s"
            % BANCO_ENSAIO_PERMITIDO
        )
        # 1) Só a URI de compatibilidade: o ensaio não a herda.
        with patch.dict(os.environ, {"EDUCAMAIS_TEST_DATABASE_URI": uri_compat}):
            os.environ.pop("EDUCAMAIS_MIGRACAO_TEST_DATABASE_URI", None)
            self.assertIsNone(resolver_uri_ensaio_migracao())
            self.assertEqual(resolver_uri_teste_postgresql(), uri_compat)
        # 2) URI de migração definida: é a única lida, com ou sem a de compatibilidade.
        with patch.dict(os.environ, {
            "EDUCAMAIS_MIGRACAO_TEST_DATABASE_URI": uri_migracao,
            "EDUCAMAIS_TEST_DATABASE_URI": uri_compat,
        }):
            self.assertEqual(resolver_uri_ensaio_migracao(), uri_migracao)
            self.assertNotEqual(resolver_uri_ensaio_migracao(), uri_compat)
        with patch.dict(os.environ, {"EDUCAMAIS_MIGRACAO_TEST_DATABASE_URI": uri_migracao}):
            os.environ.pop("EDUCAMAIS_TEST_DATABASE_URI", None)
            self.assertEqual(resolver_uri_ensaio_migracao(), uri_migracao)
            self.assertIsNone(resolver_uri_teste_postgresql())


class GrafoImportacaoTest(unittest.TestCase):
    def test_ordem_respeita_foreign_keys(self):
        schema = mapear_schema()
        plano = ordem_importacao(schema)
        self.assertEqual(plano["ciclos"], [])
        pos = {n: i for i, n in enumerate(plano["ordem"])}
        for child, parent, _col in plano["arestas"]:
            if child == parent:
                continue
            self.assertLess(
                pos[parent], pos[child],
                "%s deve vir antes de %s" % (parent, child),
            )
        for t in (
            "escola", "usuario", "turma", "aluno", "matricula",
            "registro_nota", "registro_aula", "registro_frequencia",
        ):
            self.assertIn(t, pos)

    def test_tabelas_essenciais_estao_no_mapa(self):
        from src.migracoes import TABELAS_ESSENCIAIS
        schema = mapear_schema()
        for t in TABELAS_ESSENCIAIS:
            self.assertIn(t, schema["tabelas"], t)


class SequenceHelperTest(unittest.TestCase):
    def test_tabela_vazia(self):
        self.assertEqual(proximo_setval(None), (1, False))

    def test_ids_e_nao_sequenciais(self):
        self.assertEqual(proximo_setval(10), (10, True))
        self.assertEqual(proximo_setval(1), (1, True))


class OriginalIntactoTest(unittest.TestCase):
    def test_recusa_usar_sqlite_original_como_copia(self):
        orig = caminho_sqlite_original()
        if not orig.exists():
            self.skipTest("instance/educamais.db ausente neste ambiente.")
        with self.assertRaises(FonteInsegura):
            recusar_se_original(orig)

    def test_copia_de_arquivo_temporario_nao_altera_origem(self):
        origem = _sqlite_vazio()
        dest = _sqlite_vazio()
        _apagar(dest)
        try:
            import sqlite3
            con = sqlite3.connect(origem)
            con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
            con.execute("INSERT INTO t (v) VALUES ('José')")
            con.commit()
            con.close()
            antes = fingerprint_arquivo(origem)
            copiar_sqlite_arquivo(origem, dest)
            depois = fingerprint_arquivo(origem)
            self.assertEqual(antes, depois)
            self.assertEqual(fingerprint_arquivo(dest)["sha256"], antes["sha256"])
        finally:
            _apagar(origem, dest)

    def test_preparar_copia_sem_confirmar_nao_cria(self):
        origem = _sqlite_vazio()
        dest = Path(tempfile.mkdtemp()) / "copia.db"
        try:
            import sqlite3
            sqlite3.connect(origem).close()
            rel = preparar_copia_trabalho(
                original=origem, copia=dest, confirmar=False,
            )
            self.assertFalse(rel["executado"])
            self.assertFalse(dest.exists())
        finally:
            _apagar(origem)


class AuditoriaFonteTest(unittest.TestCase):
    def test_auditoria_nao_altera_arquivo(self):
        path = _sqlite_vazio()
        try:
            from src import create_app
            from tests.support import apontar_banco_teste
            old = Config.SQLALCHEMY_DATABASE_URI
            apontar_banco_teste(uri_sqlite_arquivo(path))
            os.environ["EDUCAMAIS_ENV"] = "testing"
            os.environ.pop("EDUCAMAIS_DEMO", None)
            app = create_app()
            ids = _popular_fonte(app)
            with app.app_context():
                from src import db
                db.session.remove()
                db.engine.dispose()
            Config.SQLALCHEMY_DATABASE_URI = old
            antes = fingerprint_arquivo(path)
            rel = auditar_fonte_sqlite(original=path)
            depois = fingerprint_arquivo(path)
            self.assertEqual(antes, depois)
            self.assertEqual(rel["contagens_nomeadas"]["alunos"], 2)
            self.assertGreaterEqual(rel["nulos_fk"]["registro_nota.matricula_id"], 1)
            self.assertEqual(ids["aluno_id"], ids["aluno_id"])
        finally:
            _apagar(path)
            preparar_ambiente_teste(demo=True)


class CopiaLocalPreservacaoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = os.environ.copy()
        cls._src = _sqlite_vazio()
        cls._dst = _sqlite_vazio()
        from tests.support import apontar_banco_teste
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        apontar_banco_teste(uri_sqlite_arquivo(cls._src))
        os.environ["EDUCAMAIS_ENV"] = "testing"
        os.environ.pop("EDUCAMAIS_DEMO", None)
        from src import create_app
        cls.src_app = create_app()
        cls.ids = _popular_fonte(cls.src_app)
        with cls.src_app.app_context():
            from src import db
            db.session.remove()
            db.engine.dispose()
        cls.dest_uri = _preparar_dest_alembic(cls._dst)
        cls.rel = importar_dados(
            cls._src, cls.dest_uri,
            confirmar=True,
            permitir_destino_sqlite_teste=True,
            exigir_head_fonte=False,
        )
        cls.val = validar_equivalencia(
            cls._src, cls.dest_uri, permitir_destino_sqlite_teste=True,
        )

    @classmethod
    def tearDownClass(cls):
        os.environ.clear()
        os.environ.update(cls._env)
        preparar_ambiente_teste(demo=True)
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        _apagar(cls._src, cls._dst)

    def test_contagem_fonte_igual_destino(self):
        self.assertTrue(self.val["ok"], self.val["diferencas"][:5])
        for t, c in self.val["contagens"].items():
            self.assertEqual(c["fonte"], c["destino"], t)

    def test_preserva_ids(self):
        from sqlalchemy import create_engine, text
        eng = create_engine(self.dest_uri)
        try:
            with eng.connect() as c:
                aluno = c.execute(
                    text("SELECT id, nome FROM aluno WHERE id=:i"),
                    {"i": self.ids["aluno_id"]},
                ).first()
                self.assertIsNotNone(aluno)
                self.assertEqual(aluno[1], "José da Conceição")
                self.assertEqual(
                    c.execute(text("SELECT id FROM matricula WHERE id=:i"),
                              {"i": self.ids["mat_a"]}).scalar(),
                    self.ids["mat_a"],
                )
        finally:
            eng.dispose()

    def test_preserva_null_matricula_id(self):
        from sqlalchemy import create_engine, text
        eng = create_engine(self.dest_uri)
        try:
            with eng.connect() as c:
                n = c.execute(
                    text("SELECT COUNT(*) FROM registro_nota WHERE matricula_id IS NULL")
                ).scalar()
                self.assertGreaterEqual(n, 1)
                vazio = c.execute(
                    text(
                        "SELECT COUNT(*) FROM registro_nota "
                        "WHERE matricula_id IS NOT NULL AND matricula_id = ''"
                    )
                ).scalar()
                self.assertEqual(vazio, 0)
        finally:
            eng.dispose()

    def test_preserva_hash_senha(self):
        from sqlalchemy import create_engine, text
        eng = create_engine(self.dest_uri)
        try:
            with eng.connect() as c:
                h = c.execute(
                    text("SELECT senha_hash FROM usuario WHERE id=:i"),
                    {"i": self.ids["usuario_id"]},
                ).scalar()
            self.assertEqual(h, self.ids["senha_hash"])
            self.assertTrue(str(h).startswith("$2"))
        finally:
            eng.dispose()

    def test_preserva_notas(self):
        from sqlalchemy import create_engine, text
        eng = create_engine(self.dest_uri)
        try:
            with eng.connect() as c:
                v = c.execute(
                    text(
                        "SELECT valor FROM registro_nota "
                        "WHERE matricula_id=:m AND bimestre=11"
                    ),
                    {"m": self.ids["mat_a"]},
                ).scalar()
            self.assertEqual(float(v), 8.25)
        finally:
            eng.dispose()

    def test_preserva_faltas(self):
        from sqlalchemy import create_engine, text
        eng = create_engine(self.dest_uri)
        try:
            with eng.connect() as c:
                q = c.execute(
                    text("SELECT quantidade FROM registro_falta WHERE matricula_id=:m"),
                    {"m": self.ids["mat_a"]},
                ).scalar()
            self.assertEqual(int(q), 3)
        finally:
            eng.dispose()

    def test_preserva_frequencia(self):
        from sqlalchemy import create_engine, text
        eng = create_engine(self.dest_uri)
        try:
            with eng.connect() as c:
                s = c.execute(
                    text(
                        "SELECT situacao FROM registro_frequencia "
                        "WHERE matricula_id=:m AND registro_aula_id=:a"
                    ),
                    {"m": self.ids["mat_a"], "a": self.ids["aula_id"]},
                ).scalar()
            self.assertEqual(s, "FALTA")
        finally:
            eng.dispose()

    def test_preserva_relatorio_descritivo(self):
        from sqlalchemy import create_engine, text
        eng = create_engine(self.dest_uri)
        try:
            with eng.connect() as c:
                row = c.execute(
                    text(
                        "SELECT eixo, observacao FROM relatorio_descritivo "
                        "WHERE matricula_id=:m"
                    ),
                    {"m": self.ids["mat_ei"]},
                ).first()
            self.assertEqual(row[0], "Linguagem")
            self.assertIn("linguagem", row[1].lower())
        finally:
            eng.dispose()

    def test_preserva_historico_e_json(self):
        from sqlalchemy import create_engine, text
        eng = create_engine(self.dest_uri)
        try:
            with eng.connect() as c:
                dados = c.execute(
                    text("SELECT dados_fechamento FROM historico_anual WHERE aluno_id=:i"),
                    {"i": self.ids["aluno_id"]},
                ).scalar()
            self.assertEqual(dados, '{"notas":{"11":8.25},"obs":"José"}')
        finally:
            eng.dispose()

    def test_transferencias_mesmo_ano(self):
        from sqlalchemy import create_engine, text
        eng = create_engine(self.dest_uri)
        try:
            with eng.connect() as c:
                n = c.execute(
                    text("SELECT COUNT(*) FROM matricula WHERE aluno_id=:i"),
                    {"i": self.ids["aluno_id"]},
                ).scalar()
                notas = list(c.execute(
                    text(
                        "SELECT matricula_id, valor FROM registro_nota "
                        "WHERE matricula_id IS NOT NULL AND aluno_id=:i"
                    ),
                    {"i": self.ids["aluno_id"]},
                ))
            self.assertEqual(n, 2)
            valores = {int(r[0]): float(r[1]) for r in notas}
            self.assertEqual(valores[self.ids["mat_a"]], 8.25)
            self.assertEqual(valores[self.ids["mat_b"]], 5.5)
        finally:
            eng.dispose()

    def test_segunda_execucao_nao_duplica(self):
        with self.assertRaises(MigracaoAbortada) as ctx:
            importar_dados(
                self._src, self.dest_uri,
                confirmar=True,
                permitir_destino_sqlite_teste=True,
                exigir_head_fonte=False,
            )
        self.assertIn("já possui dados", str(ctx.exception).lower())
        from sqlalchemy import create_engine, text
        eng = create_engine(self.dest_uri)
        try:
            with eng.connect() as c:
                n = c.execute(text("SELECT COUNT(*) FROM aluno")).scalar()
            self.assertEqual(n, 2)
        finally:
            eng.dispose()

    def test_app_le_dados_migrados(self):
        from tests.support import apontar_banco_teste
        old = Config.SQLALCHEMY_DATABASE_URI
        env = os.environ.copy()
        try:
            os.environ["EDUCAMAIS_ENV"] = "development"
            os.environ["EDUCAMAIS_DEMO"] = "0"
            os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-app-migrado"
            apontar_banco_teste(self.dest_uri)
            from src import create_app
            app = create_app()
            app.testing = True
            client = app.test_client()
            resp = client.post("/api/auth/login", json={
                "login": "gestor.ensaio", "senha": SENHA_PESSOAL,
            })
            self.assertEqual(resp.status_code, 200, resp.get_json())
            dash = client.get("/api/dashboard")
            self.assertEqual(dash.status_code, 200)
            body = (dash.get_json() or {}).get("data") or {}
            self.assertTrue(body.get("escola") or body.get("escolas"))
            self.assertTrue(body.get("turmas"))
            with app.app_context():
                from src.models import RegistroNota, Matricula, HistoricoAnual
                self.assertEqual(
                    RegistroNota.query.filter_by(matricula_id=self.ids["mat_a"]).one().valor,
                    8.25,
                )
                self.assertEqual(
                    Matricula.query.filter_by(id=self.ids["mat_a"]).one().situacao,
                    "transferido",
                )
                self.assertIn("José", HistoricoAnual.query.first().dados_fechamento)
        finally:
            Config.SQLALCHEMY_DATABASE_URI = old
            os.environ.clear()
            os.environ.update(env)
            preparar_ambiente_teste(demo=True)


class RollbackEOrfaosTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        self._src = _sqlite_vazio()
        self._dst = _sqlite_vazio()
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        Config.SQLALCHEMY_DATABASE_URI = self._old_uri
        _apagar(self._src, self._dst)

    def test_rollback_em_falha_de_fk(self):
        from tests.support import apontar_banco_teste
        apontar_banco_teste(uri_sqlite_arquivo(self._src))
        os.environ["EDUCAMAIS_ENV"] = "testing"
        os.environ.pop("EDUCAMAIS_DEMO", None)
        from src import create_app
        app = create_app()
        _popular_fonte(app)
        with app.app_context():
            from src import db
            db.session.remove()
            db.engine.dispose()
        import sqlite3
        con = sqlite3.connect(self._src)
        try:
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute(
                "INSERT INTO registro_nota (valor, bimestre, tipo, aluno_id, disciplina_id, matricula_id) "
                "VALUES (9.0, 21, 'bimestral', 1, 99999, 1)"
            )
            con.commit()
        finally:
            con.close()
        dest_uri = _preparar_dest_alembic(self._dst)
        with self.assertRaises(MigracaoAbortada) as ctx:
            importar_dados(
                self._src, dest_uri,
                confirmar=True,
                permitir_destino_sqlite_teste=True,
                exigir_head_fonte=False,
            )
        self.assertIn("registro_nota", str(ctx.exception).lower())
        from sqlalchemy import create_engine, inspect, text
        eng = create_engine(dest_uri)
        try:
            with eng.connect() as c:
                insp = inspect(c)
                for t in ("aluno", "escola", "registro_nota", "usuario"):
                    if insp.has_table(t):
                        n = c.execute(text("SELECT COUNT(*) FROM %s" % t)).scalar()
                        self.assertEqual(n, 0, t)
        finally:
            eng.dispose()

    def test_orfaos_sao_detectados_na_validacao(self):
        from tests.support import apontar_banco_teste
        apontar_banco_teste(uri_sqlite_arquivo(self._src))
        os.environ["EDUCAMAIS_ENV"] = "testing"
        os.environ.pop("EDUCAMAIS_DEMO", None)
        from src import create_app
        app = create_app()
        _popular_fonte(app)
        with app.app_context():
            from src import db
            db.session.remove()
            db.engine.dispose()
        dest_uri = _preparar_dest_alembic(self._dst)
        importar_dados(
            self._src, dest_uri,
            confirmar=True,
            permitir_destino_sqlite_teste=True,
            exigir_head_fonte=False,
        )
        import sqlite3
        con = sqlite3.connect(self._dst)
        try:
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute(
                "INSERT INTO registro_nota (valor, bimestre, tipo, aluno_id, disciplina_id, matricula_id) "
                "VALUES (1.0, 31, 'bimestral', 1, 1, 99999)"
            )
            con.commit()
        finally:
            con.close()
        val = validar_equivalencia(
            self._src, dest_uri, permitir_destino_sqlite_teste=True,
        )
        self.assertFalse(val["ok"])
        self.assertTrue(val["orfaos_destino"] or val["diferencas"])


class UploadsAuditoriaTest(unittest.TestCase):
    def test_nao_altera_arquivos(self):
        pasta = tempfile.mkdtemp(prefix="educamais_up_")
        alvo = Path(pasta) / "anexo-ensaio.pdf"
        alvo.write_bytes(b"%PDF-1.4 ensaio")
        antes = fingerprint_arquivo(alvo)
        rel = auditar_uploads(sqlite_path=None, pasta=pasta)
        depois = fingerprint_arquivo(alvo)
        self.assertEqual(antes, depois)
        self.assertEqual(rel["n_arquivos"], 1)
        self.assertFalse(rel["alterado"])
        self.assertIn("cutover", rel["cutover"].lower())


class EnsaiarSemConfirmarTest(unittest.TestCase):
    def test_pipeline_sem_confirmar_nao_prepara_destino(self):
        dest = _sqlite_vazio()
        src = _sqlite_vazio()
        try:
            rel = ensaiar_migracao(
                src, uri_sqlite_arquivo(dest),
                confirmar=False,
                permitir_destino_sqlite_teste=True,
            )
            self.assertFalse(rel.get("executado"))
            self.assertNotIn("importacao", rel)
        finally:
            _apagar(src, dest)


@unittest.skipUnless(
    URI_PG_ENSAIO,
    "Defina EDUCAMAIS_MIGRACAO_TEST_DATABASE_URI para educamais_migracao_test "
    "(não reutilize educamais_pg_test). SKIP não é aprovação do ensaio real.",
)
class EnsaioPostgreSQLRealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        diag = validar_destino_postgresql(URI_PG_ENSAIO, confirmar=False)
        if diag["recusas"]:
            raise unittest.SkipTest(
                "URI de ensaio recusada (não conectar): %s"
                % "; ".join(diag["recusas"])
            )
        cls._env = os.environ.copy()
        cls._src = _sqlite_vazio()
        from tests.support import apontar_banco_teste
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        apontar_banco_teste(uri_sqlite_arquivo(cls._src))
        os.environ["EDUCAMAIS_ENV"] = "testing"
        os.environ.pop("EDUCAMAIS_DEMO", None)
        from src import create_app
        app = create_app()
        cls.ids = _popular_fonte(app)
        with app.app_context():
            from src import db
            db.session.remove()
            db.engine.dispose()

    @classmethod
    def tearDownClass(cls):
        os.environ.clear()
        os.environ.update(cls._env)
        preparar_ambiente_teste(demo=True)
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        _apagar(cls._src)

    def test_migracao_real_preserva_e_ajusta_sequence(self):
        from sqlalchemy.exc import OperationalError
        try:
            rel = ensaiar_migracao(
                self._src, URI_PG_ENSAIO,
                confirmar=True, limpar_destino=True, preparar_schema=True,
                exigir_head_fonte=False,
            )
        except OperationalError as exc:
            raise unittest.SkipTest(
                "PostgreSQL de ensaio inacessível: %s"
                % formatar_diagnostico_destino(
                    validar_destino_postgresql(URI_PG_ENSAIO, confirmar=False)
                )
            ) from exc
        self.assertTrue(rel["validacao"]["ok"], rel["validacao"].get("diferencas"))
        seqs = rel["importacao"]["sequences"]
        self.assertTrue(seqs)
        aluno_seq = next(s for s in seqs if s["tabela"] == "aluno")
        self.assertGreaterEqual(int(aluno_seq["max_id"]), int(self.ids["aluno_id"]))
        self.assertTrue(aluno_seq["is_called"])
        from sqlalchemy import create_engine, text
        from src.config import resolver_opcoes_engine
        eng = create_engine(URI_PG_ENSAIO, **resolver_opcoes_engine(URI_PG_ENSAIO))
        try:
            with eng.connect() as c:
                nxt = c.execute(
                    text("SELECT last_value, is_called FROM %s" % aluno_seq["sequence"])
                ).first()
                self.assertTrue(nxt[1])
                self.assertGreaterEqual(int(nxt[0]), int(aluno_seq["max_id"]))
        finally:
            eng.dispose()
        with self.assertRaises(MigracaoAbortada):
            importar_dados(
                self._src, URI_PG_ENSAIO,
                confirmar=True, exigir_head_fonte=False,
            )
