"""Armazenamento privado e validação de anexos de planejamento."""
import os
import uuid
import zipfile

from werkzeug.utils import secure_filename

from src.config import EXTENSOES_UPLOAD_PERMITIDAS, MAX_UPLOAD_BYTES
from src.http import err


ASSINATURA_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ASSINATURA_PDF = b"%PDF"
ASSINATURA_ZIP = b"PK"

MIME_POR_EXTENSAO = {
    "pdf": frozenset({"application/pdf"}),
    "doc": frozenset({"application/msword", "application/vnd.ms-word"}),
    "xls": frozenset({"application/vnd.ms-excel", "application/msexcel"}),
    "docx": frozenset({
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }),
    "xlsx": frozenset({
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }),
    "odt": frozenset({"application/vnd.oasis.opendocument.text"}),
    "ods": frozenset({"application/vnd.oasis.opendocument.spreadsheet"}),
}

MIME_GENERICOS = frozenset({
    "",
    "application/octet-stream",
    "binary/octet-stream",
})

EXTENSOES_PROIBIDAS = frozenset({
    "exe", "bat", "cmd", "com", "js", "html", "htm", "php", "py", "sh", "jar", "msi",
})

MENSAGEM_TIPO_INVALIDO = "Tipo de arquivo não permitido. Envie PDF, DOC, DOCX, XLS, XLSX, ODT ou ODS."
MENSAGEM_TAMANHO = "O arquivo excede o tamanho máximo permitido (10 MB)."
MENSAGEM_VAZIO = "O arquivo enviado está vazio."
MENSAGEM_ASSINATURA = "O conteúdo do arquivo não corresponde à extensão informada."


def extensao_de(filename):
    if not filename or not isinstance(filename, str):
        return None
    base = os.path.basename(filename.replace("\\", "/"))
    if "." not in base:
        return None
    return base.rsplit(".", 1)[1].lower()


def extensao_permitida(filename):
    ext = extensao_de(filename)
    return ext in EXTENSOES_UPLOAD_PERMITIDAS


def nome_original_sanitizado(filename, ext):
    base = os.path.basename((filename or "").replace("\\", "/"))
    limpo = secure_filename(base)
    if not limpo:
        return "anexo.%s" % ext
    if "." not in limpo:
        return "%s.%s" % (limpo, ext)
    return limpo


def nome_fisico_unico(ext):
    return "%s.%s" % (uuid.uuid4(), ext)


def normalizar_mime(content_type):
    if not content_type:
        return ""
    return str(content_type).split(";", 1)[0].strip().lower()


def mime_compativel(ext, content_type):
    mime = normalizar_mime(content_type)
    if mime in MIME_GENERICOS:
        return True
    permitidos = MIME_POR_EXTENSAO.get(ext, frozenset())
    return mime in permitidos


def _ler_cabecalho(stream, n=8):
    pos = stream.tell()
    try:
        dados = stream.read(n)
    finally:
        stream.seek(pos)
    return dados or b""


def _zip_interno_compativel(stream, ext):
    pos = stream.tell()
    try:
        with zipfile.ZipFile(stream) as zf:
            nomes = [n.replace("\\", "/").lower() for n in zf.namelist()]
            if ext == "docx":
                return any(n.startswith("word/") for n in nomes)
            if ext == "xlsx":
                return any(n.startswith("xl/") for n in nomes)
            if ext in ("odt", "ods") and "mimetype" in zf.namelist():
                mt = zf.read("mimetype").decode("ascii", errors="ignore")
                if ext == "odt":
                    return "opendocument.text" in mt
                return "opendocument.spreadsheet" in mt
            return False
    except (zipfile.BadZipFile, OSError, UnicodeError, RuntimeError):
        return False
    finally:
        stream.seek(pos)


