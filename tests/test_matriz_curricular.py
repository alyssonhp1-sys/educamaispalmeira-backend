"""Vigência da matriz curricular municipal (2026). Sem aplicação retroativa."""
import os
import tempfile
import unittest
from pathlib import Path


class MatrizCurricularTest(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        from src.config import Config
        Config.SQLALCHEMY_DATABASE_URI = cls._old_uri
        try:
            os.unlink(cls._db_path)
        except OSError:
            pass

    def test_2026_iniciais_tem_componentes_oficiais(self):
        with self.app.app_context():
            from src.matriz_curricular import componentes_da_serie, inferir_serie
            self.assertEqual(inferir_serie("5º A"), 5)
            nomes = [c.nome for c in componentes_da_serie(2026, 5)]
            self.assertIn("Língua Portuguesa", nomes)
            self.assertIn("Cultura Digital", nomes)
            self.assertIn("Formação Humana", nomes)
            self.assertIn("Eletivas", nomes)
            self.assertNotIn("Biologia", nomes)
            self.assertNotIn("Projeto de Vida", nomes)

    def test_2026_finais_ciencias_versus_natureza_no_9o(self):
        with self.app.app_context():
            from src.matriz_curricular import componentes_da_serie
            for serie in (6, 7, 8):
                nomes = [c.nome for c in componentes_da_serie(2026, serie)]
                self.assertIn("Ciências", nomes, serie)
                self.assertNotIn("Biologia", nomes)
                self.assertNotIn("Química", nomes)
                self.assertNotIn("Física", nomes)
                self.assertIn("Projeto de Vida", nomes)
            n9 = [c.nome for c in componentes_da_serie(2026, 9)]
            self.assertNotIn("Ciências", n9)
            self.assertIn("Biologia", n9)
            self.assertIn("Química", n9)
            self.assertIn("Física", n9)
            bio = next(c for c in componentes_da_serie(2026, 9) if c.nome == "Biologia")
            self.assertEqual(bio.carga_horaria_anual, 40)

    def test_ano_anterior_nao_tem_matriz(self):
        with self.app.app_context():
            from src.matriz_curricular import (
                ano_regido_pela_matriz, carga_oficial, componentes_da_serie, matriz_vigente,
            )
            from src.matriz_curricular import ETAPA_INICIAIS
            self.assertIsNone(matriz_vigente(2025, ETAPA_INICIAIS))
            self.assertIsNone(matriz_vigente(2024, ETAPA_INICIAIS))
            self.assertFalse(ano_regido_pela_matriz(2025, 5))
            self.assertEqual(componentes_da_serie(2025, 5), [])
            self.assertIsNone(carga_oficial(2025, 5, "Língua Portuguesa"))
            self.assertEqual(carga_oficial(2026, 5, "Português"), 320)

    def test_matriz_mais_especifica_nao_altera_ano_anterior(self):
        """2027+ não reescreve 2026; ano sem cobertura continua sem matriz."""
        with self.app.app_context():
            from src import db
            from src.matriz_curricular import (
                ETAPA_INICIAIS, matriz_vigente, nomes_oficiais_da_serie,
            )
            from src.models import MatrizComponente, MatrizCurricular
            atual = matriz_vigente(2026, ETAPA_INICIAIS)
            self.assertIsNotNone(atual)
            self.assertEqual(atual.ano_inicio, 2026)
            nomes_2026 = nomes_oficiais_da_serie(2026, 5)
            nova = MatrizCurricular(
                nome="Matriz Curricular Municipal — Anos Iniciais 2027",
                ano_inicio=2027,
                ano_fim=None,
                etapa=ETAPA_INICIAIS,
                carga_horaria_semanal=35,
                dias_letivos=200,
                carga_horaria_anual=1400,
                ativa=True,
            )
            db.session.add(nova)
            db.session.flush()
            db.session.add(MatrizComponente(
                matriz_id=nova.id,
                nome="Componente Só 2027",
                categoria="BASE_COMUM",
                area="Teste",
                ano_serie=5,
                aulas_semanais=1,
                carga_horaria_anual=40,
                ordem=1,
            ))
            db.session.commit()
            try:
                ainda_2026 = matriz_vigente(2026, ETAPA_INICIAIS)
                self.assertEqual(ainda_2026.id, atual.id)
                self.assertEqual(nomes_oficiais_da_serie(2026, 5), nomes_2026)
                self.assertNotIn("Componente Só 2027", nomes_oficiais_da_serie(2026, 5))
                de_2027 = matriz_vigente(2027, ETAPA_INICIAIS)
                self.assertEqual(de_2027.id, nova.id)
                self.assertEqual(nomes_oficiais_da_serie(2027, 5), ["Componente Só 2027"])
                self.assertIsNone(matriz_vigente(2025, ETAPA_INICIAIS))
            finally:
                MatrizComponente.query.filter_by(matriz_id=nova.id).delete()
                db.session.delete(nova)
                db.session.commit()

    def test_garantir_matriz_e_idempotente(self):
        with self.app.app_context():
            from src.matriz_curricular import garantir_matriz_2026
            from src.models import MatrizComponente, MatrizCurricular
            n_m = MatrizCurricular.query.count()
            n_c = MatrizComponente.query.count()
            garantir_matriz_2026()
            self.assertEqual(MatrizCurricular.query.count(), n_m)
            self.assertEqual(MatrizComponente.query.count(), n_c)
            self.assertGreater(n_m, 0)
            self.assertGreater(n_c, 0)
