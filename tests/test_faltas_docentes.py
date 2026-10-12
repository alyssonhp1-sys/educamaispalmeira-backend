import os
import tempfile
import unittest
from pathlib import Path

from tests.support import (
    SENHA_DEMO_ADMIN,
    SENHA_PESSOAL,
    concluir_primeiro_acesso,
    preparar_ambiente_teste,
    senha_temporaria,
)


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


PERIODO = "data_inicio=2026-02-01&data_fim=2026-02-28"


class FaltasDocentesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preparar_ambiente_teste()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cls._db_path = tmp.name
        from tests.support import apontar_banco_teste
        uri = "sqlite:///" + Path(cls._db_path).resolve().as_posix()
        from src.config import Config
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        apontar_banco_teste(uri)
        from src import create_app
        cls.app = create_app()
        cls.app.testing = True
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

        cls.inep_a = "96100001"
        cls.inep_b = "96100002"
        cls.cpf_prof_a = "96111111111"
        cls.cpf_prof_b = "96122222222"
        cls.login_coord_a = "coord961a"
        cls.login_coord_b = "coord961b"

        temps = {}
        for nome, inep in (("Escola Falta Norte", cls.inep_a), ("Escola Falta Sul", cls.inep_b)):
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
            (cls.login_coord_a, "Coord Falta Norte", cls.escola_a),
            (cls.login_coord_b, "Coord Falta Sul", cls.escola_b),
        ):
            resp = client.post(f"/api/escolas/{escola_id}/coordenadores", json={"login": login, "nome": nome})
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[login] = senha_temporaria(resp)

        for nome, cpf, escola_id in (
            ("Prof Falta A", cls.cpf_prof_a, cls.escola_a),
            ("Prof Falta B", cls.cpf_prof_b, cls.escola_b),
        ):
            resp = client.post("/api/professores", json={"nome": nome, "cpf": cpf, "escola_id": escola_id})
            if resp.status_code != 200:
                raise RuntimeError(_json(resp)[1])
            temps[cpf] = senha_temporaria(resp)

        for login, temp in temps.items():
            concluir_primeiro_acesso(client, login, temp)

        cls._login_client(client, cls.inep_a, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "9º A Falta", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_a = dash["turmas"][0]["id"]
        cls.prof_a_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_a)
        resp = client.post("/api/alunos", json={
            "nome": "Aluno Falta A", "cpf": "96133333333",
            "turma_id": cls.turma_a, "nome_mae": "Mãe Falta A",
        })
        cls.aluno_a = _json(resp)[0]["id"]

        resp = client.post("/api/alocacoes", json={
            "professor_id": cls.prof_a_id,
            "turma_id": cls.turma_a,
            "disciplina_id": cls.mat,
            "aulas_semanais": 3,
            "carga_horaria_prevista": 6,
            "grade": [
                {"dia_semana": 1, "quantidade_aulas": 1},
                {"dia_semana": 3, "quantidade_aulas": 2},
            ],
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])

        resp = client.post("/api/alocacoes", json={
            "professor_id": cls.prof_a_id,
            "turma_id": cls.turma_a,
            "disciplina_id": cls.port,
            "aulas_semanais": 1,
            "carga_horaria_prevista": 2,
            "grade": [{"dia_semana": 2, "quantidade_aulas": 1}],
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        dash = _json(client.get("/api/dashboard"))[0]
        turma_dash = next(t for t in dash["turmas"] if t["id"] == cls.turma_a)
        cls.aloc_a = next(a["id"] for a in turma_dash["alocacoes"] if a["disciplinaId"] == cls.mat)
        cls.aloc_a_port = next(a["id"] for a in turma_dash["alocacoes"] if a["disciplinaId"] == cls.port)

        cls._login_client(client, cls.inep_b, SENHA_PESSOAL)
        client.post("/api/turmas", json={"nome": "9º B Falta", "ano_letivo": 2026, "etapa": "fundamental"})
        dash = _json(client.get("/api/dashboard"))[0]
        cls.turma_b = dash["turmas"][0]["id"]
        cls.prof_b_id = next(p["id"] for p in dash["professores"] if p["cpf"] == cls.cpf_prof_b)
        client.post("/api/alunos", json={
            "nome": "Aluno Falta B", "cpf": "96144444444",
            "turma_id": cls.turma_b, "nome_mae": "Mãe Falta B",
        })
        resp = client.post("/api/alocacoes", json={
            "professor_id": cls.prof_b_id,
            "turma_id": cls.turma_b,
            "disciplina_id": cls.mat,
            "aulas_semanais": 1,
            "carga_horaria_prevista": 2,
            "grade": [{"dia_semana": 3, "quantidade_aulas": 1}],
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        dash = _json(client.get("/api/dashboard"))[0]
        cls.aloc_b = next(a["id"] for a in dash["turmas"][0]["alocacoes"] if a["disciplinaId"] == cls.mat)

        from src.models import HorarioAlocacao
        with cls.app.app_context():
            cls.horario_a_ter = HorarioAlocacao.query.filter_by(
                alocacao_id=cls.aloc_a, dia_semana=1,
            ).first().id
            cls.horario_a_qui = HorarioAlocacao.query.filter_by(
                alocacao_id=cls.aloc_a, dia_semana=3,
            ).first().id
            cls.horario_a_qua = HorarioAlocacao.query.filter_by(
                alocacao_id=cls.aloc_a_port, dia_semana=2,
            ).first().id
            cls.horario_b_qui = HorarioAlocacao.query.filter_by(
                alocacao_id=cls.aloc_b, dia_semana=3,
            ).first().id

        cls._login_client(client, "admin", SENHA_DEMO_ADMIN)
        resp = client.post("/api/anos-letivos", json={
            "ano": 2026,
            "data_inicio": "2026-02-02",
            "data_fim": "2026-02-14",
            "ativo": True,
            "gerar_calendario": True,
            "periodos": [
                {"numero": 1, "nome": "1º Bimestre", "data_inicio": "2026-02-02", "data_fim": "2026-02-14"},
            ],
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        cls.ano_id = _json(resp)[0]["id"]

        cls._login_client(client, cls.login_coord_b, SENHA_PESSOAL)
        resp = client.post("/api/faltas-docentes", json={
            "data": "2026-02-05",
            "horario_alocacao_id": cls.horario_b_qui,
        })
        if resp.status_code != 200:
            raise RuntimeError(_json(resp)[1])
        cls.occ_b = _json(resp)[0]["id"]

    def setUp(self):
        self.client = self.app.test_client()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _relatorio(self, extra=""):
        q = PERIODO + (("&" + extra) if extra else "")
        return self.client.get(f"/api/faltas-docentes/relatorio?{q}")

    def test_01_aulas_previstas_do_dia(self):
        self._login(self.login_coord_a)
        resp = self.client.get("/api/faltas-docentes/aulas-previstas?data=2026-02-05")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        ids = {a["horarioAlocacaoId"] for a in data["aulas"]}
        self.assertIn(self.horario_a_qui, ids)
        self.assertNotIn(self.horario_b_qui, ids)
        aula = next(a for a in data["aulas"] if a["horarioAlocacaoId"] == self.horario_a_qui)
        self.assertEqual(aula["estado"], "sem_registro")
        self.assertTrue(aula["podeRegistrarFalta"])
        self.assertEqual(aula["quantidadeAulas"], 2)
        self.assertEqual(aula["professorId"], self.prof_a_id)

    def test_02_coordenador_propria_escola_registra(self):
        self._login(self.login_coord_a)
        resp = self.client.post("/api/faltas-docentes", json={
            "data": "2026-02-05",
            "horario_alocacao_id": self.horario_a_qui,
            "observacao": "Atestado informado à secretaria da escola.",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        self.assertEqual(data["mensagem"], "Falta docente registrada.")
        self.assertEqual(data["status"], "ATIVA")
        self.assertEqual(data["motivoRotulo"], "Falta do professor")
        self.assertEqual(data["quantidadeAulas"], 2)
        self.assertEqual(data["escolaId"], self.escola_a)
        self.__class__.occ_a = data["id"]
        previstas, _ = _json(self.client.get("/api/faltas-docentes/aulas-previstas?data=2026-02-05"))
        aula = next(a for a in previstas["aulas"] if a["horarioAlocacaoId"] == self.horario_a_qui)
        self.assertEqual(aula["estado"], "nao_ministrada")
        self.assertFalse(aula["podeRegistrarFalta"])

    def test_03_duplicidade_bloqueada(self):
        self._login(self.login_coord_a)
        resp = self.client.post("/api/faltas-docentes", json={
            "data": "2026-02-05",
            "horario_alocacao_id": self.horario_a_qui,
        })
        self.assertEqual(resp.status_code, 409, _json(resp)[1])
        self.assertIn("ocorrência ativa", (_json(resp)[1] or "").lower())

    def test_04_gestor_propria_escola_registra(self):
        self._login(self.inep_a)
        resp = self.client.post("/api/faltas-docentes", json={
            "data": "2026-02-03",
            "horario_alocacao_id": self.horario_a_ter,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        self.assertEqual(data["status"], "ATIVA")
        self.assertEqual(data["quantidadeAulas"], 1)
        self.__class__.occ_gestor = data["id"]

    def test_05_coordenador_outra_escola_403(self):
        self._login(self.login_coord_b)
        resp = self.client.post("/api/faltas-docentes", json={
            "data": "2026-02-03",
            "horario_alocacao_id": self.horario_a_ter,
        })
        self.assertEqual(resp.status_code, 403)
        previstas = self.client.get("/api/faltas-docentes/aulas-previstas?data=2026-02-05")
        ids = {a["horarioAlocacaoId"] for a in _json(previstas)[0]["aulas"]}
        self.assertNotIn(self.horario_a_qui, ids)

    def test_06_gestor_outra_escola_403(self):
        self._login(self.inep_b)
        resp = self.client.post("/api/faltas-docentes", json={
            "data": "2026-02-03",
            "horario_alocacao_id": self.horario_a_ter,
        })
        self.assertEqual(resp.status_code, 403)

    def test_07_professor_nao_registra(self):
        self._login(self.cpf_prof_a)
        resp = self.client.post("/api/faltas-docentes", json={
            "data": "2026-02-12",
            "horario_alocacao_id": self.horario_a_qui,
        })
        self.assertEqual(resp.status_code, 403)

    def test_08_professor_nao_cancela_nem_relatorio(self):
        self._login(self.cpf_prof_a)
        occ_id = getattr(self, "occ_a", None) or getattr(type(self), "occ_a", None)
        self.assertIsNotNone(occ_id)
        resp = self.client.post(f"/api/faltas-docentes/{occ_id}/cancelar", json={
            "motivo_cancelamento": "tentativa do professor",
        })
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get(f"/api/faltas-docentes/relatorio?{PERIODO}")
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get("/api/faltas-docentes/aulas-previstas?data=2026-02-05")
        self.assertEqual(resp.status_code, 403)

    def test_09_secretaria_relatorio_rede(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._relatorio()
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        escolas = {e["escolaId"] for e in data["porEscola"]}
        self.assertIn(self.escola_a, escolas)
        self.assertIn(self.escola_b, escolas)
        self.assertGreaterEqual(data["indicadores"]["ocorrenciasAtivas"], 3)
        aulas_a = next(e["aulasNaoMinistradas"] for e in data["porEscola"] if e["escolaId"] == self.escola_a)
        self.assertGreaterEqual(aulas_a, 3)

    def test_10_isolamento_escolar(self):
        self._login(self.login_coord_a)
        resp = self.client.get(f"/api/faltas-docentes?status=TODAS&{PERIODO}")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        ids_escola = {o["escolaId"] for o in _json(resp)[0]["ocorrencias"]}
        self.assertEqual(ids_escola, {self.escola_a})
        self._login(self.inep_b)
        resp = self.client.get(f"/api/faltas-docentes?status=TODAS&{PERIODO}")
        ids_escola = {o["escolaId"] for o in _json(resp)[0]["ocorrencias"]}
        self.assertEqual(ids_escola, {self.escola_b})

    def test_11_conflito_com_registro_aula(self):
        url = f"/api/turmas/{self.turma_a}/disciplinas/{self.mat}/registros-aula"
        self._login(self.cpf_prof_a)
        resp = self.client.post(url, json={
            "data": "2026-02-10",
            "bimestre": 1,
            "quantidade_aulas": 1,
            "conteudo": "Aula prevista registrada",
            "metodologia": "Expositiva",
            "lancamentos": [],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.__class__.aula_10 = _json(resp)[0]["id"]
        self._login(self.login_coord_a)
        resp = self.client.post("/api/faltas-docentes", json={
            "data": "2026-02-10",
            "horario_alocacao_id": self.horario_a_ter,
        })
        self.assertEqual(resp.status_code, 409, _json(resp)[1])
        self.assertIn("já possui registro", (_json(resp)[1] or "").lower())

    def test_12_professor_bloqueado_com_ocorrencia_ativa(self):
        self._login(self.login_coord_a)
        resp = self.client.post("/api/faltas-docentes", json={
            "data": "2026-02-12",
            "horario_alocacao_id": self.horario_a_qui,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.__class__.occ_12 = _json(resp)[0]["id"]
        n_antes = self._contar_registros_aula()
        self._login(self.cpf_prof_a)
        resp = self.client.post(
            f"/api/turmas/{self.turma_a}/disciplinas/{self.mat}/registros-aula",
            json={
                "data": "2026-02-12",
                "bimestre": 1,
                "quantidade_aulas": 2,
                "conteudo": "Tentativa após falta",
                "metodologia": "Expositiva",
                "lancamentos": [],
            },
        )
        self.assertEqual(resp.status_code, 409, _json(resp)[1])
        self.assertIn("não ministrada", (_json(resp)[1] or "").lower())
        self.assertEqual(self._contar_registros_aula(), n_antes)

    def test_13_cancelar_exige_motivo(self):
        self._login(self.login_coord_a)
        occ_id = type(self).occ_12
        resp = self.client.post(f"/api/faltas-docentes/{occ_id}/cancelar", json={
            "motivo_cancelamento": "   ",
        })
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post(f"/api/faltas-docentes/{occ_id}/cancelar", json={})
        self.assertEqual(resp.status_code, 400)

    def test_14_cancelar_preserva_e_libera_diario(self):
        self._login(self.login_coord_a)
        occ_id = type(self).occ_12
        resp = self.client.post(f"/api/faltas-docentes/{occ_id}/cancelar", json={
            "motivo_cancelamento": "Lançamento equivocado da coordenação.",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        self.assertEqual(data["mensagem"], "Ocorrência cancelada.")
        self.assertEqual(data["status"], "CANCELADA")
        self.assertTrue(data["motivoCancelamento"])
        from src import db
        from src.models import OcorrenciaAulaNaoMinistrada
        with self.app.app_context():
            occ = db.session.get(OcorrenciaAulaNaoMinistrada, occ_id)
            self.assertEqual(occ.status, "CANCELADA")
            self.assertIsNotNone(occ.registrado_por_id)
            self.assertIsNotNone(occ.cancelado_por_id)
            self.assertIsNotNone(occ.cancelado_em)
        self._login(self.cpf_prof_a)
        resp = self.client.post(
            f"/api/turmas/{self.turma_a}/disciplinas/{self.mat}/registros-aula",
            json={
                "data": "2026-02-12",
                "bimestre": 1,
                "quantidade_aulas": 2,
                "conteudo": "Aula após cancelamento",
                "metodologia": "Expositiva",
                "lancamentos": [],
            },
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def test_15_feriado_bloqueado(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        self.client.put(f"/api/anos-letivos/{self.ano_id}/dias", json={"data": "2026-02-04", "tipo": "FERIADO"})
        try:
            self._login(self.login_coord_a)
            resp = self.client.post("/api/faltas-docentes", json={
                "data": "2026-02-04",
                "horario_alocacao_id": self.horario_a_qua,
            })
            self.assertEqual(resp.status_code, 400, _json(resp)[1])
            self.assertIn("não letivo", (_json(resp)[1] or "").lower())
            previstas, _ = _json(self.client.get("/api/faltas-docentes/aulas-previstas?data=2026-02-04"))
            self.assertTrue(previstas["diaNaoLetivo"])
        finally:
            self._login("admin", SENHA_DEMO_ADMIN)
            self.client.put(f"/api/anos-letivos/{self.ano_id}/dias", json={"data": "2026-02-04", "tipo": "LETIVO"})

    def test_16_nao_letivo_bloqueado(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        self.client.put(f"/api/anos-letivos/{self.ano_id}/dias", json={"data": "2026-02-11", "tipo": "NAO_LETIVO"})
        try:
            self._login(self.login_coord_a)
            resp = self.client.post("/api/faltas-docentes", json={
                "data": "2026-02-11",
                "horario_alocacao_id": self.horario_a_qua,
            })
            self.assertEqual(resp.status_code, 400)
        finally:
            self._login("admin", SENHA_DEMO_ADMIN)
            self.client.put(f"/api/anos-letivos/{self.ano_id}/dias", json={"data": "2026-02-11", "tipo": "LETIVO"})

    def test_17_auditlog_criacao_e_cancelamento(self):
        from src.models import AuditLog
        with self.app.app_context():
            criados = AuditLog.query.filter_by(acao="FALTA_DOCENTE_REGISTRADA").all()
            cancelados = AuditLog.query.filter_by(acao="FALTA_DOCENTE_CANCELADA").all()
            self.assertGreaterEqual(len(criados), 3)
            self.assertGreaterEqual(len(cancelados), 1)
            for log in criados + cancelados:
                novos = log.dados_novos or {}
                texto = str(novos).lower()
                self.assertNotIn("cpf", texto)
                self.assertNotIn("senha", texto)
                self.assertIn("acao", novos)
                self.assertIn("escola_id", novos)
                self.assertNotIn("observacao", novos)
                self.assertNotIn("motivo_cancelamento", novos)

    def test_18_relatorio_filtros_e_totais(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        data, _ = _json(self._relatorio())
        ativas = data["indicadores"]["ocorrenciasAtivas"]
        aulas = data["indicadores"]["aulasNaoMinistradas"]
        self.assertGreaterEqual(ativas, 3)
        self.assertGreaterEqual(aulas, 4)
        ids_ativas = {i["id"] for i in data["itens"] if i["status"] == "ATIVA"}
        self.assertNotIn(type(self).occ_12, ids_ativas)
        data_canc, _ = _json(self._relatorio("status=CANCELADA"))
        ids_canc = {i["id"] for i in data_canc["itens"]}
        self.assertIn(type(self).occ_12, ids_canc)
        self.assertEqual(data_canc["indicadores"]["ocorrenciasAtivas"], ativas)
        data_esc, _ = _json(self._relatorio(f"escola_id={self.escola_b}"))
        self.assertEqual(data_esc["indicadores"]["ocorrenciasAtivas"], 1)
        self.assertEqual(data_esc["porProfessor"][0]["ocorrencias"], 1)
        data_prof, _ = _json(self._relatorio(f"professor_id={self.prof_a_id}"))
        self.assertTrue(all(i["professorId"] == self.prof_a_id for i in data_prof["itens"]))
        data_turma, _ = _json(self._relatorio(f"turma_id={self.turma_a}"))
        self.assertTrue(all(i["turmaId"] == self.turma_a for i in data_turma["itens"]))
        data_disc, _ = _json(self._relatorio(f"disciplina_id={self.mat}"))
        self.assertTrue(all(i["disciplinaId"] == self.mat for i in data_disc["itens"]))

    def test_19_csv_e_dashboard(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.get(f"/api/faltas-docentes/relatorio.csv?{PERIODO}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/csv", resp.content_type)
        corpo = resp.get_data(as_text=True)
        self.assertIn("Escola", corpo)
        dash, _ = _json(self.client.get("/api/dashboard"))
        self.assertGreaterEqual(dash["indicadores"]["faltasDocentesAtivas"], 3)
        self.assertGreaterEqual(dash["indicadores"]["aulasNaoMinistradas"], 4)

    def test_20_nao_cria_registro_aula_ficticio(self):
        from src import db
        from src.models import OcorrenciaAulaNaoMinistrada, RegistroAula
        with self.app.app_context():
            occ = db.session.get(OcorrenciaAulaNaoMinistrada, type(self).occ_a)
            n = RegistroAula.query.filter_by(alocacao_id=occ.alocacao_id, data=occ.data).count()
            self.assertEqual(n, 0)

    def test_21_carga_validada_nao_aumenta(self):
        self._login(self.inep_a)
        detalhe, _ = _json(self.client.get(f"/api/alocacoes/{self.aloc_a}?data=2026-02-12"))
        self.assertEqual(detalhe["aulasValidadas"], 0)

    def test_22_secretaria_nao_registra(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/faltas-docentes", json={
            "data": "2026-02-04",
            "horario_alocacao_id": self.horario_a_qua,
        })
        self.assertEqual(resp.status_code, 403)

    def test_23_data_fora_do_ano(self):
        self._login(self.login_coord_a)
        resp = self.client.post("/api/faltas-docentes", json={
            "data": "2025-02-06",
            "horario_alocacao_id": self.horario_a_qui,
        })
        self.assertEqual(resp.status_code, 400)

    def _contar_registros_aula(self):
        from src.models import RegistroAula
        with self.app.app_context():
            return RegistroAula.query.count()