def assinatura_compativel(stream, ext):
    """Magic bytes leves. DOC/XLS compartilham OLE; DOCX/XLSX/ODT/ODS exigem ZIP válido."""
    cabeca = _ler_cabecalho(stream, 8)
    if ext == "pdf":
        return cabeca.startswith(ASSINATURA_PDF)
    if ext in ("doc", "xls"):
        return cabeca.startswith(ASSINATURA_OLE)
    if ext in ("docx", "xlsx", "odt", "ods"):
        if not cabeca.startswith(ASSINATURA_ZIP):
            return False
        return _zip_interno_compativel(stream, ext)
    return False


def tamanho_stream(stream):
    pos = stream.tell()
    try:
        stream.seek(0, os.SEEK_END)
        return stream.tell()
    finally:
        stream.seek(pos)


def caminho_dentro_da_pasta(pasta, nome_fisico):
    if not pasta or not nome_fisico:
        return None
    if os.path.basename(nome_fisico) != nome_fisico:
        return None
    if ".." in nome_fisico or "/" in nome_fisico or "\\" in nome_fisico:
        return None
    pasta_abs = os.path.abspath(pasta)
    destino = os.path.abspath(os.path.join(pasta_abs, nome_fisico))
    try:
        comum = os.path.commonpath([pasta_abs, destino])
    except ValueError:
        return None
    if comum != pasta_abs:
        return None
    return destino


def remover_silencioso(caminho):
    if not caminho:
        return
    try:
        if os.path.isfile(caminho):
            os.remove(caminho)
    except OSError:
        pass


def recusar_tipo():
    return err(MENSAGEM_TIPO_INVALIDO, 400)


def recusar_tamanho():
    return err(MENSAGEM_TAMANHO, 413)


def validar_storage(storage, limite=None):
    """Valida o FileStorage. Retorna (ext, nome_original) ou (None, resposta_erro)."""
    filename = getattr(storage, "filename", None) or ""
    ext = extensao_de(filename)
    if ext in EXTENSOES_PROIBIDAS or ext not in EXTENSOES_UPLOAD_PERMITIDAS:
        return None, recusar_tipo()
    if not mime_compativel(ext, getattr(storage, "mimetype", None) or getattr(storage, "content_type", None)):
        return None, recusar_tipo()
    stream = storage.stream
    try:
        tamanho = tamanho_stream(stream)
    except (OSError, AttributeError):
        return None, err("Não foi possível ler o arquivo enviado.", 400)
    maximo = limite if limite is not None else MAX_UPLOAD_BYTES
    if tamanho <= 0:
        return None, err(MENSAGEM_VAZIO, 400)
    if tamanho > maximo:
        return None, recusar_tamanho()
    if not assinatura_compativel(stream, ext):
        return None, err(MENSAGEM_ASSINATURA, 400)
    original = nome_original_sanitizado(filename, ext)
    return {"ext": ext, "original": original}, None


def salvar_anexo(storage, pasta, limite=None):
    """Grava com nome físico UUID. Retorna (info, None) ou (None, resposta_erro)."""
    meta, falha = validar_storage(storage, limite=limite)
    if falha:
        return None, falha
    os.makedirs(pasta, exist_ok=True)
    fisico = nome_fisico_unico(meta["ext"])
    destino = caminho_dentro_da_pasta(pasta, fisico)
    if not destino:
        return None, err("Não foi possível armazenar o arquivo.", 400)
    try:
        storage.stream.seek(0)
        storage.save(destino)
    except OSError:
        remover_silencioso(destino)
        return None, err("Não foi possível armazenar o arquivo.", 400)
    if not os.path.isfile(destino):
        return None, err("Não foi possível armazenar o arquivo.", 400)
    return {
        "fisico": fisico,
        "original": meta["original"],
        "caminho": destino,
    }, None


def localizar_arquivo(pasta, nome_fisico):
    destino = caminho_dentro_da_pasta(pasta, nome_fisico)
    if not destino or not os.path.isfile(destino):
        return None
    return destino
