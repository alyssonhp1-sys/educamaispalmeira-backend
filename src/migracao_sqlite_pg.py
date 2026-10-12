"""Ensaio controlado de migração SQLite -> PostgreSQL.

Não é cutover de produção. Não altera instance/educamais.db.
Schema de destino PostgreSQL entra só via Alembic (nunca db.create_all).
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import Boolean, Date, DateTime, Float, create_engine, event, inspect, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.sql.schema import UniqueConstraint

from src.config import (
    Config,
    ConfiguracaoInsegura,
    mascarar_uri_banco,
    normalizar_uri_banco,
    resolver_opcoes_engine,
    resolver_upload_folder,
    uri_e_sqlite,
)
from src.migracoes import TABELAS_ESSENCIAIS


BANCO_ENSAIO_PERMITIDO = "educamais_migracao_test"
BANCOS_RECUSADOS = frozenset({
    "postgres",
    "template0",
    "template1",
    "educamais_pg_test",
})
BANCOS_AMBIGUOS = frozenset({
    "educamais",
    "educamais_demo",
    "educamaisteste",
    "postgres",
    "template0",
    "template1",
})
HOSTS_LOCAIS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})
TABELAS_INTERNAS_PREFIXO = "sqlite_"
TABELA_ALEMBIC = "alembic_version"
IDENT_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TAMANHO_LOTE = 500
SUBDIR_ENSAIO = "ensaio_migracao"
NOME_COPIA = "educamais_copia.db"


class DestinoInseguro(ConfiguracaoInsegura):
    """URI/host/banco de destino recusado."""


class MigracaoAbortada(RuntimeError):
    """Ensaio interrompido sem commit de dados no destino."""


class FonteInsegura(ConfiguracaoInsegura):
    """Tentativa de usar o SQLite original onde só a cópia é permitida."""


def caminho_sqlite_original(raiz=None):
    raiz = Path(raiz or Config.ROOT_DIR).resolve()
    return (raiz / "instance" / "educamais.db").resolve()


def caminho_copia_padrao(raiz=None):
    raiz = Path(raiz or Config.ROOT_DIR).resolve()
    return (raiz / "instance" / SUBDIR_ENSAIO / NOME_COPIA).resolve()


def uri_sqlite_arquivo(path):
    return "sqlite:///" + Path(path).resolve().as_posix()


def fingerprint_arquivo(path):
    p = Path(path)
    if not p.is_file():
        return {"existe": False, "caminho": str(p), "size": None, "sha256": None}
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    st = p.stat()
    return {
        "existe": True,
        "caminho": str(p.resolve()),
        "size": st.st_size,
        "sha256": h.hexdigest(),
    }


def arquivos_adjacentes_sqlite(path):
    p = Path(path)
    out = []
    for suf in ("-wal", "-shm", "-journal"):
        adj = Path(str(p) + suf)
        out.append({
            "caminho": str(adj),
            "existe": adj.is_file(),
            "size": adj.stat().st_size if adj.is_file() else 0,
        })
    return out


def e_mesmo_arquivo(a, b):
    pa, pb = Path(a), Path(b)
    if not pa.exists() or not pb.exists():
        return pa.resolve() == pb.resolve()
    try:
        return pa.resolve().samefile(pb.resolve())
    except OSError:
        return pa.resolve() == pb.resolve()


def _criar_engine(uri, **extra):
    opts = dict(resolver_opcoes_engine(uri))
    opts["poolclass"] = NullPool
    opts.update(extra)
    return create_engine(uri, **opts)


def recusar_se_original(path, raiz=None):
    original = caminho_sqlite_original(raiz)
    alvo = Path(path).resolve()
    if e_mesmo_arquivo(alvo, original) or alvo == original:
        raise FonteInsegura(
            "Recusa: instance/educamais.db é o SQLite original e não pode ser "
            "fonte de escrita/alinhamento/importação. Use uma cópia de trabalho."
        )
    return alvo


def _ident(nome):
    if not nome or not IDENT_OK.match(nome):
        raise MigracaoAbortada("Identificador SQL recusado: %r" % (nome,))
    return nome


def mascarar_excecao(exc, uri=None):
    msg = str(exc)
    if not uri:
        return msg
    try:
        url = make_url(uri)
    except Exception:
        return msg
    senha = url.password
    if senha:
        msg = msg.replace(senha, "***")
        msg = msg.replace(quote_plus(senha), "***")
    mascarada = mascarar_uri_banco(uri)
    bruto = None
    try:
        bruto = url.render_as_string(hide_password=False)
    except Exception:
        bruto = None
    if bruto and mascarada:
        msg = msg.replace(bruto, mascarada)
    return msg


def validar_destino_postgresql(uri, confirmar=False, banco_permitido=None):
    """Valida URI de destino. Sem --confirmar nunca autoriza alteração.

    Não conecta. Não imprime senha.
    """
    permitido = (banco_permitido or BANCO_ENSAIO_PERMITIDO).lower()
    diag = {
        "executar": False,
        "confirmar": bool(confirmar),
        "host": None,
        "porta": None,
        "banco": None,
        "usuario": None,
        "uri_mascarada": None,
        "recusas": [],
        "avisos": [],
    }
    if uri is None or not str(uri).strip():
        diag["recusas"].append(
            "URI explícita obrigatória. Nenhuma URI padrão é assumida "
            "(nem EDUCAMAIS_DATABASE_URI, nem EDUCAMAIS_TEST_DATABASE_URI)."
        )
        return diag
    try:
        url = make_url(normalizar_uri_banco(str(uri).strip()))
    except Exception:
        diag["recusas"].append("URI inválida.")
        diag["uri_mascarada"] = "<uri-invalida>"
        return diag

    diag["host"] = url.host
    diag["porta"] = int(url.port or 5432)
    diag["banco"] = url.database
    diag["usuario"] = url.username
    diag["uri_mascarada"] = url.render_as_string(hide_password=True)

    driver = (url.drivername or "").lower()
    if not driver.startswith("postgresql"):
        diag["recusas"].append("Destino deve ser PostgreSQL (postgresql+psycopg).")

    host = (url.host or "").strip().lower()
    if not host:
        diag["recusas"].append(
            "Host omitido recusado. Informe localhost, 127.0.0.1 ou ::1."
        )
    elif host not in HOSTS_LOCAIS:
        diag["recusas"].append(
            "Host remoto recusado (%s). Apenas localhost/127.0.0.1/::1." % host
        )

    banco = (url.database or "").strip()
    banco_l = banco.lower()
    if not banco_l:
        diag["recusas"].append("Nome de banco ausente.")
    if banco_l in BANCOS_RECUSADOS:
        diag["recusas"].append("Banco recusado: %s." % banco)
    if banco_l in BANCOS_AMBIGUOS:
        diag["recusas"].append(
            "Nome de banco ambíguo ou potencialmente produtivo recusado: %s." % banco
        )
    if banco_l and banco_l != permitido:
        diag["recusas"].append(
            "Ensaio aceita apenas o banco %s (recebido: %s)."
            % (BANCO_ENSAIO_PERMITIDO, banco)
        )

    if url.password is None:
        diag["avisos"].append("URI sem senha (peer/trust local).")

    if not confirmar:
        diag["avisos"].append(
            "Sem --confirmar: apenas diagnóstico. Nenhuma alteração será feita."
        )

    # Dedup recusas mantendo ordem
    vistos = set()
    recusas = []
    for r in diag["recusas"]:
        if r not in vistos:
            vistos.add(r)
            recusas.append(r)
    diag["recusas"] = recusas
    diag["executar"] = bool(confirmar) and not diag["recusas"]
    return diag


def formatar_diagnostico_destino(diag):
    linhas = [
        "Diagnóstico do destino PostgreSQL (senha omitida).",
        "Host: %s" % (diag.get("host") or "-"),
        "Porta: %s" % (diag.get("porta") or "-"),
        "Banco: %s" % (diag.get("banco") or "-"),
        "Usuario: %s" % (diag.get("usuario") or "-"),
        "URI: %s" % (diag.get("uri_mascarada") or "-"),
        "Confirmar: %s" % ("sim" if diag.get("confirmar") else "nao"),
        "Autorizado a alterar: %s" % ("sim" if diag.get("executar") else "nao"),
    ]
    recusas = diag.get("recusas") or []
    if recusas:
        linhas.append("Recusas:")
        for r in recusas:
            linhas.append("  - %s" % r)
    else:
        linhas.append("Recusas: nenhuma.")
    for a in diag.get("avisos") or []:
        linhas.append("Aviso: %s" % a)
    return "\n".join(linhas)


def exigir_destino_autorizado(uri, confirmar, banco_permitido=None):
    diag = validar_destino_postgresql(
        uri, confirmar=confirmar, banco_permitido=banco_permitido,
    )
    if not confirmar or diag["recusas"]:
        raise DestinoInseguro(formatar_diagnostico_destino(diag))
    return diag


def _carregar_metadata():
    from src import models as _models  # noqa: F401
    from src import db
    return db.metadata


def mapear_schema(metadata=None):
    """Mapa de tabelas, PKs, FKs, UNIQUE e nullability a partir do metadata."""
    metadata = metadata if metadata is not None else _carregar_metadata()
    tabelas = {}
    arestas = []
    for nome in sorted(metadata.tables):
        table = metadata.tables[nome]
        pks = [c.name for c in table.primary_key.columns]
        colunas = []
        for c in table.columns:
            colunas.append({
                "nome": c.name,
                "nullable": bool(c.nullable),
                "tipo": type(c.type).__name__,
                "pk": bool(c.primary_key),
            })
        uniques = []
        for cons in table.constraints:
            if isinstance(cons, UniqueConstraint):
                uniques.append({
                    "nome": cons.name,
                    "colunas": [col.name for col in cons.columns],
                    "parcial": False,
                })
        for idx in table.indexes:
            if not idx.unique:
                continue
            uniques.append({
                "nome": idx.name,
                "colunas": [col.name for col in idx.columns],
                "parcial": True,
            })
        fks = []
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            fks.append({
                "coluna": fk.parent.name,
                "ref_tabela": parent,
                "ref_coluna": fk.column.name,
                "ondelete": fk.ondelete,
            })
            arestas.append((nome, parent, fk.parent.name))
        tabelas[nome] = {
            "pk": pks,
            "colunas": colunas,
            "fks": fks,
            "uniques": uniques,
        }
    return {"tabelas": tabelas, "arestas": arestas}


def ordem_importacao(schema=None):
    """Ordem topológica derivada do grafo de FKs. Empate: nome alfabético."""
    schema = schema if schema is not None else mapear_schema()
    nodes = list(schema["tabelas"])
    inbound = {n: 0 for n in nodes}
    edges = {n: [] for n in nodes}
    self_ref = []
    vistos = set()
    for child, parent, coluna in schema["arestas"]:
        if child not in inbound or parent not in inbound:
            continue
        if child == parent:
            self_ref.append((child, coluna))
            continue
        if (parent, child) in vistos:
            continue
        vistos.add((parent, child))
        edges[parent].append(child)
        inbound[child] += 1
    ready = sorted(n for n, k in inbound.items() if k == 0)
    ordem = []
    while ready:
        n = ready.pop(0)
        ordem.append(n)
        for child in sorted(set(edges[n])):
            inbound[child] -= 1
            if inbound[child] == 0:
                ready.append(child)
                ready.sort()
    ciclos = sorted(n for n in nodes if n not in ordem)
    return {
        "ordem": ordem,
        "ciclos": ciclos,
        "auto_referencias": self_ref,
        "arestas": list(schema["arestas"]),
    }


def tabelas_ignoradas_sqlite(nomes):
    out = []
    for n in nomes:
        if n == TABELA_ALEMBIC or n.startswith(TABELAS_INTERNAS_PREFIXO):
            out.append(n)
    return out


def _kind_coluna(col):
    tipo = col.type
    if isinstance(tipo, Boolean):
        return "boolean"
    if isinstance(tipo, DateTime):
        return "datetime"
    if isinstance(tipo, Date):
        return "date"
    if isinstance(tipo, Float):
        return "float"
    return "other"


def converter_valor(valor, kind):
    """Normaliza só representação de tipo. Não altera valor de negócio.

    None permanece None. String vazia permanece string vazia.
    """
    if valor is None:
        return None
    if kind == "boolean":
        if valor is True or valor is False:
            return bool(valor)
        if valor in (1, 0):
            return bool(valor)
        if isinstance(valor, str):
            low = valor.strip().lower()
            if low in ("1", "t", "true"):
                return True
            if low in ("0", "f", "false"):
                return False
        return valor
    if kind == "date":
        if isinstance(valor, datetime):
            return valor.date()
        if isinstance(valor, date):
            return valor
        if isinstance(valor, str):
            texto = valor.strip()
            if not texto:
                return valor
            return date.fromisoformat(texto[:10])
        return valor
    if kind == "datetime":
        if isinstance(valor, datetime):
            if valor.tzinfo is not None:
                return valor.replace(tzinfo=None)
            return valor
        if isinstance(valor, date) and not isinstance(valor, datetime):
            return datetime(valor.year, valor.month, valor.day)
        if isinstance(valor, str):
            texto = valor.strip()
            if not texto:
                return valor
            texto = texto.replace("T", " ")
            if texto.endswith("Z"):
                texto = texto[:-1]
            try:
                return datetime.fromisoformat(texto)
            except ValueError:
                if len(texto) >= 19:
                    return datetime.strptime(texto[:19], "%Y-%m-%d %H:%M:%S")
                return datetime.strptime(texto[:10], "%Y-%m-%d")
        return valor
    if kind == "float":
        if isinstance(valor, bool):
            return valor
        if isinstance(valor, (int, float)):
            return float(valor)
        return valor
    return valor


def converter_linha(mapping, table):
    out = {}
    for col in table.columns:
        if col.name not in mapping:
            continue
        out[col.name] = converter_valor(mapping[col.name], _kind_coluna(col))
    return out


def valor_canonico(valor, kind):
    """Comparação determinística fonte/destino. Não arredonda notas."""
    if valor is None:
        return ("NULL", None)
    convertido = converter_valor(valor, kind)
    if convertido is None:
        return ("NULL", None)
    if kind == "boolean":
        if convertido is True:
            return ("BOOL", "true")
        if convertido is False:
            return ("BOOL", "false")
        return ("RAW", repr(convertido))
    if kind == "date":
        if isinstance(convertido, datetime):
            convertido = convertido.date()
        if isinstance(convertido, date):
            return ("DATE", convertido.isoformat())
        return ("RAW", repr(convertido))
    if kind == "datetime":
        if isinstance(convertido, datetime):
            dt = convertido.replace(tzinfo=None)
            return ("DT", dt.isoformat(sep=" ", timespec="microseconds"))
        return ("RAW", repr(convertido))
    if kind == "float":
        if isinstance(convertido, bool):
            return ("RAW", repr(convertido))
        if isinstance(convertido, (int, float)):
            return ("FLOAT", repr(float(convertido)))
        return ("RAW", repr(convertido))
    if isinstance(convertido, bytes):
        return ("BYTES", convertido.hex())
    return ("STR", convertido)


def hash_tabela(linhas, colunas, kinds):
    h = hashlib.sha256()
    for row in linhas:
        partes = []
        for nome in colunas:
            marca, texto = valor_canonico(row.get(nome), kinds[nome])
            if texto is None:
                partes.append("%s=NULL" % nome)
            else:
                partes.append("%s=%s:%s" % (nome, marca, texto))
        h.update(("|".join(partes) + "\n").encode("utf-8"))
    return h.hexdigest()


@contextmanager
def engine_sqlite_leitura(path):
    uri = uri_sqlite_arquivo(path)
    engine = _criar_engine(uri)

    @event.listens_for(engine, "connect")
    def _somente_leitura(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA query_only = ON")
        cur.close()

    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def engine_destino(uri, sqlite_fk=True):
    engine = _criar_engine(uri)
    if uri_e_sqlite(uri) and sqlite_fk:

        @event.listens_for(engine, "connect")
        def _fk(dbapi_conn, _rec):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def app_temporaria(uri, *, env="development", demo=False, preparar_schema=False,
                   secret="chave-ensaio-migracao-nao-producao"):
    """App Flask apontada para URI temporária. Sempre restore Config e db.

    preparar_schema deve permanecer False no destino PostgreSQL.
    """
    from src import db
    from src.config import resolver_opcoes_engine as _opts

    flask_app_anterior = None
    try:
        from flask import current_app, has_app_context
        if has_app_context():
            flask_app_anterior = current_app._get_current_object()
    except Exception:
        flask_app_anterior = None

    snap = {
        "EDUCAMAIS_ENV": os.environ.get("EDUCAMAIS_ENV"),
        "EDUCAMAIS_DEMO": os.environ.get("EDUCAMAIS_DEMO"),
        "EDUCAMAIS_SECRET_KEY": os.environ.get("EDUCAMAIS_SECRET_KEY"),
        "EDUCAMAIS_DATABASE_URI": os.environ.get("EDUCAMAIS_DATABASE_URI"),
        "EDUCAMAIS_DEBUG": os.environ.get("EDUCAMAIS_DEBUG"),
    }
    old_uri = Config.SQLALCHEMY_DATABASE_URI
    old_engine_opts = Config.SQLALCHEMY_ENGINE_OPTIONS
    app = None
    try:
        os.environ["EDUCAMAIS_ENV"] = env
        os.environ["EDUCAMAIS_DEMO"] = "1" if demo else "0"
        os.environ["EDUCAMAIS_SECRET_KEY"] = secret
        os.environ["EDUCAMAIS_DATABASE_URI"] = uri
        os.environ.pop("EDUCAMAIS_DEBUG", None)
        Config.SQLALCHEMY_DATABASE_URI = uri
        Config.SQLALCHEMY_ENGINE_OPTIONS = _opts(uri)
        from src import create_app
        app = create_app(preparar_schema=preparar_schema)
        yield app
    finally:
        if app is not None:
            try:
                ctx = app.app_context()
                ctx.push()
                try:
                    db.session.remove()
                    db.engine.dispose()
                finally:
                    ctx.pop()
            except Exception:
                pass
        Config.SQLALCHEMY_DATABASE_URI = old_uri
        Config.SQLALCHEMY_ENGINE_OPTIONS = old_engine_opts
        for chave, valor in snap.items():
            if valor is None:
                os.environ.pop(chave, None)
            else:
                os.environ[chave] = valor
        if flask_app_anterior is not None:
            try:
                db.init_app(flask_app_anterior)
            except Exception:
                pass


def caminho_sqlite_da_uri(uri):
    if not uri_e_sqlite(uri):
        return None
    try:
        url = make_url(uri)
    except Exception:
        return None
    database = url.database
    if not database:
        return None
    return Path(database).resolve()


def verificar_engine_nao_e_original(engine, raiz=None):
    original = caminho_sqlite_original(raiz)
    try:
        url = engine.url
        if url.get_backend_name() != "sqlite":
            return
        database = url.database
        if not database:
            return
        atual = Path(database).resolve()
    except Exception as exc:
        raise DestinoInseguro("Não foi possível identificar o arquivo SQLite do engine.") from exc
    if atual == original or (atual.exists() and e_mesmo_arquivo(atual, original)):
        raise FonteInsegura(
            "Recusa: o engine aponta para instance/educamais.db. Operação abortada."
        )


def verificar_engine_e_o_arquivo(engine, path):
    esperado = Path(path).resolve()
    url = engine.url
    if url.get_backend_name() != "sqlite":
        raise DestinoInseguro("Engine não é SQLite.")
    atual = Path(url.database).resolve()
    if atual != esperado:
        raise DestinoInseguro(
            "Engine SQLite aponta para arquivo inesperado."
        )


def abrir_sqlite_somente_leitura(path):
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(str(p))
    uri = "file:%s?mode=ro" % p.as_posix()
    con = sqlite3.connect(uri, uri=True)
    con.execute("PRAGMA query_only = ON")
    con.row_factory = sqlite3.Row
    return con


def copiar_sqlite_arquivo(origem, destino):
    """Cópia de trabalho. Sem WAL: copy2 byte a byte. Com WAL: backup API (somente leitura)."""
    orig = Path(origem).resolve()
    dest = Path(destino).resolve()
    recusar_se_original(dest)
    if e_mesmo_arquivo(orig, dest):
        raise FonteInsegura("Origem e destino da cópia não podem ser o mesmo arquivo.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tem_wal = any(a["existe"] for a in arquivos_adjacentes_sqlite(orig))
    if tem_wal:
        src = abrir_sqlite_somente_leitura(orig)
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
    else:
        shutil.copy2(orig, dest)
    fp = fingerprint_arquivo(dest)
    fp["metodo"] = "sqlite_backup" if tem_wal else "copy2"
    return fp


def preparar_copia_trabalho(original=None, copia=None, confirmar=False, substituir=False, raiz=None):
    original = Path(original or caminho_sqlite_original(raiz)).resolve()
    copia = Path(copia or caminho_copia_padrao(raiz)).resolve()
    fp_antes = fingerprint_arquivo(original)
    rel = {
        "original": fp_antes,
        "copia": fingerprint_arquivo(copia) if copia.exists() else {
            "existe": False, "caminho": str(copia), "size": None, "sha256": None,
        },
        "executado": False,
        "adjacentes_original": arquivos_adjacentes_sqlite(original),
    }
    if not confirmar:
        rel["aviso"] = "Sem --confirmar: a cópia não foi criada nem substituída."
        return rel
    if not original.is_file():
        raise FileNotFoundError("SQLite original não encontrado: %s" % original)
    recusar_se_original(copia, raiz)
    if copia.exists() and not substituir:
        raise MigracaoAbortada(
            "A cópia já existe (%s). Informe --substituir-copia para recriá-la."
            % copia
        )
    if copia.exists():
        copia.unlink()
    copiar_sqlite_arquivo(original, copia)
    rel["copia"] = fingerprint_arquivo(copia)
    rel["original_depois"] = fingerprint_arquivo(original)
    rel["executado"] = True
    rel["byte_a_byte_original"] = (
        rel["original_depois"]["sha256"] == fp_antes["sha256"]
        and rel["original_depois"]["size"] == fp_antes["size"]
    )
    return rel


def listar_tabelas_sqlite(path):
    con = abrir_sqlite_somente_leitura(path)
    try:
        return [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
    finally:
        con.close()


def _min_max_id(con, tabela):
    cols = [r[1] for r in con.execute("PRAGMA table_info([%s])" % _ident(tabela))]
    if "id" not in cols:
        return None, None
    row = con.execute(
        "SELECT MIN(id), MAX(id) FROM [%s]" % _ident(tabela)
    ).fetchone()
    return row[0], row[1]


def auditar_fonte_sqlite(original=None, copia=None, raiz=None):
    """Somente leitura. Não corrige dados."""
    original = Path(original or caminho_sqlite_original(raiz)).resolve()
    copia = Path(copia).resolve() if copia else None
    from src.integridade import auditar_integridade, formatar_relatorio as fmt_int

    fp_orig = fingerprint_arquivo(original)
    rel = {
        "somente_leitura": True,
        "original": fp_orig,
        "adjacentes_original": arquivos_adjacentes_sqlite(original),
        "copia": None,
        "tabelas": [],
        "contagens": {},
        "ids": {},
        "faltando_essenciais": [],
        "extras": [],
        "internas": [],
        "alembic_version": None,
        "head_esperado": None,
        "alinhada_ao_head": False,
        "nulos_fk": {},
        "integridade": None,
        "contagens_nomeadas": {},
        "inconsistencias": [],
    }
    if copia is not None:
        rel["copia"] = fingerprint_arquivo(copia)

    if not original.is_file():
        rel["inconsistencias"].append("SQLite original ausente.")
        return rel

    from src.migracoes import revisao_head as _head_fn
    con = abrir_sqlite_somente_leitura(original)
    try:
        nomes = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ]
        rel["tabelas"] = nomes
        rel["internas"] = tabelas_ignoradas_sqlite(nomes)
        ess = set(TABELAS_ESSENCIAIS)
        rel["faltando_essenciais"] = sorted(ess - set(nomes))
        rel["extras"] = sorted(
            n for n in nomes
            if n not in ess and n not in rel["internas"] and n != TABELA_ALEMBIC
        )
        for t in nomes:
            if t.startswith(TABELAS_INTERNAS_PREFIXO):
                continue
            n = con.execute("SELECT COUNT(*) FROM [%s]" % _ident(t)).fetchone()[0]
            rel["contagens"][t] = n
            mn, mx = _min_max_id(con, t)
            if mn is not None or mx is not None:
                rel["ids"][t] = {"min": mn, "max": mx}
        if TABELA_ALEMBIC in nomes:
            row = con.execute("SELECT version_num FROM alembic_version").fetchone()
            rel["alembic_version"] = row[0] if row else None
        fks_criticas = (
            ("registro_nota", "matricula_id"),
            ("registro_falta", "matricula_id"),
            ("registro_frequencia", "matricula_id"),
            ("relatorio_descritivo", "matricula_id"),
            ("historico_anual", "matricula_id"),
            ("aluno", "turma_id"),
            ("aluno", "escola_id"),
            ("usuario", "escola_id"),
            ("registro_falta", "disciplina_id"),
            ("registro_aula", "disciplina_id"),
            ("registro_frequencia", "registro_aula_id"),
        )
        for tabela, col in fks_criticas:
            if tabela not in nomes:
                rel["nulos_fk"]["%s.%s" % (tabela, col)] = None
                continue
            cols = [r[1] for r in con.execute("PRAGMA table_info([%s])" % _ident(tabela))]
            if col not in cols:
                rel["nulos_fk"]["%s.%s" % (tabela, col)] = "(coluna ausente)"
                continue
            n = con.execute(
                "SELECT COUNT(*) FROM [%s] WHERE [%s] IS NULL" % (_ident(tabela), _ident(col))
            ).fetchone()[0]
            rel["nulos_fk"]["%s.%s" % (tabela, col)] = n
        rel["contagens_nomeadas"] = {
            "matriculas": rel["contagens"].get("matricula"),
            "notas": rel["contagens"].get("registro_nota"),
            "faltas": rel["contagens"].get("registro_falta"),
            "registros_aula": rel["contagens"].get("registro_aula"),
            "frequencias": rel["contagens"].get("registro_frequencia"),
            "relatorios_descritivos": rel["contagens"].get("relatorio_descritivo"),
            "historicos_anuais": rel["contagens"].get("historico_anual"),
            "usuarios": rel["contagens"].get("usuario"),
            "escolas": rel["contagens"].get("escola"),
            "turmas": rel["contagens"].get("turma"),
            "alunos": rel["contagens"].get("aluno"),
            "ano_letivo": rel["contagens"].get("ano_letivo"),
            "periodo_letivo": rel["contagens"].get("periodo_letivo"),
            "dia_calendario": rel["contagens"].get("dia_calendario"),
            "planejamentos": rel["contagens"].get("planejamento"),
            "avisos": rel["contagens"].get("aviso"),
            "professores": rel["contagens"].get("professor"),
            "alocacoes": rel["contagens"].get("alocacao"),
            "disciplinas": rel["contagens"].get("disciplina"),
            "horario_alocacao": rel["contagens"].get("horario_alocacao"),
            "configuracao_academica": rel["contagens"].get("configuracao_academica"),
        }
    finally:
        con.close()

    try:
        with engine_sqlite_leitura(original) as eng:
            with eng.connect() as bind:
                rel["integridade"] = auditar_integridade(bind)
    except Exception as exc:
        rel["integridade"] = {"erro": str(exc), "incompativeis": []}

    if rel["faltando_essenciais"]:
        rel["inconsistencias"].append(
            "Tabelas essenciais ausentes: %s." % ", ".join(rel["faltando_essenciais"])
        )
    if rel["alembic_version"] is None:
        rel["inconsistencias"].append(
            "alembic_version ausente: fonte não está no fluxo Alembic."
        )
    integ = rel.get("integridade") or {}
    if integ.get("incompativeis"):
        rel["inconsistencias"].append(
            "Duplicidades incompatíveis com UNIQUE moderno: %s grupo(s)."
            % len(integ["incompativeis"])
        )
        rel["integridade_texto"] = fmt_int(integ)
    return rel


def formatar_auditoria_fonte(rel):
    orig = rel.get("original") or {}
    linhas = [
        "Auditoria da fonte SQLite (somente leitura; nada foi corrigido).",
        "Original: %s" % orig.get("caminho"),
        "SHA-256 original: %s" % orig.get("sha256"),
        "Tamanho original: %s" % orig.get("size"),
    ]
    copia = rel.get("copia")
    if copia:
        linhas.append("Cópia: %s" % copia.get("caminho"))
        linhas.append("SHA-256 cópia: %s" % copia.get("sha256"))
        linhas.append("Tamanho cópia: %s" % copia.get("size"))
        if orig.get("sha256") and copia.get("sha256"):
            iguais = orig["sha256"] == copia["sha256"] and orig["size"] == copia["size"]
            linhas.append("Original e cópia idênticos: %s" % ("sim" if iguais else "nao"))
    linhas.append("Tabelas: %s" % ", ".join(rel.get("tabelas") or []))
    linhas.append("Alembic: %s" % (rel.get("alembic_version") or "(ausente)"))
    if rel.get("faltando_essenciais"):
        linhas.append("Faltando (modelo atual): %s" % ", ".join(rel["faltando_essenciais"]))
    if rel.get("extras"):
        linhas.append("Extras: %s" % ", ".join(rel["extras"]))
    linhas.append("Contagens:")
    for t, n in sorted((rel.get("contagens") or {}).items()):
        ids = (rel.get("ids") or {}).get(t) or {}
        extra = ""
        if ids:
            extra = " min_id=%s max_id=%s" % (ids.get("min"), ids.get("max"))
        linhas.append("  %s: %s%s" % (t, n, extra))
    linhas.append("Contagens nomeadas:")
    for k, v in (rel.get("contagens_nomeadas") or {}).items():
        linhas.append("  %s: %s" % (k, v if v is not None else "(tabela ausente)"))
    linhas.append("NULLs em FKs críticas:")
    for k, v in sorted((rel.get("nulos_fk") or {}).items()):
        linhas.append("  %s: %s" % (k, v if v is not None else "(tabela ausente)"))
    if rel.get("integridade_texto"):
        linhas.append(rel["integridade_texto"])
    else:
        integ = rel.get("integridade") or {}
        n = len(integ.get("incompativeis") or [])
        linhas.append("Duplicidades incompatíveis: %s." % n)
    if rel.get("inconsistencias"):
        linhas.append("Inconsistências (não corrigidas):")
        for item in rel["inconsistencias"]:
            linhas.append("  - %s" % item)
    else:
        linhas.append("Inconsistências estruturais: nenhuma listada.")
    return "\n".join(linhas)


def revisao_sqlite(path):
    nomes = listar_tabelas_sqlite(path)
    if TABELA_ALEMBIC not in nomes:
        return None
    con = abrir_sqlite_somente_leitura(path)
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _contar(conn, tabela):
    return int(conn.execute(text("SELECT COUNT(*) FROM %s" % _ident(tabela))).scalar() or 0)


def destino_tem_dados(conn, tabelas=None):
    tabelas = tabelas or TABELAS_ESSENCIAIS
    insp = inspect(conn)
    existentes = set(insp.get_table_names())
    ocupadas = []
    for t in tabelas:
        if t not in existentes:
            continue
        n = _contar(conn, t)
        if n:
            ocupadas.append((t, n))
    return ocupadas


def limpar_destino_ensaio(conn):
    """TRUNCATE/DELETE só após validação de destino. Não toca alembic_version."""
    dialect = conn.dialect.name
    insp = inspect(conn)
    nomes = [
        n for n in insp.get_table_names()
        if n != TABELA_ALEMBIC and not n.startswith(TABELAS_INTERNAS_PREFIXO)
    ]
    if not nomes:
        return
    if dialect == "postgresql":
        lista = ", ".join(_ident(n) for n in nomes)
        conn.execute(text("TRUNCATE %s RESTART IDENTITY CASCADE" % lista))
        return
    if dialect == "sqlite":
        for n in nomes:
            conn.execute(text("DELETE FROM %s" % _ident(n)))
        return
    raise DestinoInseguro("Limpeza recusada para dialect %s." % dialect)


def aplicar_migrations_destino(uri, confirmar=True, permitir_sqlite_teste=False):
    """Alembic upgrade até o head. Nunca db.create_all()."""
    if uri_e_sqlite(uri):
        if not permitir_sqlite_teste:
            raise DestinoInseguro("CLI de ensaio exige destino PostgreSQL.")
        path = caminho_sqlite_da_uri(uri)
        if path is not None:
            recusar_se_original(path)
    else:
        exigir_destino_autorizado(uri, confirmar=confirmar)
    from flask_migrate import upgrade
    from src.migracoes import revisao_atual, revisao_head
    with app_temporaria(uri, env="development", demo=False, preparar_schema=False) as app:
        with app.app_context():
            from src import db
            verificar_engine_nao_e_original(db.engine)
            upgrade()
            atual = revisao_atual()
            head = revisao_head()
            if atual != head:
                raise MigracaoAbortada(
                    "Alembic no destino ficou em %s; esperado %s." % (atual, head)
                )
            insp = inspect(db.engine)
            nomes = set(insp.get_table_names())
            faltando = [t for t in TABELAS_ESSENCIAIS if t not in nomes]
            if faltando:
                raise MigracaoAbortada(
                    "Após upgrade, tabelas ausentes no destino: %s." % ", ".join(faltando)
                )
            from src.integridade import INDICES_INTEGRIDADE
            nomes_idx = set()
            for t in (
                "registro_nota", "registro_falta", "registro_frequencia",
                "relatorio_descritivo", "historico_anual",
            ):
                for i in insp.get_indexes(t):
                    if i.get("name"):
                        nomes_idx.add(i["name"])
            faltando_idx = [n for n in INDICES_INTEGRIDADE if n not in nomes_idx]
            return {
                "alembic_version": atual,
                "head": head,
                "tabelas_ok": not faltando,
                "indices_ok": not faltando_idx,
                "indices_faltando": faltando_idx,
            }


def proximo_setval(max_id):
    """Retorna (valor, is_called) para setval.

    Tabela vazia: próximo ID = 1 (setval 1, false).
    Com IDs: próximo = max+1 (setval max, true).
    """
    if max_id is None:
        return 1, False
    return int(max_id), True


def ajustar_sequences(conn, tabelas=None):
    """Ajusta sequences PostgreSQL das tabelas com coluna id.

    Tabelas sem id (ex.: tentativa_login, PK textual) são ignoradas.
    """
    if conn.dialect.name != "postgresql":
        return []
    tabelas = tabelas or TABELAS_ESSENCIAIS
    ajustes = []
    insp = inspect(conn)
    for nome in tabelas:
        _ident(nome)
        if not insp.has_table(nome):
            continue
        colunas = {c["name"] for c in insp.get_columns(nome)}
        if "id" not in colunas:
            continue
        seq = conn.execute(
            text("SELECT pg_get_serial_sequence(:t, 'id')"),
            {"t": nome},
        ).scalar()
        if not seq:
            continue
        max_id = conn.execute(text("SELECT MAX(id) FROM %s" % _ident(nome))).scalar()
        valor, is_called = proximo_setval(max_id)
        conn.execute(
            text("SELECT setval(:s, :v, :c)"),
            {"s": seq, "v": valor, "c": is_called},
        )
        ajustes.append({
            "tabela": nome,
            "sequence": seq,
            "max_id": max_id,
            "setval": valor,
            "is_called": is_called,
        })
    return ajustes


def _tabela_metadata(metadata, nome):
    if nome not in metadata.tables:
        raise MigracaoAbortada("Tabela %s ausente no metadata." % nome)
    return metadata.tables[nome]


def _colunas_fonte(conn, nome):
    insp = inspect(conn)
    if not insp.has_table(nome):
        return []
    return [c["name"] for c in insp.get_columns(nome)]


def _ler_linhas(conn, table, colunas_fonte):
    cols = [c.name for c in table.columns if c.name in colunas_fonte]
    if not cols:
        return []
    lista = ", ".join(_ident(c) for c in cols)
    pks = [c.name for c in table.primary_key.columns if c.name in cols]
    ordem = (" ORDER BY " + ", ".join(_ident(c) for c in pks)) if pks else ""
    sql = text("SELECT %s FROM %s%s" % (lista, _ident(table.name), ordem))
    return [dict(row) for row in conn.execute(sql).mappings()]


def _inserir_linhas(conn, table, linhas):
    if not linhas:
        return 0
    from sqlalchemy import insert
    total = 0
    for i in range(0, len(linhas), TAMANHO_LOTE):
        lote = linhas[i:i + TAMANHO_LOTE]
        conn.execute(insert(table), lote)
        total += len(lote)
    return total


def importar_dados(
    fonte_sqlite,
    destino_uri,
    *,
    confirmar=False,
    limpar_destino=False,
    permitir_destino_sqlite_teste=False,
    exigir_head_fonte=True,
):
    """Copia dados da CÓPIA SQLite para o destino numa única transação.

    Não copia alembic_version nem tabelas internas do SQLite.
    Não executa regras de negócio, bootstrap nem recálculo acadêmico.
    """
    fonte = recusar_se_original(fonte_sqlite)
    if not fonte.is_file():
        raise FileNotFoundError("Fonte SQLite não encontrada: %s" % fonte)

    if uri_e_sqlite(destino_uri):
        if not permitir_destino_sqlite_teste:
            raise DestinoInseguro(
                "A importação do ensaio exige destino PostgreSQL. "
                "Destino SQLite só é permitido em testes isolados."
            )
        dest_path = caminho_sqlite_da_uri(destino_uri)
        if dest_path is not None:
            recusar_se_original(dest_path)
            if e_mesmo_arquivo(dest_path, fonte):
                raise DestinoInseguro("Fonte e destino não podem ser o mesmo arquivo.")
    else:
        exigir_destino_autorizado(destino_uri, confirmar=confirmar)

    if exigir_head_fonte:
        from src.migracoes import REVISION_HEAD
        rev = revisao_sqlite(fonte)
        if rev != REVISION_HEAD:
            raise MigracaoAbortada(
                "A cópia SQLite não está no head Alembic (atual=%s, head=%s). "
                "Não altere o original. Na CÓPIA execute: "
                "flask educamais alinhar-copia-ensaio --copia <copia> --confirmar. "
                "Nenhum dado foi importado."
                % (rev, REVISION_HEAD)
            )

    metadata = _carregar_metadata()
    plano = ordem_importacao(mapear_schema(metadata))
    if plano["ciclos"]:
        raise MigracaoAbortada(
            "Ciclo de FK impede importação segura: %s. "
            "Integridade referencial não será desligada."
            % ", ".join(plano["ciclos"])
        )

    rel = {
        "fonte": str(fonte),
        "destino_mascarado": mascarar_uri_banco(destino_uri) if not uri_e_sqlite(destino_uri) else destino_uri,
        "ordem": plano["ordem"],
        "copiadas": {},
        "ignoradas_fonte": [],
        "sequences": [],
        "tabela_falhou": None,
    }

    with engine_sqlite_leitura(fonte) as src_engine, engine_destino(destino_uri) as dest_engine:
        verificar_engine_nao_e_original(src_engine)
        verificar_engine_nao_e_original(dest_engine)
        try:
            with dest_engine.begin() as dconn:
                ocupadas = destino_tem_dados(dconn)
                if ocupadas and not limpar_destino:
                    raise MigracaoAbortada(
                        "Destino já possui dados (%s). Recusa para não duplicar. "
                        "Não há upsert. Use um banco vazio ou --limpar-destino "
                        "somente em educamais_migracao_test."
                        % ", ".join("%s=%s" % par for par in ocupadas)
                    )
                if ocupadas and limpar_destino:
                    if not permitir_destino_sqlite_teste:
                        exigir_destino_autorizado(destino_uri, confirmar=True)
                    limpar_destino_ensaio(dconn)
                    ainda = destino_tem_dados(dconn)
                    if ainda:
                        raise MigracaoAbortada(
                            "Destino ainda possui dados após limpeza: %s." % ainda
                        )
                insp_src = inspect(src_engine)
                nomes_src = set(insp_src.get_table_names())
                rel["ignoradas_fonte"] = tabelas_ignoradas_sqlite(sorted(nomes_src))
                with src_engine.connect() as sconn:
                    for nome in plano["ordem"]:
                        rel["tabela_falhou"] = nome
                        table = _tabela_metadata(metadata, nome)
                        if nome not in nomes_src:
                            rel["copiadas"][nome] = 0
                            continue
                        col_src = set(_colunas_fonte(sconn, nome))
                        obrigatorias = [
                            c.name for c in table.columns
                            if not c.nullable and c.server_default is None and c.default is None
                        ]
                        faltando_obrig = [c for c in obrigatorias if c not in col_src]
                        if faltando_obrig:
                            raise MigracaoAbortada(
                                "Fonte sem colunas obrigatórias em %s: %s."
                                % (nome, ", ".join(faltando_obrig))
                            )
                        brutas = _ler_linhas(sconn, table, col_src)
                        linhas = [converter_linha(r, table) for r in brutas]
                        _inserir_linhas(dconn, table, linhas)
                        rel["copiadas"][nome] = len(linhas)
                rel["tabela_falhou"] = "<sequences>"
                rel["sequences"] = ajustar_sequences(dconn, plano["ordem"])
                rel["tabela_falhou"] = None
        except Exception as exc:
            uri_para_mascara = destino_uri
            msg = mascarar_excecao(exc, uri_para_mascara)
            etapa = rel.get("tabela_falhou") or "?"
            raise MigracaoAbortada(
                "Falha na etapa %s. Transação revertida. Fonte não foi modificada. %s"
                % (etapa, msg)
            ) from exc
    return rel


def _linhas_ordenadas(conn, table):
    pks = [c.name for c in table.primary_key.columns]
    cols = [c.name for c in table.columns]
    ordem = ", ".join(_ident(c) for c in (pks or cols))
    lista = ", ".join(_ident(c) for c in cols)
    sql = text("SELECT %s FROM %s ORDER BY %s" % (lista, _ident(table.name), ordem))
    return [dict(row) for row in conn.execute(sql).mappings()], cols, pks


def orfaos_fk(conn, schema=None):
    schema = schema if schema is not None else mapear_schema()
    insp = inspect(conn)
    nomes = set(insp.get_table_names())
    achados = []
    for tname, info in schema["tabelas"].items():
        if tname not in nomes:
            continue
        for fk in info["fks"]:
            parent = fk["ref_tabela"]
            if parent not in nomes:
                continue
            sql = text(
                "SELECT COUNT(*) FROM %s c LEFT JOIN %s p ON c.%s = p.%s "
                "WHERE c.%s IS NOT NULL AND p.%s IS NULL"
                % (
                    _ident(tname), _ident(parent),
                    _ident(fk["coluna"]), _ident(fk["ref_coluna"]),
                    _ident(fk["coluna"]), _ident(fk["ref_coluna"]),
                )
            )
            n = int(conn.execute(sql).scalar() or 0)
            if n:
                achados.append({
                    "tabela": tname,
                    "coluna": fk["coluna"],
                    "ref": "%s.%s" % (parent, fk["ref_coluna"]),
                    "n": n,
                })
    return achados


def _ids_tabela(conn, nome):
    insp = inspect(conn)
    if not insp.has_table(nome):
        return set()
    cols = {c["name"] for c in insp.get_columns(nome)}
    if "id" not in cols:
        return set()
    return {
        row[0]
        for row in conn.execute(text("SELECT id FROM %s" % _ident(nome)))
    }


def validar_equivalencia(fonte_sqlite, destino_uri, *, permitir_destino_sqlite_teste=False):
    fonte = Path(fonte_sqlite).resolve()
    if not fonte.is_file():
        raise FileNotFoundError(str(fonte))
    if uri_e_sqlite(destino_uri):
        if not permitir_destino_sqlite_teste:
            raise DestinoInseguro("Validação de ensaio exige destino PostgreSQL.")
        dest_path = caminho_sqlite_da_uri(destino_uri)
        if dest_path is not None:
            recusar_se_original(dest_path)
    else:
        diag = validar_destino_postgresql(destino_uri, confirmar=False)
        if diag["recusas"]:
            raise DestinoInseguro(formatar_diagnostico_destino(diag))

    metadata = _carregar_metadata()
    schema = mapear_schema(metadata)
    plano = ordem_importacao(schema)
    diffs = []
    contagens = {}
    hashes = {}
    with engine_sqlite_leitura(fonte) as src_engine, engine_destino(destino_uri) as dest_engine:
        verificar_engine_nao_e_original(src_engine)
        with src_engine.connect() as sconn, dest_engine.connect() as dconn:
            orfaos_src = orfaos_fk(sconn, schema)
            orfaos_dst = orfaos_fk(dconn, schema)
            for nome in plano["ordem"]:
                table = metadata.tables[nome]
                insp_s, insp_d = inspect(sconn), inspect(dconn)
                tem_s, tem_d = insp_s.has_table(nome), insp_d.has_table(nome)
                ns = _contar(sconn, nome) if tem_s else 0
                nd = _contar(dconn, nome) if tem_d else 0
                contagens[nome] = {"fonte": ns, "destino": nd}
                if ns != nd:
                    diffs.append({
                        "tabela": nome, "pk": None, "campo": "<contagem>",
                        "detalhe": "fonte=%s destino=%s" % (ns, nd),
                    })
                ids_s = _ids_tabela(sconn, nome) if tem_s else set()
                ids_d = _ids_tabela(dconn, nome) if tem_d else set()
                if ids_s != ids_d:
                    so_s = sorted(ids_s - ids_d)[:20]
                    so_d = sorted(ids_d - ids_s)[:20]
                    diffs.append({
                        "tabela": nome, "pk": None, "campo": "<ids>",
                        "detalhe": "somente_fonte=%s somente_destino=%s" % (so_s, so_d),
                    })
                if not tem_s or not tem_d:
                    continue
                linhas_s, cols, pks = _linhas_ordenadas(sconn, table)
                linhas_d, _, _ = _linhas_ordenadas(dconn, table)
                kinds = {c.name: _kind_coluna(c) for c in table.columns}
                hashes[nome] = {
                    "fonte": hash_tabela(linhas_s, cols, kinds),
                    "destino": hash_tabela(linhas_d, cols, kinds),
                }
                if hashes[nome]["fonte"] != hashes[nome]["destino"]:
                    mapa_d = {
                        tuple(r.get(pk) for pk in pks): r for r in linhas_d
                    } if pks else {}
                    for r in linhas_s:
                        chave = tuple(r.get(pk) for pk in pks) if pks else None
                        rd = mapa_d.get(chave)
                        if rd is None:
                            diffs.append({
                                "tabela": nome, "pk": chave, "campo": "<linha>",
                                "detalhe": "ausente no destino",
                            })
                            continue
                        for col in cols:
                            vs = valor_canonico(r.get(col), kinds[col])
                            vd = valor_canonico(rd.get(col), kinds[col])
                            if vs != vd:
                                campo = col
                                detalhe = "valores divergem"
                                if col == "senha_hash":
                                    detalhe = "hash de senha diverge (valores omitidos)"
                                diffs.append({
                                    "tabela": nome,
                                    "pk": chave[0] if chave and len(chave) == 1 else chave,
                                    "campo": campo,
                                    "detalhe": detalhe,
                                })
            rel_acad = _validar_relacionamentos_academicos(sconn, dconn)
    ok = (
        not diffs
        and not orfaos_dst
        and all(h["fonte"] == h["destino"] for h in hashes.values())
    )
    return {
        "ok": ok,
        "contagens": contagens,
        "hashes": hashes,
        "diferencas": diffs,
        "orfaos_fonte": orfaos_src,
        "orfaos_destino": orfaos_dst,
        "relacionamentos": rel_acad,
    }


def _validar_relacionamentos_academicos(sconn, dconn):
    checks = []

    def _check(nome, sql):
        insp_s, insp_d = inspect(sconn), inspect(dconn)
        partes = sql.lower()
        if " matricula " in partes or partes.startswith("matricula"):
            if not insp_s.has_table("matricula") or not insp_d.has_table("matricula"):
                checks.append({"nome": nome, "ok": False, "detalhe": "tabela matricula ausente"})
                return
        ns = int(sconn.execute(text(sql)).scalar() or 0)
        nd = int(dconn.execute(text(sql)).scalar() or 0)
        checks.append({"nome": nome, "fonte": ns, "destino": nd, "ok": ns == nd})

    _check(
        "matricula_aluno_turma",
        "SELECT COUNT(*) FROM matricula m "
        "JOIN aluno a ON a.id = m.aluno_id JOIN turma t ON t.id = m.turma_id",
    )
    _check(
        "nota_matricula_disciplina",
        "SELECT COUNT(*) FROM registro_nota n "
        "LEFT JOIN matricula m ON m.id = n.matricula_id "
        "JOIN disciplina d ON d.id = n.disciplina_id "
        "WHERE n.matricula_id IS NULL OR m.id IS NOT NULL",
    )
    _check(
        "falta_matricula",
        "SELECT COUNT(*) FROM registro_falta f "
        "LEFT JOIN matricula m ON m.id = f.matricula_id "
        "WHERE f.matricula_id IS NULL OR m.id IS NOT NULL",
    )
    _check(
        "frequencia_matricula_aula",
        "SELECT COUNT(*) FROM registro_frequencia f "
        "LEFT JOIN matricula m ON m.id = f.matricula_id "
        "LEFT JOIN registro_aula a ON a.id = f.registro_aula_id "
        "WHERE (f.matricula_id IS NULL OR m.id IS NOT NULL) "
        "AND (f.registro_aula_id IS NULL OR a.id IS NOT NULL)",
    )
    _check(
        "parecer_matricula",
        "SELECT COUNT(*) FROM relatorio_descritivo r "
        "LEFT JOIN matricula m ON m.id = r.matricula_id "
        "WHERE r.matricula_id IS NULL OR m.id IS NOT NULL",
    )
    _check(
        "historico_matricula",
        "SELECT COUNT(*) FROM historico_anual h "
        "LEFT JOIN matricula m ON m.id = h.matricula_id "
        "WHERE h.matricula_id IS NULL OR m.id IS NOT NULL",
    )
    return checks


def formatar_validacao(rel):
    linhas = [
        "Validação pós-migração fonte x destino.",
        "Equivalência: %s" % ("OK" if rel.get("ok") else "DIVERGENTE"),
    ]
    for t, c in sorted((rel.get("contagens") or {}).items()):
        marca = "OK" if c["fonte"] == c["destino"] else "DIF"
        linhas.append("  [%s] %s fonte=%s destino=%s" % (marca, t, c["fonte"], c["destino"]))
    for o in rel.get("orfaos_destino") or []:
        linhas.append(
            "  FK órfã destino: %s.%s -> %s n=%s"
            % (o["tabela"], o["coluna"], o["ref"], o["n"])
        )
    for d in rel.get("diferencas") or []:
        linhas.append(
            "  DIF %s pk=%s campo=%s %s"
            % (d["tabela"], d.get("pk"), d.get("campo"), d.get("detalhe"))
        )
    for r in rel.get("relacionamentos") or []:
        linhas.append(
            "  REL %s ok=%s fonte=%s destino=%s"
            % (r["nome"], r.get("ok"), r.get("fonte"), r.get("destino"))
        )
    return "\n".join(linhas)


def auditar_uploads(sqlite_path=None, pasta=None, raiz=None):
    """Não move, não renomeia, não apaga arquivos."""
    pasta = Path(pasta or resolver_upload_folder(raiz)).resolve()
    arquivos = []
    if pasta.is_dir():
        for dirpath, dirnames, filenames in os.walk(pasta):
            dirnames.sort()
            for nome in sorted(filenames):
                p = Path(dirpath) / nome
                rel = str(p.relative_to(pasta)).replace("\\", "/")
                arquivos.append({"rel": rel, "size": p.stat().st_size})
    refs = []
    sqlite_path = Path(sqlite_path).resolve() if sqlite_path else None
    if sqlite_path and sqlite_path.is_file():
        con = abrir_sqlite_somente_leitura(sqlite_path)
        try:
            nomes = [
                r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            if "planejamento" in nomes:
                cols = [r[1] for r in con.execute("PRAGMA table_info(planejamento)")]
                tem_orig = "arquivo_original" in cols
                sql = "SELECT id, arquivo" + (
                    ", arquivo_original" if tem_orig else ""
                ) + " FROM planejamento WHERE arquivo IS NOT NULL AND arquivo != ''"
                for row in con.execute(sql):
                    refs.append({
                        "tabela": "planejamento",
                        "id": row[0],
                        "arquivo": row[1],
                        "arquivo_original": row[2] if tem_orig else None,
                    })
        finally:
            con.close()
    nomes_fisicos = {a["rel"] for a in arquivos}
    so_banco = [r for r in refs if r["arquivo"] not in nomes_fisicos]
    so_disco = [
        a for a in arquivos
        if a["rel"] not in {r["arquivo"] for r in refs}
    ]
    return {
        "pasta": str(pasta),
        "existe": pasta.is_dir(),
        "n_arquivos": len(arquivos),
        "arquivos": arquivos,
        "referencias_banco": refs,
        "n_referencias": len(refs),
        "referencias_sem_arquivo": so_banco,
        "arquivos_sem_referencia": so_disco,
        "alterado": False,
        "cutover": (
            "Banco e uploads exigem estratégias coordenadas no cutover futuro. "
            "Este bloco não copia, não move e não apaga arquivos."
        ),
    }


def formatar_auditoria_uploads(rel):
    linhas = [
        "Auditoria de uploads (somente leitura; nada foi movido/renomeado/apagado).",
        "Pasta: %s" % rel.get("pasta"),
        "Existe: %s" % ("sim" if rel.get("existe") else "nao"),
        "Arquivos: %s" % rel.get("n_arquivos"),
        "Referências no banco (planejamento.arquivo): %s" % rel.get("n_referencias"),
        "Referências sem arquivo: %s" % len(rel.get("referencias_sem_arquivo") or []),
        "Arquivos sem referência: %s" % len(rel.get("arquivos_sem_referencia") or []),
        rel.get("cutover"),
    ]
    return "\n".join(linhas)


def alinhar_copia_ensaio(copia, confirmar=False, ate_head=True, raiz=None):
    """Alinha SOMENTE a cópia com o procedimento legado existente.

    Backup adicional da cópia antes. Não toca o original.
    Não faz stamp se o alinhamento falhar.
    """
    copia = recusar_se_original(copia, raiz)
    if not copia.is_file():
        raise FileNotFoundError(str(copia))
    before = auditar_fonte_sqlite(original=copia, raiz=raiz)
    rel = {
        "copia": str(copia),
        "antes": before,
        "executado": False,
        "backup": None,
        "depois": None,
        "alembic": None,
    }
    if not confirmar:
        rel["aviso"] = "Sem --confirmar: alinhamento não executado."
        return rel
    backup = copia.with_name(copia.stem + ".pre_alinhamento.sqlite")
    if backup.exists():
        backup.unlink()
    copiar_sqlite_arquivo(copia, backup)
    recusar_se_original(backup, raiz)
    rel["backup"] = str(backup)
    uri = uri_sqlite_arquivo(copia)
    from src.migracoes import alinhar_schema_legado, revisao_atual, revisao_head
    try:
        with app_temporaria(uri, env="development", demo=False, preparar_schema=False) as app:
            with app.app_context():
                from src import db
                verificar_engine_nao_e_original(db.engine, raiz)
                verificar_engine_e_o_arquivo(db.engine, copia)
                alinhar_schema_legado()
                if ate_head:
                    from flask_migrate import upgrade
                    upgrade()
                rel["alembic"] = {
                    "atual": revisao_atual(),
                    "head": revisao_head(),
                }
    except Exception:
        rel["falhou"] = True
        raise
    rel["executado"] = True
    rel["depois"] = auditar_fonte_sqlite(original=copia, raiz=raiz)
    rel["contagens_antes"] = before.get("contagens")
    rel["contagens_depois"] = rel["depois"].get("contagens")
    return rel


def ensaiar_migracao(
    fonte,
    destino_uri,
    *,
    confirmar=False,
    limpar_destino=False,
    preparar_schema=True,
    permitir_destino_sqlite_teste=False,
    exigir_head_fonte=True,
):
    """Pipeline de ensaio: validar destino, schema Alembic, importar, validar."""
    diag = None
    if not uri_e_sqlite(destino_uri):
        diag = validar_destino_postgresql(destino_uri, confirmar=confirmar)
        if not confirmar:
            return {"diagnostico": diag, "executado": False}
        if diag["recusas"]:
            raise DestinoInseguro(formatar_diagnostico_destino(diag))
    elif not permitir_destino_sqlite_teste:
        raise DestinoInseguro("Ensaio CLI exige destino PostgreSQL.")
    elif not confirmar:
        return {"diagnostico": {"avisos": ["Sem --confirmar."]}, "executado": False}

    recusar_se_original(fonte)
    resultado = {"diagnostico": diag, "executado": True}
    if preparar_schema:
        resultado["schema"] = aplicar_migrations_destino(
            destino_uri,
            confirmar=True,
            permitir_sqlite_teste=permitir_destino_sqlite_teste,
        )
    resultado["importacao"] = importar_dados(
        fonte,
        destino_uri,
        confirmar=True,
        limpar_destino=limpar_destino,
        permitir_destino_sqlite_teste=permitir_destino_sqlite_teste,
        exigir_head_fonte=exigir_head_fonte,
    )
    resultado["validacao"] = validar_equivalencia(
        fonte,
        destino_uri,
        permitir_destino_sqlite_teste=permitir_destino_sqlite_teste,
    )
    return resultado
