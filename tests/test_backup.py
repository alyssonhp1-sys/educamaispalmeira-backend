"""Backup/restore: PostgreSQL (pg_dump mockado) + SQLite de desenvolvimento.

Não grava em instance/educamais.db nem em instance/uploads reais.
PostgreSQL real: EDUCAMAIS_RESTORE_TEST_DATABASE_URI ou CREATEDB a partir
de EDUCAMAIS_TEST_DATABASE_URI para educamais_restore_test (não trunca educamais_pg_test).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.engine.url import make_url

from src.backup import (
    ARQUIVO_DUMP_PG,
    ARQUIVO_MANIFESTO,
    BANCO_RESTORE_PERMITIDO,
    MARCADOR_COMPLETO,
    STATUS_ARQUIVO_SEM_REFERENCIA,
    STATUS_REFERENCIA_SEM_ARQUIVO,
    BackupErro,
    RestoreRecusado,
    argv_pg_dump,
    argv_pg_restore,
    auditar_consistencia_uploads,
    auditar_uploads_atual,
    copiar_uploads,
    criar_backup,
    criar_backup_sqlite_dev,
    destino_backup_proibido,
    limpar_schema_restore_test,
    listar_arquivos_uploads,
    manifesto_contem_segredo,
    mascarar_texto,
    pacote_valido,
    restaurar_pacote,
    resolver_backup_dir,
    resolver_uri_restore_teste,
    sha256_arquivo,
    validar_destino_restore,
    verificar_pacote,
    _engine as make_backup_engine,
)
from src.config import Config, resolver_uri_teste_postgresql
from src.migracoes import REVISION_HEAD
from tests.support import apontar_banco_teste, preparar_ambiente_teste


URI_PG_TESTE = resolver_uri_teste_postgresql()
URI_PG_RESTORE = resolver_uri_restore_teste()
SENHA_URI = "S3cr3tPassBackup"
URI_PG_FALSA = (
    "postgresql+psycopg://usuario:%s@localhost:5432/educamais_pg_test" % SENHA_URI
)
URI_RESTORE_FALSA = (
    "postgresql+psycopg://usuario:%s@localhost:5432/%s" % (SENHA_URI, BANCO_RESTORE_PERMITIDO)
)

_FP_ORIGINAL = None
_FP_UPLOADS = None


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _fingerprint(path):
    st = path.stat()
    return {"size": st.st_size, "sha256": _sha256(path)}


def setUpModule():
    global _FP_ORIGINAL, _FP_UPLOADS
    raiz = Path(Config.ROOT_DIR) / "instance"
    orig = raiz / "educamais.db"
    _FP_ORIGINAL = _fingerprint(orig) if orig.exists() else None
    pasta = raiz / "uploads"
    _FP_UPLOADS = {}
    if pasta.is_dir():
        for p in sorted(pasta.rglob("*")):
            if p.is_file():
                _FP_UPLOADS[str(p)] = _fingerprint(p)


def tearDownModule():
    raiz = Path(Config.ROOT_DIR) / "instance"
    orig = raiz / "educamais.db"
    if _FP_ORIGINAL is not None:
        if not orig.exists() or _fingerprint(orig) != _FP_ORIGINAL:
            raise AssertionError("instance/educamais.db foi modificado nos testes de backup.")
    for nome, fp in (_FP_UPLOADS or {}).items():
        p = Path(nome)
        if not p.exists() or _fingerprint(p) != fp:
            raise AssertionError("Upload real %s foi modificado." % nome)


class _Proc:
    def __init__(self, code=0, stdout=b"", stderr=b""):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


def _digest_bytes(dados):
    return hashlib.sha256(dados).hexdigest(), len(dados)


def _pacote_sintetico(raiz, dump=b"DUMP-PG", uploads=None, tipo="postgresql"):
    pasta = Path(raiz) / "backup_20200101_000000"
    pasta.mkdir()
    nome_dump = ARQUIVO_DUMP_PG if tipo == "postgresql" else "database.sqlite"
    (pasta / nome_dump).write_bytes(dump)
    up = pasta / "uploads"
    up.mkdir()
    arquivos = []
    for name, content in (uploads or {}).items():
        alvo = up / name
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(content)
        d, t = sha256_arquivo(alvo)
        arquivos.append({"rel": name.replace("\\", "/"), "tamanho": t, "sha256": d})
    dh, dt = sha256_arquivo(pasta / nome_dump)
    man = {
        "formato": 1,
        "versao_formato": "educamais-backup-1",
        "status": "completo",
        "criado_em": "2020-01-01T00:00:00+00:00",
        "ambiente": "testing",
        "banco": {
            "tipo": tipo,
            "nome": BANCO_RESTORE_PERMITIDO if tipo == "postgresql" else "tmp.db",
            "host": "localhost" if tipo == "postgresql" else None,
            "porta": 5432 if tipo == "postgresql" else None,
            "usuario": "usuario" if tipo == "postgresql" else None,
        },
        "alembic": {"revisao": None, "head_esperado": REVISION_HEAD},
        "aplicacao": {"git_commit": None},
        "dump": {"arquivo": nome_dump, "tamanho": dt, "sha256": dh},
        "uploads": {
            "n_arquivos": len(arquivos),
            "tamanho_total": sum(a["tamanho"] for a in arquivos),
            "arquivos": arquivos,
        },
        "consistencia": {
            "n_ok": 0,
            "n_referencia_sem_arquivo": 0,
            "n_arquivo_sem_referencia": 0,
            "referencia_sem_arquivo": [],
            "arquivo_sem_referencia": [],
        },
        "contagens": {},
        "pks": {},
    }
    (pasta / ARQUIVO_MANIFESTO).write_text(
        json.dumps(man, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (pasta / MARCADOR_COMPLETO).write_text("completo\n", encoding="utf-8")
    return pasta, man


class HelpersBackupTest(unittest.TestCase):
    def test_a_mascarar_senha_e_uri(self):
        texto = mascarar_texto(
            "erro " + URI_PG_FALSA + " senha=" + SENHA_URI,
            URI_PG_FALSA,
            [SENHA_URI],
        )
        self.assertNotIn(SENHA_URI, texto)
        self.assertNotIn("usuario:%s@" % SENHA_URI, texto)

    def test_b_sha256(self):
        tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            tmp.write(b"abc123")
            tmp.close()
            d, n = sha256_arquivo(tmp.name)
            self.assertEqual(n, 6)
            self.assertEqual(d, hashlib.sha256(b"abc123").hexdigest())
        finally:
            os.unlink(tmp.name)

    def test_c_copia_byte_a_byte(self):
        origem = tempfile.mkdtemp(prefix="edu_up_src_")
        destino = tempfile.mkdtemp(prefix="edu_up_dst_")
        try:
            (Path(origem) / "a.pdf").write_bytes(b"%PDF-1.4 bytes")
            sub = Path(origem) / "sub"
            sub.mkdir()
            (sub / "b.docx").write_bytes(b"PK\x03\x04doc")
            copiados, ign = copiar_uploads(origem, destino)
            self.assertEqual(ign, [])
            self.assertEqual(len(copiados), 2)
            self.assertEqual((Path(destino) / "a.pdf").read_bytes(), b"%PDF-1.4 bytes")
            self.assertEqual((Path(destino) / "sub" / "b.docx").read_bytes(), b"PK\x03\x04doc")
        finally:
            shutil.rmtree(origem, ignore_errors=True)
            shutil.rmtree(destino, ignore_errors=True)

    def test_d_referencia_sem_arquivo(self):
        cons = auditar_consistencia_uploads(
            [{"tabela": "planejamento", "id": 1, "arquivo": "sumiu.pdf"}],
            [],
        )
        self.assertEqual(cons["n_referencia_sem_arquivo"], 1)
        self.assertEqual(cons["referencia_sem_arquivo"][0]["status"], STATUS_REFERENCIA_SEM_ARQUIVO)

    def test_e_arquivo_orfao(self):
        cons = auditar_consistencia_uploads(
            [],
            [{"rel": "orfao.pdf", "tamanho": 10}],
        )
        self.assertEqual(cons["n_arquivo_sem_referencia"], 1)
        self.assertEqual(
            cons["arquivo_sem_referencia"][0]["status"], STATUS_ARQUIVO_SEM_REFERENCIA,
        )

    def test_f_pacote_incompleto_nao_e_valido(self):
        tmp = tempfile.mkdtemp(prefix="edu_bk_")
        try:
            partial = Path(tmp) / "backup_20200101_000000.partial"
            partial.mkdir()
            (partial / MARCADOR_COMPLETO).write_text("completo\n", encoding="utf-8")
            (partial / ARQUIVO_MANIFESTO).write_text(
                json.dumps({"status": "completo"}), encoding="utf-8",
            )
            self.assertFalse(pacote_valido(partial))
            incompleto = Path(tmp) / "backup_20200101_000001"
            incompleto.mkdir()
            (incompleto / ARQUIVO_MANIFESTO).write_text(
                json.dumps({"status": "completo"}), encoding="utf-8",
            )
            self.assertFalse(pacote_valido(incompleto))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_j_destino_nao_publico(self):
        raiz = Config.ROOT_DIR
        self.assertTrue(destino_backup_proibido(os.path.join(raiz, "frontend", "public")))
        self.assertTrue(destino_backup_proibido(os.path.join(raiz, "frontend", "dist", "x")))
        self.assertTrue(destino_backup_proibido(os.path.join(raiz, "src", "static")))
        self.assertTrue(destino_backup_proibido(os.path.join(raiz, "instance", "uploads")))
        self.assertFalse(destino_backup_proibido(os.path.join(raiz, "var", "backups")))

    def test_k_gitignore(self):
        texto = Path(Config.ROOT_DIR, ".gitignore").read_text(encoding="utf-8")
        self.assertIn("var/backups/", texto)

    def test_argv_sem_senha(self):
        url = make_url(URI_PG_FALSA)
        argv = argv_pg_dump("pg_dump.exe", url, "C:/tmp/database.dump")
        self.assertIn("-Fc", argv)
        self.assertIn("--no-password", argv)
        self.assertTrue(all(SENHA_URI not in str(a) for a in argv))
        argv_r = argv_pg_restore("pg_restore.exe", url, "C:/tmp/database.dump")
        self.assertTrue(all(SENHA_URI not in str(a) for a in argv_r))

    def test_l_restore_sem_trava(self):
        diag = validar_destino_restore(
            URI_RESTORE_FALSA, confirmar=False, ciente_destruicao=False,
        )
        self.assertFalse(diag["executar"])
        with self.assertRaises(RestoreRecusado):
            restaurar_pacote(
                "/inexistente",
                URI_RESTORE_FALSA,
                tempfile.mkdtemp(),
                confirmar=False,
                ciente_destruicao=False,
            )
        prod = validar_destino_restore(
            "postgresql+psycopg://u:p@localhost/educamais",
            confirmar=True,
            ciente_destruicao=True,
        )
        self.assertTrue(prod["recusas"])
        pg_test = validar_destino_restore(
            "postgresql+psycopg://u:p@localhost/educamais_pg_test",
            confirmar=True,
            ciente_destruicao=True,
        )
        self.assertTrue(pg_test["recusas"])

    def test_descoberta_pg_dump_nao_hardcoda_pc(self):
        from src.backup import descobrir_executavel_pg, BackupErro
        try:
            exe = descobrir_executavel_pg("pg_dump")
        except BackupErro:
            self.skipTest("pg_dump não disponível neste ambiente")
        self.assertTrue(os.path.isfile(exe))
        self.assertIn("pg_dump", Path(exe).name.lower())
        self.assertNotIn("pgadmin", exe.lower())

    def test_limpar_schema_recusa_outro_banco(self):
        with self.assertRaises(RestoreRecusado):
            limpar_schema_restore_test(
                "postgresql+psycopg://u:p@localhost/educamais_pg_test"
            )


class ConsistenciaComBancoTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        preparar_ambiente_teste(demo=True)
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db = tmp.name
        from src.config import Config as Cfg
        self._old_uri = Cfg.SQLALCHEMY_DATABASE_URI
        apontar_banco_teste("sqlite:///" + Path(self._db).resolve().as_posix())
        self._uploads = tempfile.mkdtemp(prefix="edu_cons_")
        os.environ["EDUCAMAIS_UPLOAD_FOLDER"] = self._uploads
        from src import create_app
        self.app = create_app()
        self.app.testing = True

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        from src.config import Config as Cfg
        Cfg.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db)
        except OSError:
            pass
        shutil.rmtree(self._uploads, ignore_errors=True)

    def test_detecta_ausente_e_orfao(self):
        (Path(self._uploads) / "orfao.pdf").write_bytes(b"%PDF-1.4 x")
        with self.app.app_context():
            from src import db
            from src.models import Escola, Planejamento, Professor, Turma, Usuario
            escola = Escola(nome="E", inep="99900001")
            db.session.add(escola)
            db.session.flush()
            user = Usuario(
                login="p1", senha_hash="x", papel="professor", nome="P",
            )
            db.session.add(user)
            db.session.flush()
            prof = Professor(nome="P", cpf="00000000001", usuario_id=user.id, escola_id=escola.id)
            db.session.add(prof)
            turma = Turma(nome="T", ano_letivo=2026, escola_id=escola.id)
            db.session.add(turma)
            db.session.flush()
            db.session.add(Planejamento(
                professor_id=prof.id, turma_id=turma.id, conteudo="x",
                arquivo="lessons_sent_83ec06c86dac1621652253e327a9843c.docx",
            ))
            db.session.commit()
            rel = auditar_uploads_atual(pasta=self._uploads)
        self.assertEqual(rel["n_referencia_sem_arquivo"], 1)
        self.assertEqual(rel["n_arquivo_sem_referencia"], 1)
        self.assertFalse(rel["alterado"])
        self.assertEqual(
            Path(self._uploads, "orfao.pdf").read_bytes(), b"%PDF-1.4 x",
        )


class BackupPostgresMockTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        preparar_ambiente_teste(demo=True)
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db = tmp.name
        from src.config import Config as Cfg
        self._old_uri = Cfg.SQLALCHEMY_DATABASE_URI
        apontar_banco_teste("sqlite:///" + Path(self._db).resolve().as_posix())
        self._uploads = tempfile.mkdtemp(prefix="edu_bkup_")
        self._dest = tempfile.mkdtemp(prefix="edu_bkdst_")
        os.environ["EDUCAMAIS_UPLOAD_FOLDER"] = self._uploads
        from src import create_app
        self.app = create_app()
        self.app.testing = True
        self._meta_uri = "sqlite:///" + Path(self._db).resolve().as_posix()
        with self.app.app_context():
            (Path(self._uploads) / "ok.pdf").write_bytes(b"%PDF-1.4 ok")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        from src.config import Config as Cfg
        Cfg.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db)
        except OSError:
            pass
        shutil.rmtree(self._uploads, ignore_errors=True)
        shutil.rmtree(self._dest, ignore_errors=True)

    def _fake_run(self, argv, env=None, timeout=None, cwd=None):
        self._argv.append(list(argv))
        self._envs.append({k: env.get(k) for k in ("PGPASSWORD",) if env})
        nome = str(argv[0]).lower()
        if "pg_dump" in nome:
            if self._dump_code != 0:
                return _Proc(
                    self._dump_code,
                    b"",
                    ("pg_dump: password=" + SENHA_URI).encode(),
                )
            dest = argv[argv.index("-f") + 1]
            Path(dest).write_bytes(b"FAKE-PG-DUMP")
            return _Proc(0)
        if "pg_restore" in nome:
            return _Proc(self._restore_code, b"", b"restore failed " + SENHA_URI.encode())
        if len(argv) >= 2 and argv[0] == "git":
            return _Proc(0, b"abc1234\n")
        return _Proc(0)

    def test_a_i_manifesto_sem_segredos_e_log(self):
        self._argv = []
        self._envs = []
        self._dump_code = 0
        self._restore_code = 0
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-secreta-nao-vai-ao-manifesto"
        with self.app.app_context():
            with patch("src.backup._rodar_subprocesso", side_effect=self._fake_run), \
                 patch("src.backup.descobrir_executavel_pg", return_value="pg_dump"), \
                 patch("src.backup._engine", lambda _uri: make_backup_engine(self._meta_uri)):
                rel = criar_backup(
                    uri=URI_PG_FALSA,
                    pasta_uploads=self._uploads,
                    destino_raiz=self._dest,
                )
        blob = json.dumps(rel["manifesto"])
        self.assertNotIn(SENHA_URI, blob)
        self.assertNotIn(SENHA_URI, rel["relatorio"])
        self.assertNotIn("chave-secreta-nao-vai-ao-manifesto", blob)
        self.assertNotIn("chave-secreta-nao-vai-ao-manifesto", rel["relatorio"])
        self.assertFalse(manifesto_contem_segredo(rel["manifesto"], URI_PG_FALSA, [SENHA_URI]))
        self.assertTrue(rel["ok"])
        self.assertTrue(pacote_valido(rel["pacote"]))
        dump_argv = [a for a in self._argv if a and "pg_dump" in str(a[0])][0]
        self.assertTrue(all(SENHA_URI not in str(x) for x in dump_argv))
        self.assertIn("-Fc", dump_argv)
        self.assertEqual(self._envs[0].get("PGPASSWORD"), SENHA_URI)
        man = rel["manifesto"]
        self.assertEqual(man["dump"]["sha256"], hashlib.sha256(b"FAKE-PG-DUMP").hexdigest())
        self.assertEqual(man["uploads"]["n_arquivos"], 1)
        self.assertEqual(man["uploads"]["arquivos"][0]["rel"], "ok.pdf")

    def test_f_g_pg_dump_falha_nao_marca_valido(self):
        self._argv = []
        self._envs = []
        self._dump_code = 1
        self._restore_code = 0
        with self.app.app_context():
            with patch("src.backup._rodar_subprocesso", side_effect=self._fake_run), \
                 patch("src.backup.descobrir_executavel_pg", return_value="pg_dump"), \
                 patch("src.backup._engine", lambda _uri: make_backup_engine(self._meta_uri)):
                with self.assertRaises(BackupErro) as ctx:
                    criar_backup(
                        uri=URI_PG_FALSA,
                        pasta_uploads=self._uploads,
                        destino_raiz=self._dest,
                    )
        self.assertNotIn(SENHA_URI, str(ctx.exception))
        self.assertFalse(any(pacote_valido(p) for p in Path(self._dest).iterdir()))
        self.assertFalse(any(p.name.endswith(".partial") and p.is_dir() for p in Path(self._dest).iterdir()))

    def test_f_falha_apos_dump_nao_marca_valido(self):
        self._argv = []
        self._envs = []
        self._dump_code = 0

        def boom(*_a, **_k):
            raise RuntimeError("falha copia " + SENHA_URI)

        with self.app.app_context():
            with patch("src.backup._rodar_subprocesso", side_effect=self._fake_run), \
                 patch("src.backup.descobrir_executavel_pg", return_value="pg_dump"), \
                 patch("src.backup.copiar_uploads", side_effect=boom):
                with self.assertRaises(BackupErro) as ctx:
                    criar_backup(
                        uri=URI_PG_FALSA,
                        pasta_uploads=self._uploads,
                        destino_raiz=self._dest,
                    )
        self.assertNotIn(SENHA_URI, str(ctx.exception))
        self.assertFalse(any(pacote_valido(p) for p in Path(self._dest).iterdir() if p.is_dir()))

    def test_h_pg_restore_falha(self):
        self._argv = []
        self._envs = []
        self._dump_code = 0
        self._restore_code = 1
        pacote, _man = _pacote_sintetico(self._dest, uploads={"a.pdf": b"%PDF-1.4"})
        up_dest = tempfile.mkdtemp(prefix="edu_rst_")
        try:
            with patch("src.backup._rodar_subprocesso", side_effect=self._fake_run), \
                 patch("src.backup.descobrir_executavel_pg", return_value="pg_restore"):
                with self.assertRaises(BackupErro) as ctx:
                    restaurar_pacote(
                        pacote,
                        URI_RESTORE_FALSA,
                        up_dest,
                        confirmar=True,
                        ciente_destruicao=True,
                        substituir_uploads=True,
                    )
            self.assertNotIn(SENHA_URI, str(ctx.exception))
            self.assertIn("pg_restore", str(ctx.exception).lower() + ctx.exception.args[0].lower())
        finally:
            shutil.rmtree(up_dest, ignore_errors=True)

    def test_verificar_dump_alterado(self):
        pacote, _ = _pacote_sintetico(self._dest, dump=b"AAAA")
        (Path(pacote) / ARQUIVO_DUMP_PG).write_bytes(b"BBBB")
        rel = verificar_pacote(pacote)
        self.assertFalse(rel["ok"])

    def test_verificar_arquivo_ausente(self):
        pacote, _ = _pacote_sintetico(self._dest, uploads={"x.pdf": b"%PDF-1.4"})
        (Path(pacote) / "uploads" / "x.pdf").unlink()
        rel = verificar_pacote(pacote)
        self.assertFalse(rel["ok"])

    def test_backup_recusa_sqlite(self):
        with self.assertRaises(BackupErro):
            criar_backup(
                uri="sqlite:///" + Path(self._db).resolve().as_posix(),
                pasta_uploads=self._uploads,
                destino_raiz=self._dest,
            )

    def test_backup_recusa_pasta_publica(self):
        publico = os.path.join(Config.ROOT_DIR, "frontend", "public", "bk-teste-nao-usar")
        self._argv = []
        self._envs = []
        self._dump_code = 0
        with self.assertRaises(BackupErro):
            criar_backup(
                uri=URI_PG_FALSA,
                pasta_uploads=self._uploads,
                destino_raiz=publico,
            )
        self.assertFalse(os.path.isdir(publico))


def _popular_academico(app, pasta_uploads):
    from src import db
    from src.auditoria import ESCOLA_CRIADA, registrar_auditoria
    from src.models import (
        Aluno,
        AuditLog,
        Escola,
        HistoricoAnual,
        Matricula,
        Planejamento,
        Professor,
        RegistroFalta,
        RegistroFrequencia,
        RegistroNota,
        Turma,
        Usuario,
        Disciplina,
        Alocacao,
        RegistroAula,
    )
    with app.app_context():
        escola = Escola(nome="Escola Backup", inep="99100001", oferta_fundamental=True)
        db.session.add(escola)
        db.session.flush()
        user = Usuario(login="profbk", senha_hash="x", papel="professor", nome="Prof B")
        db.session.add(user)
        db.session.flush()
        prof = Professor(nome="Prof B", cpf="99111111111", usuario_id=user.id, escola_id=escola.id)
        db.session.add(prof)
        turma = Turma(nome="5A", ano_letivo=2026, etapa_ensino="fundamental", escola_id=escola.id)
        db.session.add(turma)
        disc = Disciplina.query.filter_by(nome="Matemática").first()
        if disc is None:
            disc = Disciplina(nome="Matemática")
            db.session.add(disc)
            db.session.flush()
        aloc = Alocacao(professor_id=prof.id, turma_id=turma.id, disciplina_id=disc.id)
        db.session.add(aloc)
        aluno = Aluno(
            nome="Aluno B", cpf="99222222222", data_nascimento=date(2014, 1, 1),
            escola_id=escola.id, turma_id=turma.id,
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
            aluno_id=aluno.id, matricula_id=mat.id, disciplina_id=disc.id,
            bimestre=11, valor=8.0, tipo="bimestral",
        ))
        db.session.add(RegistroFalta(
            aluno_id=aluno.id, matricula_id=mat.id, disciplina_id=disc.id,
            bimestre=1, quantidade=2,
        ))
        aula = RegistroAula(
            professor_id=prof.id, turma_id=turma.id, disciplina_id=disc.id,
            alocacao_id=aloc.id, data=date(2026, 3, 10), bimestre=1,
            quantidade_aulas=1, conteudo="c", metodologia="m", status="validada",
        )
        db.session.add(aula)
        db.session.flush()
        db.session.add(RegistroFrequencia(
            aluno_id=aluno.id, turma_id=turma.id, matricula_id=mat.id,
            disciplina_id=disc.id, professor_id=prof.id, alocacao_id=aloc.id,
            registro_aula_id=aula.id, data=date(2026, 3, 10), bimestre=1,
            situacao="PRESENTE",
        ))
        db.session.add(HistoricoAnual(
            ano_letivo=2025, aluno_id=aluno.id, escola_nome="E", turma_nome="4A",
            etapa="fundamental", dados_fechamento="{}", situacao_final="ativo",
        ))
        fisico = "anexo-ok.pdf"
        (Path(pasta_uploads) / fisico).write_bytes(b"%PDF-1.4 anexo-ok")
        db.session.add(Planejamento(
            professor_id=prof.id, turma_id=turma.id, conteudo="plano",
            arquivo=fisico, arquivo_original="plano.pdf",
        ))
        registrar_auditoria(ESCOLA_CRIADA, "Escola", entidade_id=escola.id, dados_novos={"id": escola.id})
        db.session.commit()
        return {
            "escola_id": escola.id,
            "aluno_id": aluno.id,
            "matricula_id": mat.id,
            "n_audit": AuditLog.query.count(),
            "n_nota": RegistroNota.query.count(),
        }


class SqliteDevRoundtripTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        preparar_ambiente_teste(demo=True)
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db = tmp.name
        from src.config import Config as Cfg
        self._old_uri = Cfg.SQLALCHEMY_DATABASE_URI
        apontar_banco_teste("sqlite:///" + Path(self._db).resolve().as_posix())
        self._uploads = tempfile.mkdtemp(prefix="edu_sqlup_")
        self._dest = tempfile.mkdtemp(prefix="edu_sqldst_")
        os.environ["EDUCAMAIS_UPLOAD_FOLDER"] = self._uploads
        from src import create_app
        self.app = create_app()
        self.app.testing = True
        self.ids = _popular_academico(self.app, self._uploads)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        from src.config import Config as Cfg
        Cfg.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db)
        except OSError:
            pass
        shutil.rmtree(self._uploads, ignore_errors=True)
        shutil.rmtree(self._dest, ignore_errors=True)

    def test_m_n_o_p_q_roundtrip(self):
        uri_src = "sqlite:///" + Path(self._db).resolve().as_posix()
        with self.app.app_context():
            rel = criar_backup_sqlite_dev(
                uri=uri_src,
                pasta_uploads=self._uploads,
                destino_raiz=self._dest,
            )
        self.assertTrue(rel["ok"])
        self.assertTrue(pacote_valido(rel["pacote"]))
        dest_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        dest_db.close()
        os.unlink(dest_db.name)
        uri_dst = "sqlite:///" + Path(dest_db.name).resolve().as_posix()
        up_dst = tempfile.mkdtemp(prefix="edu_sqlrst_")
        try:
            with patch("src.notas.calcular_notas_aluno") as calc, \
                 patch("src.notas.calcular_de_notas") as calc2:
                out = restaurar_pacote(
                    rel["pacote"],
                    uri_dst,
                    up_dst,
                    confirmar=True,
                    ciente_destruicao=True,
                    substituir_uploads=True,
                )
                calc.assert_not_called()
                calc2.assert_not_called()
            self.assertTrue(out["ok"])
            val = out["validacao"]
            self.assertFalse(val["recalculou_academico"])
            self.assertEqual(val["contagens"]["registro_nota"], rel["manifesto"]["contagens"]["registro_nota"])
            self.assertEqual(val["pks"]["escola"], rel["manifesto"]["pks"]["escola"])
            self.assertEqual(val["consulta"]["audit_log"], self.ids["n_audit"])
            self.assertEqual(
                Path(up_dst, "anexo-ok.pdf").read_bytes(),
                b"%PDF-1.4 anexo-ok",
            )
            self.assertEqual(val["pks"]["aluno"]["min"], self.ids["aluno_id"])
        finally:
            shutil.rmtree(up_dst, ignore_errors=True)
            try:
                os.unlink(dest_db.name)
            except OSError:
                pass

    def test_sqlite_dev_recusa_producao(self):
        os.environ["EDUCAMAIS_ENV"] = "production"
        with self.assertRaises(BackupErro):
            criar_backup_sqlite_dev(
                uri="sqlite:///" + Path(self._db).resolve().as_posix(),
                pasta_uploads=self._uploads,
                destino_raiz=self._dest,
            )

    def test_restore_recusa_sqlite_original(self):
        original = str((Path(Config.ROOT_DIR) / "instance" / "educamais.db").resolve())
        diag = validar_destino_restore(
            "sqlite:///" + original.replace("\\", "/"),
            confirmar=True,
            ciente_destruicao=True,
        )
        self.assertTrue(any("educamais.db" in r for r in diag["recusas"]))


class CliBackupTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        preparar_ambiente_teste(demo=True)
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db = tmp.name
        from src.config import Config as Cfg
        self._old_uri = Cfg.SQLALCHEMY_DATABASE_URI
        apontar_banco_teste("sqlite:///" + Path(self._db).resolve().as_posix())
        from src import create_app
        self.app = create_app()
        self.app.testing = True

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        preparar_ambiente_teste(demo=True)
        from src.config import Config as Cfg
        Cfg.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db)
        except OSError:
            pass

    def test_backup_principal_recusa_sqlite(self):
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["educamais", "backup"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertNotIn(SENHA_URI, result.output)

    def test_l_restaurar_sem_flags(self):
        runner = self.app.test_cli_runner()
        pasta = tempfile.mkdtemp(prefix="edu_cli_")
        try:
            result = runner.invoke(args=[
                "educamais", "restaurar",
                "--pacote", "nao-existe",
                "--destino-uri", URI_RESTORE_FALSA,
                "--pasta-uploads-destino", pasta,
            ])
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
        self.assertEqual(result.exit_code, 2)
        self.assertNotIn(SENHA_URI, result.output)


def _uri_restore_derivada():
    if URI_PG_RESTORE:
        return URI_PG_RESTORE
    if not URI_PG_TESTE:
        return None
    url = make_url(URI_PG_TESTE)
    return url.set(database=BANCO_RESTORE_PERMITIDO).render_as_string(hide_password=False)


def _tentar_criar_restore_test(uri_admin):
    from sqlalchemy import create_engine, text
    from src.config import resolver_opcoes_engine
    url = make_url(uri_admin)
    engine = create_engine(
        url.render_as_string(hide_password=False),
        isolation_level="AUTOCOMMIT",
        **resolver_opcoes_engine(uri_admin),
    )
    try:
        with engine.connect() as conn:
            existe = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": BANCO_RESTORE_PERMITIDO},
            ).first()
            if existe:
                return False
            conn.execute(text('CREATE DATABASE %s ENCODING \'UTF8\'' % BANCO_RESTORE_PERMITIDO))
            return True
    finally:
        engine.dispose()


@unittest.skipUnless(
    URI_PG_TESTE or URI_PG_RESTORE,
    "Defina EDUCAMAIS_RESTORE_TEST_DATABASE_URI ou EDUCAMAIS_TEST_DATABASE_URI. "
    "SKIP não é aprovação do restore PostgreSQL real.",
)
class PostgreSQLBackupRestoreRealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri_restore = _uri_restore_derivada()
        if not cls.uri_restore:
            raise unittest.SkipTest("URI de restore não derivável.")
        url = make_url(cls.uri_restore)
        if (url.database or "").lower() != BANCO_RESTORE_PERMITIDO:
            raise unittest.SkipTest("URI de restore não aponta para educamais_restore_test.")
        cls._criou = False
        try:
            admin = URI_PG_TESTE or cls.uri_restore
            cls._criou = _tentar_criar_restore_test(admin)
        except Exception as exc:
            raise unittest.SkipTest(
                "Não foi possível garantir educamais_restore_test (sem improvisar credenciais): %s"
                % type(exc).__name__
            ) from exc

    def test_roundtrip_pg_real(self):
        from src.backup import descobrir_executavel_pg
        try:
            descobrir_executavel_pg("pg_dump")
            descobrir_executavel_pg("pg_restore")
        except BackupErro as exc:
            self.skipTest(str(exc))
        env = os.environ.copy()
        preparar_ambiente_teste(demo=False)
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-backup-pg"
        os.environ["EDUCAMAIS_DEMO"] = "1"
        apontar_banco_teste(self.uri_restore)
        from tests.support import criar_app_migrada
        from src.backup import limpar_schema_restore_test as _limpar
        _limpar(self.uri_restore)
        app = criar_app_migrada()
        app.testing = True
        uploads = tempfile.mkdtemp(prefix="edu_pgr_up_")
        dest = tempfile.mkdtemp(prefix="edu_pgr_bk_")
        rst_up = tempfile.mkdtemp(prefix="edu_pgr_rst_")
        try:
            os.environ["EDUCAMAIS_UPLOAD_FOLDER"] = uploads
            ids = _popular_academico(app, uploads)
            with app.app_context():
                rel = criar_backup(
                    uri=self.uri_restore,
                    pasta_uploads=uploads,
                    destino_raiz=dest,
                )
                from src import db
                db.session.remove()
                db.engine.dispose()
            self.assertTrue(rel["ok"])
            _limpar(self.uri_restore)
            with patch("src.notas.calcular_notas_aluno") as calc:
                out = restaurar_pacote(
                    rel["pacote"],
                    self.uri_restore,
                    rst_up,
                    confirmar=True,
                    ciente_destruicao=True,
                    substituir_uploads=True,
                    uri_origem="postgresql+psycopg://u@localhost/educamais",
                )
                calc.assert_not_called()
            self.assertTrue(out.get("ok"), out.get("relatorio"))
            self.assertEqual(
                out["validacao"]["consulta"]["audit_log"],
                ids["n_audit"],
            )
            self.assertEqual(
                Path(rst_up, "anexo-ok.pdf").read_bytes(),
                b"%PDF-1.4 anexo-ok",
            )
        finally:
            os.environ.clear()
            os.environ.update(env)
            preparar_ambiente_teste(demo=True)
            shutil.rmtree(uploads, ignore_errors=True)
            shutil.rmtree(dest, ignore_errors=True)
            shutil.rmtree(rst_up, ignore_errors=True)


class PadraoBackupDirTest(unittest.TestCase):
    def test_padrao_var_backups(self):
        os.environ.pop("EDUCAMAIS_BACKUP_DIR", None)
        pasta = resolver_backup_dir()
        self.assertTrue(pasta.replace("\\", "/").endswith("/var/backups"))
        self.assertFalse(destino_backup_proibido(pasta))


class ListarUploadsTraversalTest(unittest.TestCase):
    def test_nao_lista_fora_da_pasta(self):
        origem = tempfile.mkdtemp(prefix="edu_trav_")
        segredo = tempfile.NamedTemporaryFile(delete=False)
        try:
            segredo.write(b"SEGREDO")
            segredo.close()
            (Path(origem) / "ok.pdf").write_bytes(b"%PDF-1.4")
            link = Path(origem) / "link.pdf"
            try:
                os.symlink(segredo.name, link)
            except OSError:
                self.skipTest("symlink indisponível neste Windows")
            itens = listar_arquivos_uploads(origem)
            rels = {i["rel"] for i in itens}
            self.assertIn("ok.pdf", rels)
            self.assertNotIn("link.pdf", rels)
        finally:
            os.unlink(segredo.name)
            shutil.rmtree(origem, ignore_errors=True)
