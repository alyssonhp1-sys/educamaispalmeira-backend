import os
import tempfile
import unittest
from pathlib import Path


class ValidacaoScriptDemoTest(unittest.TestCase):
    def test_recusa_sem_confirmar(self):
        from scripts.gerar_dados_demo import validar_destino
        _, erro = validar_destino("instance/educamais_demo.db", confirmar=False, substituir=False, permitir_padrao=False)
        self.assertIsNotNone(erro)
        self.assertIn("confirmar", erro.lower())

    def test_recusa_banco_padrao_sem_permissao(self):
        from scripts.gerar_dados_demo import banco_padrao, validar_destino
        _, erro = validar_destino(banco_padrao(), confirmar=True, substituir=True, permitir_padrao=False)
        self.assertIsNotNone(erro)
        self.assertIn("padrão", erro.lower())

    def test_cli_sem_confirmar_nao_cria_arquivo(self):
        from scripts.gerar_dados_demo import main
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.unlink(path)
        code = main(["--destino", path])
        self.assertEqual(code, 2)
        self.assertFalse(os.path.exists(path))

    def test_cli_como_arquivo_encontra_pacote_src(self):
        """python scripts/gerar_dados_demo.py precisa resolver src/ a partir da raiz."""
        import subprocess
        import sys
        root = Path(__file__).resolve().parent.parent
        r = subprocess.run(
            [
                sys.executable,
                str(root / "scripts" / "gerar_dados_demo.py"),
                "--destino",
                str(root / "instance" / "educamais.db"),
                "--confirmar",
            ],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 2, r.stderr or r.stdout)
        self.assertIn("padrão", (r.stderr or "").lower())

    def test_uri_relativa_da_demo_fica_na_raiz_do_projeto(self):
        from src.config import Config, resolver_sqlite_uri
        uri = resolver_sqlite_uri("sqlite:///instance/educamais_demo.db", Config.ROOT_DIR)
        normalizado = uri.replace("\\", "/")
        self.assertTrue(normalizado.endswith("/instance/educamais_demo.db"), uri)
        self.assertTrue(normalizado.startswith("sqlite:///"), uri)
        self.assertIn("EDUCA_MAIS_PALMEIRA", normalizado.upper())

    def test_script_sem_env_permite_arquivo_demo(self):
        from scripts.gerar_dados_demo import validar_destino
        antigo = os.environ.pop("EDUCAMAIS_ENV", None)
        try:
            path, erro = validar_destino(
                "instance/educamais_demo.db", confirmar=True, substituir=True, permitir_padrao=False,
            )
            self.assertIsNone(erro)
            self.assertIsNotNone(path)
        finally:
            if antigo is not None:
                os.environ["EDUCAMAIS_ENV"] = antigo
        from scripts.gerar_dados_demo import executar_preparar_demo
        antigo = os.environ.get("EDUCAMAIS_ENV")
        os.environ["EDUCAMAIS_ENV"] = "production"
        try:
            cred, erro = executar_preparar_demo(confirmar=True, destino="instance/educamais_demo.db")
            self.assertIsNone(cred)
            self.assertIsNotNone(erro)
            self.assertIn("produção", erro.lower())
        finally:
            if antigo is None:
                os.environ.pop("EDUCAMAIS_ENV", None)
            else:
                os.environ["EDUCAMAIS_ENV"] = antigo

    def test_substituir_sem_destino_nao_apaga_banco_atual(self):
        from scripts.gerar_dados_demo import executar_preparar_demo
        cred, erro = executar_preparar_demo(confirmar=True, destino=None, substituir=True)
        self.assertIsNone(cred)
        self.assertIn("substituir", (erro or "").lower())


class DadosDemoPopuladoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        cls._db_path = tmp.name
        from src.config import Config
        from tests.support import apontar_banco_teste, preparar_ambiente_teste
        cls._old_uri = Config.SQLALCHEMY_DATABASE_URI
        cls._old_engine_opts = Config.SQLALCHEMY_ENGINE_OPTIONS
        cls._old_demo = os.environ.get("EDUCAMAIS_DEMO")
        cls._old_db_env = os.environ.get("EDUCAMAIS_DATABASE_URI")
        preparar_ambiente_teste(demo=True)
        uri = "sqlite:///" + Path(cls._db_path).resolve().as_posix()
        apontar_banco_teste(uri)
        from src import create_app
        cls.app = create_app()
        cls.app.testing = True
        with cls.app.app_context():
            from scripts.gerar_dados_demo import popular_dados_demo
            from src.models import Aluno, Escola
            cls.cred = popular_dados_demo()
            cls.n_escolas = Escola.query.count()
            cls.n_alunos = Aluno.query.count()
            cls.cred2 = popular_dados_demo()

    @classmethod
    def tearDownClass(cls):
        from src import db
        from src.config import Config
        try:
            with cls.app.app_context():
                db.session.remove()
                db.engine.dispose()
        except Exception:
            pass
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        Config.SQLALCHEMY_ENGINE_OPTIONS = cls._old_engine_opts
        if cls._old_demo is None:
            os.environ.pop("EDUCAMAIS_DEMO", None)
        else:
            os.environ["EDUCAMAIS_DEMO"] = cls._old_demo
        if cls._old_db_env is None:
            os.environ.pop("EDUCAMAIS_DATABASE_URI", None)
        else:
            os.environ["EDUCAMAIS_DATABASE_URI"] = cls._old_db_env
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass

    def test_estrutura_minima_ficticia(self):
        with self.app.app_context():
            from src.models import Aluno, Escola, Planejamento, Professor, RegistroFalta, RegistroNota, Turma, Usuario
            from scripts.gerar_dados_demo import (
                INEP_CAMINHOS, INEP_CRECHE, INEP_LUA, INEP_SERRA, INEP_SOL,
            )
            self.assertEqual(Escola.query.count(), 5)
            self.assertTrue(Escola.query.filter_by(inep=INEP_SOL).first())
            self.assertTrue(Escola.query.filter_by(inep=INEP_LUA).first())
            self.assertTrue(Escola.query.filter_by(inep=INEP_CAMINHOS).first())
            self.assertTrue(Escola.query.filter_by(inep=INEP_SERRA).first())
            self.assertTrue(Escola.query.filter_by(inep=INEP_CRECHE).first())
            self.assertGreaterEqual(Usuario.query.filter_by(papel="gestor_escolar").count(), 5)
            self.assertGreaterEqual(Usuario.query.filter_by(papel="coordenador_pedagogico").count(), 1)
            self.assertGreaterEqual(Professor.query.count(), 2)
            self.assertGreaterEqual(Turma.query.count(), 2)
            self.assertTrue(Aluno.query.filter_by(nome="Carla Sem Turma Demo", turma_id=None).first())
            self.assertTrue(Aluno.query.filter_by(nome="Bruno Recuperação Demo").first())
            self.assertTrue(Aluno.query.filter_by(nome="Ana Aprovada Demo").first())
            self.assertGreater(RegistroNota.query.count(), 0)
            self.assertGreater(RegistroFalta.query.count(), 0)
            self.assertTrue(Planejamento.query.filter_by(status="pendente").first())
            self.assertTrue(Planejamento.query.filter_by(status="aprovado").first())

    def test_rede_demo_coerente(self):
        with self.app.app_context():
            from src.models import Aluno, Turma
            n = Aluno.query.count()
            self.assertGreaterEqual(n, 80)
            self.assertLessEqual(n, 140)
            self.assertTrue(Turma.query.filter_by(etapa_ensino="infantil", status="ativa").first())
            self.assertTrue(Turma.query.filter_by(etapa_ensino="fundamental", status="ativa").first())
            self.assertTrue(Aluno.query.filter_by(situacao="inativo").first())
            self.assertTrue(Aluno.query.filter_by(nome="Carla Sem Turma Demo", turma_id=None).first())

    def test_diario_e_falta_docente(self):
        with self.app.app_context():
            from src.models import (
                AnoLetivo,
                DiaCalendario,
                HorarioAlocacao,
                OcorrenciaAulaNaoMinistrada,
                RegistroAula,
                RegistroFrequencia,
                RelatorioDescritivo,
            )
            self.assertTrue(AnoLetivo.query.filter_by(ano=2026, ativo=True).first())
            tipos = {d.tipo for d in DiaCalendario.query.all()}
            for tipo in ("LETIVO", "FERIADO", "RECESSO", "REPOSICAO", "NAO_LETIVO"):
                self.assertIn(tipo, tipos, tipo)
            self.assertTrue(HorarioAlocacao.query.first())
            self.assertTrue(RegistroAula.query.filter_by(status="pendente").first())
            self.assertTrue(RegistroAula.query.filter_by(status="validada").first())
            self.assertTrue(RegistroAula.query.filter_by(status="invalidada").first())
            pendentes = RegistroAula.query.filter_by(status="pendente").count()
            self.assertLessEqual(pendentes, 5)
            self.assertTrue(RegistroFrequencia.query.filter_by(situacao="PRESENTE").first())
            self.assertTrue(RegistroFrequencia.query.filter_by(situacao="FALTA").first())
            self.assertTrue(OcorrenciaAulaNaoMinistrada.query.filter_by(status="ATIVA").first())
            self.assertTrue(OcorrenciaAulaNaoMinistrada.query.filter_by(status="CANCELADA").first())
            self.assertTrue(RelatorioDescritivo.query.first())

    def test_relatorio_municipal_e_risco(self):
        with self.app.app_context():
            from src.faltas_docentes import relatorio_faltas_docentes, resumo_faltas_docentes
            from src.indicadores import listar_alunos_em_risco, resumo_rede
            from src.models import STATUS_OCORRENCIA_ATIVA
            from scripts.gerar_dados_demo import DATA_FALTA_ATIVA
            resumo = resumo_rede()
            self.assertEqual(resumo["totalEscolas"], 5)
            self.assertGreater(resumo["registrosAulaPendentes"], 0)
            self.assertGreater(resumo["alunosSemTurma"], 0)
            risco = listar_alunos_em_risco(limite=20)
            self.assertGreater(len(risco), 0)
            fd = resumo_faltas_docentes()
            self.assertGreaterEqual(fd["faltasDocentesAtivas"], 1)
            rel = relatorio_faltas_docentes(filtros={
                "data_inicio": DATA_FALTA_ATIVA,
                "data_fim": DATA_FALTA_ATIVA,
                "status": STATUS_OCORRENCIA_ATIVA,
            })
            self.assertGreater(rel["indicadores"]["ocorrenciasAtivas"], 0)
            self.assertTrue(rel["porEscola"])

    def test_historico_auditoria_e_credenciais_ficticias(self):
        with self.app.app_context():
            from src.models import Aluno, AuditLog, HistoricoAnual, Professor, TrajetoriaEscolarLegada
            self.assertTrue(HistoricoAnual.query.first())
            self.assertTrue(TrajetoriaEscolarLegada.query.first())
            acoes = {a.acao for a in AuditLog.query.all()}
            for necessaria in (
                "NOTA_CRIADA", "REGISTRO_AULA_CRIADO", "REGISTRO_AULA_VALIDADO",
                "REGISTRO_AULA_INVALIDADO", "FALTA_DOCENTE_REGISTRADA",
                "FALTA_DOCENTE_CANCELADA",
            ):
                self.assertIn(necessaria, acoes, necessaria)
            for aluno in Aluno.query.all():
                self.assertTrue(str(aluno.cpf or "").startswith("88"), aluno.nome)
            for prof in Professor.query.all():
                self.assertTrue(str(prof.cpf or "").startswith("88"), prof.nome)
            self.assertEqual(self.cred["senha"], "demo1234")
            self.assertTrue(self.cred.get("ficticio"))

    def test_situacoes_academicas_demo(self):
        with self.app.app_context():
            from src.models import Aluno, Disciplina
            from src.notas import calcular_notas_aluno, calcular_notas_matricula
            from src.matriculas import matricula_cursando
            mat = Disciplina.query.filter_by(nome="Matemática").first()
            ana = Aluno.query.filter_by(nome="Ana Aprovada Demo").first()
            bruno = Aluno.query.filter_by(nome="Bruno Recuperação Demo").first()
            self.assertEqual(calcular_notas_aluno(ana.id, mat.id)["situacao"], "APROVADO")
            self.assertEqual(calcular_notas_aluno(bruno.id, mat.id)["situacao"], "EM RECUPERAÇÃO")
            self.assertEqual(
                calcular_notas_matricula(matricula_cursando(ana).id, mat.id)["situacao"],
                "APROVADO",
            )

    def test_uma_matricula_cursando_e_backfill_preserva_contagem(self):
        with self.app.app_context():
            from src.models import Aluno, Matricula, RegistroFalta, RegistroNota, RelatorioDescritivo, ensure_schema
            from src.matriculas import VINCULO_CURSANDO, backfill_registros_academicos
            for aluno in Aluno.query.filter(Aluno.turma_id.isnot(None)).all():
                self.assertEqual(
                    Matricula.query.filter_by(aluno_id=aluno.id, situacao=VINCULO_CURSANDO).count(),
                    1,
                    aluno.nome,
                )
                self.assertGreaterEqual(Matricula.query.filter_by(aluno_id=aluno.id).count(), 1)
            n_notas = RegistroNota.query.count()
            n_faltas = RegistroFalta.query.count()
            self.assertGreater(n_notas, 0)
            self.assertGreater(n_faltas, 0)
            backfill_registros_academicos()
            ensure_schema()
            self.assertEqual(RegistroNota.query.count(), n_notas)
            self.assertEqual(RegistroFalta.query.count(), n_faltas)
            self.assertTrue(all(n.matricula_id for n in RegistroNota.query.all()))
            self.assertTrue(all(f.matricula_id for f in RegistroFalta.query.all()))
            self.assertTrue(all(r.matricula_id for r in RelatorioDescritivo.query.all()))

    def test_segunda_execucao_nao_duplica(self):
        with self.app.app_context():
            from src.models import Aluno, Escola, OcorrenciaAulaNaoMinistrada, RegistroAula
            self.assertEqual(Escola.query.count(), self.n_escolas)
            self.assertEqual(Aluno.query.count(), self.n_alunos)
            n_aulas = RegistroAula.query.count()
            n_occ = OcorrenciaAulaNaoMinistrada.query.count()
            from scripts.gerar_dados_demo import popular_dados_demo
            popular_dados_demo()
            self.assertEqual(Escola.query.count(), self.n_escolas)
            self.assertEqual(Aluno.query.count(), self.n_alunos)
            self.assertEqual(RegistroAula.query.count(), n_aulas)
            self.assertEqual(OcorrenciaAulaNaoMinistrada.query.count(), n_occ)

    def test_login_demo_gestor(self):
        client = self.app.test_client()
        from scripts.gerar_dados_demo import INEP_SOL, SENHA_DEMO
        resp = client.post("/api/auth/login", json={"login": INEP_SOL, "senha": SENHA_DEMO})
        self.assertEqual(resp.status_code, 200, (resp.get_json() or {}).get("error"))

    def test_seed_nao_usa_sql_sqlite_especifico(self):
        texto = Path(__file__).resolve().parents[1].joinpath("scripts", "gerar_dados_demo.py").read_text(encoding="utf-8")
        self.assertNotIn("sqlite_master", texto)
        self.assertNotIn("PRAGMA", texto)


if __name__ == "__main__":
    unittest.main()
