"""Backup e restauração administrativos do Educa+.

Produção: PostgreSQL via pg_dump/pg_restore (formato custom) + uploads.
SQLite: comando separado, apenas desenvolvimento. Não é estratégia de produção.

Não altera regras acadêmicas, schema ou arquivos de origem.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.pool import NullPool

from src.config import (
    Config,
    ConfiguracaoInsegura,
    mascarar_uri_banco,
    normalizar_uri_banco,
    resolver_ambiente,
    resolver_opcoes_engine,
    resolver_upload_folder,
    resolver_uri_banco,
    uri_e_sqlite,
)
from src.migracoes import REVISION_HEAD, TABELAS_ESSENCIAIS


FORMATO_BACKUP = 1
NOME_FORMATO = "educamais-backup-1"
ARQUIVO_DUMP_PG = "database.dump"
ARQUIVO_DUMP_SQLITE = "database.sqlite"
ARQUIVO_MANIFESTO = "manifest.json"
MARCADOR_COMPLETO = "COMPLETED"
PASTA_UPLOADS = "uploads"

STATUS_OK = "OK"
STATUS_REFERENCIA_SEM_ARQUIVO = "REFERENCIA_SEM_ARQUIVO"
STATUS_ARQUIVO_SEM_REFERENCIA = "ARQUIVO_SEM_REFERENCIA"

BANCO_RESTORE_PERMITIDO = "educamais_restore_test"
BANCOS_RESTORE_RECUSADOS = frozenset({
    "postgres",
    "template0",
    "template1",
    "educamais",
    "educamais_demo",
    "educamaisteste",
    "educamais_pg_test",
    "educamais_migracao_test",
})
HOSTS_LOCAIS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
IDENT_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_URI_COM_SENHA = re.compile(
    r"(postgres(?:ql)?(?:\+\w+)?://[^:/?#]+:)([^@/]+)(@)",
    re.IGNORECASE,
)
TABELAS_POS_RESTORE = TABELAS_ESSENCIAIS + ("alembic_version",)
CHAVES_MANIFESTO_PROIBIDAS = frozenset({
    "password",
    "senha",
    "secret",
    "secret_key",
    "token",
    "cookie",
    "session",
    "authorization",
    "database_uri",
    "sqlalchemy_database_uri",
    "pgpassword",
})


class BackupErro(RuntimeError):
    """Falha operacional de backup/restore. Pacote não deve ser considerado válido."""


class RestoreRecusado(ConfiguracaoInsegura):
    """Restore recusado (fail-closed). Nada foi restaurado."""


class RelatorioOperacao:
    """Linhas [OK]/[AVISO]/[ERRO] sem credenciais."""

    def __init__(self, uri=None, extras=None):
        self.linhas = []
        self.uri = uri
        self.extras = [e for e in (extras or []) if e]
        self.n_ok = 0
        self.n_aviso = 0
        self.n_erro = 0

    def ok(self, msg):
        self.n_ok += 1
        self.linhas.append("[OK] " + mascarar_texto(msg, self.uri, self.extras))

    def aviso(self, msg):
        self.n_aviso += 1
        self.linhas.append("[AVISO] " + mascarar_texto(msg, self.uri, self.extras))

    def erro(self, msg):
        self.n_erro += 1
        self.linhas.append("[ERRO] " + mascarar_texto(msg, self.uri, self.extras))

    def texto(self):
        return "\n".join(self.linhas)


def resolver_backup_dir(raiz=None):
    raiz = os.path.abspath(raiz or Config.ROOT_DIR)
    bruto = (os.environ.get("EDUCAMAIS_BACKUP_DIR") or "").strip()
    if not bruto:
        return os.path.join(raiz, "var", "backups")
    if os.path.isabs(bruto):
        return os.path.abspath(bruto)
    return os.path.abspath(os.path.join(raiz, bruto))


def resolver_pg_bin():
    return (os.environ.get("EDUCAMAIS_PG_BIN") or "").strip() or None


def resolver_uri_restore_teste():
    return (os.environ.get("EDUCAMAIS_RESTORE_TEST_DATABASE_URI") or "").strip() or None


def _ident(nome):
    if not nome or not IDENT_OK.match(nome):
        raise BackupErro("Identificador SQL recusado.")
    return nome


def mascarar_texto(texto, uri=None, extras=None):
    """Remove senha, URI completa, SECRET_KEY e tokens de uma string operacional."""
    if texto is None:
        return ""
    out = str(texto)
    segredos = []
    for extra in extras or []:
        if extra:
            segredos.append(str(extra))
    if uri:
        try:
            url = make_url(uri)
            if url.password:
                segredos.append(url.password)
                segredos.append(quote_plus(url.password))
            bruto = url.render_as_string(hide_password=False)
            mascarada = mascarar_uri_banco(uri)
            if bruto and mascarada:
                out = out.replace(bruto, mascarada)
        except Exception:
            pass
    for envk in ("PGPASSWORD", "SECRET_KEY", "EDUCAMAIS_SECRET_KEY"):
        valor = os.environ.get(envk)
        if valor:
            segredos.append(valor)
    for s in segredos:
        if s:
            out = out.replace(s, "***")
    out = _URI_COM_SENHA.sub(r"\1***\3", out)
    return out


def caminho_dentro(raiz, alvo):
    try:
        raiz_r = os.path.abspath(os.path.realpath(raiz))
        alvo_r = os.path.abspath(os.path.realpath(alvo))
        comum = os.path.commonpath([raiz_r, alvo_r])
        return comum == raiz_r
    except (ValueError, OSError):
        return False


def diretorios_servidos(raiz=None):
    raiz = Path(raiz or Config.ROOT_DIR).resolve()
    return (
        raiz / "frontend" / "public",
        raiz / "frontend" / "dist",
        raiz / "src" / "static",
        raiz / "static",
        Path(resolver_upload_folder(str(raiz))).resolve(),
    )


def destino_backup_proibido(pasta, raiz=None):
    """True se o diretório de backup cairia em conteúdo servido pelo Flask."""
    alvo = Path(pasta).resolve()
    for base in diretorios_servidos(raiz):
        if alvo == base or caminho_dentro(str(base), str(alvo)):
            return True
        if base == alvo:
            return True
    return False


def sha256_arquivo(path, tamanho_max=None):
    path = Path(path)
    h = hashlib.sha256()
    total = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            total += len(chunk)
            if tamanho_max is not None and total > tamanho_max:
                raise BackupErro("Arquivo excede o tamanho esperado durante o hash.")
            h.update(chunk)
    return h.hexdigest(), total


def git_commit_seguro(raiz=None):
    raiz = raiz or Config.ROOT_DIR
    try:
        concl = _rodar_subprocesso(
            ["git", "rev-parse", "HEAD"],
            env=_env_sem_segredos_pg(),
            timeout=8,
            cwd=raiz,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if concl.returncode != 0:
        return None
    texto = (concl.stdout or b"").decode("utf-8", errors="replace").strip()
    if re.fullmatch(r"[0-9a-f]{7,40}", texto):
        return texto
    return None


def _env_sem_segredos_pg():
    env = os.environ.copy()
    env.pop("PGPASSWORD", None)
    return env


def _env_libpq(url):
    """Ambiente para pg_dump/pg_restore. Senha só em PGPASSWORD, nunca em argv."""
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    elif "PGPASSWORD" not in env:
        env.pop("PGPASSWORD", None)
    return env


def _rodar_subprocesso(argv, env, timeout, cwd=None):
    kwargs = {
        "args": argv,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "timeout": timeout,
        "cwd": cwd,
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(**kwargs)


def _url_postgres(uri):
    try:
        return make_url(normalizar_uri_banco(uri))
    except Exception as exc:
        raise BackupErro(mascarar_texto("URI PostgreSQL inválida: %s" % exc, uri)) from exc


def descobrir_executavel_pg(nome, pg_bin=None):
    """Localiza pg_dump/pg_restore. Não grava caminho de um único PC como regra."""
    if os.name == "nt" and not nome.lower().endswith(".exe"):
        exe_nome = nome + ".exe"
    else:
        exe_nome = nome

    configurado = pg_bin if pg_bin is not None else resolver_pg_bin()
    if configurado:
        p = Path(configurado)
        if p.is_file():
            if p.name.lower().startswith(nome.lower()):
                return str(p.resolve())
            candidato = p.parent / exe_nome
            if candidato.is_file():
                return str(candidato.resolve())
        elif p.is_dir():
            candidato = p / exe_nome
            if candidato.is_file():
                return str(candidato.resolve())
        raise BackupErro(
            "EDUCAMAIS_PG_BIN não aponta para %s. Informe o diretório bin/ "
            "ou o executável." % nome
        )

    no_path = shutil.which(nome) or shutil.which(exe_nome)
    if no_path:
        return str(Path(no_path).resolve())

    encontrados = []
    if os.name == "nt":
        bases = []
        pf = os.environ.get("ProgramFiles")
        pfx = os.environ.get("ProgramFiles(x86)")
        if pf:
            bases.append(Path(pf) / "PostgreSQL")
        if pfx:
            bases.append(Path(pfx) / "PostgreSQL")
        for base in bases:
            if not base.is_dir():
                continue
            for versao in base.iterdir():
                cand = versao / "bin" / exe_nome
                if cand.is_file():
                    encontrados.append(cand)
    if encontrados:
        encontrados.sort(key=lambda p: p.parts, reverse=True)
        return str(encontrados[0].resolve())

    raise BackupErro(
        "%s não encontrado. Defina EDUCAMAIS_PG_BIN com o diretório bin/ "
        "do PostgreSQL ou inclua pg_dump no PATH." % nome
    )


def argv_pg_dump(exe, url, destino_dump):
    argv = [exe, "-Fc", "--no-password", "-f", str(destino_dump)]
    if url.host:
        argv.extend(["-h", url.host])
    if url.port:
        argv.extend(["-p", str(url.port)])
    if url.username:
        argv.extend(["-U", url.username])
    argv.extend(["-d", url.database])
    _recusar_senha_em_argv(argv, url)
    return argv


def argv_pg_restore(exe, url, dump):
    argv = [
        exe,
        "--no-password",
        "--no-owner",
        "--no-acl",
        "--exit-on-error",
        "--single-transaction",
    ]
    if url.host:
        argv.extend(["-h", url.host])
    if url.port:
        argv.extend(["-p", str(url.port)])
    if url.username:
        argv.extend(["-U", url.username])
    argv.extend(["-d", url.database, str(dump)])
    _recusar_senha_em_argv(argv, url)
    return argv


def _recusar_senha_em_argv(argv, url):
    if url.password:
        for item in argv:
            if url.password in str(item):
                raise BackupErro("Senha não pode aparecer na linha de comando.")


def _saida_processo(concl, uri=None):
    stdout = (concl.stdout or b"").decode("utf-8", errors="replace")
    stderr = (concl.stderr or b"").decode("utf-8", errors="replace")
    return mascarar_texto(stdout, uri), mascarar_texto(stderr, uri)


def listar_arquivos_uploads(pasta, calcular_hash=False):
    """Inventário somente leitura. Não segue symlink que saia da pasta."""
    pasta = Path(pasta)
    itens = []
    if not pasta.is_dir():
        return itens
    raiz = pasta.resolve()
    for dirpath, dirnames, filenames in os.walk(raiz, followlinks=False):
        atual = Path(dirpath)
        if not caminho_dentro(str(raiz), str(atual)):
            dirnames[:] = []
            continue
        dirnames[:] = sorted(
            d for d in dirnames
            if not (atual / d).is_symlink()
            or caminho_dentro(str(raiz), str((atual / d).resolve()))
        )
        for nome in sorted(filenames):
            src = atual / nome
            if src.is_symlink():
                try:
                    alvo = src.resolve()
                except OSError:
                    continue
                if not caminho_dentro(str(raiz), str(alvo)):
                    continue
                if not alvo.is_file():
                    continue
            elif not src.is_file():
                continue
            try:
                rel = src.relative_to(raiz).as_posix()
            except ValueError:
                continue
            if ".." in Path(rel).parts:
                continue
            item = {"rel": rel, "tamanho": src.stat().st_size}
            if calcular_hash:
                item["sha256"], _ = sha256_arquivo(src)
            itens.append(item)
    return itens


def copiar_uploads(origem, destino, relatorio=None):
    """Copia bytes e estrutura. Não modifica a origem. Não segue escape de symlink."""
    origem = Path(origem)
    destino = Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    dest_raiz = destino.resolve()
    copiados = []
    ignorados = []
    if not origem.is_dir():
        if relatorio:
            relatorio.aviso("Pasta de uploads inexistente; backup segue sem arquivos.")
        return copiados, ignorados
    raiz = origem.resolve()
    for item in listar_arquivos_uploads(raiz, calcular_hash=False):
        rel = item["rel"]
        if Path(rel).is_absolute() or ".." in Path(rel).parts:
            ignorados.append(rel)
            if relatorio:
                relatorio.aviso("Caminho de upload recusado: %s" % rel)
            continue
        src = raiz / rel
        dst = dest_raiz / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not caminho_dentro(str(dest_raiz), str(dst.parent.resolve())):
            ignorados.append(rel)
            continue
        shutil.copyfile(src, dst, follow_symlinks=True)
        if not dst.is_file() or not caminho_dentro(str(dest_raiz), str(dst.resolve())):
            if dst.exists():
                dst.unlink()
            raise BackupErro("Falha ao copiar upload %s." % rel)
        digest, tamanho = sha256_arquivo(dst)
        orig_digest, orig_tam = sha256_arquivo(src)
        if digest != orig_digest or tamanho != orig_tam:
            raise BackupErro("Hash divergente após copiar %s." % rel)
        copiados.append({
            "rel": rel,
            "tamanho": tamanho,
            "sha256": digest,
        })
    return copiados, ignorados


def coletar_referencias_arquivos(bind):
    """Referências físicas em planejamento.arquivo. Somente leitura."""
    insp = inspect(bind)
    if not insp.has_table("planejamento"):
        return []
    colunas = {c["name"] for c in insp.get_columns("planejamento")}
    if "arquivo" not in colunas:
        return []
    campos = ["id", "arquivo"]
    if "arquivo_original" in colunas:
        campos.append("arquivo_original")
    sql = text(
        "SELECT %s FROM planejamento WHERE arquivo IS NOT NULL AND arquivo != ''"
        % ", ".join(_ident(c) for c in campos)
    )
    refs = []
    with bind.connect() as conn:
        for row in conn.execute(sql):
            mapa = dict(row._mapping)
            refs.append({
                "tabela": "planejamento",
                "id": mapa.get("id"),
                "arquivo": mapa.get("arquivo"),
                "arquivo_original": mapa.get("arquivo_original"),
            })
    return refs


def auditar_consistencia_uploads(referencias, arquivos):
    """Compara referências do banco com arquivos físicos. Não apaga nada."""
    nomes = {a["rel"] for a in arquivos}
    ok, sem_arquivo = [], []
    for ref in referencias:
        arq = (ref.get("arquivo") or "").replace("\\", "/").lstrip("/")
        item = {
            "tabela": ref.get("tabela"),
            "id": ref.get("id"),
            "arquivo": arq,
            "arquivo_original": ref.get("arquivo_original"),
        }
        if arq and arq in nomes:
            item["status"] = STATUS_OK
            ok.append(item)
        else:
            item["status"] = STATUS_REFERENCIA_SEM_ARQUIVO
            sem_arquivo.append(item)
    referenciados = {
        (r.get("arquivo") or "").replace("\\", "/").lstrip("/")
        for r in referencias
    }
    orfaos = []
    for arq in arquivos:
        if arq["rel"] not in referenciados:
            orfaos.append({
                "rel": arq["rel"],
                "tamanho": arq.get("tamanho"),
                "status": STATUS_ARQUIVO_SEM_REFERENCIA,
            })
    return {
        "ok": ok,
        "referencia_sem_arquivo": sem_arquivo,
        "arquivo_sem_referencia": orfaos,
        "n_ok": len(ok),
        "n_referencia_sem_arquivo": len(sem_arquivo),
        "n_arquivo_sem_referencia": len(orfaos),
    }


def formatar_consistencia(rel):
    linhas = [
        "Consistência uploads (somente leitura; nada foi apagado/renomeado).",
        "OK: %s" % rel.get("n_ok", 0),
        "REFERENCIA_SEM_ARQUIVO: %s" % rel.get("n_referencia_sem_arquivo", 0),
        "ARQUIVO_SEM_REFERENCIA: %s" % rel.get("n_arquivo_sem_referencia", 0),
        "Arquivo sem referência no banco não é lixo automático.",
    ]
    for item in rel.get("referencia_sem_arquivo") or []:
        linhas.append(
            "  %s planejamento.id=%s arquivo=%s"
            % (STATUS_REFERENCIA_SEM_ARQUIVO, item.get("id"), item.get("arquivo"))
        )
    for item in rel.get("arquivo_sem_referencia") or []:
        linhas.append(
            "  %s %s" % (STATUS_ARQUIVO_SEM_REFERENCIA, item.get("rel"))
        )
    return "\n".join(linhas)


def auditar_uploads_atual(bind=None, pasta=None, raiz=None):
    from src import db
    pasta = pasta or resolver_upload_folder(raiz)
    arquivos = listar_arquivos_uploads(pasta, calcular_hash=False)
    engine = bind if bind is not None else db.engine
    refs = coletar_referencias_arquivos(engine)
    cons = auditar_consistencia_uploads(refs, arquivos)
    cons.update({
        "pasta": str(Path(pasta).resolve()) if pasta else None,
        "existe": Path(pasta).is_dir() if pasta else False,
        "n_arquivos": len(arquivos),
        "n_referencias": len(refs),
        "alterado": False,
    })
    return cons


def _engine(uri):
    opts = dict(resolver_opcoes_engine(uri))
    opts["poolclass"] = NullPool
    return create_engine(uri, **opts)


def _contar(conn, tabela):
    return int(conn.execute(text("SELECT COUNT(*) FROM %s" % _ident(tabela))).scalar() or 0)


def coletar_contagens_e_pks(bind):
    insp = inspect(bind)
    nomes = set(insp.get_table_names())
    contagens = {}
    pks = {}
    with bind.connect() as conn:
        for tabela in TABELAS_POS_RESTORE:
            if tabela not in nomes:
                contagens[tabela] = None
                continue
            contagens[tabela] = _contar(conn, tabela)
            colunas = {c["name"] for c in insp.get_columns(tabela)}
            if "id" in colunas and contagens[tabela]:
                row = conn.execute(
                    text(
                        "SELECT MIN(id), MAX(id), COUNT(id) FROM %s"
                        % _ident(tabela)
                    )
                ).first()
                pks[tabela] = {
                    "min": row[0],
                    "max": row[1],
                    "n": int(row[2] or 0),
                }
    return contagens, pks


def coletar_alembic(bind):
    insp = inspect(bind)
    if not insp.has_table("alembic_version"):
        return None
    with bind.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
    return None if row is None else row[0]


def _resumo_banco(url, tipo):
    return {
        "tipo": tipo,
        "nome": url.database,
        "host": url.host,
        "porta": int(url.port) if url.port else None,
        "usuario": url.username,
    }


def _recusar_segredos_manifesto(obj, caminho=""):
    if isinstance(obj, dict):
        for chave, valor in obj.items():
            baixo = str(chave).strip().lower().replace("-", "_")
            if baixo in CHAVES_MANIFESTO_PROIBIDAS or baixo.endswith("_uri"):
                raise BackupErro("Manifesto recusou chave sensível.")
            _recusar_segredos_manifesto(valor, caminho + "/" + str(chave))
    elif isinstance(obj, list):
        for item in obj:
            _recusar_segredos_manifesto(item, caminho)


def pacote_valido(pasta):
    p = Path(pasta)
    if not p.is_dir():
        return False
    nome = p.name
    if nome.endswith(".partial") or nome.startswith("._staging"):
        return False
    if not (p / MARCADOR_COMPLETO).is_file():
        return False
    man = p / ARQUIVO_MANIFESTO
    if not man.is_file():
        return False
    try:
        dados = json.loads(man.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return dados.get("status") == "completo"


def _exigir_destino_backup(pasta, raiz=None):
    if destino_backup_proibido(pasta, raiz):
        raise BackupErro(
            "Diretório de backup recusado: não pode ficar em conteúdo servido "
            "(frontend/public, frontend/dist, static, uploads)."
        )


def _nome_pacote(agora):
    return "backup_%s" % agora.strftime("%Y%m%d_%H%M%S")


def _rmtree_staging(staging, raiz):
    staging = Path(staging).resolve()
    raiz = Path(raiz).resolve()
    if not caminho_dentro(str(raiz), str(staging)):
        return
    if not staging.name.endswith(".partial"):
        return
    shutil.rmtree(staging, ignore_errors=True)


def criar_backup(
    uri=None,
    pasta_uploads=None,
    destino_raiz=None,
    pg_bin=None,
    raiz=None,
    agora=None,
):
    """Cria pacote atômico de backup PostgreSQL + uploads.

    Staging .partial só vira diretório final após dump, cópia, hashes e manifesto.
    """
    raiz = raiz or Config.ROOT_DIR
    uri = uri or resolver_uri_banco()
    relatorio = RelatorioOperacao(uri=uri)
    if uri_e_sqlite(uri):
        relatorio.erro(
            "Backup de produção exige PostgreSQL. "
            "Para desenvolvimento use: flask educamais backup-sqlite-dev"
        )
        raise BackupErro(relatorio.texto())

    url = _url_postgres(uri)
    if not url.database:
        raise BackupErro("URI sem nome de banco.")
    destino_raiz = Path(destino_raiz or resolver_backup_dir(raiz)).resolve()
    _exigir_destino_backup(destino_raiz, raiz)
    destino_raiz.mkdir(parents=True, exist_ok=True)

    pasta_uploads = pasta_uploads or resolver_upload_folder(raiz)
    agora = agora or datetime.now(timezone.utc)
    nome = _nome_pacote(agora)
    final = destino_raiz / nome
    staging = destino_raiz / (nome + ".partial")
    if final.exists() or staging.exists():
        nome = nome + "_2"
        final = destino_raiz / nome
        staging = destino_raiz / (nome + ".partial")
    if final.exists() or staging.exists():
        raise BackupErro("Colisão de nome de backup. Tente novamente.")

    extras = [url.password] if url.password else []
    relatorio = RelatorioOperacao(uri=uri, extras=extras)
    engine = None
    movido = False
    try:
        staging.mkdir(parents=True, exist_ok=False)
        relatorio.ok("Ambiente validado. Banco=%s host=%s" % (
            url.database, url.host or "-",
        ))
        dump_exe = descobrir_executavel_pg("pg_dump", pg_bin=pg_bin)
        relatorio.ok("pg_dump localizado.")
        dump_path = staging / ARQUIVO_DUMP_PG
        argv = argv_pg_dump(dump_exe, url, dump_path)
        env = _env_libpq(url)
        concl = _rodar_subprocesso(argv, env=env, timeout=_timeout_pg())
        out, err = _saida_processo(concl, uri)
        if concl.returncode != 0:
            relatorio.erro(
                "pg_dump falhou (exit %s). %s %s"
                % (concl.returncode, out.strip(), err.strip())
            )
            raise BackupErro("pg_dump falhou.")
        if not dump_path.is_file() or dump_path.stat().st_size <= 0:
            relatorio.erro("pg_dump não gerou dump utilizável.")
            raise BackupErro("Dump vazio ou ausente.")
        dump_hash, dump_tam = sha256_arquivo(dump_path)
        relatorio.ok("Dump PostgreSQL criado (%s bytes)." % dump_tam)

        uploads_dest = staging / PASTA_UPLOADS
        copiados, ignorados = copiar_uploads(pasta_uploads, uploads_dest, relatorio)
        if ignorados:
            relatorio.aviso("Uploads ignorados (fora da pasta): %s" % len(ignorados))
        relatorio.ok("Uploads copiados: %s arquivo(s)." % len(copiados))

        engine = _engine(uri)
        refs = coletar_referencias_arquivos(engine)
        cons = auditar_consistencia_uploads(refs, copiados)
        if cons["n_referencia_sem_arquivo"] or cons["n_arquivo_sem_referencia"]:
            relatorio.aviso(
                "Inconsistência uploads: REFERENCIA_SEM_ARQUIVO=%s "
                "ARQUIVO_SEM_REFERENCIA=%s"
                % (cons["n_referencia_sem_arquivo"], cons["n_arquivo_sem_referencia"])
            )
        else:
            relatorio.ok("Consistência de uploads: OK.")
        alembic = coletar_alembic(engine)
        contagens, pks = coletar_contagens_e_pks(engine)
        manifesto = {
            "formato": FORMATO_BACKUP,
            "versao_formato": NOME_FORMATO,
            "status": "completo",
            "criado_em": agora.isoformat(),
            "ambiente": resolver_ambiente(),
            "banco": _resumo_banco(url, "postgresql"),
            "alembic": {
                "revisao": alembic,
                "head_esperado": REVISION_HEAD,
            },
            "aplicacao": {
                "git_commit": git_commit_seguro(raiz),
            },
            "dump": {
                "arquivo": ARQUIVO_DUMP_PG,
                "tamanho": dump_tam,
                "sha256": dump_hash,
            },
            "uploads": {
                "n_arquivos": len(copiados),
                "tamanho_total": sum(i["tamanho"] for i in copiados),
                "arquivos": copiados,
            },
            "consistencia": {
                "n_ok": cons["n_ok"],
                "n_referencia_sem_arquivo": cons["n_referencia_sem_arquivo"],
                "n_arquivo_sem_referencia": cons["n_arquivo_sem_referencia"],
                "referencia_sem_arquivo": cons["referencia_sem_arquivo"],
                "arquivo_sem_referencia": cons["arquivo_sem_referencia"],
            },
            "contagens": contagens,
            "pks": pks,
            "ferramentas": {
                "pg_dump": Path(dump_exe).name,
            },
        }
        _recusar_segredos_manifesto(manifesto)
        _escrever_json(staging / ARQUIVO_MANIFESTO, manifesto)
        (staging / MARCADOR_COMPLETO).write_text(
            "completo\n%s\n" % agora.isoformat(), encoding="utf-8"
        )
        os.replace(str(staging), str(final))
        movido = True
        if not pacote_valido(final):
            shutil.rmtree(final, ignore_errors=True)
            raise BackupErro("Pacote final não passou na validação mínima.")
        relatorio.ok("Backup concluído: %s" % final)
        return {
            "ok": True,
            "pacote": str(final),
            "manifesto": manifesto,
            "relatorio": relatorio.texto(),
        }
    except BackupErro:
        if not movido:
            _rmtree_staging(staging, destino_raiz)
        raise
    except Exception as exc:
        if not movido:
            _rmtree_staging(staging, destino_raiz)
        relatorio.erro("Falha inesperada durante o backup.")
        raise BackupErro(relatorio.texto()) from exc
    finally:
        if engine is not None:
            engine.dispose()


def criar_backup_sqlite_dev(
    uri=None,
    pasta_uploads=None,
    destino_raiz=None,
    raiz=None,
    agora=None,
):
    """Cópia simples de SQLite + uploads. Não é backup de produção."""
    raiz = raiz or Config.ROOT_DIR
    uri = uri or resolver_uri_banco()
    if resolver_ambiente() == "production":
        raise BackupErro("SQLite não é estratégia de backup de produção.")
    if not uri_e_sqlite(uri):
        raise BackupErro("backup-sqlite-dev recusa PostgreSQL.")
    url = make_url(uri)
    db_path = url.database
    if not db_path or not os.path.isfile(db_path):
        raise BackupErro("Arquivo SQLite de origem não encontrado.")
    destino_raiz = Path(destino_raiz or resolver_backup_dir(raiz)).resolve()
    _exigir_destino_backup(destino_raiz, raiz)
    destino_raiz.mkdir(parents=True, exist_ok=True)
    pasta_uploads = pasta_uploads or resolver_upload_folder(raiz)
    agora = agora or datetime.now(timezone.utc)
    nome = _nome_pacote(agora) + "_sqlite"
    final = destino_raiz / nome
    staging = destino_raiz / (nome + ".partial")
    relatorio = RelatorioOperacao(uri=uri)
    engine = None
    movido = False
    try:
        staging.mkdir(parents=True, exist_ok=False)
        dest_db = staging / ARQUIVO_DUMP_SQLITE
        _copiar_sqlite_somente_leitura(db_path, dest_db)
        dump_hash, dump_tam = sha256_arquivo(dest_db)
        relatorio.ok("Cópia SQLite de desenvolvimento criada (%s bytes)." % dump_tam)
        relatorio.aviso("Este pacote NÃO substitui backup PostgreSQL de produção.")
        uploads_dest = staging / PASTA_UPLOADS
        copiados, _ign = copiar_uploads(pasta_uploads, uploads_dest, relatorio)
        engine = _engine(uri)
        refs = coletar_referencias_arquivos(engine)
        cons = auditar_consistencia_uploads(refs, copiados)
        if cons["n_referencia_sem_arquivo"] or cons["n_arquivo_sem_referencia"]:
            relatorio.aviso(
                "Inconsistência uploads: REFERENCIA_SEM_ARQUIVO=%s "
                "ARQUIVO_SEM_REFERENCIA=%s"
                % (cons["n_referencia_sem_arquivo"], cons["n_arquivo_sem_referencia"])
            )
        alembic = coletar_alembic(engine)
        contagens, pks = coletar_contagens_e_pks(engine)
        manifesto = {
            "formato": FORMATO_BACKUP,
            "versao_formato": NOME_FORMATO,
            "status": "completo",
            "criado_em": agora.isoformat(),
            "ambiente": resolver_ambiente(),
            "banco": {
                "tipo": "sqlite",
                "nome": Path(db_path).name,
                "host": None,
                "porta": None,
                "usuario": None,
            },
            "alembic": {
                "revisao": alembic,
                "head_esperado": REVISION_HEAD,
            },
            "aplicacao": {"git_commit": git_commit_seguro(raiz)},
            "dump": {
                "arquivo": ARQUIVO_DUMP_SQLITE,
                "tamanho": dump_tam,
                "sha256": dump_hash,
            },
            "uploads": {
                "n_arquivos": len(copiados),
                "tamanho_total": sum(i["tamanho"] for i in copiados),
                "arquivos": copiados,
            },
            "consistencia": {
                "n_ok": cons["n_ok"],
                "n_referencia_sem_arquivo": cons["n_referencia_sem_arquivo"],
                "n_arquivo_sem_referencia": cons["n_arquivo_sem_referencia"],
                "referencia_sem_arquivo": cons["referencia_sem_arquivo"],
                "arquivo_sem_referencia": cons["arquivo_sem_referencia"],
            },
            "contagens": contagens,
            "pks": pks,
            "aviso": "Pacote SQLite de desenvolvimento. Não usar como backup de produção.",
        }
        _recusar_segredos_manifesto(manifesto)
        _escrever_json(staging / ARQUIVO_MANIFESTO, manifesto)
        (staging / MARCADOR_COMPLETO).write_text(
            "completo\n%s\n" % agora.isoformat(), encoding="utf-8"
        )
        os.replace(str(staging), str(final))
        movido = True
        if not pacote_valido(final):
            shutil.rmtree(final, ignore_errors=True)
            raise BackupErro("Pacote SQLite final não passou na validação mínima.")
        relatorio.ok("Backup SQLite de desenvolvimento concluído: %s" % final)
        return {
            "ok": True,
            "pacote": str(final),
            "manifesto": manifesto,
            "relatorio": relatorio.texto(),
        }
    except BackupErro:
        if not movido:
            _rmtree_staging(staging, destino_raiz)
        raise
    except Exception as exc:
        if not movido:
            _rmtree_staging(staging, destino_raiz)
        raise BackupErro("Falha no backup SQLite de desenvolvimento.") from exc
    finally:
        if engine is not None:
            engine.dispose()


def _copiar_sqlite_somente_leitura(origem, destino):
    import sqlite3
    src_uri = "file:%s?mode=ro" % Path(origem).resolve().as_posix()
    src = sqlite3.connect(src_uri, uri=True)
    try:
        dst = sqlite3.connect(str(destino))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    if not Path(destino).is_file() or Path(destino).stat().st_size <= 0:
        raise BackupErro("Cópia SQLite vazia.")


def _escrever_json(path, dados):
    texto = json.dumps(dados, ensure_ascii=False, indent=2, sort_keys=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _timeout_pg():
    bruto = (os.environ.get("EDUCAMAIS_BACKUP_TIMEOUT") or "").strip()
    if not bruto:
        return 3600
    try:
        n = int(bruto)
    except ValueError:
        return 3600
    return max(30, n)


def ler_manifesto(pacote):
    man = Path(pacote) / ARQUIVO_MANIFESTO
    dados = json.loads(man.read_text(encoding="utf-8"))
    _recusar_segredos_manifesto(dados)
    return dados


def verificar_pacote(pacote, relatorio=None):
    """Confere marcador, manifesto e SHA-256. Não restaura."""
    pacote = Path(pacote)
    relatorio = relatorio or RelatorioOperacao()
    if not pacote_valido(pacote):
        relatorio.erro("Pacote inválido ou incompleto.")
        return {"ok": False, "relatorio": relatorio.texto()}
    manifesto = ler_manifesto(pacote)
    dump_meta = manifesto.get("dump") or {}
    dump_path = pacote / dump_meta.get("arquivo", "")
    if not dump_path.is_file():
        relatorio.erro("Dump ausente no pacote.")
        return {"ok": False, "manifesto": manifesto, "relatorio": relatorio.texto()}
    digest, tam = sha256_arquivo(dump_path)
    if digest != dump_meta.get("sha256") or tam != dump_meta.get("tamanho"):
        relatorio.erro("Dump alterado ou hash divergente.")
        return {"ok": False, "manifesto": manifesto, "relatorio": relatorio.texto()}
    relatorio.ok("Dump íntegro.")
    uploads_meta = (manifesto.get("uploads") or {}).get("arquivos") or []
    pasta = pacote / PASTA_UPLOADS
    for item in uploads_meta:
        f = pasta / item["rel"]
        if not f.is_file():
            relatorio.erro("Arquivo ausente no pacote: %s" % item["rel"])
            return {"ok": False, "manifesto": manifesto, "relatorio": relatorio.texto()}
        if not caminho_dentro(str(pasta.resolve()), str(f.resolve())):
            relatorio.erro("Arquivo fora de uploads no pacote: %s" % item["rel"])
            return {"ok": False, "manifesto": manifesto, "relatorio": relatorio.texto()}
        d, t = sha256_arquivo(f)
        if d != item.get("sha256") or t != item.get("tamanho"):
            relatorio.erro("Arquivo alterado: %s" % item["rel"])
            return {"ok": False, "manifesto": manifesto, "relatorio": relatorio.texto()}
    relatorio.ok("Uploads do pacote íntegros (%s)." % len(uploads_meta))
    return {"ok": True, "manifesto": manifesto, "relatorio": relatorio.texto()}


def validar_destino_restore(
    uri,
    confirmar=False,
    ciente_destruicao=False,
    mesmo_em_producao=False,
    uri_origem=None,
):
    diag = {
        "executar": False,
        "confirmar": bool(confirmar),
        "ciente_destruicao": bool(ciente_destruicao),
        "host": None,
        "porta": None,
        "banco": None,
        "usuario": None,
        "uri_mascarada": None,
        "recusas": [],
        "avisos": [],
    }
    if not confirmar:
        diag["recusas"].append("Restore exige --confirmar.")
    if not ciente_destruicao:
        diag["recusas"].append(
            "Restore exige --estou-ciente-da-destruicao. "
            "A operação apaga o conteúdo do banco de destino."
        )
    if resolver_ambiente() == "production" and not mesmo_em_producao:
        diag["recusas"].append(
            "EDUCAMAIS_ENV=production: restore recusado sem --mesmo-em-producao. "
            "A CLI não restaura o banco de produção."
        )
    if uri is None or not str(uri).strip():
        diag["recusas"].append(
            "URI de destino explícita obrigatória. "
            "Nunca assume EDUCAMAIS_DATABASE_URI."
        )
        return diag
    try:
        url = make_url(normalizar_uri_banco(str(uri).strip()) if not uri_e_sqlite(uri) else uri)
    except Exception:
        diag["recusas"].append("URI de destino inválida.")
        diag["uri_mascarada"] = "<uri-invalida>"
        return diag
    diag["host"] = url.host
    diag["porta"] = int(url.port) if url.port else None
    diag["banco"] = url.database
    diag["usuario"] = url.username
    diag["uri_mascarada"] = mascarar_uri_banco(str(uri))

    if uri_e_sqlite(uri):
        path = Path(url.database).resolve() if url.database else None
        original = (Path(Config.ROOT_DIR) / "instance" / "educamais.db").resolve()
        if path is None:
            diag["recusas"].append("Caminho SQLite de destino ausente.")
        elif path == original:
            diag["recusas"].append("Recusa: não restaura sobre instance/educamais.db.")
        elif path.name == "educamais.db" and path.parent.name == "instance":
            diag["recusas"].append("Recusa: não restaura sobre o SQLite original.")
        diag["executar"] = bool(confirmar) and bool(ciente_destruicao) and not diag["recusas"]
        return diag

    driver = (url.drivername or "").lower()
    if not driver.startswith("postgresql"):
        diag["recusas"].append("Destino PostgreSQL obrigatório para pacote PostgreSQL.")
    host = (url.host or "").strip().lower()
    if not host:
        diag["recusas"].append("Host omitido recusado.")
    elif host not in HOSTS_LOCAIS:
        diag["recusas"].append(
            "Host remoto recusado. Restore de teste só em localhost/127.0.0.1/::1."
        )
    banco = (url.database or "").strip()
    banco_l = banco.lower()
    if banco_l in BANCOS_RESTORE_RECUSADOS:
        diag["recusas"].append("Banco recusado: %s." % banco)
    if banco_l != BANCO_RESTORE_PERMITIDO:
        diag["recusas"].append(
            "Restore aceita apenas o banco descartável %s (recebido: %s)."
            % (BANCO_RESTORE_PERMITIDO, banco)
        )
    if uri_origem:
        try:
            origem = make_url(normalizar_uri_banco(uri_origem) if not uri_e_sqlite(uri_origem) else uri_origem)
            if (
                (origem.host or "").lower() == host
                and int(origem.port or 5432) == int(url.port or 5432)
                and (origem.database or "").lower() == banco_l
            ):
                diag["recusas"].append("Destino não pode ser o mesmo banco de origem.")
        except Exception:
            pass
    vistos = set()
    recusas = []
    for r in diag["recusas"]:
        if r not in vistos:
            vistos.add(r)
            recusas.append(r)
    diag["recusas"] = recusas
    diag["executar"] = bool(confirmar) and bool(ciente_destruicao) and not diag["recusas"]
    return diag


def formatar_diagnostico_restore(diag):
    linhas = [
        "Diagnóstico de restore (senha omitida).",
        "Host: %s" % (diag.get("host") or "-"),
        "Porta: %s" % (diag.get("porta") or "-"),
        "Banco: %s" % (diag.get("banco") or "-"),
        "Usuario: %s" % (diag.get("usuario") or "-"),
        "URI: %s" % (diag.get("uri_mascarada") or "-"),
        "Confirmar: %s" % ("sim" if diag.get("confirmar") else "nao"),
        "Ciente da destruição: %s" % ("sim" if diag.get("ciente_destruicao") else "nao"),
        "Autorizado: %s" % ("sim" if diag.get("executar") else "nao"),
    ]
    for r in diag.get("recusas") or []:
        linhas.append("Recusa: %s" % r)
    for a in diag.get("avisos") or []:
        linhas.append("Aviso: %s" % a)
    return "\n".join(linhas)


def _recusar_uploads_vivos(pasta_destino, raiz=None):
    vivo = Path(resolver_upload_folder(raiz)).resolve()
    alvo = Path(pasta_destino).resolve()
    if alvo == vivo or caminho_dentro(str(vivo), str(alvo)):
        raise RestoreRecusado(
            "Recusa: não restaura uploads sobre a pasta viva instance/uploads "
            "(nem EDUCAMAIS_UPLOAD_FOLDER atual)."
        )


def limpar_schema_restore_test(uri):
    """DROP SCHEMA public só em educamais_restore_test. Nunca em outro banco."""
    if uri_e_sqlite(uri):
        path = make_url(uri).database
        if path and os.path.isfile(path):
            original = (Path(Config.ROOT_DIR) / "instance" / "educamais.db").resolve()
            if Path(path).resolve() == original:
                raise RestoreRecusado("Recusa: não apaga instance/educamais.db.")
            os.unlink(path)
        return
    url = _url_postgres(uri)
    if (url.database or "").lower() != BANCO_RESTORE_PERMITIDO:
        raise RestoreRecusado("Limpeza de schema só no banco %s." % BANCO_RESTORE_PERMITIDO)
    engine = _engine(uri)
    try:
        with engine.connect() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
            conn.commit()
    finally:
        engine.dispose()


def restaurar_pacote(
    pacote,
    destino_uri,
    pasta_uploads_destino,
    confirmar=False,
    ciente_destruicao=False,
    mesmo_em_producao=False,
    substituir_uploads=False,
    limpar_destino=False,
    pg_bin=None,
    uri_origem=None,
    raiz=None,
):
    """Restaura pacote em banco descartável. Fail-closed. Não toca produção."""
    raiz = raiz or Config.ROOT_DIR
    relatorio = RelatorioOperacao(uri=destino_uri)
    diag = validar_destino_restore(
        destino_uri,
        confirmar=confirmar,
        ciente_destruicao=ciente_destruicao,
        mesmo_em_producao=mesmo_em_producao,
        uri_origem=uri_origem,
    )
    if not diag["executar"]:
        relatorio.erro(formatar_diagnostico_restore(diag))
        raise RestoreRecusado(relatorio.texto())
    ver = verificar_pacote(pacote, relatorio)
    if not ver.get("ok"):
        raise BackupErro(relatorio.texto())
    manifesto = ver["manifesto"]
    tipo = (manifesto.get("banco") or {}).get("tipo")
    pacote = Path(pacote)
    pasta_uploads_destino = Path(pasta_uploads_destino).resolve()
    _recusar_uploads_vivos(pasta_uploads_destino, raiz)
    if pasta_uploads_destino.exists() and any(pasta_uploads_destino.iterdir()):
        if not substituir_uploads:
            raise RestoreRecusado(
                "Pasta de uploads de destino não está vazia. "
                "Informe --substituir-uploads apenas em destino descartável."
            )
        if pasta_uploads_destino.exists():
            shutil.rmtree(pasta_uploads_destino)
    pasta_uploads_destino.mkdir(parents=True, exist_ok=True)

    dump_nome = (manifesto.get("dump") or {}).get("arquivo")
    dump_path = pacote / dump_nome
    if limpar_destino:
        limpar_schema_restore_test(destino_uri)
        relatorio.ok("Destino descartável limpo antes do restore.")

    if tipo == "postgresql":
        if uri_e_sqlite(destino_uri):
            raise RestoreRecusado("Pacote PostgreSQL não restaura em SQLite.")
        url = _url_postgres(destino_uri)
        exe = descobrir_executavel_pg("pg_restore", pg_bin=pg_bin)
        argv = argv_pg_restore(exe, url, dump_path)
        env = _env_libpq(url)
        concl = _rodar_subprocesso(argv, env=env, timeout=_timeout_pg())
        out, err = _saida_processo(concl, destino_uri)
        if concl.returncode != 0:
            relatorio.erro(
                "pg_restore falhou (exit %s). %s %s"
                % (concl.returncode, out.strip(), err.strip())
            )
            raise BackupErro("pg_restore falhou.")
        relatorio.ok("pg_restore concluído no banco descartável.")
    elif tipo == "sqlite":
        if not uri_e_sqlite(destino_uri):
            raise RestoreRecusado("Pacote SQLite de desenvolvimento não restaura em PostgreSQL.")
        dest = make_url(destino_uri).database
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        if Path(dest).exists():
            Path(dest).unlink()
        shutil.copyfile(dump_path, dest)
        relatorio.ok("SQLite de desenvolvimento restaurado em destino descartável.")
        relatorio.aviso("Restore SQLite não é procedimento de produção.")
    else:
        raise BackupErro("Tipo de pacote desconhecido.")

    origem_up = pacote / PASTA_UPLOADS
    copiados, _ign = copiar_uploads(origem_up, pasta_uploads_destino, relatorio)
    relatorio.ok("Uploads restaurados em destino descartável: %s" % len(copiados))
    pos = validar_pos_restore(
        destino_uri, manifesto, pasta_uploads_destino, relatorio=relatorio,
    )
    return {
        "ok": pos.get("ok"),
        "diagnostico": diag,
        "validacao": pos,
        "relatorio": relatorio.texto(),
        "pasta_uploads": str(pasta_uploads_destino),
    }


def validar_pos_restore(uri, manifesto, pasta_uploads, relatorio=None):
    """Não recalcula notas, faltas nem frequência. Só lê e compara."""
    relatorio = relatorio or RelatorioOperacao(uri=uri)
    engine = _engine(uri)
    try:
        alembic = coletar_alembic(engine)
        esperado = ((manifesto or {}).get("alembic") or {}).get("revisao")
        if alembic != esperado:
            relatorio.erro(
                "Revisão Alembic divergente: restaurado=%s manifesto=%s"
                % (alembic, esperado)
            )
        else:
            relatorio.ok("Alembic: %s" % (alembic or "(ausente)"))
        insp = inspect(engine)
        nomes = set(insp.get_table_names())
        faltando = [t for t in TABELAS_ESSENCIAIS if t not in nomes]
        if faltando:
            relatorio.erro("Tabelas essenciais ausentes: %s" % ", ".join(faltando))
        else:
            relatorio.ok("Tabelas essenciais presentes.")
        contagens, pks = coletar_contagens_e_pks(engine)
        orig_c = (manifesto or {}).get("contagens") or {}
        orig_p = (manifesto or {}).get("pks") or {}
        divergencias = []
        for tabela, n in orig_c.items():
            if orig_c[tabela] != contagens.get(tabela):
                divergencias.append(tabela)
        if divergencias:
            relatorio.erro("Contagens divergentes: %s" % ", ".join(divergencias))
        else:
            relatorio.ok("Contagens por tabela coincidem com o manifesto.")
        for tabela, info in orig_p.items():
            if pks.get(tabela) != info:
                relatorio.erro("Integridade de IDs divergente: %s" % tabela)
                divergencias.append(tabela)
        if orig_p and not any(pks.get(t) != orig_p.get(t) for t in orig_p):
            relatorio.ok("min/max/count de PKs preservados.")
        orfaos = []
        try:
            from src.migracao_sqlite_pg import mapear_schema, orfaos_fk
            schema = mapear_schema()
            with engine.connect() as conn:
                orfaos = orfaos_fk(conn, schema)
        except Exception:
            relatorio.aviso("Checagem de FK não disponível neste contexto.")
        if orfaos:
            relatorio.aviso("FKs órfãs após restore: %s" % len(orfaos))
        else:
            relatorio.ok("Checagem de FKs sem órfãos detectados.")
        arquivos = listar_arquivos_uploads(pasta_uploads, calcular_hash=True)
        refs = coletar_referencias_arquivos(engine)
        cons = auditar_consistencia_uploads(refs, arquivos)
        man_up = (manifesto.get("uploads") or {}).get("arquivos") or []
        mapa_orig = {i["rel"]: i for i in man_up}
        for arq in arquivos:
            orig = mapa_orig.get(arq["rel"])
            if not orig or orig.get("sha256") != arq.get("sha256"):
                relatorio.erro("Upload restaurado divergente: %s" % arq["rel"])
                divergencias.append(arq["rel"])
        if len(arquivos) != len(man_up):
            relatorio.erro("Quantidade de uploads restaurados diverge do manifesto.")
            divergencias.append("uploads")
        else:
            relatorio.ok("Hashes de uploads restaurados coincidem.")
        consulta = _consultar_essenciais(engine, nomes)
        relatorio.ok("Consulta de dados essenciais executada (sem recálculo acadêmico).")
        ok = (
            alembic == esperado
            and not faltando
            and not divergencias
        )
        return {
            "ok": ok,
            "alembic": alembic,
            "contagens": contagens,
            "pks": pks,
            "orfaos_fk": orfaos,
            "consistencia": cons,
            "consulta": consulta,
            "recalculou_academico": False,
            "relatorio": relatorio.texto(),
        }
    finally:
        engine.dispose()


def _consultar_essenciais(bind, nomes):
    out = {}
    with bind.connect() as conn:
        for tabela in (
            "usuario", "escola", "turma", "matricula",
            "registro_nota", "registro_falta", "registro_frequencia",
            "historico_anual", "audit_log",
        ):
            if tabela in nomes:
                out[tabela] = _contar(conn, tabela)
            else:
                out[tabela] = None
    return out


def manifesto_contem_segredo(manifesto, uri=None, extras=None):
    blob = json.dumps(manifesto, ensure_ascii=False)
    if uri:
        try:
            url = make_url(uri)
            if url.password and url.password in blob:
                return True
            bruto = url.render_as_string(hide_password=False)
            if bruto in blob:
                return True
        except Exception:
            pass
    for extra in extras or []:
        if extra and extra in blob:
            return True
    baixo = blob.lower()
    for chave in ("secret_key", "pgpassword"):
        if chave in baixo:
            return True
    return False


def register_cli_backup(educamais):
    import click

    @educamais.command("backup")
    @click.option("--destino", default=None, help="Diretório raiz dos pacotes (padrão: var/backups).")
    @click.option("--pasta-uploads", default=None, help="Override da pasta de uploads (testes).")
    def backup_cli(destino, pasta_uploads):
        """Backup PostgreSQL (pg_dump -Fc) + uploads. Não é restore."""
        from flask import current_app
        uri = current_app.config.get("SQLALCHEMY_DATABASE_URI")
        try:
            rel = criar_backup(
                uri=uri,
                pasta_uploads=pasta_uploads,
                destino_raiz=destino,
            )
        except BackupErro as exc:
            click.echo(mascarar_texto(str(exc), uri))
            raise SystemExit(1)
        click.echo(rel["relatorio"])
        click.echo("[OK] pacote=%s" % rel["pacote"])

    @educamais.command("backup-sqlite-dev")
    @click.option("--destino", default=None)
    @click.option("--pasta-uploads", default=None)
    def backup_sqlite_cli(destino, pasta_uploads):
        """Backup simples de SQLite para desenvolvimento. Não usar em produção."""
        from flask import current_app
        uri = current_app.config.get("SQLALCHEMY_DATABASE_URI")
        try:
            rel = criar_backup_sqlite_dev(
                uri=uri,
                pasta_uploads=pasta_uploads,
                destino_raiz=destino,
            )
        except BackupErro as exc:
            click.echo(mascarar_texto(str(exc), uri))
            raise SystemExit(1)
        click.echo(rel["relatorio"])

    @educamais.command("auditar-uploads")
    def auditar_uploads_cli():
        """Compara planejamento.arquivo com a pasta de uploads. Somente leitura."""
        rel = auditar_uploads_atual()
        click.echo(formatar_consistencia(rel))
        click.echo("Pasta: %s" % rel.get("pasta"))
        click.echo("Arquivos: %s  Referências: %s" % (
            rel.get("n_arquivos"), rel.get("n_referencias"),
        ))

    @educamais.command("verificar-backup")
    @click.option("--pacote", required=True)
    def verificar_backup_cli(pacote):
        """Confere hashes do pacote. Não restaura."""
        rel = verificar_pacote(pacote)
        click.echo(rel["relatorio"])
        if not rel.get("ok"):
            raise SystemExit(1)

    @educamais.command("ensaiar-restore")
    @click.option("--pacote", required=True)
    @click.option("--destino-uri", required=True, help="URI explícita. Sem padrão.")
    @click.option("--pasta-uploads-destino", required=True)
    @click.option("--confirmar", is_flag=True)
    @click.option("--estou-ciente-da-destruicao", is_flag=True)
    @click.option("--mesmo-em-producao", is_flag=True)
    @click.option("--substituir-uploads", is_flag=True)
    @click.option("--limpar-destino", is_flag=True, help="DROP SCHEMA só em educamais_restore_test.")
    def ensaiar_restore_cli(
        pacote, destino_uri, pasta_uploads_destino, confirmar,
        estou_ciente_da_destruicao, mesmo_em_producao, substituir_uploads,
        limpar_destino,
    ):
        """Restaura em banco descartável e valida. Não usa produção."""
        _rodar_restore_cli(
            pacote, destino_uri, pasta_uploads_destino, confirmar,
            estou_ciente_da_destruicao, mesmo_em_producao, substituir_uploads,
            limpar_destino,
        )

    @educamais.command("restaurar")
    @click.option("--pacote", required=True)
    @click.option("--destino-uri", required=True, help="URI explícita. Sem padrão.")
    @click.option("--pasta-uploads-destino", required=True)
    @click.option("--confirmar", is_flag=True)
    @click.option("--estou-ciente-da-destruicao", is_flag=True)
    @click.option("--mesmo-em-producao", is_flag=True)
    @click.option("--substituir-uploads", is_flag=True)
    @click.option("--limpar-destino", is_flag=True, help="DROP SCHEMA só em educamais_restore_test.")
    def restaurar_cli(
        pacote, destino_uri, pasta_uploads_destino, confirmar,
        estou_ciente_da_destruicao, mesmo_em_producao, substituir_uploads,
        limpar_destino,
    ):
        """Atalho explícito e perigoso: mesmo fail-closed de ensaiar-restore."""
        _rodar_restore_cli(
            pacote, destino_uri, pasta_uploads_destino, confirmar,
            estou_ciente_da_destruicao, mesmo_em_producao, substituir_uploads,
            limpar_destino,
        )


def _rodar_restore_cli(
    pacote, destino_uri, pasta_uploads_destino, confirmar,
    ciente, mesmo_em_producao, substituir_uploads, limpar_destino=False,
):
    import click
    from flask import current_app
    origem = current_app.config.get("SQLALCHEMY_DATABASE_URI")
    diag = validar_destino_restore(
        destino_uri,
        confirmar=confirmar,
        ciente_destruicao=ciente,
        mesmo_em_producao=mesmo_em_producao,
        uri_origem=origem,
    )
    click.echo(formatar_diagnostico_restore(diag))
    if not diag["executar"]:
        raise SystemExit(2)
    try:
        rel = restaurar_pacote(
            pacote,
            destino_uri,
            pasta_uploads_destino,
            confirmar=True,
            ciente_destruicao=True,
            mesmo_em_producao=mesmo_em_producao,
            substituir_uploads=substituir_uploads,
            limpar_destino=limpar_destino,
            uri_origem=origem,
        )
    except RestoreRecusado as exc:
        click.echo(mascarar_texto(str(exc), destino_uri))
        raise SystemExit(2)
    except BackupErro as exc:
        click.echo(mascarar_texto(str(exc), destino_uri))
        raise SystemExit(1)
    click.echo(rel["relatorio"])
    if not rel.get("ok"):
        raise SystemExit(1)
    click.echo("[OK] restore validado em destino descartável.")
