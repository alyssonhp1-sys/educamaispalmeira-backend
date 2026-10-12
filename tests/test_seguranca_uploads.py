import io
import os
import shutil
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from src.config import MAX_UPLOAD_BYTES, resolver_upload_folder
from src.uploads import (
    ASSINATURA_OLE,
    caminho_dentro_da_pasta,
    extensao_de,
    nome_fisico_unico,
    nome_original_sanitizado,
)
from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


def _pdf():
    return b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _ole():
    return ASSINATURA_OLE + b"\x00" * 64


def _docx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types></Types>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buf.getvalue()


def _xlsx():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types></Types>")
        zf.writestr("xl/workbook.xml", "<workbook/>")
    return buf.getvalue()


class HelpersUploadTest(unittest.TestCase):
    def test_extensao_ignora_caminho(self):
        self.assertEqual(extensao_de("../../etc/passwd.pdf"), "pdf")
        self.assertEqual(extensao_de(r"..\..\Windows\arquivo.PDF"), "pdf")
        self.assertEqual(extensao_de("C:\\Windows\\notepad.exe"), "exe")
        self.assertIsNone(extensao_de("../../etc/passwd"))
        self.assertIsNone(extensao_de("C:\\Windows\\nopath"))

    def test_nome_original_nao_vira_caminho(self):
        nome = nome_original_sanitizado("../../etc/passwd.pdf", "pdf")
        self.assertNotIn("..", nome)
        self.assertNotIn("/", nome)
        self.assertNotIn("\\", nome)
        self.assertTrue(nome.endswith(".pdf"))

    def test_nome_fisico_e_uuid(self):
        a = nome_fisico_unico("pdf")
        b = nome_fisico_unico("pdf")
        self.assertNotEqual(a, b)
        uuid.UUID(a.rsplit(".", 1)[0])
        self.assertTrue(a.endswith(".pdf"))

    def test_caminho_rejeita_traversal(self):
        pasta = tempfile.mkdtemp()
        try:
            self.assertIsNone(caminho_dentro_da_pasta(pasta, "../segredo.pdf"))
            self.assertIsNone(caminho_dentro_da_pasta(pasta, r"..\segredo.pdf"))
            self.assertIsNone(caminho_dentro_da_pasta(pasta, "/etc/passwd"))
            fisico = "%s.pdf" % uuid.uuid4()
            destino = caminho_dentro_da_pasta(pasta, fisico)
            self.assertIsNotNone(destino)
            self.assertTrue(destino.startswith(os.path.abspath(pasta)))
        finally:
            shutil.rmtree(pasta, ignore_errors=True)

    def test_pasta_padrao_nao_e_static(self):
        antigo = os.environ.pop("EDUCAMAIS_UPLOAD_FOLDER", None)
        try:
            pasta = resolver_upload_folder().replace("\\", "/")
            self.assertTrue(pasta.endswith("instance/uploads"))
            self.assertNotIn("/static/", pasta)
        finally:
            if antigo is not None:
                os.environ["EDUCAMAIS_UPLOAD_FOLDER"] = antigo

    def test_codigo_sem_caminho_windows_hardcoded(self):
        raiz = Path(__file__).resolve().parents[1] / "src"
        for path in raiz.rglob("*.py"):
            texto = path.read_text(encoding="utf-8")
            self.assertNotIn("C:\\Users\\", texto, path)
            self.assertNotIn("C:/Users/", texto, path)


class SegurancaUploadsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cls._db_path = tmp.name
        cls._upload_dir = tempfile.mkdtemp(prefix="educamais_uploads_")
        cls._old_upload = os.environ.get("EDUCAMAIS_UPLOAD_FOLDER")
        os.environ["EDUCAMAIS_UPLOAD_FOLDER"] = cls._upload_dir
        from src.config import Config
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + Path(cls._db_path).resolve().as_posix()
        from src import create_app
        cls.app = create_app()
        cls.app.testing = True
        cls._montar()

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        if cls._old_upload is None:
            os.environ.pop("EDUCAMAIS_UPLOAD_FOLDER", None)
        else:
            os.environ["EDUCAMAIS_UPLOAD_FOLDER"] = cls._old_upload
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass
        shutil.rmtree(cls._upload_dir, ignore_errors=True)

    @classmethod
    def _login_client(cls, client, login, senha):
        resp = client.post("/api/auth/login", json={"login": login, "senha": senha})
        if resp.status_code != 200:
            raise RuntimeError("login %s: %s" % (login, _json(resp)[1]))

    @classmethod
    def _montar(cls):
        client = cls.app.test_client()
        cls._login_client(client, "admin", SENHA_DEMO_ADMIN)
        from src.models import Disciplina
        with cls.app.app_context():
            cls.disc_id = Disciplina.query.filter_by(nome="Matemática").first().id
            cls.disc_port = Disciplina.query.filter_by(nome="Português").first().id

        cls.inep_a = "93000001"
        cls.inep_b = "93000002"
        cls.cpf_prof_a = "93011111111"
        cls.cpf_prof_b = "93022222222"
        cls.cpf_prof_a2 = "93033333333"
        cls.login_coord_a = "coord930a"
        cls.login_coord_b = "coord930b"
        temps = {}
        for nome, inep in (("Escola Upload Norte", cls.inep_a), ("Escola Upload Sul", cls.inep_b)):
            resp = client.post("/api/escolas", json={
                "nome": nome, "inep": inep,
                "oferta_infantil": True, "oferta_fundamental": True,
            })
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[inep] = senha_temporaria(resp)

        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_a = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_a)
        cls.escola_b = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_b)

        for login, nome, escola_id in (
            (cls.login_coord_a, "Coord Upload A", cls.escola_a),
            (cls.login_coord_b, "Coord Upload B", cls.escola_b),
        ):
            resp = client.post(f"/api/escolas/{escola_id}/coordenadores", json={"login": login, "nome": nome})
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[login] = senha_temporaria(resp)

        for cpf, nome, escola_id in (
            (cls.cpf_prof_a, "Prof Upload Alfa", cls.escola_a),
            (cls.cpf_prof_a2, "Prof Upload Colega", cls.escola_a),
            (cls.cpf_prof_b, "Prof Upload Beta", cls.escola_b),
        ):
            resp = client.post("/api/professores", json={"nome": nome, "cpf": cpf, "escola_id": escola_id})
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[cpf] = senha_temporaria(resp)
        for login, temp in temps.items():
            concluir_primeiro_acesso(client, login, temp)

        cls._login_client(client, cls.inep_a, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano Upload A", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_a = dash["turmas"][0]["id"]
        cls.prof_a_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_a)
        cls.prof_a2_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_a2)
        resp = client.post("/api/alocacoes", json={
            "professor_id": cls.prof_a_id, "turma_id": cls.turma_a, "disciplina_id": cls.disc_id,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        resp = client.post("/api/alocacoes", json={
            "professor_id": cls.prof_a2_id, "turma_id": cls.turma_a, "disciplina_id": cls.disc_port,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])

        cls._login_client(client, cls.inep_b, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "5º Ano Upload B", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_b = dash["turmas"][0]["id"]
        cls.prof_b_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_b)
        resp = client.post("/api/alocacoes", json={
            "professor_id": cls.prof_b_id, "turma_id": cls.turma_b, "disciplina_id": cls.disc_id,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _enviar(self, turma_id, conteudo, filename, bruto, content_type=None):
        if content_type is None:
            arquivo = (io.BytesIO(bruto), filename)
        else:
            arquivo = (io.BytesIO(bruto), filename, content_type)
        return self.client.post("/api/planejamento/enviar", data={
            "turma_id": str(turma_id),
            "conteudo": conteudo,
            "arquivo": arquivo,
        })

    def _ultimo_plano(self, conteudo):
        with self.app.app_context():
            from src.models import Planejamento
            return Planejamento.query.filter_by(conteudo=conteudo).order_by(Planejamento.id.desc()).first()

    def _pasta(self):
        return os.path.abspath(self.app.config["UPLOAD_FOLDER"])

    def _assert_sem_caminho(self, resp):
        corpo = resp.get_data()
        pasta = self._pasta().encode("utf-8")
        self.assertNotIn(pasta, corpo)
        self.assertNotIn(b"Traceback", corpo)
        texto = ""
        try:
            texto = corpo.decode("utf-8", errors="ignore")
        except Exception:
            pass
        self.assertNotIn("src\\static", texto)
        self.assertNotIn("src/static", texto)

    def test_01_upload_permitido_funciona(self):
        self._login(self.cpf_prof_a)
        resp = self._enviar(self.turma_a, "Plano PDF ok", "plano.pdf", _pdf(), "application/pdf")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        plano = self._ultimo_plano("Plano PDF ok")
        self.assertIsNotNone(plano)
        self.assertTrue(plano.arquivo)
        self.assertEqual(plano.arquivo_original, "plano.pdf")
        caminho = os.path.join(self._pasta(), plano.arquivo)
        self.assertTrue(os.path.isfile(caminho))

    def test_02_arquivo_salvo_fora_de_static(self):
        self._login(self.cpf_prof_a)
        resp = self._enviar(self.turma_a, "Plano fora static", "aula.pdf", _pdf())
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        plano = self._ultimo_plano("Plano fora static")
        pasta = self._pasta().replace("\\", "/")
        self.assertNotIn("/static/", pasta)
        static_dir = str(Path(__file__).resolve().parents[1] / "src" / "static" / "uploads")
        self.assertFalse(os.path.isfile(os.path.join(static_dir, plano.arquivo)))
        self.assertTrue(os.path.isfile(os.path.join(self._pasta(), plano.arquivo)))

    def test_03_nome_fisico_unico_e_nao_colide(self):
        self._login(self.cpf_prof_a)
        self.assertEqual(self._enviar(self.turma_a, "Colisao um", "planejamento.pdf", _pdf()).status_code, 200)
        self.assertEqual(self._enviar(self.turma_a, "Colisao dois", "planejamento.pdf", _pdf() + b"\n").status_code, 200)
        p1 = self._ultimo_plano("Colisao um")
        p2 = self._ultimo_plano("Colisao dois")
        self.assertNotEqual(p1.arquivo, p2.arquivo)
        uuid.UUID(p1.arquivo.rsplit(".", 1)[0])
        uuid.UUID(p2.arquivo.rsplit(".", 1)[0])
        c1 = Path(self._pasta()) / p1.arquivo
        c2 = Path(self._pasta()) / p2.arquivo
        self.assertTrue(c1.is_file())
        self.assertTrue(c2.is_file())
        self.assertNotEqual(c1.read_bytes(), c2.read_bytes())
        self.assertEqual(p1.arquivo_original, "planejamento.pdf")
        self.assertEqual(p2.arquivo_original, "planejamento.pdf")

    def test_04_path_traversal_nao_controla_caminho(self):
        self._login(self.cpf_prof_a)
        nomes = [
            "../../etc/passwd.pdf",
            r"..\..\Windows\win.ini.pdf",
            "/etc/passwd.pdf",
            "C:\\Windows\\system32\\plano.pdf",
        ]
        for i, nome in enumerate(nomes):
            conteudo = "Traversal %s" % i
            resp = self._enviar(self.turma_a, conteudo, nome, _pdf(), "application/pdf")
            self.assertEqual(resp.status_code, 200, (nome, _json(resp)[1]))
            plano = self._ultimo_plano(conteudo)
            self.assertNotIn("..", plano.arquivo)
            self.assertNotIn("/", plano.arquivo)
            self.assertNotIn("\\", plano.arquivo)
            self.assertEqual(os.path.basename(plano.arquivo), plano.arquivo)
            self.assertTrue(os.path.isfile(os.path.join(self._pasta(), plano.arquivo)))
            self.assertFalse(os.path.isfile(os.path.join(self._pasta(), "..", "passwd.pdf")))

    def test_05_path_traversal_sem_extensao_permitida_e_rejeitado(self):
        self._login(self.cpf_prof_a)
        for nome in ("../../etc/passwd", r"..\..\arquivo", "/etc/passwd", "C:\\Windows\\notepad.exe"):
            resp = self._enviar(self.turma_a, "Traversal rejeitado", nome, b"conteudo-invalido")
            self.assertEqual(resp.status_code, 400, nome)
            self.assertIsNone(self._ultimo_plano("Traversal rejeitado"))

    def test_06_extensao_proibida_recusada(self):
        self._login(self.cpf_prof_a)
        mz = b"MZ" + b"\x00" * 32
        for nome, bruto, mime in (
            ("malware.exe", mz, "application/x-msdownload"),
            ("script.bat", b"@echo off\n", "application/x-bat"),
            ("page.html", b"<html></html>", "text/html"),
            ("code.js", b"alert(1)", "application/javascript"),
            ("foto.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16, "image/png"),
        ):
            resp = self._enviar(self.turma_a, "Proibido %s" % nome, nome, bruto, mime)
            self.assertEqual(resp.status_code, 400, nome)
            self.assertIsNone(self._ultimo_plano("Proibido %s" % nome))

    def test_07_mime_incompativel_recusado(self):
        self._login(self.cpf_prof_a)
        resp = self._enviar(self.turma_a, "MIME ruim", "plano.pdf", _pdf(), "text/html")
        self.assertEqual(resp.status_code, 400)
        self.assertIsNone(self._ultimo_plano("MIME ruim"))
        resp = self._enviar(self.turma_a, "Assinatura ruim", "plano.pdf", b"<html>nao e pdf</html>", "application/pdf")
        self.assertEqual(resp.status_code, 400)
        self.assertIsNone(self._ultimo_plano("Assinatura ruim"))

    def test_08_arquivo_grande_retorna_413(self):
        self.assertEqual(self.app.config["MAX_CONTENT_LENGTH"], MAX_UPLOAD_BYTES)
        self.assertEqual(MAX_UPLOAD_BYTES, 10 * 1024 * 1024)
        antigo = self.app.config["MAX_CONTENT_LENGTH"]
        self.app.config["MAX_CONTENT_LENGTH"] = 2048
        try:
            self._login(self.cpf_prof_a)
            bruto = b"%PDF-1.4\n" + (b"A" * 4000)
            resp = self._enviar(self.turma_a, "Arquivo gigante", "grande.pdf", bruto, "application/pdf")
            self.assertEqual(resp.status_code, 413)
            self._assert_sem_caminho(resp)
            self.assertIsNone(self._ultimo_plano("Arquivo gigante"))
        finally:
            self.app.config["MAX_CONTENT_LENGTH"] = antigo

    def test_09_download_exige_autenticacao(self):
        self._login(self.cpf_prof_a)
        self.assertEqual(self._enviar(self.turma_a, "Plano auth", "auth.pdf", _pdf()).status_code, 200)
        plano = self._ultimo_plano("Plano auth")
        anon = self.app.test_client()
        resp = anon.get(f"/api/planejamentos/{plano.id}/arquivo")
        self.assertEqual(resp.status_code, 401)
        self._assert_sem_caminho(resp)

    def test_10_autorizado_baixa_attachment(self):
        self._login(self.cpf_prof_a)
        self.assertEqual(self._enviar(self.turma_a, "Plano baixar", "relatorio.pdf", _pdf()).status_code, 200)
        plano = self._ultimo_plano("Plano baixar")
        resp = self.client.get(f"/api/planejamentos/{plano.id}/arquivo")
        self.assertEqual(resp.status_code, 200)
        disp = (resp.headers.get("Content-Disposition") or "").lower()
        self.assertIn("attachment", disp)
        self.assertIn("relatorio.pdf", disp)
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.get_data()[:4], b"%PDF")
        self._assert_sem_caminho(resp)
        self._login(self.login_coord_a)
        resp = self.client.get(f"/api/planejamentos/{plano.id}/arquivo")
        self.assertEqual(resp.status_code, 200)

    def test_11_idor_outra_escola_e_colega(self):
        self._login(self.cpf_prof_a)
        self.assertEqual(self._enviar(self.turma_a, "Plano IDOR A", "a.pdf", _pdf()).status_code, 200)
        plan_a = self._ultimo_plano("Plano IDOR A")
        self._login(self.cpf_prof_b)
        self.assertEqual(self._enviar(self.turma_b, "Plano IDOR B", "b.pdf", _pdf()).status_code, 200)
        plan_b = self._ultimo_plano("Plano IDOR B")

        self._login(self.cpf_prof_b)
        resp = self.client.get(f"/api/planejamentos/{plan_a.id}/arquivo")
        self.assertEqual(resp.status_code, 403)
        self._assert_sem_caminho(resp)

        self._login(self.login_coord_b)
        resp = self.client.get(f"/api/planejamentos/{plan_a.id}/arquivo")
        self.assertEqual(resp.status_code, 403)

        self._login(self.inep_b)
        resp = self.client.get(f"/api/planejamentos/{plan_a.id}/arquivo")
        self.assertEqual(resp.status_code, 403)

        self._login(self.cpf_prof_a2)
        resp = self.client.get(f"/api/planejamentos/{plan_a.id}/arquivo")
        self.assertEqual(resp.status_code, 403)

        self._login(self.cpf_prof_a)
        resp = self.client.get(f"/api/planejamentos/{plan_b.id}/arquivo")
        self.assertEqual(resp.status_code, 403)

    def test_12_payload_nao_expoe_nome_fisico_nem_caminho(self):
        self._login(self.cpf_prof_a)
        self.assertEqual(self._enviar(self.turma_a, "Plano payload", "exibicao.pdf", _pdf()).status_code, 200)
        plano = self._ultimo_plano("Plano payload")
        dash, err = _json(self.client.get("/api/dashboard"))
        self.assertIsNone(err)
        item = next(p for p in dash["planejamentos"] if p["id"] == plano.id)
        self.assertEqual(item["arquivo"], "exibicao.pdf")
        self.assertNotEqual(item["arquivo"], plano.arquivo)
        serial = str(dash)
        self.assertNotIn(plano.arquivo, serial)
        self.assertNotIn(self._pasta(), serial)

    def test_13_arquivo_inexistente_404_nao_500(self):
        self._login(self.cpf_prof_a)
        self.assertEqual(self._enviar(self.turma_a, "Plano fantasma", "fantasma.pdf", _pdf()).status_code, 200)
        plano = self._ultimo_plano("Plano fantasma")
        os.remove(os.path.join(self._pasta(), plano.arquivo))
        resp = self.client.get(f"/api/planejamentos/{plano.id}/arquivo")
        self.assertEqual(resp.status_code, 404)
        self.assertNotEqual(resp.status_code, 500)
        data, error = _json(resp)
        self.assertIsNone(data)
        self.assertTrue(error)
        self._assert_sem_caminho(resp)
        resp = self.client.get("/api/planejamentos/999999/arquivo")
        self.assertEqual(resp.status_code, 404)

    def test_14_static_nao_serve_upload(self):
        self._login(self.cpf_prof_a)
        self.assertEqual(self._enviar(self.turma_a, "Plano static", "publico.pdf", _pdf()).status_code, 200)
        plano = self._ultimo_plano("Plano static")
        resp = self.client.get("/static/uploads/%s" % plano.arquivo)
        self.assertNotEqual(resp.status_code, 200)
        resp = self.client.get("/static/uploads/publico.pdf")
        self.assertNotEqual(resp.status_code, 200)

    def test_15_api_direta_sem_autorizacao_nao_envia(self):
        bruto = _pdf()
        anon = self.app.test_client()
        resp = anon.post("/api/planejamento/enviar", data={
            "turma_id": str(self.turma_a),
            "conteudo": "Intruso anon",
            "arquivo": (io.BytesIO(bruto), "x.pdf", "application/pdf"),
        })
        self.assertEqual(resp.status_code, 401)

        self._login(self.login_coord_a)
        resp = self._enviar(self.turma_a, "Intruso coord", "x.pdf", bruto, "application/pdf")
        self.assertEqual(resp.status_code, 403)

        self._login(self.cpf_prof_b)
        resp = self._enviar(self.turma_a, "Intruso outra escola", "x.pdf", bruto, "application/pdf")
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(self._ultimo_plano("Intruso outra escola"))

        self._login(self.inep_a)
        resp = self._enviar(self.turma_a, "Intruso gestor", "x.pdf", bruto, "application/pdf")
        self.assertEqual(resp.status_code, 403)

    def test_16_falha_commit_nao_deixa_orfao(self):
        self._login(self.cpf_prof_a)
        antes = set(os.listdir(self._pasta()))
        with patch("src.routes.api.db.session.commit", side_effect=RuntimeError("falha proposital")):
            resp = self._enviar(self.turma_a, "Plano orfao", "orfao.pdf", _pdf(), "application/pdf")
        self.assertEqual(resp.status_code, 500)
        self._assert_sem_caminho(resp)
        depois = set(os.listdir(self._pasta()))
        self.assertEqual(antes, depois)
        self.assertIsNone(self._ultimo_plano("Plano orfao"))

    def test_17_docx_e_doc_validos(self):
        self._login(self.cpf_prof_a)
        resp = self._enviar(
            self.turma_a, "Plano docx", "unidade.docx", _docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._enviar(self.turma_a, "Plano doc", "unidade.doc", _ole(), "application/msword")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._enviar(
            self.turma_a, "Plano xlsx", "notas.xlsx", _xlsx(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_18_limite_global_dez_mb(self):
        self.assertEqual(self.app.config["MAX_CONTENT_LENGTH"], 10 * 1024 * 1024)
        pasta = self.app.config["UPLOAD_FOLDER"]
        self.assertEqual(os.path.abspath(pasta), os.path.abspath(self._upload_dir))


class UploadsLegadosStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cls._db_path = tmp.name
        from src.config import Config
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        Config.SQLALCHEMY_DATABASE_URI = "sqlite:///" + Path(cls._db_path).resolve().as_posix()
        from src import create_app
        cls.app = create_app()
        cls.app.testing = True
        cls._uploads = Path(__file__).resolve().parents[1] / "src" / "static" / "uploads"
        cls._uploads.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass

    def test_arquivo_fisico_em_static_uploads_nao_e_publico(self):
        alvo = self._uploads / "arquivo_legado_teste.pdf"
        alvo.write_bytes(b"%PDF-1.4\nlegado-nao-deve-vazar\n%%EOF\n")
        try:
            anon = self.app.test_client()
            resp = anon.get("/static/uploads/arquivo_legado_teste.pdf")
            self.assertEqual(resp.status_code, 404)
            self.assertNotIn(b"legado-nao-deve-vazar", resp.get_data())
            logo = anon.get("/static/img/logo2.png")
            self.assertEqual(logo.status_code, 200)
            self.assertGreater(len(logo.get_data()), 100)
        finally:
            alvo.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
