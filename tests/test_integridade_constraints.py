"""Constraints UNIQUE parciais de integridade acadêmica (Bloco 2)."""
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class ConstraintsIntegridadeTest(unittest.TestCase):
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

    def _proximo(self):
        ConstraintsIntegridadeTest._seq += 1
        return ConstraintsIntegridadeTest._seq

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _rede(self, etapa="fundamental"):
        n = self._proximo()
        inep = f"7900{n:04d}"
        cpf_prof = f"7901{n:07d}"[:11]
        cpf_aluno = f"7902{n:07d}"[:11]
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": f"Escola Cons {inep}",
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Cons", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        nome_turma = "5º Ano Cons" if etapa == "fundamental" else "Pré Cons"
        self.client.post("/api/turmas", json={
            "nome": nome_turma, "ano_letivo": 2026, "etapa": etapa,
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == nome_turma)
        port_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Português")
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": "Aluno Cons", "cpf": cpf_aluno, "turma_id": turma_id, "nome_mae": "Mãe Cons",
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

    def test_nota_duplicata_moderna_integrity_error(self):
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

    def test_nota_celulas_legitimas_coexistem(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroNota
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            for bim, val in ((11, 7.0), (12, 8.0), (13, 6.0), (51, 7.5), (99, 8.0)):
                db.session.add(RegistroNota(
                    aluno_id=mat.aluno_id, matricula_id=mat.id,
                    disciplina_id=rede["port_id"], bimestre=bim, valor=val, tipo="bimestral",
                ))
            db.session.commit()
            self.assertEqual(
                RegistroNota.query.filter_by(matricula_id=mat.id).count(), 5,
            )

    def test_nota_null_legado_nao_quebra_unique_parcial(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroNota
            db.session.add(RegistroNota(
                aluno_id=rede["aluno_id"], matricula_id=None,
                disciplina_id=rede["port_id"], bimestre=11, valor=5.0, tipo="bimestral",
            ))
            db.session.add(RegistroNota(
                aluno_id=rede["aluno_id"], matricula_id=None,
                disciplina_id=rede["port_id"], bimestre=11, valor=4.0, tipo="bimestral",
            ))
            db.session.commit()
            self.assertEqual(
                RegistroNota.query.filter_by(
                    aluno_id=rede["aluno_id"], matricula_id=None, bimestre=11,
                ).count(),
                2,
            )

    def test_falta_duplicata_e_outro_bimestre(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroFalta
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            db.session.add(RegistroFalta(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=1, quantidade=2,
            ))
            db.session.commit()
            db.session.add(RegistroFalta(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=1, quantidade=3,
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
            db.session.add(RegistroFalta(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=2, quantidade=1,
            ))
            db.session.commit()
            self.assertEqual(RegistroFalta.query.filter_by(matricula_id=mat.id).count(), 2)

    def test_falta_null_legado_permitido(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import RegistroFalta
            db.session.add(RegistroFalta(
                aluno_id=rede["aluno_id"], matricula_id=None,
                disciplina_id=rede["port_id"], bimestre=1, quantidade=2,
            ))
            db.session.add(RegistroFalta(
                aluno_id=rede["aluno_id"], matricula_id=None,
                disciplina_id=rede["port_id"], bimestre=1, quantidade=4,
            ))
            db.session.commit()
            self.assertEqual(
                RegistroFalta.query.filter_by(
                    aluno_id=rede["aluno_id"], matricula_id=None, bimestre=1,
                ).count(),
                2,
            )

    def test_frequencia_unidade_e_registro_aula(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import Professor, RegistroAula, RegistroFrequencia
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            prof = Professor.query.filter_by(id=rede["prof_id"]).one()
            aula1 = RegistroAula(
                professor_id=prof.id, turma_id=rede["turma_id"],
                disciplina_id=rede["port_id"], data=date(2026, 3, 10),
                bimestre=1, quantidade_aulas=2, conteudo="A", metodologia="B",
                status="pendente",
            )
            aula2 = RegistroAula(
                professor_id=prof.id, turma_id=rede["turma_id"],
                disciplina_id=rede["port_id"], data=date(2026, 3, 10),
                bimestre=1, quantidade_aulas=1, conteudo="C", metodologia="D",
                status="pendente",
            )
            db.session.add_all([aula1, aula2])
            db.session.flush()
            db.session.add(RegistroFrequencia(
                aluno_id=mat.aluno_id, turma_id=mat.turma_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], registro_aula_id=aula1.id,
                data=date(2026, 3, 10), bimestre=1, situacao="PRESENTE",
            ))
            db.session.commit()
            db.session.add(RegistroFrequencia(
                aluno_id=mat.aluno_id, turma_id=mat.turma_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], registro_aula_id=aula1.id,
                data=date(2026, 3, 10), bimestre=1, situacao="FALTA",
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
            db.session.add(RegistroFrequencia(
                aluno_id=mat.aluno_id, turma_id=mat.turma_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], registro_aula_id=aula2.id,
                data=date(2026, 3, 10), bimestre=1, situacao="FALTA",
            ))
            db.session.commit()
            self.assertEqual(
                RegistroFrequencia.query.filter_by(matricula_id=mat.id).count(), 2,
            )

    def test_frequencia_outra_matricula_permitida(self):
        rede = self._rede()
        n = self._proximo()
        self._login(rede["inep"])
        resp = self.client.post("/api/alunos", json={
            "nome": "Colega Cons",
            "cpf": f"7908{n:07d}"[:11],
            "turma_id": rede["turma_id"],
            "nome_mae": "Mãe Colega",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        colega_id = _json(resp)[0]["id"]
        with self.app.app_context():
            from src import db
            from src.models import Professor, RegistroAula, RegistroFrequencia
            mat_a = self._mat(rede["aluno_id"], rede["turma_id"])
            mat_b = self._mat(colega_id, rede["turma_id"])
            prof = Professor.query.filter_by(id=rede["prof_id"]).one()
            aula = RegistroAula(
                professor_id=prof.id, turma_id=rede["turma_id"],
                disciplina_id=rede["port_id"], data=date(2026, 3, 11),
                bimestre=1, quantidade_aulas=1, conteudo="A", metodologia="B",
                status="pendente",
            )
            db.session.add(aula)
            db.session.flush()
            db.session.add(RegistroFrequencia(
                aluno_id=mat_a.aluno_id, turma_id=mat_a.turma_id, matricula_id=mat_a.id,
                disciplina_id=rede["port_id"], registro_aula_id=aula.id,
                data=date(2026, 3, 11), bimestre=1, situacao="PRESENTE",
            ))
            db.session.add(RegistroFrequencia(
                aluno_id=mat_b.aluno_id, turma_id=mat_b.turma_id, matricula_id=mat_b.id,
                disciplina_id=rede["port_id"], registro_aula_id=aula.id,
                data=date(2026, 3, 11), bimestre=1, situacao="FALTA",
            ))
            db.session.commit()
            self.assertEqual(
                RegistroFrequencia.query.filter_by(registro_aula_id=aula.id).count(), 2,
            )

    def test_parecer_duplicata_e_outro_eixo(self):
        rede = self._rede(etapa="infantil")
        with self.app.app_context():
            from src import db
            from src.models import RelatorioDescritivo
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            db.session.add(RelatorioDescritivo(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                eixo="Linguagem", bimestre=1, avaliacao="Em desenvolvimento",
            ))
            db.session.commit()
            db.session.add(RelatorioDescritivo(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                eixo="Linguagem", bimestre=1, avaliacao="Conquistado",
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
            db.session.add(RelatorioDescritivo(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                eixo="Socioafetivo", bimestre=1, avaliacao="Em desenvolvimento",
            ))
            db.session.add(RelatorioDescritivo(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                eixo="Linguagem", bimestre=2, avaliacao="Conquistado",
            ))
            db.session.commit()
            self.assertEqual(
                RelatorioDescritivo.query.filter_by(matricula_id=mat.id).count(), 3,
            )

    def test_parecer_null_legado_permitido(self):
        rede = self._rede(etapa="infantil")
        with self.app.app_context():
            from src import db
            from src.models import RelatorioDescritivo
            db.session.add(RelatorioDescritivo(
                aluno_id=rede["aluno_id"], matricula_id=None,
                eixo="Linguagem", bimestre=1, avaliacao="Em desenvolvimento",
            ))
            db.session.add(RelatorioDescritivo(
                aluno_id=rede["aluno_id"], matricula_id=None,
                eixo="Linguagem", bimestre=1, avaliacao="Conquistado",
            ))
            db.session.commit()
            self.assertEqual(
                RelatorioDescritivo.query.filter_by(
                    aluno_id=rede["aluno_id"], matricula_id=None,
                ).count(),
                2,
            )

    def test_historico_uma_matricula_e_null_legado(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            db.session.add(HistoricoAnual(
                ano_letivo=2026, aluno_id=mat.aluno_id, matricula_id=mat.id,
                escola_nome="E", turma_nome="T", etapa="fundamental",
                dados_fechamento="{}",
            ))
            db.session.commit()
            db.session.add(HistoricoAnual(
                ano_letivo=2026, aluno_id=mat.aluno_id, matricula_id=mat.id,
                escola_nome="E", turma_nome="T", etapa="fundamental",
                dados_fechamento="{}",
            ))
            with self.assertRaises(IntegrityError):
                db.session.commit()
            db.session.rollback()
            db.session.add(HistoricoAnual(
                ano_letivo=2025, aluno_id=rede["aluno_id"], matricula_id=None,
                escola_nome="L1", turma_nome="T1", etapa="fundamental",
                dados_fechamento="{}",
            ))
            db.session.add(HistoricoAnual(
                ano_letivo=2024, aluno_id=rede["aluno_id"], matricula_id=None,
                escola_nome="L2", turma_nome="T2", etapa="fundamental",
                dados_fechamento="{}",
            ))
            db.session.commit()
            self.assertEqual(
                HistoricoAnual.query.filter_by(aluno_id=rede["aluno_id"], matricula_id=None).count(),
                2,
            )
            self.assertEqual(
                HistoricoAnual.query.filter_by(matricula_id=mat.id).count(), 1,
            )

    def test_transferencia_mesmo_ano_duas_matriculas(self):
        rede = self._rede()
        self._login(rede["inep"])
        resp = self.client.post("/api/turmas", json={
            "nome": "5º Ano B Transf", "ano_letivo": 2026, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_b = next(t["id"] for t in dash["turmas"] if t["nome"] == "5º Ano B Transf")
        with self.app.app_context():
            from src import db
            from src.matriculas import VINCULO_CURSANDO, VINCULO_TRANSFERIDO
            from src.models import Matricula, RegistroNota, Turma
            mat_a = self._mat(rede["aluno_id"], rede["turma_id"])
            mat_a.situacao = VINCULO_TRANSFERIDO
            mat_a.data_fim = date(2026, 6, 30)
            turma_b_obj = db.session.get(Turma, turma_b)
            self.assertEqual(turma_b_obj.ano_letivo, 2026)
            db.session.add(Matricula(
                aluno_id=rede["aluno_id"], turma_id=turma_b,
                data_inicio=date(2026, 7, 1), situacao=VINCULO_CURSANDO,
            ))
            db.session.commit()
            mats = Matricula.query.filter_by(aluno_id=rede["aluno_id"]).all()
            self.assertEqual(len(mats), 2)
            anos = {m.turma.ano_letivo for m in mats}
            self.assertEqual(anos, {2026})
            mat_b = self._mat(rede["aluno_id"], turma_b)
            self.assertNotEqual(mat_a.id, mat_b.id)
            db.session.add(RegistroNota(
                aluno_id=rede["aluno_id"], matricula_id=mat_a.id,
                disciplina_id=rede["port_id"], bimestre=11, valor=8.0, tipo="bimestral",
            ))
            db.session.add(RegistroNota(
                aluno_id=rede["aluno_id"], matricula_id=mat_b.id,
                disciplina_id=rede["port_id"], bimestre=11, valor=5.0, tipo="bimestral",
            ))
            db.session.commit()
            self.assertEqual(
                RegistroNota.query.filter_by(matricula_id=mat_a.id, bimestre=11).one().valor,
                8.0,
            )
            self.assertEqual(
                RegistroNota.query.filter_by(matricula_id=mat_b.id, bimestre=11).one().valor,
                5.0,
            )

    def test_cli_auditar_integridade_readonly(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.integridade import auditar_integridade
            from src.models import RegistroNota
            n_antes = RegistroNota.query.count()
            mat = self._mat(rede["aluno_id"], rede["turma_id"])
            db.session.add(RegistroNota(
                aluno_id=mat.aluno_id, matricula_id=mat.id,
                disciplina_id=rede["port_id"], bimestre=12, valor=7.0, tipo="bimestral",
            ))
            db.session.commit()
        runner = self.app.test_cli_runner()
        result = runner.invoke(args=["educamais", "auditar-integridade"])
        self.assertEqual(result.exit_code, 0, result.output or result.exception)
        self.assertIn("somente leitura", result.output.lower())
        self.assertNotIn("Aluno Cons", result.output)
        with self.app.app_context():
            from src.models import RegistroNota
            self.assertEqual(RegistroNota.query.count(), n_antes + 1)
            rel = auditar_integridade()
            self.assertTrue(rel["somente_leitura"])
            self.assertEqual(rel["incompativeis"], [])


if __name__ == "__main__":
    unittest.main()
