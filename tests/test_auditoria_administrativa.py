"""Estágio 4 / Bloco 3: auditoria das operações administrativas críticas."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.auditoria import (
    ALOCACAO_ALTERADA,
    ALOCACAO_CRIADA,
    ANO_LETIVO_ALTERADO,
    ANO_LETIVO_CRIADO,
    CALENDARIO_ATUALIZADO,
    CALENDARIO_GERADO,
    CONFIGURACAO_ACADEMICA_ALTERADA,
    DIA_CALENDARIO_ALTERADO,
    DISCIPLINA_CRIADA,
    DISCIPLINA_REMOVIDA,
    ESCOLA_ALTERADA,
    ESCOLA_CRIADA,
    ESCOLA_REMOVIDA,
    PERIODO_LETIVO_ALTERADO,
    PERIODO_LETIVO_CRIADO,
    TURMA_ALTERADA,
    TURMA_CRIADA,
    TURMA_ENCERRADA,
    TURMA_REMOVIDA,
    USUARIO_ALTERADO,
    USUARIO_CRIADO,
    USUARIO_REMOVIDO,
)
from src.config import resolver_uri_teste_postgresql
from tests.support import (
    SENHA_DEMO_ADMIN,
    SENHA_PESSOAL,
    apontar_banco_teste,
    concluir_primeiro_acesso,
    criar_app_migrada,
    preparar_ambiente_teste,
    senha_temporaria,
)


URI_PG = resolver_uri_teste_postgresql()

CHAVES_PII = (
    "cpf",
    "telefone",
    "endereco",
    "endereço",
    "responsavel",
    "responsável",
    "senha",
    "senha_hash",
    "password",
    "password_hash",
    "token",
    "secret",
    "cookie",
    "session",
    "authorization",
    "client_secret",
    "access_token",
)

ACOES_ADMIN = {
    USUARIO_CRIADO, USUARIO_ALTERADO, USUARIO_REMOVIDO,
    CONFIGURACAO_ACADEMICA_ALTERADA,
    ANO_LETIVO_CRIADO, ANO_LETIVO_ALTERADO,
    PERIODO_LETIVO_CRIADO, PERIODO_LETIVO_ALTERADO,
    DIA_CALENDARIO_ALTERADO, CALENDARIO_GERADO, CALENDARIO_ATUALIZADO,
    ESCOLA_CRIADA, ESCOLA_ALTERADA, ESCOLA_REMOVIDA,
    TURMA_CRIADA, TURMA_ALTERADA, TURMA_REMOVIDA,
    DISCIPLINA_CRIADA, DISCIPLINA_REMOVIDA,
    ALOCACAO_CRIADA, ALOCACAO_ALTERADA,
}


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


def _como_dict(valor):
    if valor is None:
        return None
    if isinstance(valor, dict):
        return valor
    if isinstance(valor, str):
        return json.loads(valor)
    return dict(valor)


def _coletar_chaves(obj, acc):
    if isinstance(obj, dict):
        for chave, valor in obj.items():
            acc.append(str(chave).casefold())
            _coletar_chaves(valor, acc)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _coletar_chaves(item, acc)


def _texto_log(log):
    return json.dumps(
        {
            "acao": log.acao,
            "anteriores": _como_dict(log.dados_anteriores),
            "novos": _como_dict(log.dados_novos),
        },
        ensure_ascii=False,
    )


def _assert_sem_pii(test, logs):
    for log in logs:
        chaves = []
        _coletar_chaves(_como_dict(log.dados_anteriores), chaves)
        _coletar_chaves(_como_dict(log.dados_novos), chaves)
        for proibida in CHAVES_PII:
            test.assertNotIn(proibida, chaves, "%s %s" % (log.acao, proibida))
        blob = _texto_log(log).casefold()
        for trecho in ("senha_hash", "password_hash", "client_secret", "access_token"):
            test.assertNotIn(trecho, blob, log.acao)


class AuditoriaAdministrativaTest(unittest.TestCase):
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
        cls._montar()

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass

    @classmethod
    def _login_client(cls, client, login, senha):
        resp = client.post("/api/auth/login", json={"login": login, "senha": senha})
        if resp.status_code != 200:
            raise RuntimeError("login %s: %s" % (login, _json(resp)[1]))

    @classmethod
    def _montar(cls):
        client = cls.app.test_client()
        cls._login_client(client, "admin", SENHA_DEMO_ADMIN)
        from src.models import Disciplina, Usuario
        with cls.app.app_context():
            cls.mat = Disciplina.query.filter_by(nome="Matemática").first().id
            cls.admin_id = Usuario.query.filter_by(login="admin").one().id

        cls.inep = "96000001"
        cls.cpf_prof = "96011111111"
        cls.login_coord = "coord960a"

        resp = client.post("/api/escolas", json={
            "nome": "Escola Admin Audit",
            "inep": cls.inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep)

        resp = client.post(
            "/api/escolas/%s/coordenadores" % cls.escola_id,
            json={"login": cls.login_coord, "nome": "Coord Admin Audit"},
        )
        temp_coord = senha_temporaria(resp)
        resp = client.post("/api/professores", json={
            "nome": "Prof Admin Audit", "cpf": cls.cpf_prof, "escola_id": cls.escola_id,
        })
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(client, cls.inep, temp_gestor)
        concluir_primeiro_acesso(client, cls.login_coord, temp_coord)
        concluir_primeiro_acesso(client, cls.cpf_prof, temp_prof)

        cls._login_client(client, cls.inep, SENHA_PESSOAL)
        client.post("/api/turmas", json={
            "nome": "6º Ano Admin Audit", "ano_letivo": 2026, "etapa": "fundamental",
        })
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º Ano Admin Audit")
        cls.prof_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof)
        with cls.app.app_context():
            from src.models import Usuario
            cls.gestor_id = Usuario.query.filter_by(login=cls.inep).one().id
            cls.prof_user_id = Usuario.query.filter_by(login=cls.cpf_prof).one().id
            cls.coord_id = Usuario.query.filter_by(login=cls.login_coord).one().id

    def setUp(self):
        self.client = self.app.test_client()

    def _proximo(self):
        AuditoriaAdministrativaTest._seq += 1
        return AuditoriaAdministrativaTest._seq

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _contar(self, acao=None):
        from src.models import AuditLog
        q = AuditLog.query
        if acao:
            q = q.filter_by(acao=acao)
        return q.count()

    def _logs(self, acao=None):
        from src.models import AuditLog
        q = AuditLog.query
        if acao:
            q = q.filter_by(acao=acao)
        return q.order_by(AuditLog.id.asc()).all()

    def _ultimo(self, acao):
        from src.models import AuditLog
        return AuditLog.query.filter_by(acao=acao).order_by(AuditLog.id.desc()).first()

    def _put_media(self, valor, senha=SENHA_DEMO_ADMIN):
        return self.client.put("/api/configuracao-academica", json={
            "mediaMinima": valor,
            "senhaConfirmacao": senha,
        })

    def test_a_criacao_usuario_gera_log(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        n = self._proximo()
        login_coord = "coord960x%s" % n
        with self.app.app_context():
            antes = self._contar(USUARIO_CRIADO)
        resp = self.client.post(
            "/api/escolas/%s/coordenadores" % self.escola_id,
            json={"login": login_coord, "nome": "Coord Novo Audit"},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp = senha_temporaria(resp)
        self.assertTrue(temp)
        with self.app.app_context():
            self.assertEqual(self._contar(USUARIO_CRIADO), antes + 1)
            log = self._ultimo(USUARIO_CRIADO)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(log.usuario_id, self.admin_id)
            self.assertEqual(novos["login"], login_coord)
            self.assertEqual(novos["papel"], "coordenador_pedagogico")
            self.assertEqual(novos["escola_id"], self.escola_id)
            self.assertTrue(novos["senha_temporaria_gerada"])
            self.assertNotEqual(log.usuario_id, novos["usuario_alvo_id"])
            blob = _texto_log(log)
            self.assertNotIn(temp, blob)
            self.assertNotIn("senha_hash", blob.casefold())

    def test_b_alteracao_real_usuario(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        with self.app.app_context():
            antes = self._contar(USUARIO_ALTERADO)
        novo_login = "coord960edit%s" % self._proximo()
        resp = self.client.put("/api/editar/usuario/%s" % self.coord_id, json={
            "login": novo_login,
            "nome": "Coord Renomeado",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(USUARIO_ALTERADO), antes + 1)
            log = self._ultimo(USUARIO_ALTERADO)
            self.assertEqual(log.usuario_id, self.admin_id)
            ant = _como_dict(log.dados_anteriores)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(ant["usuario_alvo_id"], self.coord_id)
            self.assertEqual(novos["usuario_alvo_id"], self.coord_id)
            self.assertEqual(novos["login"], novo_login)
            self.assertEqual(novos["nome"], "Coord Renomeado")
        AuditoriaAdministrativaTest.login_coord = novo_login

    def test_c_papel_nao_e_alteravel_pela_api(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        with self.app.app_context():
            from src import db
            from src.models import Usuario
            papel = db.session.get(Usuario, self.coord_id).papel
            n_papel = sum(1 for l in self._logs() if l.acao == "PAPEL_USUARIO_ALTERADO")
        resp = self.client.put("/api/editar/usuario/%s" % self.coord_id, json={
            "login": AuditoriaAdministrativaTest.login_coord,
            "nome": "Coord Renomeado",
            "papel": "dirigente",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Usuario
            self.assertEqual(db.session.get(Usuario, self.coord_id).papel, papel)
            self.assertEqual(
                sum(1 for l in self._logs() if l.acao == "PAPEL_USUARIO_ALTERADO"),
                n_papel,
            )

    def test_d_ator_diferente_do_alvo(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        n = self._proximo()
        cpf = ("9602%07d" % n)[:11]
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Alvo Audit", "cpf": cpf, "escola_id": self.escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            log = self._ultimo(USUARIO_CRIADO)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(log.usuario_id, self.admin_id)
            self.assertEqual(novos["usuario_alvo_id"], novos["usuario_alvo_id"])
            self.assertNotEqual(log.usuario_id, novos["usuario_alvo_id"])
            self.assertEqual(novos["papel"], "professor")

    def test_e_escola_id_nao_e_alteravel_pela_api(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        with self.app.app_context():
            from src import db
            from src.models import Usuario
            escola_id = db.session.get(Usuario, self.coord_id).escola_id
            n_esc = sum(1 for l in self._logs() if l.acao == "ESCOLA_USUARIO_ALTERADA")
        resp = self.client.put("/api/editar/usuario/%s" % self.coord_id, json={
            "login": AuditoriaAdministrativaTest.login_coord,
            "nome": "Coord Renomeado",
            "escola_id": 99999,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Usuario
            self.assertEqual(db.session.get(Usuario, self.coord_id).escola_id, escola_id)
            self.assertEqual(
                sum(1 for l in self._logs() if l.acao == "ESCOLA_USUARIO_ALTERADA"),
                n_esc,
            )

    def test_f_senha_temporaria_nao_entra_no_log(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        n = self._proximo()
        inep = "9610%04d" % n
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola Senha Audit %s" % inep,
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp = senha_temporaria(resp)
        self.assertTrue(temp)
        with self.app.app_context():
            log_user = self._ultimo(USUARIO_CRIADO)
            log_esc = self._ultimo(ESCOLA_CRIADA)
            blob = _texto_log(log_user) + _texto_log(log_esc)
            self.assertNotIn(temp, blob)
            self.assertNotIn("senha_hash", blob.casefold())
            self.assertTrue(_como_dict(log_user.dados_novos)["senha_temporaria_gerada"])
            _assert_sem_pii(self, [log_user, log_esc])

    def test_g_idempotente_nao_gera_log(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        with self.app.app_context():
            n_esc = self._contar(ESCOLA_ALTERADA)
            n_cfg = self._contar(CONFIGURACAO_ACADEMICA_ALTERADA)
            n_user = self._contar(USUARIO_ALTERADO)
        resp = self.client.put("/api/escolas/%s" % self.escola_id, json={
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self._put_media(6.0)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        resp = self.client.put("/api/editar/usuario/%s" % self.coord_id, json={
            "login": AuditoriaAdministrativaTest.login_coord,
            "nome": "Coord Renomeado",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(ESCOLA_ALTERADA), n_esc)
            self.assertEqual(self._contar(CONFIGURACAO_ACADEMICA_ALTERADA), n_cfg)
            self.assertEqual(self._contar(USUARIO_ALTERADO), n_user)

    def test_h_acl_negada_nao_gera_log(self):
        self._login(self.cpf_prof)
        with self.app.app_context():
            total = self._contar()
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola ACL", "inep": "96999999",
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 403)
        resp = self._put_media(9.0, senha=SENHA_PESSOAL)
        self.assertEqual(resp.status_code, 403)
        resp = self.client.post("/api/anos-letivos", json={
            "ano": 2099, "data_inicio": "2099-02-01", "data_fim": "2099-12-01",
        })
        self.assertEqual(resp.status_code, 403)
        with self.app.app_context():
            self.assertEqual(self._contar(), total)

    def test_i_configuracao_academica_alterada(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        with self.app.app_context():
            antes = self._contar(CONFIGURACAO_ACADEMICA_ALTERADA)
        resp = self._put_media(7.0)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(CONFIGURACAO_ACADEMICA_ALTERADA), antes + 1)
            log = self._ultimo(CONFIGURACAO_ACADEMICA_ALTERADA)
            self.assertEqual(log.usuario_id, self.admin_id)
            ant = _como_dict(log.dados_anteriores)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(ant["parametro"], "media_minima")
            self.assertEqual(novos["parametro"], "media_minima")
            self.assertEqual(novos["escopo"], "rede")
            self.assertEqual(float(novos["valor"]), 7.0)
            self.assertNotEqual(float(ant["valor"]), float(novos["valor"]))
            blob = _texto_log(log).casefold()
            self.assertNotIn("senha", blob)
            self.assertNotIn(SENHA_DEMO_ADMIN.casefold(), blob)
        self._put_media(6.0)

    def test_j_configuracao_idempotente(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        self._put_media(6.0)
        with self.app.app_context():
            antes = self._contar(CONFIGURACAO_ACADEMICA_ALTERADA)
        resp = self._put_media(6.0)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(CONFIGURACAO_ACADEMICA_ALTERADA), antes)

    def test_k_alteracao_calendario(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        n = self._proximo()
        ano_num = 2040 + n
        with self.app.app_context():
            n_ano = self._contar(ANO_LETIVO_CRIADO)
            n_per = self._contar(PERIODO_LETIVO_CRIADO)
        resp = self.client.post("/api/anos-letivos", json={
            "ano": ano_num,
            "data_inicio": "%s-02-02" % ano_num,
            "data_fim": "%s-12-18" % ano_num,
            "ativo": False,
            "periodos": [
                {
                    "numero": 1, "nome": "1º Bimestre",
                    "data_inicio": "%s-02-02" % ano_num,
                    "data_fim": "%s-04-30" % ano_num,
                },
            ],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        ano_id = _json(resp)[0]["id"]
        with self.app.app_context():
            self.assertEqual(self._contar(ANO_LETIVO_CRIADO), n_ano + 1)
            self.assertEqual(self._contar(PERIODO_LETIVO_CRIADO), n_per + 1)
            log = self._ultimo(ANO_LETIVO_CRIADO)
            self.assertEqual(_como_dict(log.dados_novos)["ano"], ano_num)
        resp = self.client.put("/api/anos-letivos/%s" % ano_id, json={
            "data_fim": "%s-12-20" % ano_num,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            log = self._ultimo(ANO_LETIVO_ALTERADO)
            self.assertEqual(_como_dict(log.dados_novos)["data_fim"], "%s-12-20" % ano_num)

    def test_l_lote_nao_gera_centenas_de_logs(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        n = self._proximo()
        ano_num = 2050 + n
        resp = self.client.post("/api/anos-letivos", json={
            "ano": ano_num,
            "data_inicio": "%s-02-01" % ano_num,
            "data_fim": "%s-02-28" % ano_num,
            "ativo": False,
            "gerar_calendario": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        ano_id = _json(resp)[0]["id"]
        with self.app.app_context():
            from src.models import DiaCalendario
            n_dias = DiaCalendario.query.filter_by(ano_letivo_id=ano_id).count()
            n_ger = sum(1 for l in self._logs(CALENDARIO_GERADO) if _como_dict(l.dados_novos).get("ano_letivo_id") == ano_id)
            n_dia_log = sum(
                1 for l in self._logs(DIA_CALENDARIO_ALTERADO)
                if (_como_dict(l.dados_novos) or {}).get("ano_letivo_id") == ano_id
            )
            self.assertGreaterEqual(n_dias, 20)
            self.assertEqual(n_ger, 1)
            self.assertEqual(n_dia_log, 0)
            log = next(l for l in self._logs(CALENDARIO_GERADO) if _como_dict(l.dados_novos).get("ano_letivo_id") == ano_id)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["quantidade_dias_criados"], n_dias)
            self.assertNotIn("dias", novos)
        dias_feriado = [
            {"data": "%s-02-%02d" % (ano_num, dia), "tipo": "FERIADO"}
            for dia in range(2, 12)
        ]
        with self.app.app_context():
            n_upd = self._contar(CALENDARIO_ATUALIZADO)
            n_dia = self._contar(DIA_CALENDARIO_ALTERADO)
        resp = self.client.put("/api/anos-letivos/%s/dias" % ano_id, json={"dias": dias_feriado})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(CALENDARIO_ATUALIZADO), n_upd + 1)
            self.assertEqual(self._contar(DIA_CALENDARIO_ALTERADO), n_dia)
            log = self._ultimo(CALENDARIO_ATUALIZADO)
            novos = _como_dict(log.dados_novos)
            self.assertGreaterEqual(novos["quantidade_dias_alterados"] + novos["quantidade_dias_criados"], 1)
            self.assertLess(novos["quantidade_dias_alterados"], 365)
            self.assertNotIn("dias", novos)

    def test_m_edicao_manual_de_um_dia(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        n = self._proximo()
        ano_num = 2060 + n
        resp = self.client.post("/api/anos-letivos", json={
            "ano": ano_num,
            "data_inicio": "%s-03-01" % ano_num,
            "data_fim": "%s-03-07" % ano_num,
            "ativo": False,
            "gerar_calendario": True,
        })
        ano_id = _json(resp)[0]["id"]
        with self.app.app_context():
            n_dia = self._contar(DIA_CALENDARIO_ALTERADO)
        resp = self.client.put("/api/anos-letivos/%s/dias" % ano_id, json={
            "data": "%s-03-02" % ano_num, "tipo": "FERIADO",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(DIA_CALENDARIO_ALTERADO), n_dia + 1)
            log = self._ultimo(DIA_CALENDARIO_ALTERADO)
            novos = _como_dict(log.dados_novos)
            ant = _como_dict(log.dados_anteriores)
            self.assertEqual(novos["tipo"], "FERIADO")
            self.assertNotEqual(ant["tipo"], "FERIADO")
            self.assertEqual(novos["data"], "%s-03-02" % ano_num)
        resp = self.client.put("/api/anos-letivos/%s/dias" % ano_id, json={
            "data": "%s-03-02" % ano_num, "tipo": "FERIADO",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(DIA_CALENDARIO_ALTERADO), n_dia + 1)

    def test_n_criacao_alteracao_escola(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        n = self._proximo()
        inep = "9620%04d" % n
        with self.app.app_context():
            n_cria = self._contar(ESCOLA_CRIADA)
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola N %s" % inep, "inep": inep,
            "oferta_infantil": True, "oferta_fundamental": False,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        escola_id = _json(resp)[0]["id"]
        with self.app.app_context():
            self.assertEqual(self._contar(ESCOLA_CRIADA), n_cria + 1)
            log = self._ultimo(ESCOLA_CRIADA)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["inep"], inep)
            self.assertFalse(novos["oferta_fundamental"])
            self.assertNotIn("usuarios", novos)
        with self.app.app_context():
            n_alt = self._contar(ESCOLA_ALTERADA)
        resp = self.client.put("/api/escolas/%s" % escola_id, json={
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(ESCOLA_ALTERADA), n_alt + 1)
            log = self._ultimo(ESCOLA_ALTERADA)
            self.assertTrue(_como_dict(log.dados_novos)["oferta_fundamental"])

    def test_o_protecao_exclusao_continua(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        with self.app.app_context():
            n_rem = self._contar(ESCOLA_REMOVIDA)
        resp = self.client.delete("/api/excluir/escola/%s" % self.escola_id)
        self.assertEqual(resp.status_code, 409)
        with self.app.app_context():
            from src import db
            from src.models import Escola
            self.assertIsNotNone(db.session.get(Escola, self.escola_id))
            self.assertEqual(self._contar(ESCOLA_REMOVIDA), n_rem)
        n = self._proximo()
        inep = "9630%04d" % n
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola Vazia Audit %s" % inep, "inep": inep,
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        escola_id = _json(resp)[0]["id"]
        resp = self.client.delete("/api/excluir/escola/%s" % escola_id)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(ESCOLA_REMOVIDA), n_rem + 1)
            log = self._ultimo(ESCOLA_REMOVIDA)
            self.assertEqual(_como_dict(log.dados_anteriores)["escola_id"], escola_id)
            self.assertTrue(self._logs(USUARIO_REMOVIDO))

    def test_p_criacao_alteracao_turma(self):
        self._login(self.inep)
        nome = "Turma P %s" % self._proximo()
        with self.app.app_context():
            n_cria = self._contar(TURMA_CRIADA)
        resp = self.client.post("/api/turmas", json={
            "nome": nome, "ano_letivo": 2026, "etapa": "fundamental", "capacidade": 25,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == nome)
        with self.app.app_context():
            self.assertEqual(self._contar(TURMA_CRIADA), n_cria + 1)
            log = self._ultimo(TURMA_CRIADA)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["turma_id"], turma_id)
            self.assertEqual(novos["escola_id"], self.escola_id)
            self.assertEqual(novos["capacidade"], 25)
            self.assertNotIn("alunos", novos)
        with self.app.app_context():
            n_alt = self._contar(TURMA_ALTERADA)
        resp = self.client.put("/api/turmas/%s" % turma_id, json={"capacidade": 30})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(TURMA_ALTERADA), n_alt + 1)
            log = self._ultimo(TURMA_ALTERADA)
            self.assertEqual(_como_dict(log.dados_anteriores)["capacidade"], 25)
            self.assertEqual(_como_dict(log.dados_novos)["capacidade"], 30)
        resp = self.client.put("/api/turmas/%s" % turma_id, json={"capacidade": 30})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(TURMA_ALTERADA), n_alt + 1)

    def test_q_fechamento_nao_duplica_log_administrativo(self):
        self._login(self.inep)
        nome = "Turma Encerrar %s" % self._proximo()
        resp = self.client.post("/api/turmas", json={
            "nome": nome, "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == nome)
        cpf = ("9603%07d" % self._proximo())[:11]
        resp = self.client.post("/api/alunos", json={
            "nome": "Aluno Encerrar Audit", "cpf": cpf,
            "turma_id": turma_id, "nome_mae": "Mãe Encerrar",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            n_alt = self._contar(TURMA_ALTERADA)
            n_enc = self._contar(TURMA_ENCERRADA)
        resp = self.client.post("/api/turmas/%s/encerrar" % turma_id)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(TURMA_ENCERRADA), n_enc + 1)
            self.assertEqual(self._contar(TURMA_ALTERADA), n_alt)

    def test_r_criacao_disciplina(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        nome = "Disc Audit %s" % self._proximo()
        with self.app.app_context():
            n_cria = self._contar(DISCIPLINA_CRIADA)
        resp = self.client.post("/api/disciplinas", json={"nome": nome})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import Disciplina
            disc_id = Disciplina.query.filter_by(nome=nome).one().id
            self.assertEqual(self._contar(DISCIPLINA_CRIADA), n_cria + 1)
            log = self._ultimo(DISCIPLINA_CRIADA)
            self.assertEqual(_como_dict(log.dados_novos)["nome"], nome)
        with self.app.app_context():
            n_rem = self._contar(DISCIPLINA_REMOVIDA)
        resp = self.client.delete("/api/excluir/disciplina/%s" % disc_id)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(DISCIPLINA_REMOVIDA), n_rem + 1)

    def test_s_t_criacao_e_alteracao_alocacao(self):
        self._login(self.inep)
        with self.app.app_context():
            n_cria = self._contar(ALOCACAO_CRIADA)
        resp = self.client.post("/api/alocacoes", json={
            "professor_id": self.prof_id,
            "turma_id": self.turma_id,
            "disciplina_id": self.mat,
            "carga_horaria_prevista": 80,
            "grade": [
                {"dia_semana": 0, "quantidade_aulas": 2},
                {"dia_semana": 2, "quantidade_aulas": 2},
            ],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma = next(t for t in dash["turmas"] if t["id"] == self.turma_id)
        aloc = next(a for a in turma["alocacoes"] if a["disciplinaId"] == self.mat)
        aloc_id = aloc["id"]
        with self.app.app_context():
            self.assertEqual(self._contar(ALOCACAO_CRIADA), n_cria + 1)
            log = self._ultimo(ALOCACAO_CRIADA)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["alocacao_id"], aloc_id)
            self.assertEqual(novos["professor_id"], self.prof_id)
            self.assertEqual(novos["carga_horaria_prevista"], 80)
            self.assertEqual(len(novos["grade"]), 2)
            self.assertNotIn("professor_nome", novos)
            self.assertNotIn("cpf", json.dumps(novos).casefold())
        with self.app.app_context():
            n_alt = self._contar(ALOCACAO_ALTERADA)
        resp = self.client.put("/api/alocacoes/%s" % aloc_id, json={
            "cargaHorariaPrevista": 90,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(ALOCACAO_ALTERADA), n_alt + 1)
            log = self._ultimo(ALOCACAO_ALTERADA)
            self.assertEqual(_como_dict(log.dados_anteriores)["carga_horaria_prevista"], 80)
            self.assertEqual(_como_dict(log.dados_novos)["carga_horaria_prevista"], 90)
        resp = self.client.put("/api/alocacoes/%s" % aloc_id, json={
            "cargaHorariaPrevista": 90,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(ALOCACAO_ALTERADA), n_alt + 1)
        resp = self.client.put("/api/alocacoes/%s" % aloc_id, json={
            "grade": [
                {"dia_semana": 0, "quantidade_aulas": 3},
                {"dia_semana": 2, "quantidade_aulas": 2},
            ],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(ALOCACAO_ALTERADA), n_alt + 2)
            log = self._ultimo(ALOCACAO_ALTERADA)
            grade = _como_dict(log.dados_novos)["grade"]
            self.assertEqual(grade[0]["quantidade_aulas"], 3)

    def test_u_alocacao_sem_delete_administrativo(self):
        resp = self.client.open("/api/alocacoes/1", method="DELETE")
        self.assertNotEqual(resp.status_code, 200)
        self.assertLess(resp.status_code, 500)

    def test_v_horario_sem_crud_autonomo(self):
        achados = []
        for rule in self.app.url_map.iter_rules():
            texto = "%s %s" % (rule.endpoint or "", rule.rule or "")
            baixo = texto.lower()
            if "horario" in baixo and "aloc" in baixo:
                achados.append(rule.rule)
        self.assertEqual(achados, [])

    def test_w1_rollback_configuracao(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        with self.app.app_context():
            from src import db
            from src.models import AuditLog, ConfiguracaoAcademica
            cfg = ConfiguracaoAcademica.query.order_by(ConfiguracaoAcademica.id).first()
            media = cfg.media_minima
            n_log = AuditLog.query.filter_by(acao=CONFIGURACAO_ACADEMICA_ALTERADA).count()
        with patch("src.routes.api.db.session.commit", side_effect=RuntimeError("falha-audit-cfg")):
            with self.assertRaises(RuntimeError):
                self._put_media(8.5)
        with self.app.app_context():
            from src import db
            from src.models import AuditLog, ConfiguracaoAcademica
            db.session.rollback()
            cfg = ConfiguracaoAcademica.query.order_by(ConfiguracaoAcademica.id).first()
            self.assertEqual(float(cfg.media_minima), float(media))
            self.assertEqual(
                AuditLog.query.filter_by(acao=CONFIGURACAO_ACADEMICA_ALTERADA).count(),
                n_log,
            )

    def test_w2_rollback_disciplina(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        nome = "Disc Rollback %s" % self._proximo()
        with self.app.app_context():
            from src.models import AuditLog, Disciplina
            n_disc = Disciplina.query.filter_by(nome=nome).count()
            n_log = AuditLog.query.filter_by(acao=DISCIPLINA_CRIADA).count()
        with patch("src.routes.api.db.session.commit", side_effect=RuntimeError("falha-audit-disc")):
            with self.assertRaises(RuntimeError):
                self.client.post("/api/disciplinas", json={"nome": nome})
        with self.app.app_context():
            from src import db
            from src.models import AuditLog, Disciplina
            db.session.rollback()
            self.assertEqual(Disciplina.query.filter_by(nome=nome).count(), n_disc)
            self.assertEqual(
                AuditLog.query.filter_by(acao=DISCIPLINA_CRIADA).count(),
                n_log,
            )

    def test_x_ausencia_de_pii_e_segredos(self):
        with self.app.app_context():
            from src.models import AuditLog
            logs = AuditLog.query.filter(AuditLog.acao.in_(ACOES_ADMIN)).all()
            self.assertTrue(logs)
            _assert_sem_pii(self, logs)

    def test_y_z_secretaria_preservada_no_log(self):
        from src import db
        from src.auditoria import registrar_auditoria
        from src.models import AuditLog
        with self.app.app_context():
            log = registrar_auditoria(
                "teste_secretaria",
                "Usuario",
                dados_novos={
                    "secretaria": "valor legítimo",
                    "papel": "secretaria",
                    "client_secret": "x",
                    "access_token": "y",
                    "password_hash": "z",
                },
            )
            db.session.commit()
            gravado = db.session.get(AuditLog, log.id)
            novos = _como_dict(gravado.dados_novos)
            self.assertEqual(novos["secretaria"], "valor legítimo")
            self.assertEqual(novos["papel"], "secretaria")
            self.assertNotIn("client_secret", novos)
            self.assertNotIn("access_token", novos)
            self.assertNotIn("password_hash", novos)


def _limpar_pg(app):
    from sqlalchemy import inspect, text
    from src import db
    with app.app_context():
        insp = inspect(db.engine)
        nomes = [n for n in insp.get_table_names() if n != "alembic_version"]
        if nomes:
            db.session.execute(
                text("TRUNCATE %s RESTART IDENTITY CASCADE" % ", ".join(nomes))
            )
            db.session.commit()
        from src import _bootstrap_demo
        from src.notas import garantir_configuracao_padrao
        from src.passwords import completar_senhas_iniciais_ausentes
        _bootstrap_demo()
        completar_senhas_iniciais_ausentes()
        garantir_configuracao_padrao()


@unittest.skipUnless(
    URI_PG,
    "EDUCAMAIS_TEST_DATABASE_URI ausente; PostgreSQL de testes pulado (não se usa URI de migração/produção).",
)
class AuditoriaAdministrativaPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = os.environ.copy()
        from src.config import Config
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        cls._old_opts = Config.SQLALCHEMY_ENGINE_OPTIONS
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-pg-audit-adm"
        os.environ["EDUCAMAIS_DEMO"] = "1"
        os.environ.pop("EDUCAMAIS_DEBUG", None)
        apontar_banco_teste(URI_PG)
        cls.app = criar_app_migrada()
        cls.app.testing = True
        cls._seq = 0

    @classmethod
    def tearDownClass(cls):
        from src import db
        from src.config import Config
        with cls.app.app_context():
            db.session.remove()
            db.engine.dispose()
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        Config.SQLALCHEMY_ENGINE_OPTIONS = cls._old_opts
        os.environ.clear()
        os.environ.update(cls._env)
        preparar_ambiente_teste(demo=True)

    def setUp(self):
        _limpar_pg(self.app)
        self.client = self.app.test_client()

    def _proximo(self):
        AuditoriaAdministrativaPostgresTest._seq += 1
        return AuditoriaAdministrativaPostgresTest._seq

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_pg_usuario_config_e_rollback(self):
        from src import db
        from src.models import AuditLog, ConfiguracaoAcademica, Usuario
        self._login("admin", SENHA_DEMO_ADMIN)
        n = self._proximo()
        inep = "9700%04d" % n
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola PG Adm %s" % inep, "inep": inep,
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp = senha_temporaria(resp)
        with self.app.app_context():
            self.assertEqual(db.engine.dialect.name, "postgresql")
            log = AuditLog.query.filter_by(acao=USUARIO_CRIADO).order_by(AuditLog.id.desc()).first()
            self.assertIsNotNone(log)
            blob = _texto_log(log)
            self.assertNotIn(temp, blob)
            self.assertTrue(_como_dict(log.dados_novos)["senha_temporaria_gerada"])
            ator = Usuario.query.filter_by(login="admin").one()
            self.assertEqual(log.usuario_id, ator.id)
            self.assertNotEqual(log.usuario_id, _como_dict(log.dados_novos)["usuario_alvo_id"])
        resp = self.client.put("/api/configuracao-academica", json={
            "mediaMinima": 7.5, "senhaConfirmacao": SENHA_DEMO_ADMIN,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            n_cfg = AuditLog.query.filter_by(acao=CONFIGURACAO_ACADEMICA_ALTERADA).count()
            self.assertEqual(n_cfg, 1)
            media = ConfiguracaoAcademica.query.first().media_minima
            self.assertEqual(float(media), 7.5)
            n_total = AuditLog.query.count()
            cfg = ConfiguracaoAcademica.query.first()
            cfg.media_minima = 4.0
            from src.auditoria import registrar_auditoria
            registrar_auditoria(
                CONFIGURACAO_ACADEMICA_ALTERADA,
                "ConfiguracaoAcademica",
                entidade_id=cfg.id,
                dados_novos={"parametro": "media_minima", "valor": 4.0},
            )
            db.session.rollback()
            self.assertEqual(float(ConfiguracaoAcademica.query.first().media_minima), 7.5)
            self.assertEqual(AuditLog.query.count(), n_total)

    def test_pg_calendario_lote_e_secretaria(self):
        from src import db
        from src.auditoria import registrar_auditoria
        from src.models import AuditLog, DiaCalendario
        self._login("admin", SENHA_DEMO_ADMIN)
        n = self._proximo()
        ano_num = 2080 + n
        resp = self.client.post("/api/anos-letivos", json={
            "ano": ano_num,
            "data_inicio": "%s-02-01" % ano_num,
            "data_fim": "%s-02-28" % ano_num,
            "ativo": False,
            "gerar_calendario": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        ano_id = _json(resp)[0]["id"]
        with self.app.app_context():
            n_dias = DiaCalendario.query.filter_by(ano_letivo_id=ano_id).count()
            n_ger = AuditLog.query.filter_by(acao=CALENDARIO_GERADO).count()
            n_dia = AuditLog.query.filter_by(acao=DIA_CALENDARIO_ALTERADO).count()
            self.assertGreaterEqual(n_dias, 20)
            self.assertEqual(n_ger, 1)
            self.assertEqual(n_dia, 0)
            log = registrar_auditoria(
                "teste_pg_secretaria",
                "Usuario",
                dados_novos={"secretaria": "ok", "client_secret": "x"},
            )
            db.session.commit()
            novos = _como_dict(db.session.get(AuditLog, log.id).dados_novos)
            self.assertEqual(novos["secretaria"], "ok")
            self.assertNotIn("client_secret", novos)


if __name__ == "__main__":
    unittest.main()
