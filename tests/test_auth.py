import os
import tempfile
import unittest
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class AutenticacaoTest(unittest.TestCase):
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
        cls._seq = 0

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass

    def setUp(self):
        self.client = self.app.test_client()
        with self.app.app_context():
            from src.passwords import resetar_falhas_login_para_testes
            resetar_falhas_login_para_testes()

    def _login_admin(self):
        resp = self.client.post("/api/auth/login", json={"login": "admin", "senha": SENHA_DEMO_ADMIN})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        return _json(resp)[0]

    def _criar_gestor_novo(self):
        AutenticacaoTest._seq += 1
        inep = f"7100{AutenticacaoTest._seq:04d}"
        self._login_admin()
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola Auth {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp = senha_temporaria(resp)
        self.assertTrue(temp)
        self.client.post("/api/auth/logout")
        return inep, temp

    def _concluir_primeiro_acesso(self, inep, senha_temp, nova="novasenha"):
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": senha_temp})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self.client.post("/api/auth/definir-senha", json={"senha": nova, "confirmar": nova})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        return _json(resp)[0]

    def test_16_aplicacao_inicia(self):
        self.assertIsNotNone(self.app)
        self._login_admin()

    def test_01_usuario_inexistente_nao_autentica(self):
        resp = self.client.post("/api/auth/login", json={"login": "nao-existe", "senha": "qualquer"})
        self.assertNotEqual(resp.status_code, 200)
        self.assertEqual(resp.status_code, 400)
        _, error = _json(resp)
        self.assertEqual(error, "Usuário ou senha inválidos.")

    def test_02_senha_errada_nao_autentica(self):
        resp = self.client.post("/api/auth/login", json={"login": "admin", "senha": "errada999"})
        self.assertEqual(resp.status_code, 400)
        _, error = _json(resp)
        self.assertEqual(error, "Usuário ou senha inválidos.")

    def test_03_senha_vazia_nao_autentica(self):
        resp = self.client.post("/api/auth/login", json={"login": "admin", "senha": ""})
        self.assertNotEqual(resp.status_code, 200)
        _, error = _json(resp)
        self.assertTrue(error)
        inep, temp = self._criar_gestor_novo()
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": ""})
        self.assertNotEqual(resp.status_code, 200)
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": "   "})
        self.assertNotEqual(resp.status_code, 200)
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": temp})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_04_usuario_normal_com_senha_correta_autentica(self):
        data = self._login_admin()
        self.assertEqual(data["papel"], "dirigente")
        self.assertFalse(data["primeiroAcesso"])
        self.assertEqual(data["login"], "admin")

    def test_05_primeiro_acesso_com_credencial_inicial_autentica(self):
        inep, temp = self._criar_gestor_novo()
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": temp})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        self.assertEqual(data["papel"], "gestor_escolar")
        self.assertTrue(data["primeiroAcesso"])

    def test_06_primeiro_acesso_indica_definir_senha(self):
        inep, temp = self._criar_gestor_novo()
        self.client.post("/api/auth/login", json={"login": inep, "senha": temp})
        data, _ = _json(self.client.get("/api/me"))
        self.assertTrue(data["primeiroAcesso"])
        dash, _ = _json(self.client.get("/api/dashboard"))
        self.assertTrue(dash["precisaDefinirSenha"])
        resp = self.client.post("/api/turmas", json={"nome": "X", "ano_letivo": 2026, "etapa": "fundamental"})
        self.assertEqual(resp.status_code, 403)

    def test_07_definir_nova_senha_funciona(self):
        inep, temp = self._criar_gestor_novo()
        user = self._concluir_primeiro_acesso(inep, temp, "novasenha")
        self.assertFalse(user["primeiroAcesso"])

    def test_08_primeiro_acesso_passa_para_false(self):
        inep, temp = self._criar_gestor_novo()
        self._concluir_primeiro_acesso(inep, temp, "novasenha")
        with self.app.app_context():
            from src.models import Usuario
            usuario = Usuario.query.filter_by(login=inep).first()
            self.assertIsNotNone(usuario)
            self.assertFalse(usuario.primeiro_acesso)

    def test_09_senha_inicial_deixa_de_funcionar(self):
        inep, temp = self._criar_gestor_novo()
        self._concluir_primeiro_acesso(inep, temp, "novasenha")
        self.client.post("/api/auth/logout")
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": temp})
        self.assertEqual(resp.status_code, 400)
        _, error = _json(resp)
        self.assertIn("senha", error.lower())

    def test_10_nova_senha_funciona(self):
        inep, temp = self._criar_gestor_novo()
        self._concluir_primeiro_acesso(inep, temp, "novasenha")
        self.client.post("/api/auth/logout")
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": "novasenha"})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.assertFalse(_json(resp)[0]["primeiroAcesso"])

    def test_11_alterar_senha_funciona(self):
        inep, temp = self._criar_gestor_novo()
        self._concluir_primeiro_acesso(inep, temp, "novasenha")
        resp = self.client.post("/api/auth/alterar-senha", json={
            "senhaAtual": "novasenha",
            "novaSenha": "trocada1",
            "confirmarSenha": "trocada1",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.assertIn("sucesso", (_json(resp)[0] or {}).get("mensagem", "").lower())

    def test_12_senha_antiga_deixa_de_funcionar_apos_alteracao(self):
        inep, temp = self._criar_gestor_novo()
        self._concluir_primeiro_acesso(inep, temp, "novasenha")
        self.client.post("/api/auth/alterar-senha", json={
            "senhaAtual": "novasenha",
            "novaSenha": "trocada1",
            "confirmarSenha": "trocada1",
        })
        self.client.post("/api/auth/logout")
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": "novasenha"})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": "trocada1"})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_13_logout_encerra_sessao(self):
        self._login_admin()
        resp = self.client.post("/api/auth/logout")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_14_endpoint_protegido_apos_logout_e_recusado(self):
        self._login_admin()
        self.client.post("/api/auth/logout")
        resp = self.client.get("/api/me")
        self.assertEqual(resp.status_code, 401)
        resp = self.client.get("/api/dashboard")
        self.assertEqual(resp.status_code, 401)
        resp = self.client.post("/api/escolas", json={"nome": "X", "inep": "000"})
        self.assertEqual(resp.status_code, 401)

    def test_15_senha_nao_fica_em_texto_puro(self):
        inep, temp = self._criar_gestor_novo()
        with self.app.app_context():
            from src.models import Usuario
            from src.passwords import verificar_senha
            usuario = Usuario.query.filter_by(login=inep).first()
            self.assertIsNotNone(usuario.senha_hash)
            self.assertNotEqual(usuario.senha_hash, temp)
            self.assertNotIn(temp, usuario.senha_hash)
            self.assertTrue(verificar_senha(usuario.senha_hash, temp))
        self._concluir_primeiro_acesso(inep, temp, "novasenha")
        with self.app.app_context():
            from src.models import Usuario
            from src.passwords import verificar_senha
            usuario = Usuario.query.filter_by(login=inep).first()
            self.assertNotEqual(usuario.senha_hash, "novasenha")
            self.assertNotIn("novasenha", usuario.senha_hash)
            self.assertTrue(verificar_senha(usuario.senha_hash, "novasenha"))
            self.assertFalse(verificar_senha(usuario.senha_hash, temp))

    def test_definir_senha_rejeita_minimo(self):
        inep, temp = self._criar_gestor_novo()
        self.client.post("/api/auth/login", json={"login": inep, "senha": temp})
        resp = self.client.post("/api/auth/definir-senha", json={"senha": "1234567", "confirmar": "1234567"})
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/auth/definir-senha", json={"senha": SENHA_PESSOAL, "confirmar": SENHA_PESSOAL})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_lockout_temporario_apos_cinco_falhas(self):
        inep, temp = self._criar_gestor_novo()
        self._concluir_primeiro_acesso(inep, temp, "novasenha")
        self.client.post("/api/auth/logout")
        for _ in range(5):
            resp = self.client.post("/api/auth/login", json={"login": inep, "senha": "errada"})
            self.assertIn(resp.status_code, (400, 403))
        resp = self.client.post("/api/auth/login", json={"login": inep, "senha": "novasenha"})
        self.assertEqual(resp.status_code, 403)
        _, error = _json(resp)
        self.assertIn("bloqueado", error.lower())


if __name__ == "__main__":
    unittest.main()
