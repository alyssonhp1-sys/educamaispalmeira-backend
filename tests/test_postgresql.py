"""Smoke PostgreSQL real. Não usa instance/educamais.db nem simula dialect.

Requer EDUCAMAIS_TEST_DATABASE_URI apontando para um database descartável
(ex.: educamais_pg_test). Sem essa variável a classe é ignorada: a suíte
SQLite continua independente.

  python -m unittest tests.test_postgresql
"""
import os
import unittest
from datetime import date

from sqlalchemy.exc import IntegrityError

from src.config import resolver_uri_teste_postgresql
from src.integridade import INDICES_INTEGRIDADE
from src.migracoes import REVISION_HEAD, TABELAS_ESSENCIAIS
from tests.support import (
    SENHA_DEMO_ADMIN,
    SENHA_PESSOAL,
    apontar_banco_teste,
    concluir_primeiro_acesso,
    criar_app_migrada,
    senha_temporaria,
)


URI_PG = resolver_uri_teste_postgresql()

# UNIQUE parciais: tabela, colunas do índice, colunas exigidas no WHERE.
_INDICES_PARCIAIS = {
    "uq_registro_nota_matricula_disc_bim": (
        "registro_nota",
        ["matricula_id", "disciplina_id", "bimestre"],
        ["matricula_id"],
    ),
    "uq_registro_falta_matricula_disc_bim": (
        "registro_falta",
        ["matricula_id", "disciplina_id", "bimestre"],
        ["matricula_id"],
    ),
    "uq_registro_frequencia_matricula_aula": (
        "registro_frequencia",
        ["matricula_id", "registro_aula_id"],
        ["matricula_id", "registro_aula_id"],
    ),
    "uq_relatorio_descritivo_matricula_eixo_bim": (
        "relatorio_descritivo",
        ["matricula_id", "eixo", "bimestre"],
        ["matricula_id"],
    ),
    "uq_historico_anual_matricula": (
        "historico_anual",
        ["matricula_id"],
        ["matricula_id"],
    ),
}


def _predicado_normalizado(valor):
    """Remove parênteses/aspas/espaços irrelevantes do predicado do índice."""
    if valor is None:
        return ""
    if hasattr(valor, "text"):
        valor = valor.text
    texto = str(valor).replace('"', " ").replace("(", " ").replace(")", " ")
    return " ".join(texto.split()).upper()


def _predicado_do_indice(bind, nome, dialect_options):
    where = (dialect_options or {}).get("postgresql_where")
    if where is not None:
        return where
    from sqlalchemy import text
    row = bind.execute(
        text(
            "SELECT pg_get_expr(i.indpred, i.indrelid) "
            "FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indexrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = :n"
        ),
        {"n": nome},
    ).first()
    return None if row is None else row[0]


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


def _limpar_dados(app):
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


class PredicadoIndiceParcialTest(unittest.TestCase):
    """Normalização sintática do WHERE parcial; não exige PostgreSQL."""

    def test_parenteses_do_postgresql_nao_alteram_a_semantica(self):
        bruto = "((matricula_id IS NOT NULL) AND (registro_aula_id IS NOT NULL))"
        self.assertEqual(
            _predicado_normalizado(bruto),
            "MATRICULA_ID IS NOT NULL AND REGISTRO_AULA_ID IS NOT NULL",
        )

    def test_predicado_simples_matricula(self):
        self.assertEqual(
            _predicado_normalizado("(matricula_id IS NOT NULL)"),
            "MATRICULA_ID IS NOT NULL",
        )


