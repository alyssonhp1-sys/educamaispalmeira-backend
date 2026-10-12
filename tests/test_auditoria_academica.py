"""Estágio 4 / Bloco 2: auditoria das operações acadêmicas críticas."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.auditoria import (
    ALUNO_ENTURMADO,
    ALUNO_INATIVADO,
    ALUNO_REMATRICULADO,
    FALTA_ALTERADA,
    FALTA_CRIADA,
    FREQUENCIA_ALTERADA,
    FREQUENCIA_CRIADA,
    MATRICULA_CRIADA,
    NOTA_ALTERADA,
    NOTA_CRIADA,
    NOTA_REMOVIDA,
    REGISTRO_AULA_CRIADO,
    REGISTRO_AULA_INVALIDADO,
    REGISTRO_AULA_VALIDADO,
    RELATORIO_DESCRITIVO_ALTERADO,
    RELATORIO_DESCRITIVO_CRIADO,
    RELATORIO_DESCRITIVO_REMOVIDO,
    TURMA_ENCERRADA,
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
)

PARECER_SENSIVEL = (
    "A criança reside na Rua das Palmeiras 100 com a responsável Maria "
    "Silva telefone 11999999999 e CPF 11144477735."
)

EIXO_A = "Eu, o outro e o nós"


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


def _assert_sem_pii(test, logs, texto_parecer=None):
    for log in logs:
        chaves = []
        _coletar_chaves(_como_dict(log.dados_anteriores), chaves)
        _coletar_chaves(_como_dict(log.dados_novos), chaves)
        for proibida in CHAVES_PII:
            test.assertNotIn(proibida, chaves, "%s %s" % (log.acao, proibida))
        blob = _texto_log(log).casefold()
        for trecho in ("senha_hash", "password_hash"):
            test.assertNotIn(trecho, blob, log.acao)
        if texto_parecer:
            test.assertNotIn(texto_parecer, blob)
            test.assertNotIn(texto_parecer.casefold(), blob)


class AuditoriaAcademicaTest(unittest.TestCase):
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
        from src.models import Disciplina
        with cls.app.app_context():
            cls.mat = Disciplina.query.filter_by(nome="Matemática").first().id
            cls.port = Disciplina.query.filter_by(nome="Português").first().id

        cls.inep_a = "94000001"
        cls.inep_b = "94000002"
        cls.cpf_prof_a = "94011111111"
        cls.cpf_prof_ei = "94022222222"
        cls.cpf_prof_b = "94033333333"
        cls.login_coord_a = "coord940a"

        temps = {}
        for nome, inep in (("Escola Audit Norte", cls.inep_a), ("Escola Audit Sul", cls.inep_b)):
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

        resp = client.post(
            f"/api/escolas/{cls.escola_a}/coordenadores",
            json={"login": cls.login_coord_a, "nome": "Coord Audit"},
        )
        temps[cls.login_coord_a] = senha_temporaria(resp)
        for nome, cpf, escola_id in (
            ("Prof Audit A", cls.cpf_prof_a, cls.escola_a),
            ("Prof Audit EI", cls.cpf_prof_ei, cls.escola_a),
            ("Prof Audit B", cls.cpf_prof_b, cls.escola_b),
        ):
            resp = client.post("/api/professores", json={
                "nome": nome, "cpf": cpf, "escola_id": escola_id,
            })
            temps[cpf] = senha_temporaria(resp)
        for login, temp in temps.items():
            concluir_primeiro_acesso(client, login, temp)

        cls._login_client(client, cls.inep_a, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "6º Ano Audit", "ano_letivo": 2026, "etapa": "fundamental"})
        client.post("/api/turmas", json={"nome": "Maternal Audit", "ano_letivo": 2026, "etapa": "infantil"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_a = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º Ano Audit")
        cls.turma_ei = next(t["id"] for t in dash["turmas"] if t["nome"] == "Maternal Audit")
        cls.prof_a_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_a)
        cls.prof_ei_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_ei)

        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_a_id, "turma_id": cls.turma_a, "disciplina_id": cls.mat,
            "carga_horaria_prevista": 40,
        })
        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_ei_id, "turma_id": cls.turma_ei,
            "carga_horaria_prevista": 20,
        })

        cls._login_client(client, cls.inep_b, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "6º Ano Sul Audit", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_b = dash["turmas"][0]["id"]
        cls.prof_b_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_b)
        client.post("/api/alocacoes", json={
            "professor_id": cls.prof_b_id, "turma_id": cls.turma_b, "disciplina_id": cls.mat,
        })

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _proximo(self):
        AuditoriaAcademicaTest._seq += 1
        return AuditoriaAcademicaTest._seq

    def _cpf(self, prefixo="941"):
        n = self._proximo()
        return ("%s%08d" % (prefixo, n))[:11]

    def _criar_aluno(self, turma_id=None, nome=None, **extra):
        n = self._proximo()
        payload = {
            "nome": nome or ("Aluno Audit %s" % n),
            "cpf": extra.pop("cpf", self._cpf()),
            "nome_mae": extra.pop("nome_mae", "Mãe Audit"),
            "endereco": extra.pop("endereco", "Rua Teste 10"),
            "telefone": extra.pop("telefone", "11988887777"),
        }
        if turma_id is not None:
            payload["turma_id"] = turma_id
        payload.update(extra)
        resp = self.client.post("/api/alunos", json=payload)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data = _json(resp)[0]
        data["cpf"] = payload["cpf"]
        data["nome"] = payload["nome"]
        return data

    def _url_notas(self, turma_id=None, disc_id=None):
        return "/api/turmas/%s/disciplinas/%s/notas" % (
            turma_id or self.turma_a, disc_id or self.mat,
        )

    def _url_chamada(self, turma_id=None, disc_id=None):
        return "/api/turmas/%s/disciplinas/%s/chamada" % (
            turma_id or self.turma_a, disc_id or self.mat,
        )

    def _url_aula(self, turma_id=None, disc_id=None):
        return "/api/turmas/%s/disciplinas/%s/registros-aula" % (
            turma_id or self.turma_a, disc_id if disc_id is not None else self.mat,
        )

    def _logs(self, acao=None):
        from src.models import AuditLog
        q = AuditLog.query
        if acao:
            q = q.filter_by(acao=acao)
        return q.order_by(AuditLog.id.asc()).all()

    def _contar(self, acao=None):
        from src.models import AuditLog
        q = AuditLog.query
        if acao:
            q = q.filter_by(acao=acao)
        return q.count()

    def _ultimo(self, acao):
        logs = self._logs(acao)
        self.assertTrue(logs, acao)
        return logs[-1]

    def _mat_do_aluno(self, aluno_id, turma_id=None):
        from src.models import Matricula
        q = Matricula.query.filter_by(aluno_id=aluno_id)
        if turma_id is not None:
            q = q.filter_by(turma_id=turma_id)
        return q.order_by(Matricula.id.desc()).first()

    def test_a_criacao_de_nota(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        with self.app.app_context():
            antes = self._contar(NOTA_CRIADA)
        resp = self.client.post(self._url_notas(), json=[{
            "aluno_id": aluno["id"], "valor": 8, "tipo": "nota", "bimestre": 11,
        }])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(NOTA_CRIADA), antes + 1)
            log = self._ultimo(NOTA_CRIADA)
            novos = _como_dict(log.dados_novos)
            mat = self._mat_do_aluno(aluno["id"], self.turma_a)
            self.assertEqual(novos["matricula_id"], mat.id)
            self.assertEqual(novos["disciplina_id"], self.mat)
            self.assertEqual(novos["bimestre"], 11)
            self.assertEqual(novos["campo"], "n1")
            self.assertEqual(novos["valor"], 8)
            self.assertIsNone(log.dados_anteriores)
            self.assertEqual(log.entidade, "RegistroNota")
            self.assertEqual(log.usuario_id, self._usuario_id(self.cpf_prof_a))

    def _usuario_id(self, login):
        from src.models import Usuario
        return Usuario.query.filter_by(login=login).one().id

    def test_b_alteracao_de_nota(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        self.client.post(self._url_notas(), json=[{
            "aluno_id": aluno["id"], "valor": 6, "tipo": "nota", "bimestre": 12,
        }])
        with self.app.app_context():
            antes = self._contar(NOTA_ALTERADA)
        resp = self.client.post(self._url_notas(), json=[{
            "aluno_id": aluno["id"], "valor": 7.5, "tipo": "nota", "bimestre": 12,
        }])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(NOTA_ALTERADA), antes + 1)
            log = self._ultimo(NOTA_ALTERADA)
            ant = _como_dict(log.dados_anteriores)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(ant["valor"], 6)
            self.assertEqual(novos["valor"], 7.5)
            self.assertEqual(novos["campo"], "n2")

    def test_c_remocao_null_de_nota(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        self.client.post(self._url_notas(), json=[{
            "aluno_id": aluno["id"], "valor": 9, "tipo": "nota", "bimestre": 21,
        }])
        with self.app.app_context():
            antes = self._contar(NOTA_REMOVIDA)
        resp = self.client.post(self._url_notas(), json=[{
            "aluno_id": aluno["id"], "valor": None, "tipo": "nota", "bimestre": 21,
        }])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src.models import RegistroNota
            mat = self._mat_do_aluno(aluno["id"], self.turma_a)
            self.assertIsNone(RegistroNota.query.filter_by(
                matricula_id=mat.id, disciplina_id=self.mat, bimestre=21,
            ).first())
            self.assertEqual(self._contar(NOTA_REMOVIDA), antes + 1)
            log = self._ultimo(NOTA_REMOVIDA)
            self.assertEqual(_como_dict(log.dados_anteriores)["valor"], 9)
            self.assertIsNone(_como_dict(log.dados_novos)["valor"])

    def test_d_nota_idempotente_nao_gera_log(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        payload = [{"aluno_id": aluno["id"], "valor": 8, "tipo": "nota", "bimestre": 22}]
        self.assertEqual(self.client.post(self._url_notas(), json=payload).status_code, 200)
        with self.app.app_context():
            total = self._contar()
            n_alt = self._contar(NOTA_ALTERADA)
        resp = self.client.post(self._url_notas(), json=payload)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(), total)
            self.assertEqual(self._contar(NOTA_ALTERADA), n_alt)

    def test_recuperacao_e_exame_usam_mesma_celula(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url_notas(), json=[
            {"aluno_id": aluno["id"], "valor": 5, "tipo": "nota", "bimestre": 13},
            {"aluno_id": aluno["id"], "valor": 6, "tipo": "nota", "bimestre": 99},
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            logs = [l for l in self._logs(NOTA_CRIADA) if _como_dict(l.dados_novos)["matricula_id"] == self._mat_do_aluno(aluno["id"]).id]
            campos = { _como_dict(l.dados_novos)["campo"] for l in logs }
            bims = { _como_dict(l.dados_novos)["bimestre"] for l in logs }
            self.assertIn("rp1", campos)
            self.assertIn("pf", campos)
            self.assertIn(13, bims)
            self.assertIn(99, bims)

    def test_e_faltas(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        with self.app.app_context():
            antes_c = self._contar(FALTA_CRIADA)
            antes_a = self._contar(FALTA_ALTERADA)
        self.assertEqual(self.client.post(self._url_notas(), json=[{
            "aluno_id": aluno["id"], "valor": 2, "tipo": "falta", "bimestre": 1,
        }]).status_code, 200)
        self.assertEqual(self.client.post(self._url_notas(), json=[{
            "aluno_id": aluno["id"], "valor": 4, "tipo": "falta", "bimestre": 1,
        }]).status_code, 200)
        with self.app.app_context():
            self.assertEqual(self._contar(FALTA_CRIADA), antes_c + 1)
            self.assertEqual(self._contar(FALTA_ALTERADA), antes_a + 1)
            criacao = self._ultimo(FALTA_CRIADA)
            alt = self._ultimo(FALTA_ALTERADA)
            self.assertEqual(_como_dict(criacao.dados_novos)["quantidade"], 2)
            self.assertEqual(_como_dict(alt.dados_anteriores)["quantidade"], 2)
            self.assertEqual(_como_dict(alt.dados_novos)["quantidade"], 4)
            self.assertEqual(_como_dict(alt.dados_novos)["bimestre"], 1)

    def test_f_falta_idempotente(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        payload = [{"aluno_id": aluno["id"], "valor": 3, "tipo": "falta", "bimestre": 2}]
        self.assertEqual(self.client.post(self._url_notas(), json=payload).status_code, 200)
        with self.app.app_context():
            total = self._contar()
        self.assertEqual(self.client.post(self._url_notas(), json=payload).status_code, 200)
        with self.app.app_context():
            self.assertEqual(self._contar(), total)

    def test_g_criacao_de_registro_de_aula(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        with self.app.app_context():
            antes = self._contar(REGISTRO_AULA_CRIADO)
            n_freq = self._contar(FREQUENCIA_CRIADA)
        resp = self.client.post(self._url_aula(), json={
            "data": "2026-06-01",
            "bimestre": 2,
            "quantidade_aulas": 2,
            "conteudo": "Números naturais",
            "metodologia": "Expositiva",
            "lancamentos": [{"aluno_id": aluno["id"], "situacao": "PRESENTE"}],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(REGISTRO_AULA_CRIADO), antes + 1)
            self.assertEqual(self._contar(FREQUENCIA_CRIADA), n_freq)
            log = self._ultimo(REGISTRO_AULA_CRIADO)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["turma_id"], self.turma_a)
            self.assertEqual(novos["n_faltas"], 0)
            self.assertNotIn("alunos", novos)
            self.assertNotEqual(novos.get("conteudo"), "Números naturais")
            self.assertEqual(novos["conteudo"]["havia_conteudo"], True)

    def test_h_alteracao_p_para_f(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        payload_p = {
            "data": "2026-06-02", "bimestre": 2,
            "lancamentos": [{"aluno_id": aluno["id"], "situacao": "PRESENTE"}],
        }
        self.assertEqual(self.client.post(self._url_chamada(), json=payload_p).status_code, 200)
        with self.app.app_context():
            antes = self._contar(FREQUENCIA_ALTERADA)
        payload_f = {
            "data": "2026-06-02", "bimestre": 2,
            "lancamentos": [{"aluno_id": aluno["id"], "situacao": "FALTA"}],
        }
        self.assertEqual(self.client.post(self._url_chamada(), json=payload_f).status_code, 200)
        with self.app.app_context():
            self.assertEqual(self._contar(FREQUENCIA_ALTERADA), antes + 1)
            log = self._ultimo(FREQUENCIA_ALTERADA)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["situacao_anterior"], "PRESENTE")
            self.assertEqual(novos["situacao_nova"], "FALTA")
            mat = self._mat_do_aluno(aluno["id"], self.turma_a)
            self.assertEqual(novos["matricula_id"], mat.id)

    def test_i_alteracao_f_para_p(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        self.client.post(self._url_chamada(), json={
            "data": "2026-06-03", "bimestre": 2,
            "lancamentos": [{"aluno_id": aluno["id"], "situacao": "FALTA"}],
        })
        with self.app.app_context():
            antes = self._contar(FREQUENCIA_ALTERADA)
        self.assertEqual(self.client.post(self._url_chamada(), json={
            "data": "2026-06-03", "bimestre": 2,
            "lancamentos": [{"aluno_id": aluno["id"], "situacao": "PRESENTE"}],
        }).status_code, 200)
        with self.app.app_context():
            self.assertEqual(self._contar(FREQUENCIA_ALTERADA), antes + 1)
            log = self._ultimo(FREQUENCIA_ALTERADA)
            self.assertEqual(_como_dict(log.dados_novos)["situacao_anterior"], "FALTA")
            self.assertEqual(_como_dict(log.dados_novos)["situacao_nova"], "PRESENTE")

    def test_j_frequencia_idempotente(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        payload = {
            "data": "2026-06-04", "bimestre": 2,
            "lancamentos": [{"aluno_id": aluno["id"], "situacao": "PRESENTE"}],
        }
        self.assertEqual(self.client.post(self._url_chamada(), json=payload).status_code, 200)
        with self.app.app_context():
            total = self._contar()
        self.assertEqual(self.client.post(self._url_chamada(), json=payload).status_code, 200)
        with self.app.app_context():
            self.assertEqual(self._contar(), total)

    def test_aula_com_falta_inicial_gera_frequencia_criada(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        with self.app.app_context():
            antes = self._contar(FREQUENCIA_CRIADA)
        resp = self.client.post(self._url_aula(), json={
            "data": "2026-06-05",
            "bimestre": 2,
            "quantidade_aulas": 1,
            "conteudo": "Aula com falta",
            "metodologia": "Prática",
            "lancamentos": [{"aluno_id": aluno["id"], "situacao": "FALTA"}],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aula_id = _json(resp)[0]["id"]
        with self.app.app_context():
            self.assertEqual(self._contar(FREQUENCIA_CRIADA), antes + 1)
            log = self._ultimo(FREQUENCIA_CRIADA)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["registro_aula_id"], aula_id)
            self.assertEqual(novos["situacao_nova"], "FALTA")

    def test_k_validacao_de_aula(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url_aula(), json={
            "data": "2026-06-06", "bimestre": 2, "quantidade_aulas": 1,
            "conteudo": "Validar", "metodologia": "Expositiva",
            "lancamentos": [{"aluno_id": aluno["id"], "situacao": "PRESENTE"}],
        })
        aula_id = _json(resp)[0]["id"]
        with self.app.app_context():
            antes = self._contar(REGISTRO_AULA_VALIDADO)
        self._login(self.login_coord_a)
        resp = self.client.post(
            "/api/coordenacao/registros-aula/%s" % aula_id,
            json={"status": "validada"},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(REGISTRO_AULA_VALIDADO), antes + 1)
            log = self._ultimo(REGISTRO_AULA_VALIDADO)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["registro_aula_id"], aula_id)
            self.assertEqual(novos["status_anterior"], "pendente")
            self.assertEqual(novos["status_novo"], "validada")
            self.assertEqual(log.usuario_id, self._usuario_id(self.login_coord_a))
        self._login(self.login_coord_a)
        self.client.post(
            "/api/coordenacao/registros-aula/%s" % aula_id,
            json={"status": "validada"},
        )
        with self.app.app_context():
            self.assertEqual(self._contar(REGISTRO_AULA_VALIDADO), antes + 1)

    def test_l_invalidacao_de_aula(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        resp = self.client.post(self._url_aula(), json={
            "data": "2026-06-07", "bimestre": 2, "quantidade_aulas": 1,
            "conteudo": "Invalidar", "metodologia": "Expositiva",
            "lancamentos": [{"aluno_id": aluno["id"], "situacao": "PRESENTE"}],
        })
        aula_id = _json(resp)[0]["id"]
        motivo = "Conteúdo incompatível com o aluno João da Silva CPF 11144477735."
        self._login(self.login_coord_a)
        resp = self.client.post(
            "/api/coordenacao/registros-aula/%s" % aula_id,
            json={"status": "invalidada", "motivo": motivo},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            log = self._ultimo(REGISTRO_AULA_INVALIDADO)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["status_novo"], "invalidada")
            self.assertTrue(novos["motivo_informado"])
            self.assertEqual(novos["tamanho_motivo"], len(motivo))
            self.assertNotIn("motivo", novos)
            self.assertNotIn(motivo, _texto_log(log))

    def test_m_criacao_de_matricula(self):
        self._login(self.inep_a)
        with self.app.app_context():
            antes = self._contar(MATRICULA_CRIADA)
            n_ent = self._contar(ALUNO_ENTURMADO)
        aluno = self._criar_aluno(self.turma_a)
        with self.app.app_context():
            self.assertEqual(self._contar(MATRICULA_CRIADA), antes + 1)
            self.assertEqual(self._contar(ALUNO_ENTURMADO), n_ent)
            log = self._ultimo(MATRICULA_CRIADA)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["aluno_id"], aluno["id"])
            self.assertEqual(novos["turma_id"], self.turma_a)
            self.assertEqual(novos["situacao_vinculo"], "cursando")
            self.assertEqual(novos["ano_letivo"], 2026)
            self.assertNotIn("cpf", json.dumps(novos))

    def test_n_enturmacao(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno()
        with self.app.app_context():
            antes = self._contar(ALUNO_ENTURMADO)
            n_rem = self._contar(ALUNO_REMATRICULADO)
        resp = self.client.post(
            "/api/alunos/%s/enturmar" % aluno["id"],
            json={"turma_id": self.turma_a},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(ALUNO_ENTURMADO), antes + 1)
            self.assertEqual(self._contar(ALUNO_REMATRICULADO), n_rem)
            log = self._ultimo(ALUNO_ENTURMADO)
            self.assertEqual(_como_dict(log.dados_novos)["turma_id"], self.turma_a)

    def test_o_rematricula(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Encerrar Audit %s" % self._proximo(),
            "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_old = next(t["id"] for t in dash["turmas"] if t["nome"].startswith("Turma Encerrar Audit"))
        aluno = self._criar_aluno(turma_old)
        self.assertEqual(self.client.post("/api/turmas/%s/encerrar" % turma_old).status_code, 200)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Nova Audit %s" % self._proximo(),
            "ano_letivo": 2027, "etapa": "fundamental",
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_nova = next(t["id"] for t in dash["turmas"] if t["nome"].startswith("Turma Nova Audit"))
        with self.app.app_context():
            antes_rem = self._contar(ALUNO_REMATRICULADO)
            n_ent = self._contar(ALUNO_ENTURMADO)
        resp = self.client.post(
            "/api/alunos/%s/enturmar" % aluno["id"],
            json={"turma_id": turma_nova},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(ALUNO_REMATRICULADO), antes_rem + 1)
            self.assertEqual(self._contar(ALUNO_ENTURMADO), n_ent)
            log = self._ultimo(ALUNO_REMATRICULADO)
            novos = _como_dict(log.dados_novos)
            ant = _como_dict(log.dados_anteriores)
            self.assertEqual(novos["turma_id"], turma_nova)
            self.assertEqual(novos["ano_letivo"], 2027)
            self.assertEqual(ant["situacao_vinculo"], "concluida")
            self.assertNotEqual(ant["matricula_id"], novos["matricula_id"])

    def test_p_inativacao(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        with self.app.app_context():
            antes = self._contar(ALUNO_INATIVADO)
        resp = self.client.post("/api/alunos/%s/inativar" % aluno["id"])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(ALUNO_INATIVADO), antes + 1)
            log = self._ultimo(ALUNO_INATIVADO)
            ant = _como_dict(log.dados_anteriores)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(ant["situacao"], "ativo")
            self.assertEqual(novos["situacao"], "inativo")
            self.assertEqual(novos["situacao_vinculo"], "inativo")
            self.assertIsNone(novos["turma_id"])
        self.client.post("/api/alunos/%s/inativar" % aluno["id"])
        with self.app.app_context():
            self.assertEqual(self._contar(ALUNO_INATIVADO), antes + 1)

    def test_q_relatorio_ei_sem_texto_integral(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_ei)
        self._login(self.cpf_prof_ei)
        with self.app.app_context():
            antes = self._contar(RELATORIO_DESCRITIVO_CRIADO)
        resp = self.client.post(
            "/api/turmas/%s/disciplinas/0/notas" % self.turma_ei,
            json=[{
                "aluno_id": aluno["id"],
                "eixo": EIXO_A,
                "bimestre": 1,
                "avaliacao": "Em Desenvolvimento",
                "observacao": PARECER_SENSIVEL,
            }],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(RELATORIO_DESCRITIVO_CRIADO), antes + 1)
            log = self._ultimo(RELATORIO_DESCRITIVO_CRIADO)
            novos = _como_dict(log.dados_novos)
            self.assertEqual(novos["eixo"], EIXO_A)
            self.assertEqual(novos["bimestre"], 1)
            self.assertEqual(novos["avaliacao"], "Em Desenvolvimento")
            self.assertTrue(novos["observacao"]["havia_conteudo"])
            self.assertEqual(novos["observacao"]["tamanho_conteudo"], len(PARECER_SENSIVEL))
            self.assertTrue(novos["observacao"]["hash_sha256"])
            self.assertNotIn(PARECER_SENSIVEL, _texto_log(log))
        resp = self.client.post(
            "/api/turmas/%s/disciplinas/0/notas" % self.turma_ei,
            json=[{
                "aluno_id": aluno["id"],
                "eixo": EIXO_A,
                "bimestre": 1,
                "avaliacao": "Em Desenvolvimento",
                "observacao": PARECER_SENSIVEL,
            }],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(RELATORIO_DESCRITIVO_CRIADO), antes + 1)
            n_alt = self._contar(RELATORIO_DESCRITIVO_ALTERADO)
        self.client.post(
            "/api/turmas/%s/disciplinas/0/notas" % self.turma_ei,
            json=[{
                "aluno_id": aluno["id"],
                "eixo": EIXO_A,
                "bimestre": 1,
                "avaliacao": "Conquistado",
                "observacao": PARECER_SENSIVEL + " atualizado",
            }],
        )
        with self.app.app_context():
            self.assertEqual(self._contar(RELATORIO_DESCRITIVO_ALTERADO), n_alt + 1)
            alt = self._ultimo(RELATORIO_DESCRITIVO_ALTERADO)
            self.assertNotIn(PARECER_SENSIVEL, _texto_log(alt))
        with self.app.app_context():
            n_rem = self._contar(RELATORIO_DESCRITIVO_REMOVIDO)
        self.client.post(
            "/api/turmas/%s/disciplinas/0/notas" % self.turma_ei,
            json=[{
                "aluno_id": aluno["id"],
                "eixo": EIXO_A,
                "bimestre": 1,
                "avaliacao": None,
                "observacao": "",
            }],
        )
        with self.app.app_context():
            self.assertEqual(self._contar(RELATORIO_DESCRITIVO_REMOVIDO), n_rem + 1)
            rem = self._ultimo(RELATORIO_DESCRITIVO_REMOVIDO)
            self.assertNotIn(PARECER_SENSIVEL, _texto_log(rem))

    def test_r_fechamento_de_turma(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Fecha Audit %s" % self._proximo(),
            "ano_letivo": 2026, "etapa": "fundamental",
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"].startswith("Turma Fecha Audit"))
        aluno = self._criar_aluno(turma_id)
        with self.app.app_context():
            antes = self._contar(TURMA_ENCERRADA)
        resp = self.client.post("/api/turmas/%s/encerrar" % turma_id)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual, Turma
            self.assertEqual(self._contar(TURMA_ENCERRADA), antes + 1)
            log = self._ultimo(TURMA_ENCERRADA)
            novos = _como_dict(log.dados_novos)
            ant = _como_dict(log.dados_anteriores)
            self.assertEqual(novos["turma_id"], turma_id)
            self.assertEqual(novos["ano_letivo"], 2026)
            self.assertEqual(novos["n_matriculas_processadas"], 1)
            self.assertEqual(novos["n_historicos_produzidos"], 1)
            self.assertEqual(ant["status"], "ativa")
            self.assertEqual(novos["status"], "concluida")
            self.assertNotIn("disciplinas", novos)
            self.assertNotIn("pareceres", novos)
            self.assertNotIn("notas", novos)
            self.assertEqual(db.session.get(Turma, turma_id).status, "concluida")
            self.assertEqual(HistoricoAnual.query.filter_by(aluno_id=aluno["id"]).count(), 1)
        resp = self.client.post("/api/turmas/%s/encerrar" % turma_id)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            self.assertEqual(self._contar(TURMA_ENCERRADA), antes + 1)

    def test_s_rollback_nota(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        with self.app.app_context():
            from src import db
            from src.models import AuditLog, RegistroNota
            from src.notas import upsert_nota_matricula
            mat = self._mat_do_aluno(aluno["id"], self.turma_a)
            n_notas = RegistroNota.query.count()
            n_log = AuditLog.query.count()
            upsert_nota_matricula(mat, self.mat, 31, 9.0)
            self.assertGreater(AuditLog.query.count(), n_log)
            db.session.rollback()
            self.assertEqual(RegistroNota.query.count(), n_notas)
            self.assertEqual(AuditLog.query.count(), n_log)
            self.assertIsNone(RegistroNota.query.filter_by(
                matricula_id=mat.id, disciplina_id=self.mat, bimestre=31,
            ).first())

    def test_s2_rollback_fechamento(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/turmas", json={
            "nome": "Turma Rollback Audit %s" % self._proximo(),
            "ano_letivo": 2026, "etapa": "fundamental",
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"].startswith("Turma Rollback Audit"))
        aluno = self._criar_aluno(turma_id)
        with self.app.app_context():
            from src import db
            from src.historico import encerrar_turma_por_matriculas
            from src.models import AuditLog, HistoricoAnual, Turma
            turma = db.session.get(Turma, turma_id)
            n_hist = HistoricoAnual.query.count()
            n_log = AuditLog.query.count()
            n_turma_log = self._contar(TURMA_ENCERRADA)
            encerrar_turma_por_matriculas(turma)
            self.assertEqual(self._contar(TURMA_ENCERRADA), n_turma_log + 1)
            db.session.rollback()
            turma = db.session.get(Turma, turma_id)
            self.assertEqual(turma.status, "ativa")
            self.assertEqual(HistoricoAnual.query.count(), n_hist)
            self.assertEqual(AuditLog.query.count(), n_log)
            self.assertEqual(self._mat_do_aluno(aluno["id"], turma_id).situacao, "cursando")

    def test_s3_rollback_aula_http(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        with self.app.app_context():
            from src.models import AuditLog, RegistroAula
            n_aula = RegistroAula.query.count()
            n_log = AuditLog.query.count()
        with patch("src.diario.db.session.commit", side_effect=RuntimeError("falha-audit")):
            with self.assertRaises(RuntimeError):
                self.client.post(self._url_aula(), json={
                    "data": "2026-06-08", "bimestre": 2, "quantidade_aulas": 1,
                    "conteudo": "Rollback aula", "metodologia": "Expositiva",
                    "lancamentos": [{"aluno_id": aluno["id"], "situacao": "FALTA"}],
                })
        with self.app.app_context():
            from src.models import AuditLog, RegistroAula
            self.assertEqual(RegistroAula.query.count(), n_aula)
            self.assertEqual(AuditLog.query.count(), n_log)

    def test_t_ausencia_de_pii(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(
            self.turma_a,
            nome="Aluno PII Audit",
            cpf=self._cpf("942"),
            nome_mae="Responsável Completo",
            endereco="Av Central 999",
            telefone="11977776666",
        )
        self._login(self.cpf_prof_a)
        self.client.post(self._url_notas(), json=[{
            "aluno_id": aluno["id"], "valor": 8, "tipo": "nota", "bimestre": 32,
        }])
        self.client.post(self._url_notas(), json=[{
            "aluno_id": aluno["id"], "valor": 1, "tipo": "falta", "bimestre": 3,
        }])
        with self.app.app_context():
            from src.models import AuditLog
            acoes = {
                NOTA_CRIADA, NOTA_ALTERADA, NOTA_REMOVIDA,
                FALTA_CRIADA, FALTA_ALTERADA,
                FREQUENCIA_CRIADA, FREQUENCIA_ALTERADA,
                REGISTRO_AULA_CRIADO, REGISTRO_AULA_VALIDADO, REGISTRO_AULA_INVALIDADO,
                MATRICULA_CRIADA, ALUNO_ENTURMADO, ALUNO_REMATRICULADO, ALUNO_INATIVADO,
                RELATORIO_DESCRITIVO_CRIADO, RELATORIO_DESCRITIVO_ALTERADO,
                RELATORIO_DESCRITIVO_REMOVIDO, TURMA_ENCERRADA,
            }
            logs = AuditLog.query.filter(AuditLog.acao.in_(acoes)).all()
            self.assertTrue(logs)
            _assert_sem_pii(self, logs, texto_parecer=PARECER_SENSIVEL)
            blob = " ".join(_texto_log(l) for l in logs)
            self.assertNotIn(aluno["cpf"], blob)
            self.assertNotIn("11977776666", blob)
            self.assertNotIn("Av Central 999", blob)
            self.assertNotIn("Responsável Completo", blob)

    def test_u_acl_nao_gera_audit_log(self):
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_b)
        with self.app.app_context():
            total = self._contar()
        resp = self.client.post(self._url_notas(), json=[{
            "aluno_id": aluno["id"], "valor": 10, "tipo": "nota", "bimestre": 41,
        }])
        self.assertEqual(resp.status_code, 403)
        with self.app.app_context():
            self.assertEqual(self._contar(), total)
        self._login(self.inep_b)
        resp = self.client.post("/api/alunos/%s/inativar" % aluno["id"])
        self.assertEqual(resp.status_code, 403)
        with self.app.app_context():
            self.assertEqual(self._contar(), total)
        resp = self.client.post("/api/turmas/%s/encerrar" % self.turma_a)
        self.assertEqual(resp.status_code, 403)
        with self.app.app_context():
            self.assertEqual(self._contar(), total)

    def test_v_regras_academicas_inalteradas(self):
        from src.notas import calcular_de_notas
        self._login(self.inep_a)
        aluno = self._criar_aluno(self.turma_a)
        self._login(self.cpf_prof_a)
        self.client.post(self._url_notas(), json=[
            {"aluno_id": aluno["id"], "valor": 7, "tipo": "nota", "bimestre": 11},
            {"aluno_id": aluno["id"], "valor": 7, "tipo": "nota", "bimestre": 12},
        ])
        esperado = calcular_de_notas({11: 7.0, 12: 7.0})
        mon = _json(self.client.get(
            "/api/monitoramento/turma/%s/disciplina/%s" % (self.turma_a, self.mat)
        ))[0]
        item = next(i for i in mon if i["id"] == aluno["id"])
        self.assertEqual(item["resumo"]["mb1"], esperado["mb1"])
        self.assertEqual(item["resumo"]["situacao"], esperado["situacao"])
        self.assertEqual(item["resumo"]["ma"], esperado["ma"])

    def test_cadastro_sem_turma_nao_inventa_matricula(self):
        self._login(self.inep_a)
        with self.app.app_context():
            antes = self._contar(MATRICULA_CRIADA)
        self._criar_aluno()
        with self.app.app_context():
            self.assertEqual(self._contar(MATRICULA_CRIADA), antes)


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
class AuditoriaAcademicaPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = os.environ.copy()
        from src.config import Config
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        cls._old_opts = Config.SQLALCHEMY_ENGINE_OPTIONS
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-pg-audit-acad"
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
        AuditoriaAcademicaPostgresTest._seq += 1
        return AuditoriaAcademicaPostgresTest._seq

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _rede(self, etapa="fundamental"):
        n = self._proximo()
        inep = "8500%04d" % n
        cpf_prof = ("8501%07d" % n)[:11]
        cpf_aluno = ("8502%07d" % n)[:11]
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola PG Audit %s" % inep,
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof PG Audit", "cpf": cpf_prof, "escola_id": escola_id,
        })
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        nome_turma = "5º PG Audit" if etapa == "fundamental" else "Pré PG Audit"
        self.client.post("/api/turmas", json={
            "nome": nome_turma, "ano_letivo": 2026, "etapa": etapa,
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == nome_turma)
        port_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Português")
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": "Aluno PG Audit",
            "cpf": cpf_aluno,
            "turma_id": turma_id,
            "nome_mae": "Mãe PG Audit",
            "endereco": "Rua PG 1",
            "telefone": "11900001111",
        })
        aluno_id = _json(resp)[0]["id"]
        payload_aloc = {"professor_id": prof_id, "turma_id": turma_id}
        if etapa == "fundamental":
            payload_aloc["disciplina_id"] = port_id
        self.client.post("/api/alocacoes", json=payload_aloc)
        concluir_primeiro_acesso(self.client, cpf_prof, temp_prof)
        return {
            "inep": inep,
            "cpf_prof": cpf_prof,
            "turma_id": turma_id,
            "port_id": port_id,
            "aluno_id": aluno_id,
            "cpf_aluno": cpf_aluno,
        }

    def test_pg_nota_noop_e_rollback(self):
        from src import db
        from src.models import AuditLog, Matricula, RegistroNota
        from src.notas import upsert_nota_matricula
        rede = self._rede()
        with self.app.app_context():
            self.assertEqual(db.engine.dialect.name, "postgresql")
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_id"],
            ).one()
            upsert_nota_matricula(mat, rede["port_id"], 11, 8.0)
            db.session.commit()
            n_log = AuditLog.query.filter_by(acao=NOTA_CRIADA).count()
            self.assertEqual(n_log, 1)
            upsert_nota_matricula(mat, rede["port_id"], 11, 8.0)
            db.session.commit()
            self.assertEqual(AuditLog.query.filter_by(acao=NOTA_CRIADA).count(), 1)
            self.assertEqual(AuditLog.query.filter_by(acao=NOTA_ALTERADA).count(), 0)
            n_notas = RegistroNota.query.count()
            n_total = AuditLog.query.count()
            upsert_nota_matricula(mat, rede["port_id"], 12, 7.0)
            db.session.rollback()
            self.assertEqual(RegistroNota.query.count(), n_notas)
            self.assertEqual(AuditLog.query.count(), n_total)

    def test_pg_parecer_sem_texto_e_pii(self):
        from src import db
        from src.models import AuditLog, Matricula
        from src.pareceres import aplicar_parecer_matricula
        rede = self._rede(etapa="infantil")
        with self.app.app_context():
            mat = Matricula.query.filter_by(
                aluno_id=rede["aluno_id"], turma_id=rede["turma_id"],
            ).one()
            aplicar_parecer_matricula(mat, EIXO_A, 1, "Em Desenvolvimento", PARECER_SENSIVEL)
            db.session.commit()
            logs = AuditLog.query.filter_by(acao=RELATORIO_DESCRITIVO_CRIADO).all()
            self.assertEqual(len(logs), 1)
            _assert_sem_pii(self, logs, texto_parecer=PARECER_SENSIVEL)
            novos = _como_dict(logs[0].dados_novos)
            self.assertNotIn(rede["cpf_aluno"], json.dumps(novos))


if __name__ == "__main__":
    unittest.main()
