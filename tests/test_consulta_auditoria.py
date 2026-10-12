"""Estágio 4 / Bloco 4: consulta administrativa somente leitura dos AuditLogs."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from src.config import resolver_uri_teste_postgresql
from src.consulta_auditoria import PER_PAGE_MAX, PER_PAGE_PADRAO
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
RAIZ = Path(__file__).resolve().parents[1]


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


def _texto(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


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


class ConsultaAuditoriaTest(unittest.TestCase):
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
        from src.models import Usuario
        from src.passwords import hash_senha
        from src import db

        cls.inep_a = "64000001"
        cls.inep_b = "64000002"
        cls.login_coord = "coordAud640"
        cls.login_sec = "secretariaAud640"
        cls.cpf_prof = "64011111111"

        temps = {}
        for nome, inep in (("Escola Aud Norte", cls.inep_a), ("Escola Aud Sul", cls.inep_b)):
            resp = client.post("/api/escolas", json={
                "nome": nome,
                "inep": inep,
                "oferta_infantil": True,
                "oferta_fundamental": True,
            })
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[inep] = senha_temporaria(resp)

        dash = _json(client.get("/api/dashboard"))[0]
        cls.escola_a = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_a)
        cls.escola_b = next(e["id"] for e in dash["escolas"] if e["inep"] == cls.inep_b)

        resp = client.post(
            "/api/escolas/%s/coordenadores" % cls.escola_a,
            json={"login": cls.login_coord, "nome": "Coord Aud"},
        )
        temp_coord = senha_temporaria(resp)
        resp = client.post("/api/professores", json={
            "nome": "Prof Aud", "cpf": cls.cpf_prof, "escola_id": cls.escola_a,
        })
        temp_prof = senha_temporaria(resp)

        with cls.app.app_context():
            db.session.add(Usuario(
                login=cls.login_sec,
                senha_hash=hash_senha(SENHA_PESSOAL),
                papel="secretaria",
                nome="Secretaria Aud",
                primeiro_acesso=False,
            ))
            db.session.commit()
            cls.admin_id = Usuario.query.filter_by(login="admin").one().id
            cls.sec_id = Usuario.query.filter_by(login=cls.login_sec).one().id

        concluir_primeiro_acesso(client, cls.inep_a, temps[cls.inep_a])
        concluir_primeiro_acesso(client, cls.inep_b, temps[cls.inep_b])
        concluir_primeiro_acesso(client, cls.login_coord, temp_coord)
        concluir_primeiro_acesso(client, cls.cpf_prof, temp_prof)

        with cls.app.app_context():
            cls.gestor_a_id = Usuario.query.filter_by(login=cls.inep_a).one().id
            cls.gestor_b_id = Usuario.query.filter_by(login=cls.inep_b).one().id
            cls.coord_id = Usuario.query.filter_by(login=cls.login_coord).one().id
            cls.prof_user_id = Usuario.query.filter_by(login=cls.cpf_prof).one().id

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _acao(self):
        ConsultaAuditoriaTest._seq += 1
        return "CONSULTA_AUD_%04d" % ConsultaAuditoriaTest._seq

    def _inserir(self, **kwargs):
        from src import db
        from src.models import AuditLog
        dados = {
            "acao": kwargs.pop("acao", None) or self._acao(),
            "entidade": "RegistroNota",
            "entidade_id": 1,
            "data_hora": datetime(2026, 6, 15, 12, 0, 0),
        }
        dados.update(kwargs)
        with self.app.app_context():
            log = AuditLog(**dados)
            db.session.add(log)
            db.session.commit()
            return log.id, log.acao

    def _listar(self, **params):
        qs = urlencode({k: v for k, v in params.items() if v is not None})
        path = "/api/auditoria"
        if qs:
            path = "%s?%s" % (path, qs)
        return self.client.get(path)

    def _ids(self, data):
        return [item["id"] for item in (data or {}).get("items") or []]

    def test_a_dirigente_lista_logs(self):
        log_id, acao = self._inserir(
            escola_id=self.escola_a, usuario_id=self.admin_id, ip="203.0.113.10",
        )
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._listar(acao=acao)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, err = _json(resp)
        self.assertIsNone(err)
        for chave in ("items", "page", "per_page", "total", "pages"):
            self.assertIn(chave, data, chave)
        self.assertEqual(data["per_page"], PER_PAGE_PADRAO)
        self.assertEqual(data["total"], 1)
        self.assertEqual(self._ids(data), [log_id])
        item = data["items"][0]
        self.assertEqual(item["acao"], acao)
        self.assertEqual(item["usuario_id"], self.admin_id)
        self.assertTrue(item["usuario_nome"])
        self.assertEqual(item["escola_id"], self.escola_a)

    def test_b_secretaria_lista_logs(self):
        log_id, acao = self._inserir(escola_id=self.escola_b, usuario_id=self.sec_id)
        self._login(self.login_sec)
        resp = self._listar(acao=acao)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, err = _json(resp)
        self.assertIsNone(err)
        self.assertEqual(self._ids(data), [log_id])

    def test_c_gestor_lista_somente_propria_escola(self):
        id_a, acao_a = self._inserir(escola_id=self.escola_a, usuario_id=self.gestor_a_id)
        id_b, acao_b = self._inserir(escola_id=self.escola_b, usuario_id=self.gestor_b_id)
        self._login(self.inep_a)
        resp = self._listar()
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        for item in data["items"]:
            self.assertEqual(item["escola_id"], self.escola_a)
            self.assertIsNotNone(item["escola_id"])
            self.assertNotEqual(item["escola_id"], self.escola_b)
        resp_a = self._listar(acao=acao_a)
        self.assertEqual(self._ids(_json(resp_a)[0]), [id_a])
        resp_b = self._listar(acao=acao_b)
        self.assertEqual(_json(resp_b)[0]["total"], 0)

    def test_d_gestor_nao_recebe_outra_escola(self):
        _id_b, acao = self._inserir(escola_id=self.escola_b)
        self._login(self.inep_a)
        data, _ = _json(self._listar(acao=acao))
        self.assertEqual(data["total"], 0)
        self.assertNotIn(_id_b, self._ids(data))

    def test_e_gestor_nao_recebe_eventos_globais(self):
        log_id, acao = self._inserir(escola_id=None, usuario_id=self.admin_id)
        self._login(self.inep_a)
        data, _ = _json(self._listar(acao=acao))
        self.assertEqual(data["total"], 0)
        self.assertNotIn(log_id, self._ids(data))
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao))
        self.assertEqual(self._ids(data), [log_id])
        self.assertIsNone(data["items"][0]["escola_id"])

    def test_f_professor_recebe_403(self):
        self._login(self.cpf_prof)
        resp = self._listar()
        self.assertEqual(resp.status_code, 403)
        data, err = _json(resp)
        self.assertIsNone(data)
        self.assertTrue(err)
        resp = self.client.get("/api/auditoria/filtros")
        self.assertEqual(resp.status_code, 403)

    def test_g_coordenador_recebe_403(self):
        self._login(self.login_coord)
        resp = self._listar()
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get("/api/auditoria/filtros")
        self.assertEqual(resp.status_code, 403)

    def test_h_paginacao(self):
        acao = self._acao()
        ids = []
        base = datetime(2026, 3, 1, 8, 0, 0)
        for i in range(30):
            log_id, _ = self._inserir(
                acao=acao,
                data_hora=base + timedelta(minutes=i),
                escola_id=self.escola_a,
            )
            ids.append(log_id)
        self._login("admin", SENHA_DEMO_ADMIN)
        p1 = _json(self._listar(acao=acao, page=1, per_page=10))[0]
        p2 = _json(self._listar(acao=acao, page=2, per_page=10))[0]
        p3 = _json(self._listar(acao=acao, page=3, per_page=10))[0]
        self.assertEqual(p1["total"], 30)
        self.assertEqual(p1["pages"], 3)
        self.assertEqual(len(p1["items"]), 10)
        self.assertEqual(len(p2["items"]), 10)
        self.assertEqual(len(p3["items"]), 10)
        juntos = self._ids(p1) + self._ids(p2) + self._ids(p3)
        self.assertEqual(len(set(juntos)), 30)
        self.assertEqual(sorted(juntos), sorted(ids))

    def test_i_per_page_maximo(self):
        acao = self._acao()
        for i in range(12):
            self._inserir(acao=acao, entidade_id=i + 1, data_hora=datetime(2026, 4, 1, i, 0, 0))
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao, per_page=1000))
        self.assertEqual(data["per_page"], PER_PAGE_MAX)
        self.assertLessEqual(len(data["items"]), PER_PAGE_MAX)

    def test_j_ordem_desc(self):
        acao = self._acao()
        t0 = datetime(2026, 5, 1, 10, 0, 0)
        id1, _ = self._inserir(acao=acao, data_hora=t0, entidade_id=1)
        id2, _ = self._inserir(acao=acao, data_hora=t0 + timedelta(hours=1), entidade_id=2)
        id3, _ = self._inserir(acao=acao, data_hora=t0 + timedelta(hours=2), entidade_id=3)
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao, order_by="id", sort="asc"))
        self.assertEqual(self._ids(data), [id3, id2, id1])
        horas = [item["data_hora"] for item in data["items"]]
        self.assertEqual(horas, sorted(horas, reverse=True))

    def test_k_filtro_data_inicial(self):
        acao = self._acao()
        id_ant, _ = self._inserir(acao=acao, data_hora=datetime(2026, 1, 10, 12, 0, 0))
        id_novo, _ = self._inserir(acao=acao, data_hora=datetime(2026, 1, 20, 12, 0, 0))
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao, data_inicial="2026-01-15"))
        self.assertEqual(self._ids(data), [id_novo])
        self.assertNotIn(id_ant, self._ids(data))

    def test_l_filtro_data_final(self):
        acao = self._acao()
        id_ant, _ = self._inserir(acao=acao, data_hora=datetime(2026, 2, 5, 8, 0, 0))
        id_novo, _ = self._inserir(acao=acao, data_hora=datetime(2026, 2, 25, 8, 0, 0))
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao, data_final="2026-02-10"))
        self.assertEqual(self._ids(data), [id_ant])
        self.assertNotIn(id_novo, self._ids(data))

    def test_m_intervalo_invalido_400(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._listar(data_inicial="2026-03-10", data_final="2026-03-01")
        self.assertEqual(resp.status_code, 400)
        _, err = _json(resp)
        self.assertTrue(err)
        resp = self._listar(data_inicial="nao-e-data")
        self.assertEqual(resp.status_code, 400)
        resp = self._listar(data_final="2026-13-40")
        self.assertEqual(resp.status_code, 400)

    def test_n_filtro_acao(self):
        _, acao = self._inserir()
        self._inserir()
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao))
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["items"][0]["acao"], acao)

    def test_o_filtro_entidade(self):
        acao = self._acao()
        id_ok, _ = self._inserir(acao=acao, entidade="Turma", entidade_id=7)
        self._inserir(acao=acao, entidade="Escola", entidade_id=7)
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao, entidade="Turma"))
        self.assertEqual(self._ids(data), [id_ok])

    def test_p_filtro_entidade_id(self):
        acao = self._acao()
        id_ok, _ = self._inserir(acao=acao, entidade_id=4242)
        self._inserir(acao=acao, entidade_id=1)
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao, entidade_id=4242))
        self.assertEqual(self._ids(data), [id_ok])

    def test_q_filtro_usuario(self):
        acao = self._acao()
        id_ok, _ = self._inserir(acao=acao, usuario_id=self.admin_id)
        self._inserir(acao=acao, usuario_id=self.gestor_a_id)
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao, usuario_id=self.admin_id))
        self.assertEqual(self._ids(data), [id_ok])

    def test_r_filtro_escola_para_rede(self):
        acao = self._acao()
        id_a, _ = self._inserir(acao=acao, escola_id=self.escola_a)
        id_b, _ = self._inserir(acao=acao, escola_id=self.escola_b)
        id_g, _ = self._inserir(acao=acao, escola_id=None)
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao, escola_id=self.escola_a))
        self.assertEqual(self._ids(data), [id_a])
        self.assertNotIn(id_b, self._ids(data))
        self.assertNotIn(id_g, self._ids(data))
        data, _ = _json(self._listar(acao=acao))
        self.assertEqual(set(self._ids(data)), {id_a, id_b, id_g})

    def test_s_gestor_escola_alheia_403(self):
        self._login(self.inep_a)
        resp = self._listar(escola_id=self.escola_b)
        self.assertEqual(resp.status_code, 403)
        data, err = _json(resp)
        self.assertIsNone(data)
        self.assertTrue(err)

    def test_idor_entidade_e_request_id(self):
        rid = "rid-idor-escola-b"
        log_b, _ = self._inserir(
            escola_id=self.escola_b,
            entidade="Turma",
            entidade_id=88881,
            request_id=rid,
        )
        glob_id, acao_g = self._inserir(escola_id=None, request_id="rid-global-rede")
        self._login(self.inep_a)
        data, _ = _json(self._listar(entidade_id=88881, entidade="Turma"))
        self.assertEqual(data["total"], 0)
        self.assertNotIn(log_b, self._ids(data))
        data, _ = _json(self._listar(request_id=rid))
        self.assertEqual(data["total"], 0)
        data, _ = _json(self._listar(request_id="rid-global-rede"))
        self.assertEqual(data["total"], 0)
        self.assertNotIn(glob_id, self._ids(data))
        resp = self._listar(escola_id=self.escola_b, acao=acao_g)
        self.assertEqual(resp.status_code, 403)

    def test_t_usuario_removido_nao_quebra(self):
        from src import db
        from src.models import Usuario
        acao = self._acao()
        with self.app.app_context():
            u = Usuario(
                login="removido_aud_%s" % acao[-4:],
                nome="Removido Aud",
                papel="professor",
                senha_hash="x",
                primeiro_acesso=False,
            )
            db.session.add(u)
            db.session.commit()
            uid = u.id
        log_id, _ = self._inserir(
            acao=acao, usuario_id=uid, escola_id=self.escola_a,
        )
        with self.app.app_context():
            alvo = db.session.get(Usuario, uid)
            db.session.delete(alvo)
            db.session.commit()
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._listar(acao=acao)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        item = _json(resp)[0]["items"][0]
        self.assertEqual(item["id"], log_id)
        if item["usuario_id"] is None:
            self.assertIsNone(item["usuario_nome"])
        else:
            self.assertIn("Usuário removido", item["usuario_nome"])
            self.assertIn(str(uid), item["usuario_nome"])

    def test_u_escola_null_nao_quebra(self):
        log_id, acao = self._inserir(escola_id=None, usuario_id=None)
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._listar(acao=acao)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        item = _json(resp)[0]["items"][0]
        self.assertEqual(item["id"], log_id)
        self.assertIsNone(item["escola_id"])
        self.assertIsNone(item["escola_nome"])
        self.assertIsNone(item["usuario_id"])
        self.assertIsNone(item["usuario_nome"])

    def test_v_w_payload_sanitizado(self):
        acao = self._acao()
        self._inserir(
            acao=acao,
            dados_anteriores={
                "senha": "nao-vazar",
                "password_hash": "hash-secreto",
                "token": "tok-secreto",
                "nota": 7.5,
            },
            dados_novos={
                "access_token": "abc",
                "client_secret": "xyz",
                "Password": "outra",
                "secretaria": "valor legitimo",
                "nome": "ok",
            },
        )
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao))
        item = data["items"][0]
        blob = _texto(item).casefold()
        self.assertNotIn("nao-vazar", blob)
        self.assertNotIn("hash-secreto", blob)
        self.assertNotIn("tok-secreto", blob)
        self.assertNotIn("password_hash", blob)
        self.assertNotIn("access_token", blob)
        self.assertNotIn("client_secret", blob)
        self.assertNotIn("senha", item["dados_anteriores"] or {})
        self.assertEqual((item["dados_anteriores"] or {}).get("nota"), 7.5)
        self.assertEqual((item["dados_novos"] or {}).get("nome"), "ok")
        self.assertEqual((item["dados_novos"] or {}).get("secretaria"), "valor legitimo")

    def test_x_gestor_nao_recebe_ip(self):
        _, acao = self._inserir(
            escola_id=self.escola_a, ip="198.51.100.20", usuario_id=self.admin_id,
        )
        self._login(self.inep_a)
        item = _json(self._listar(acao=acao))[0]["items"][0]
        self.assertNotIn("ip", item)
        blob = _texto(item)
        self.assertNotIn("198.51.100.20", blob)

    def test_y_dirigente_secretaria_recebem_ip(self):
        _, acao = self._inserir(
            escola_id=self.escola_a, ip="192.0.2.55", usuario_id=self.admin_id,
        )
        self._login("admin", SENHA_DEMO_ADMIN)
        item = _json(self._listar(acao=acao))[0]["items"][0]
        self.assertEqual(item.get("ip"), "192.0.2.55")
        self._login(self.login_sec)
        item = _json(self._listar(acao=acao))[0]["items"][0]
        self.assertEqual(item.get("ip"), "192.0.2.55")

    def test_z_nenhuma_rota_mutacao(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        permitidas = {"/api/auditoria", "/api/auditoria/filtros"}
        for rule in self.app.url_map.iter_rules():
            texto = "%s %s" % (rule.endpoint or "", rule.rule or "")
            if "audit" in texto.lower() or "auditoria" in texto.lower():
                self.assertIn(rule.rule, permitidas)
                metodos = set(rule.methods or [])
                self.assertIn("GET", metodos)
                self.assertFalse(metodos & {"POST", "PUT", "PATCH", "DELETE"})
        for metodo, path in (
            ("POST", "/api/auditoria"),
            ("PUT", "/api/auditoria"),
            ("PATCH", "/api/auditoria"),
            ("DELETE", "/api/auditoria"),
            ("PUT", "/api/auditoria/1"),
            ("DELETE", "/api/auditoria/filtros"),
        ):
            resp = self.client.open(path, method=metodo)
            self.assertIn(resp.status_code, (404, 405), path)
        resp = self.client.delete("/api/excluir/audit_log/1")
        self.assertNotEqual(resp.status_code, 200)
        resp = self.client.put("/api/editar/audit_log/1", json={"acao": "x"})
        self.assertNotEqual(resp.status_code, 200)

    def test_nao_autenticado_401(self):
        resp = self.client.get("/api/auditoria")
        self.assertEqual(resp.status_code, 401)
        resp = self.client.get("/api/auditoria/filtros")
        self.assertEqual(resp.status_code, 401)

    def test_get_nao_gera_audit_log(self):
        from src.models import AuditLog
        with self.app.app_context():
            n = AuditLog.query.count()
        self._login("admin", SENHA_DEMO_ADMIN)
        self.assertEqual(self._listar().status_code, 200)
        self.assertEqual(self.client.get("/api/auditoria/filtros").status_code, 200)
        with self.app.app_context():
            self.assertEqual(AuditLog.query.count(), n)

    def test_filtros_rede_e_escola(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        data, err = _json(self.client.get("/api/auditoria/filtros"))
        self.assertIsNone(err)
        self.assertEqual(data["escopo"], "rede")
        self.assertTrue(data["exibe_ip"])
        self.assertTrue(data["acoes"])
        self.assertTrue(any(a["valor"] == "NOTA_ALTERADA" for a in data["acoes"]))
        ids = {e["id"] for e in data["escolas"]}
        self.assertIn(self.escola_a, ids)
        self.assertIn(self.escola_b, ids)
        self._login(self.inep_a)
        data, _ = _json(self.client.get("/api/auditoria/filtros"))
        self.assertEqual(data["escopo"], "escola")
        self.assertFalse(data["exibe_ip"])
        self.assertNotIn("escolas", data)

    def test_gestor_escola_propria_ok(self):
        log_id, acao = self._inserir(escola_id=self.escola_a)
        self._login(self.inep_a)
        resp = self._listar(acao=acao, escola_id=self.escola_a)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._ids(_json(resp)[0]), [log_id])

    def test_request_id_exato(self):
        rid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        log_id, acao = self._inserir(request_id=rid, escola_id=self.escola_a)
        self._inserir(acao=acao, request_id="outra", escola_id=self.escola_a)
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._listar(acao=acao, request_id=rid))
        self.assertEqual(self._ids(data), [log_id])


class ConsultaAuditoriaFrontendEstaticoTest(unittest.TestCase):
    def test_pagina_rota_menu_e_somente_leitura(self):
        app_js = (RAIZ / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
        nav_js = (RAIZ / "frontend" / "src" / "nav.js").read_text(encoding="utf-8")
        pagina = (RAIZ / "frontend" / "src" / "pages" / "Auditoria.jsx").read_text(encoding="utf-8")
        self.assertIn('path="/auditoria"', app_js)
        self.assertIn("Auditoria", nav_js)
        bloco_prof = nav_js.split('papel === "professor"')[1].split("if (papel")[0]
        bloco_coord = nav_js.split("coordenador_pedagogico")[1].split("if (papel")[0]
        bloco_gestor = nav_js.split("gestor_escolar")[1].split("if (papel")[0]
        bloco_rede = nav_js.split('papel === "dirigente"')[1]
        self.assertNotIn("/auditoria", bloco_prof)
        self.assertNotIn("/auditoria", bloco_coord)
        self.assertIn("/auditoria", bloco_gestor)
        self.assertIn("/auditoria", bloco_rede)
        self.assertNotIn("dangerouslySetInnerHTML", pagina)
        self.assertNotIn("Editar JSON", pagina)
        self.assertNotIn("Apagar", pagina)
        self.assertNotIn("Limpar logs", pagina)
        self.assertNotIn("method: \"PUT\"", pagina)
        self.assertNotIn("method: \"DELETE\"", pagina)
        self.assertIn("/api/auditoria", pagina)
        self.assertIn("Nenhum registro de auditoria encontrado.", pagina)
        self.assertIn("Anterior", pagina)
        self.assertIn("Próxima", pagina)
        self.assertIn("Detalhes", pagina)
        self.assertIn("json-view", pagina)
        self.assertIn("onSubmit={filtrar}", pagina)


@unittest.skipUnless(
    URI_PG,
    "EDUCAMAIS_TEST_DATABASE_URI ausente; PostgreSQL de testes pulado (não se usa URI de migração/produção).",
)
class ConsultaAuditoriaPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = os.environ.copy()
        from src.config import Config
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        cls._old_opts = Config.SQLALCHEMY_ENGINE_OPTIONS
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-pg-consulta-audit"
        os.environ["EDUCAMAIS_DEMO"] = "1"
        os.environ.pop("EDUCAMAIS_DEBUG", None)
        apontar_banco_teste(URI_PG)
        cls.app = criar_app_migrada()
        cls.app.testing = True

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

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _inserir(self, **kwargs):
        from src import db
        from src.models import AuditLog
        dados = {
            "acao": "CONSULTA_PG",
            "entidade": "Usuario",
            "entidade_id": 1,
            "data_hora": datetime(2026, 7, 1, 9, 0, 0),
        }
        dados.update(kwargs)
        with self.app.app_context():
            log = AuditLog(**dados)
            db.session.add(log)
            db.session.commit()
            return log.id

    def test_pg_acl_paginacao_sanitizacao_ip(self):
        from src.models import Usuario
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola PG Consulta",
            "inep": "65000001",
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == "65000001")
        concluir_primeiro_acesso(self.client, "65000001", temp)

        with self.app.app_context():
            from src import db
            self.assertEqual(db.engine.dialect.name, "postgresql")
            admin_id = Usuario.query.filter_by(login="admin").one().id

        id_escola = self._inserir(
            acao="CONSULTA_PG_ESC", escola_id=escola_id, ip="203.0.113.9",
            usuario_id=admin_id, dados_novos={"senha": "nao", "ok": 1},
        )
        id_global = self._inserir(
            acao="CONSULTA_PG_GLOB", escola_id=None, ip="203.0.113.8",
        )
        ids_pag = [
            self._inserir(
                acao="CONSULTA_PG_PAGE",
                escola_id=escola_id,
                data_hora=datetime(2026, 7, 2, i, 0, 0),
            )
            for i in range(6)
        ]

        self._login("admin", SENHA_DEMO_ADMIN)
        data, err = _json(self.client.get("/api/auditoria?acao=CONSULTA_PG_ESC"))
        self.assertIsNone(err)
        self.assertEqual(data["items"][0]["id"], id_escola)
        self.assertEqual(data["items"][0].get("ip"), "203.0.113.9")
        self.assertNotIn("senha", data["items"][0]["dados_novos"] or {})
        self.assertEqual((data["items"][0]["dados_novos"] or {}).get("ok"), 1)

        page = _json(self.client.get("/api/auditoria?acao=CONSULTA_PG_PAGE&per_page=2&page=1"))[0]
        self.assertEqual(page["total"], 6)
        self.assertEqual(page["pages"], 3)
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["items"][0]["id"], ids_pag[-1])

        resp = self.client.get("/api/auditoria?data_inicial=2026-08-01&data_final=2026-07-01")
        self.assertEqual(resp.status_code, 400)

        self._login("65000001")
        data, _ = _json(self.client.get("/api/auditoria?acao=CONSULTA_PG_ESC"))
        self.assertEqual(data["items"][0]["id"], id_escola)
        self.assertNotIn("ip", data["items"][0])
        data, _ = _json(self.client.get("/api/auditoria?acao=CONSULTA_PG_GLOB"))
        self.assertEqual(data["total"], 0)
        self.assertNotIn(id_global, [i["id"] for i in data["items"]])
        resp = self.client.get("/api/auditoria?escola_id=%s" % (escola_id + 99))
        self.assertEqual(resp.status_code, 403)