@unittest.skipUnless(
    URI_PG,
    "Defina EDUCAMAIS_TEST_DATABASE_URI com PostgreSQL real (banco descartável).",
)
class PostgreSQLCompatibilidadeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._env = os.environ.copy()
        from src.config import Config
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        cls._old_opts = Config.SQLALCHEMY_ENGINE_OPTIONS
        os.environ["EDUCAMAIS_ENV"] = "development"
        os.environ["EDUCAMAIS_SECRET_KEY"] = "chave-dev-pg-teste"
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
        from tests.support import preparar_ambiente_teste
        preparar_ambiente_teste(demo=True)

    def setUp(self):
        _limpar_dados(self.app)
        self.client = self.app.test_client()

    def _proximo(self):
        PostgreSQLCompatibilidadeTest._seq += 1
        return PostgreSQLCompatibilidadeTest._seq

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _rede(self, etapa="fundamental"):
        n = self._proximo()
        inep = "8100%04d" % n
        cpf_prof = ("8101%07d" % n)[:11]
        cpf_aluno = ("8102%07d" % n)[:11]
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": "Escola PG %s" % inep,
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof PG", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        nome_turma = "5º Ano PG" if etapa == "fundamental" else "Pré PG"
        self.client.post("/api/turmas", json={
            "nome": nome_turma, "ano_letivo": 2026, "etapa": etapa,
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == nome_turma)
        port_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Português")
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": "Aluno PG", "cpf": cpf_aluno, "turma_id": turma_id, "nome_mae": "Mãe PG",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
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
            "prof_id": prof_id,
            "escola_id": escola_id,
        }

    def _mat(self, aluno_id, turma_id):
        from src.models import Matricula
        return Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).one()

    def test_01_select_1(self):
        with self.app.app_context():
            from sqlalchemy import text
            from src import db
            self.assertEqual(db.session.execute(text("SELECT 1")).scalar(), 1)
            self.assertEqual(db.engine.dialect.name, "postgresql")
            opts = self.app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {}
            self.assertNotIn("check_same_thread", opts.get("connect_args") or {})

    def test_02_alembic_na_head(self):
        from src.migracoes import revisao_atual
        with self.app.app_context():
            self.assertEqual(revisao_atual(), REVISION_HEAD)

    def test_03_tabelas_essenciais(self):
        from sqlalchemy import inspect
        from src import db
        with self.app.app_context():
            nomes = set(inspect(db.engine).get_table_names())
            faltando = [t for t in TABELAS_ESSENCIAIS if t not in nomes]
            self.assertEqual(faltando, [])
            self.assertIn("alembic_version", nomes)

    def test_03b_audit_log(self):
        from sqlalchemy import inspect
        from src import db
        with self.app.app_context():
            insp = inspect(db.engine)
            cols = {c["name"] for c in insp.get_columns("audit_log")}
            esperadas = {
                "id", "usuario_id", "acao", "entidade", "entidade_id",
                "escola_id", "data_hora", "request_id", "ip",
                "dados_anteriores", "dados_novos",
            }
            self.assertTrue(esperadas <= cols, cols)
            refs = {fk["referred_table"] for fk in insp.get_foreign_keys("audit_log")}
            self.assertIn("usuario", refs)
            self.assertIn("escola", refs)
            idx = {i["name"] for i in insp.get_indexes("audit_log") if i.get("name")}
            self.assertIn("ix_audit_log_usuario_id", idx)
            self.assertIn("ix_audit_log_escola_id", idx)
            self.assertIn("ix_audit_log_data_hora", idx)
            self.assertIn("ix_audit_log_entidade_id", idx)

    def test_04_indices_unique_parciais(self):
        from sqlalchemy import inspect
        from src import db
        self.assertEqual(set(_INDICES_PARCIAIS), set(INDICES_INTEGRIDADE))
        with self.app.app_context():
            insp = inspect(db.engine)
            for nome, (tabela, colunas, not_null) in _INDICES_PARCIAIS.items():
                achados = [
                    i for i in insp.get_indexes(tabela) if i.get("name") == nome
                ]
                self.assertEqual(len(achados), 1, "índice ausente: %s" % nome)
                idx = achados[0]
                self.assertTrue(idx.get("unique"), nome)
                self.assertEqual(list(idx.get("column_names") or []), colunas, nome)
                where = _predicado_do_indice(
                    db.session, nome, idx.get("dialect_options"),
                )
                self.assertTrue(where, "predicado WHERE ausente: %s" % nome)
                pred = _predicado_normalizado(where)
                esperado = " AND ".join(
                    "%s IS NOT NULL" % col.upper() for col in not_null
                )
                self.assertEqual(pred, esperado, "%s: %s" % (nome, pred))

    def test_05_criar_escola_turma_aluno_matricula(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Escola, Matricula, Turma
            self.assertIsNotNone(db.session.get(Escola, rede["escola_id"]))
            self.assertIsNotNone(db.session.get(Turma, rede["turma_id"]))
            self.assertIsNotNone(db.session.get(Aluno, rede["aluno_id"]))
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            self.assertEqual(mat.situacao, "cursando")

    def test_06_lancar_nota_via_servico(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroNota
            from src.notas import upsert_nota_matricula
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            upsert_nota_matricula(mat, rede["port_id"], 11, 8.0)
            db.session.commit()
            self.assertEqual(
                RegistroNota.query.filter_by(matricula_id=mat.id, bimestre=11).one().valor,
                8.0,
            )

    def test_07_lancar_falta_via_servico(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.frequencia import upsert_falta_matricula
            from src.models import RegistroFalta
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            upsert_falta_matricula(mat, rede["port_id"], 1, 2)
            db.session.commit()
            self.assertEqual(
                RegistroFalta.query.filter_by(matricula_id=mat.id, bimestre=1).one().quantidade,
                2,
            )

    def test_08_registro_aula_e_frequencia(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.frequencia import FALTA, aplicar_frequencia
            from src.models import RegistroAula, RegistroFrequencia
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            aula = RegistroAula(
                professor_id=rede["prof_id"],
                turma_id=rede["turma_id"],
                disciplina_id=rede["port_id"],
                data=date(2026, 3, 10),
                bimestre=1,
                quantidade_aulas=1,
                conteudo="Conteúdo PG",
                metodologia="Metodologia PG",
                status="pendente",
            )
            db.session.add(aula)
            db.session.flush()
            aplicar_frequencia(
                mat, rede["port_id"], date(2026, 3, 10), 1, FALTA,
                professor_id=rede["prof_id"], registro_aula_id=aula.id,
                aluno_id=mat.aluno_id, turma_id=mat.turma_id,
            )
            db.session.commit()
            self.assertEqual(
                RegistroFrequencia.query.filter_by(
                    matricula_id=mat.id, registro_aula_id=aula.id,
                ).one().situacao,
                FALTA,
            )

    def test_09_parecer_ei(self):
        rede = self._rede(etapa="infantil")
        with self.app.app_context():
            from src import db
            from src.models import RelatorioDescritivo
            from src.pareceres import upsert_parecer_matricula
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            upsert_parecer_matricula(mat, "Linguagem", 1, "Conquistado", "ok")
            db.session.commit()
            self.assertEqual(
                RelatorioDescritivo.query.filter_by(matricula_id=mat.id).one().eixo,
                "Linguagem",
            )

    def test_10_encerramento_historico_anual(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.historico import encerrar_turma_por_matriculas
            from src.models import HistoricoAnual, Turma
            from src.notas import upsert_nota_matricula
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            upsert_nota_matricula(mat, rede["port_id"], 11, 7.0)
            turma = db.session.get(Turma, rede["turma_id"])
            encerrar_turma_por_matriculas(turma)
            db.session.commit()
            hist = HistoricoAnual.query.filter_by(matricula_id=mat.id).one()
            self.assertIn("notas", hist.dados_fechamento or "")
            db.session.refresh(mat)
            self.assertEqual(mat.situacao, "concluida")

    def test_11_unique_nota(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroNota
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            db.session.add(RegistroNota(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=11, valor=8.0, tipo="bimestral",
            ))
            db.session.commit()
            db.session.add(RegistroNota(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=11, valor=9.0, tipo="bimestral",
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
            self.assertEqual(
                RegistroNota.query.filter_by(matricula_id=mat.id, bimestre=11).count(), 1,
            )

    def test_12_unique_falta(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroFalta
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            db.session.add(RegistroFalta(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=1, quantidade=1,
            ))
            db.session.commit()
            db.session.add(RegistroFalta(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=1, quantidade=2,
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_13_unique_frequencia(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroAula, RegistroFrequencia
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            aula = RegistroAula(
                professor_id=rede["prof_id"], turma_id=rede["turma_id"],
                disciplina_id=rede["port_id"], data=date(2026, 3, 11),
                bimestre=1, quantidade_aulas=1, conteudo="A", metodologia="B",
                status="pendente",
            )
            db.session.add(aula)
            db.session.flush()
            db.session.add(RegistroFrequencia(
                aluno_id=mat.aluno_id, turma_id=mat.turma_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], registro_aula_id=aula.id,
                data=date(2026, 3, 11), bimestre=1, situacao="PRESENTE",
            ))
            db.session.commit()
            db.session.add(RegistroFrequencia(
                aluno_id=mat.aluno_id, turma_id=mat.turma_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], registro_aula_id=aula.id,
                data=date(2026, 3, 11), bimestre=1, situacao="FALTA",
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_14_unique_parecer(self):
        rede = self._rede(etapa="infantil")
        with self.app.app_context():
            from src import db
            from src.models import RelatorioDescritivo
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            db.session.add(RelatorioDescritivo(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                eixo="Linguagem", bimestre=1, avaliacao="A",
            ))
            db.session.commit()
            db.session.add(RelatorioDescritivo(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                eixo="Linguagem", bimestre=1, avaliacao="B",
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_15_unique_historico(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            db.session.add(HistoricoAnual(
                ano_letivo=2026, aluno_id=mat.aluno_id, matricula_id=mat.id,
                dados_fechamento="{}", situacao_final="ativo",
            ))
            db.session.commit()
            db.session.add(HistoricoAnual(
                ano_letivo=2026, aluno_id=mat.aluno_id, matricula_id=mat.id,
                dados_fechamento="{}", situacao_final="ativo",
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_16_transferencia_mesmo_ano(self):
        rede = self._rede()
        self._login(rede["inep"])
        resp = self.client.post("/api/turmas", json={
            "nome": "5º Ano B PG", "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_b = next(t["id"] for t in dash["turmas"] if t["nome"] == "5º Ano B PG")
        with self.app.app_context():
            from src import db
            from src.matriculas import VINCULO_CURSANDO, VINCULO_TRANSFERIDO
            from src.models import Matricula, RegistroNota
            mat_a = self._mat(rede["aluno_id"], rede["turma_id"])
            mat_a.situacao = VINCULO_TRANSFERIDO
            mat_a.data_fim = date(2026, 6, 30)
            db.session.add(Matricula(
                aluno_id=rede["aluno_id"], turma_id=turma_b,
                data_inicio=date(2026, 7, 1), situacao=VINCULO_CURSANDO,
            ))
            db.session.commit()
            mat_b = self._mat(rede["aluno_id"], turma_b)
            db.session.add(RegistroNota(
                aluno_id=rede["aluno_id"], matricula_id=mat_a.id,
                disciplina_id=rede["port_id"], bimestre=11, valor=8.0, tipo="bimestral",
            ))
            db.session.add(RegistroNota(
                aluno_id=rede["aluno_id"], matricula_id=mat_b.id,
                disciplina_id=rede["port_id"], bimestre=11, valor=5.0, tipo="bimestral",
            ))
            db.session.commit()
            self.assertEqual(len(Matricula.query.filter_by(aluno_id=rede["aluno_id"]).all()), 2)

    def test_17_rollback_apos_integrity_error(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroNota
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            db.session.add(RegistroNota(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=11, valor=8.0, tipo="bimestral",
            ))
            db.session.commit()
            db.session.add(RegistroNota(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=11, valor=1.0, tipo="bimestral",
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
            db.session.add(RegistroNota(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=12, valor=7.0, tipo="bimestral",
            ))
            db.session.commit()
            self.assertEqual(RegistroNota.query.filter_by(matricula_id=mat.id).count(), 2)

    def test_18_fk_matricula_invalida_recusada(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import Matricula
            db.session.add(Matricula(
                aluno_id=999999, turma_id=rede["turma_id"],
                data_inicio=date(2026, 2, 1), situacao="cursando",
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_19_aluno_com_historico_nao_e_apagado_fisicamente(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroNota
            from src.notas import upsert_nota_matricula
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            upsert_nota_matricula(mat, rede["port_id"], 11, 6.5)
            db.session.commit()
        self._login(rede["inep"])
        resp = self.client.delete("/api/excluir/aluno/%s" % rede["aluno_id"])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula, RegistroNota
            aluno = db.session.get(Aluno, rede["aluno_id"])
            self.assertIsNotNone(aluno)
            self.assertEqual(aluno.situacao, "inativo")
            self.assertTrue(RegistroNota.query.filter_by(aluno_id=aluno.id).count())
            self.assertTrue(Matricula.query.filter_by(aluno_id=aluno.id).count())

    def test_20_delete_orm_aluno_nao_cascateia_matricula(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Matricula
            aluno = db.session.get(Aluno, rede["aluno_id"])
            db.session.delete(aluno)
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
            self.assertIsNotNone(db.session.get(Aluno, rede["aluno_id"]))
            self.assertTrue(Matricula.query.filter_by(aluno_id=rede["aluno_id"]).count())

    def test_21_falta_disciplina_null_postgres(self):
        """Dívida C: UNIQUE PostgreSQL trata NULL de disciplina_id como distinto."""
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroFalta
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            db.session.add(RegistroFalta(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=None, bimestre=1, quantidade=1,
            ))
            db.session.add(RegistroFalta(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=None, bimestre=1, quantidade=2,
            ))
            db.session.commit()
            n = RegistroFalta.query.filter_by(
                matricula_id=mat.id, disciplina_id=None, bimestre=1,
            ).count()
            self.assertEqual(n, 2)

    def test_22_tipos_colunas(self):
        from sqlalchemy import inspect
        from src import db
        with self.app.app_context():
            insp = inspect(db.engine)
            aluno = {c["name"]: c for c in insp.get_columns("aluno")}
            self.assertIn("DATE", str(aluno["data_nascimento"]["type"]).upper())
            aula = {c["name"]: c for c in insp.get_columns("registro_aula")}
            self.assertIn("TIMESTAMP", str(aula["criado_em"]["type"]).upper())
            usuario = {c["name"]: c for c in insp.get_columns("usuario")}
            self.assertIn("BOOLEAN", str(usuario["primeiro_acesso"]["type"]).upper())
            hist = {c["name"]: c for c in insp.get_columns("historico_anual")}
            self.assertIn("TEXT", str(hist["dados_fechamento"]["type"]).upper())
            nota = {c["name"]: c for c in insp.get_columns("registro_nota")}
            self.assertTrue(
                "DOUBLE" in str(nota["valor"]["type"]).upper()
                or "FLOAT" in str(nota["valor"]["type"]).upper()
                or "REAL" in str(nota["valor"]["type"]).upper()
            )

    def test_23_fks_sem_on_delete_cascade_academico(self):
        from sqlalchemy import inspect
        from src import db
        with self.app.app_context():
            insp = inspect(db.engine)
            for tabela in (
                "matricula", "registro_nota", "registro_falta",
                "registro_frequencia", "relatorio_descritivo", "historico_anual",
            ):
                for fk in insp.get_foreign_keys(tabela):
                    ondelete = (fk.get("ondelete") or "").upper()
                    self.assertNotEqual(
                        ondelete, "CASCADE",
                        "%s.%s ondelete=%s" % (tabela, fk.get("constrained_columns"), ondelete),
                    )

    def test_24_autogenerate_sem_diff_estrutural(self):
        from alembic.autogenerate import compare_metadata
        from alembic.runtime.migration import MigrationContext
        from src import db
        with self.app.app_context():
            with db.engine.connect() as conn:
                ctx = MigrationContext.configure(
                    connection=conn,
                    opts={"compare_type": True, "compare_server_default": True},
                )
                diffs = compare_metadata(ctx, db.metadata)
            essenciais = []
            for item in diffs:
                texto = str(item).lower()
                if "nextval" in texto or "serial" in texto or "identity" in texto:
                    continue
                essenciais.append(str(item))
            self.assertEqual(essenciais, [])


if __name__ == "__main__":
    unittest.main()
