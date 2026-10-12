import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria, texto_pdf


EIXO_A = "Eu, o outro e o nós"


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class DocumentosHistoricosTest(unittest.TestCase):
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
        self._definir_media(6.0)

    def tearDown(self):
        self._definir_media(6.0)
        with self.app.app_context():
            from src import db
            from src.models import Disciplina
            mat = Disciplina.query.filter_by(nome="Matemática").first()
            if mat is None:
                outro = Disciplina.query.filter(
                    Disciplina.nome.like("Matemática%"),
                ).order_by(Disciplina.id.asc()).first()
                if outro is not None:
                    outro.nome = "Matemática"
                    db.session.commit()

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _definir_media(self, valor):
        with self.app.app_context():
            from src import db
            from src.models import ConfiguracaoAcademica
            cfg = ConfiguracaoAcademica.query.order_by(ConfiguracaoAcademica.id).first()
            if cfg is not None:
                cfg.media_minima = float(valor)
                db.session.commit()

    def _proximo(self):
        DocumentosHistoricosTest._seq += 1
        return DocumentosHistoricosTest._seq

    def _rede(self, infantil=False):
        n = self._proximo()
        inep = f"7900{n:04d}"
        cpf_prof = f"7901{n:07d}"[:11]
        cpf_joao = f"7902{n:07d}"[:11]
        etapa = "infantil" if infantil else "fundamental"
        nome_turma = "Infantil V" if infantil else "5º A"
        escola_nome = f"Escola Doc {inep}"
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": escola_nome,
            "inep": inep,
            "oferta_infantil": True,
            "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        login_coord = f"coordDoc{n}"
        resp = self.client.post(
            f"/api/escolas/{escola_id}/coordenadores",
            json={"login": login_coord, "nome": "Coord Doc"},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_coord = senha_temporaria(resp)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Doc", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        self.client.post("/api/turmas", json={
            "nome": nome_turma, "ano_letivo": 2026, "etapa": etapa,
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2026 = dash["turmas"][0]["id"]
        mat_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Matemática")
        port_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Português")
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": "Ana" if infantil else "João",
            "cpf": cpf_joao,
            "turma_id": turma_2026,
            "nome_mae": "Mãe Principal",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aluno_id = _json(resp)[0]["id"]
        aloc = {"professor_id": prof_id, "turma_id": turma_2026}
        if not infantil:
            aloc["disciplina_id"] = mat_id
        self.assertEqual(self.client.post("/api/alocacoes", json=aloc).status_code, 200)
        concluir_primeiro_acesso(self.client, cpf_prof, temp_prof)
        self._login("admin", SENHA_DEMO_ADMIN)
        concluir_primeiro_acesso(self.client, login_coord, temp_coord)
        return {
            "inep": inep,
            "cpf_prof": cpf_prof,
            "cpf_aluno": cpf_joao,
            "login_coord": login_coord,
            "turma_2026": turma_2026,
            "turma_nome": nome_turma,
            "escola_id": escola_id,
            "escola_nome": escola_nome,
            "mat_id": mat_id,
            "port_id": port_id,
            "aluno_id": aluno_id,
            "prof_id": prof_id,
            "infantil": infantil,
        }

    def _nota(self, aluno_id, valor, bimestre=11, tipo="nota"):
        return {"aluno_id": aluno_id, "valor": valor, "tipo": tipo, "bimestre": bimestre}

    def _notas_ano(self, aluno_id, n1, n2):
        payload = []
        for a, b in ((11, 12), (21, 22), (31, 32), (41, 42)):
            payload.append(self._nota(aluno_id, str(n1), a))
            payload.append(self._nota(aluno_id, str(n2), b))
        return payload

    def _salvar_notas(self, turma_id, disc_id, payload):
        return self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/{disc_id}/notas",
            json=payload,
        )

    def _parecer(self, aluno_id, texto, eixo=EIXO_A, bimestre=1):
        return {
            "aluno_id": aluno_id,
            "eixo": eixo,
            "bimestre": bimestre,
            "avaliacao": "Em Desenvolvimento",
            "observacao": texto,
        }

    def _salvar_parecer(self, turma_id, payload):
        return self.client.post(
            f"/api/turmas/{turma_id}/disciplinas/0/notas",
            json=payload,
        )

    def _encerrar(self, turma_id, inep):
        self._login(inep)
        resp = self.client.post(f"/api/turmas/{turma_id}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _mat_id(self, aluno_id, turma_id):
        from src.models import Matricula
        return Matricula.query.filter_by(aluno_id=aluno_id, turma_id=turma_id).one().id

    def _hist(self, aluno_id, turma_id=None):
        from src.models import HistoricoAnual
        q = HistoricoAnual.query.filter_by(aluno_id=aluno_id)
        if turma_id is not None:
            q = q.filter_by(matricula_id=self._mat_id(aluno_id, turma_id))
        return q.one()

    def _html(self, resp):
        return texto_pdf(resp)

    def _boletim_hist(self, hist_id):
        return self.client.get(f"/api/historicos/{hist_id}/boletim.pdf")

    def _parecer_hist(self, hist_id):
        return self.client.get(f"/api/historicos/{hist_id}/parecer.pdf")

    def _lista(self, aluno_id):
        return self.client.get(f"/api/alunos/{aluno_id}/historicos")

    def _fechar_fundamental(self, n1=8, n2=8):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], n1, n2),
        ).status_code, 200)
        self._encerrar(rede["turma_2026"], rede["inep"])
        with self.app.app_context():
            hist = self._hist(rede["aluno_id"], rede["turma_2026"])
            hist_id = hist.id
            json_antes = hist.dados_fechamento
        return rede, hist_id, json_antes

    def test_lista_historicos_disponiveis_do_aluno(self):
        rede, hist_id, _ = self._fechar_fundamental()
        self._login(rede["inep"])
        resp = self._lista(rede["aluno_id"])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        itens, _ = _json(resp)
        self.assertEqual(len(itens), 1)
        item = itens[0]
        self.assertEqual(item["historico_id"], hist_id)
        self.assertEqual(item["ano_letivo"], 2026)
        self.assertEqual(item["escola_nome"], rede["escola_nome"])
        self.assertEqual(item["turma_nome"], "5º A")
        self.assertEqual(item["tipo"], "fundamental")
        self.assertEqual(item["documento"], "boletim")
        self.assertIn("matricula_id", item)
        self.assertNotIn("cpf", json.dumps(item))
        self.assertNotIn("nome_mae", json.dumps(item))

    def test_lista_respeita_acl_professor_e_outra_escola(self):
        rede_a, _, _ = self._fechar_fundamental()
        rede_b = self._rede()
        self._login(rede_a["cpf_prof"])
        resp = self._lista(rede_a["aluno_id"])
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(_json(resp)[0])
        self._login(rede_b["inep"])
        resp = self._lista(rede_a["aluno_id"])
        self.assertEqual(resp.status_code, 403)
        corpo = resp.get_data(as_text=True)
        self.assertNotIn("João", corpo)
        self.assertNotIn(rede_a["escola_nome"], corpo)
        self.assertNotIn("5º A", corpo)

    def test_boletim_historico_usa_historico_anual(self):
        rede, hist_id, json_antes = self._fechar_fundamental(8, 8)
        self._login(rede["inep"])
        resp = self._boletim_hist(hist_id)
        self.assertEqual(resp.status_code, 200)
        html = self._html(resp)
        self.assertIn("João", html)
        self.assertIn(rede["escola_nome"], html)
        self.assertIn("5º A", html)
        self.assertIn("2026", html)
        self.assertIn("Matemática", html)
        self.assertIn("Documento referente ao ano letivo de 2026", html)
        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual
            self.assertEqual(db.session.get(HistoricoAnual, hist_id).dados_fechamento, json_antes)

    def test_boletim_2026_nao_mistura_matricula_2027(self):
        rede, hist_2026, _ = self._fechar_fundamental(8, 8)
        self._login(rede["inep"])
        self.assertEqual(self.client.post("/api/turmas", json={
            "nome": "6º B", "ano_letivo": 2027, "etapa": "fundamental",
        }).status_code, 200)
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º B")
        self.assertEqual(self.client.post(
            f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027},
        ).status_code, 200)
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027,
            "disciplina_id": rede["mat_id"],
        })
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            turma_2027, rede["mat_id"], self._notas_ano(rede["aluno_id"], 3, 3),
        ).status_code, 200)
        self._login(rede["inep"])
        resp = self._boletim_hist(hist_2026)
        self.assertEqual(resp.status_code, 200)
        html = self._html(resp)
        self.assertIn("8,0", html)
        self.assertNotIn("3,0", html)
        self.assertIn("5º A", html)
        self.assertNotIn("6º B", html)
        self.assertIn("2026", html)
        self.assertNotIn("Documento referente ao ano letivo de 2027", html)

    def test_aluno_duas_matriculas_seleciona_historico_correto(self):
        rede, hist_2026, _ = self._fechar_fundamental(8, 8)
        self._login(rede["inep"])
        self.assertEqual(self.client.post("/api/turmas", json={
            "nome": "6º B", "ano_letivo": 2027, "etapa": "fundamental",
        }).status_code, 200)
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º B")
        self.assertEqual(self.client.post(
            f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027},
        ).status_code, 200)
        self.client.post("/api/alocacoes", json={
            "professor_id": rede["prof_id"], "turma_id": turma_2027,
            "disciplina_id": rede["mat_id"],
        })
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            turma_2027, rede["mat_id"], self._notas_ano(rede["aluno_id"], 3, 3),
        ).status_code, 200)
        self._encerrar(turma_2027, rede["inep"])
        with self.app.app_context():
            hist_2027 = self._hist(rede["aluno_id"], turma_2027).id
        self._login(rede["inep"])
        itens, _ = _json(self._lista(rede["aluno_id"]))
        ids = {i["historico_id"] for i in itens}
        self.assertEqual(ids, {hist_2026, hist_2027})
        html_26 = self._html(self._boletim_hist(hist_2026))
        html_27 = self._html(self._boletim_hist(hist_2027))
        self.assertIn("5º A", html_26)
        self.assertIn("8,0", html_26)
        self.assertNotIn("6º B", html_26)
        self.assertIn("6º B", html_27)
        self.assertIn("3,0", html_27)
        self.assertNotIn("5º A", html_27)

    def test_renomear_aluno_escola_turma_disciplina_depois_nao_altera(self):
        rede, hist_id, _ = self._fechar_fundamental(8, 8)
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Disciplina, Escola, Turma
            db.session.get(Aluno, rede["aluno_id"]).nome = "João Atualizado"
            db.session.get(Escola, rede["escola_id"]).nome = "Escola Renomeada 2099"
            db.session.get(Turma, rede["turma_2026"]).nome = "Turma Z"
            db.session.get(Disciplina, rede["mat_id"]).nome = "Matemática Geral"
            db.session.commit()
        self._login(rede["inep"])
        html = self._html(self._boletim_hist(hist_id))
        self.assertEqual(self._boletim_hist(hist_id).status_code, 200)
        self.assertIn("João", html)
        self.assertNotIn("João Atualizado", html)
        self.assertIn(rede["escola_nome"], html)
        self.assertNotIn("Escola Renomeada 2099", html)
        self.assertIn("5º A", html)
        self.assertNotIn("Turma Z", html)
        self.assertIn("Matemática", html)
        self.assertNotIn("Matemática Geral", html)

    def test_alterar_media_minima_depois_nao_altera_documento(self):
        self._definir_media(6.0)
        rede, hist_id, _ = self._fechar_fundamental(8, 8)
        self._definir_media(7.0)
        self._login(rede["inep"])
        html = self._html(self._boletim_hist(hist_id))
        self.assertIn("Média mínima", html)
        self.assertIn("6,0", html)
        self.assertNotIn("7,0", html)

    def test_alterar_registro_nota_depois_nao_altera_documento(self):
        rede, hist_id, json_antes = self._fechar_fundamental(8, 8)
        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual, RegistroNota
            mat_id = self._mat_id(rede["aluno_id"], rede["turma_2026"])
            for nota in RegistroNota.query.filter_by(matricula_id=mat_id).all():
                nota.valor = 1.0
            db.session.commit()
        self._login(rede["inep"])
        html = self._html(self._boletim_hist(hist_id))
        self.assertIn("8,0", html)
        self.assertNotIn("1,0", html)
        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual
            self.assertEqual(db.session.get(HistoricoAnual, hist_id).dados_fechamento, json_antes)

    def test_boletim_mostra_resultado_por_disciplina_sem_global(self):
        rede, hist_id, _ = self._fechar_fundamental(8, 8)
        self._login(rede["inep"])
        html = self._html(self._boletim_hist(hist_id))
        self.assertIn("APROVADO", html)
        self.assertNotIn("Situação final", html)
        self.assertNotIn("situacao_final", html)
        self.assertNotIn("resultado_academico", html)

    def test_situacao_final_cadastral_nao_aparece_como_aprovacao(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 5, 5),
        ).status_code, 200)
        self._encerrar(rede["turma_2026"], rede["inep"])
        with self.app.app_context():
            from src import db
            hist = self._hist(rede["aluno_id"], rede["turma_2026"])
            hist.situacao_final = "APROVADO"
            db.session.commit()
            hist_id = hist.id
        self._login(rede["inep"])
        html = self._html(self._boletim_hist(hist_id))
        self.assertIn("EM RECUPERAÇÃO", html)
        self.assertNotIn("APROVADO", html)
        self.assertNotIn("Situação final", html)

    def test_parecer_ei_usa_snapshot_nao_relatorio_atual(self):
        rede = self._rede(infantil=True)
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_parecer(rede["turma_2026"], [
            self._parecer(rede["aluno_id"], "Parecer congelado 2026"),
        ]).status_code, 200)
        self._encerrar(rede["turma_2026"], rede["inep"])
        with self.app.app_context():
            from src import db
            from src.models import RelatorioDescritivo
            hist = self._hist(rede["aluno_id"], rede["turma_2026"])
            hist_id = hist.id
            json_antes = hist.dados_fechamento
            for r in RelatorioDescritivo.query.filter_by(aluno_id=rede["aluno_id"]).all():
                r.observacao = "TEXTO OPERACIONAL NOVO"
            db.session.commit()
        self._login(rede["inep"])
        resp = self._parecer_hist(hist_id)
        self.assertEqual(resp.status_code, 200)
        html = self._html(resp)
        self.assertIn("Parecer congelado 2026", html)
        self.assertNotIn("TEXTO OPERACIONAL NOVO", html)
        self.assertIn("Ana", html)
        self.assertIn("Infantil V", html)
        self.assertIn("Documento referente ao ano letivo de 2026", html)
        self.assertNotIn("APROVADO", html)
        self.assertNotIn("REPROVADO", html)
        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual
            self.assertEqual(db.session.get(HistoricoAnual, hist_id).dados_fechamento, json_antes)

    def test_ei_nao_recebe_boletim_e_fundamental_nao_recebe_parecer(self):
        rede_ei = self._rede(infantil=True)
        self._login(rede_ei["cpf_prof"])
        self.assertEqual(self._salvar_parecer(rede_ei["turma_2026"], [
            self._parecer(rede_ei["aluno_id"], "Texto EI"),
        ]).status_code, 200)
        self._encerrar(rede_ei["turma_2026"], rede_ei["inep"])
        rede_fund, hist_fund, _ = self._fechar_fundamental()
        with self.app.app_context():
            hist_ei = self._hist(rede_ei["aluno_id"], rede_ei["turma_2026"]).id
        self._login(rede_ei["inep"])
        resp = self._boletim_hist(hist_ei)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Educação Infantil", _json(resp)[1])
        self._login(rede_fund["inep"])
        resp = self._parecer_hist(hist_fund)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Educação Infantil", _json(resp)[1])

    def test_usuario_escola_a_nao_abre_historico_escola_b(self):
        rede_a, hist_a, _ = self._fechar_fundamental()
        rede_b, _, _ = self._fechar_fundamental()
        self._login(rede_a["inep"])
        self.assertEqual(self._boletim_hist(hist_a).status_code, 200)
        with self.app.app_context():
            hist_b = self._hist(rede_b["aluno_id"], rede_b["turma_2026"]).id
        resp = self._boletim_hist(hist_b)
        self.assertEqual(resp.status_code, 403)
        corpo = resp.get_data(as_text=True)
        self.assertNotIn("João", corpo)
        self.assertNotIn(rede_b["escola_nome"], corpo)
        self.assertNotIn("5º A", corpo)
        self.assertNotIn("8,0", corpo)
        self.assertNotIn("Matemática", corpo)

    def test_idor_por_historico_id_e_bloqueado(self):
        _, hist_a, _ = self._fechar_fundamental()
        rede_b, hist_b, _ = self._fechar_fundamental()
        self._login(rede_b["inep"])
        resp = self._boletim_hist(hist_a)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(_json(resp)[1], "Acesso negado")
        self.assertNotIn("João", resp.get_data(as_text=True))
        self._login(rede_b["cpf_prof"])
        self.assertEqual(self._boletim_hist(hist_b).status_code, 403)
        self.assertEqual(self._lista(rede_b["aluno_id"]).status_code, 403)

    def test_coordenador_emite_parecer_historico_nao_boletim(self):
        rede_ei = self._rede(infantil=True)
        self._login(rede_ei["cpf_prof"])
        self.assertEqual(self._salvar_parecer(rede_ei["turma_2026"], [
            self._parecer(rede_ei["aluno_id"], "Parecer coord"),
        ]).status_code, 200)
        self._encerrar(rede_ei["turma_2026"], rede_ei["inep"])
        rede_fund, hist_fund, _ = self._fechar_fundamental()
        with self.app.app_context():
            hist_ei = self._hist(rede_ei["aluno_id"], rede_ei["turma_2026"]).id
        self._login(rede_ei["login_coord"])
        self.assertEqual(self._parecer_hist(hist_ei).status_code, 200)
        self.assertEqual(self._lista(rede_ei["aluno_id"]).status_code, 200)
        self._login(rede_fund["login_coord"])
        self.assertEqual(self._boletim_hist(hist_fund).status_code, 403)

    def test_snapshot_legado_nao_enriquecido_e_falha_controlada(self):
        rede = self._rede()
        with self.app.app_context():
            from src import db
            from src.models import Aluno, Disciplina, HistoricoAnual
            disc = Disciplina.query.filter_by(nome="Matemática").one()
            aluno = db.session.get(Aluno, rede["aluno_id"])
            payload = json.dumps({
                "notas": [{"disc": disc.id, "bim": 11, "val": 9.0}],
                "faltas": [{"disc": disc.id, "bim": 1, "qtd": 2}],
            })
            hist = HistoricoAnual(
                ano_letivo=2025, aluno_id=aluno.id, matricula_id=None,
                escola_nome="Escola no Snapshot Antigo", turma_nome="5º Legado",
                etapa="fundamental", situacao_final="ativo",
                dados_fechamento=payload,
            )
            db.session.add(hist)
            db.session.commit()
            hist_id = hist.id
            disc.nome = "Matemática Geral"
            aluno.nome = "Nome Atual Distinto"
            db.session.commit()
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._boletim_hist(hist_id)
        self.assertEqual(resp.status_code, 400)
        erro = _json(resp)[1]
        self.assertIn("legado", erro.lower())
        corpo = resp.get_data(as_text=True)
        self.assertNotIn("Matemática Geral", corpo)
        self.assertNotIn("Nome Atual Distinto", corpo)
        itens, _ = _json(self._lista(rede["aluno_id"]))
        legado = next(i for i in itens if i["historico_id"] == hist_id)
        self.assertIsNone(legado["documento"])
        self.assertEqual(legado["escola_nome"], "Escola no Snapshot Antigo")

    def test_documentos_atuais_continuam_funcionando(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_2026"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 8, 8),
        ).status_code, 200)
        self._login(rede["inep"])
        boletim = self.client.get(f"/api/alunos/{rede['aluno_id']}/documentos/boletim.pdf")
        self.assertEqual(boletim.status_code, 200)
        self.assertIn("João", self._html(boletim))
        self.assertEqual(
            self.client.get(f"/api/alunos/{rede['aluno_id']}/documentos/declaracao/cursando.pdf").status_code, 200,
        )
        self.assertEqual(
            self.client.get(f"/api/alunos/{rede['aluno_id']}/documentos/comprovante-matricula.pdf").status_code, 200,
        )
        rede_ei = self._rede(infantil=True)
        self._login(rede_ei["cpf_prof"])
        self.assertEqual(self._salvar_parecer(rede_ei["turma_2026"], [
            self._parecer(rede_ei["aluno_id"], "Parecer atual"),
        ]).status_code, 200)
        self._login(rede_ei["inep"])
        parecer = self.client.get(f"/api/alunos/{rede_ei['aluno_id']}/documentos/parecer.pdf")
        self.assertEqual(parecer.status_code, 200)
        self.assertIn("Parecer atual", self._html(parecer))

    def test_reemissao_historica_nao_altera_historico_anual(self):
        rede, hist_id, json_antes = self._fechar_fundamental()
        self._login(rede["inep"])
        self.assertEqual(self._boletim_hist(hist_id).status_code, 200)
        self.assertEqual(self._boletim_hist(hist_id).status_code, 200)
        self._lista(rede["aluno_id"])
        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual
            depois = db.session.get(HistoricoAnual, hist_id)
            self.assertEqual(depois.dados_fechamento, json_antes)

    def test_historico_inexistente_404(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._boletim_hist(999999)
        self.assertEqual(resp.status_code, 404)

    def test_boletim_historico_nao_inclui_cpf(self):
        rede, hist_id, _ = self._fechar_fundamental()
        self._login(rede["inep"])
        html = self._html(self._boletim_hist(hist_id))
        self.assertNotIn(rede["cpf_aluno"], html)

    def test_abrir_2026_com_aluno_em_2027_outra_turma(self):
        rede, hist_2026, _ = self._fechar_fundamental(8, 8)
        with self.app.app_context():
            from src import db
            from src.models import Escola
            nome_escola_2026 = db.session.get(Escola, rede["escola_id"]).nome
        self._login(rede["inep"])
        self.assertEqual(self.client.post("/api/turmas", json={
            "nome": "6º B", "ano_letivo": 2027, "etapa": "fundamental",
        }).status_code, 200)
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2027 = next(t["id"] for t in dash["turmas"] if t["nome"] == "6º B")
        self.assertEqual(self.client.post(
            f"/api/alunos/{rede['aluno_id']}/enturmar", json={"turma_id": turma_2027},
        ).status_code, 200)
        html = self._html(self._boletim_hist(hist_2026))
        self.assertIn(nome_escola_2026, html)
        self.assertIn("5º A", html)
        self.assertNotIn("6º B", html)
        atual = self.client.get(f"/api/alunos/{rede['aluno_id']}/documentos/boletim.pdf")
        self.assertEqual(atual.status_code, 200)
        html_atual = self._html(atual)
        self.assertIn("6º B", html_atual)
        self.assertNotIn("8,0", html_atual)

    def _matricular_em_outra_escola(self, aluno_id, rede_destino, ano=2027, nome_turma="6º B Destino"):
        with self.app.app_context():
            from src import db
            from src.models import Aluno
            aluno = db.session.get(Aluno, aluno_id)
            aluno.escola_id = rede_destino["escola_id"]
            db.session.commit()
        self._login(rede_destino["inep"])
        resp = self.client.post("/api/turmas", json={
            "nome": nome_turma, "ano_letivo": ano, "etapa": "fundamental",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == nome_turma)
        resp = self.client.post(
            f"/api/alunos/{aluno_id}/enturmar", json={"turma_id": turma_id},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        with self.app.app_context():
            from src import db
            from src.models import Aluno
            aluno = db.session.get(Aluno, aluno_id)
            self.assertEqual(aluno.turma_id, turma_id)
            self.assertEqual(aluno.escola_id, rede_destino["escola_id"])
        return turma_id

    def _ids_lista(self, aluno_id):
        resp = self._lista(aluno_id)
        itens, _ = _json(resp)
        if resp.status_code != 200:
            return resp.status_code, []
        return resp.status_code, [i["historico_id"] for i in (itens or [])]

    def test_escola_atual_nao_autoriza_historico_de_outra_escola(self):
        rede_a, hist_a, _ = self._fechar_fundamental(8, 8)
        rede_b = self._rede()
        turma_b = self._matricular_em_outra_escola(rede_a["aluno_id"], rede_b)
        with self.app.app_context():
            from src import db
            from src.models import Aluno
            aluno = db.session.get(Aluno, rede_a["aluno_id"])
            self.assertEqual(aluno.turma_id, turma_b)
            self.assertEqual(aluno.escola_id, rede_b["escola_id"])

        self._login(rede_b["inep"])
        status, ids = self._ids_lista(rede_a["aluno_id"])
        self.assertEqual(status, 403)
        self.assertNotIn(hist_a, ids)
        resp = self._boletim_hist(hist_a)
        self.assertEqual(resp.status_code, 403)
        corpo = resp.get_data(as_text=True)
        self.assertNotIn("João", corpo)
        self.assertNotIn(rede_a["escola_nome"], corpo)
        self.assertNotIn("5º A", corpo)
        self.assertNotIn("8,0", corpo)

        self._login(rede_b["login_coord"])
        self.assertEqual(self._lista(rede_a["aluno_id"]).status_code, 403)
        self.assertEqual(self._boletim_hist(hist_a).status_code, 403)
        self.assertEqual(self._parecer_hist(hist_a).status_code, 403)
        self.assertNotIn("João", self._parecer_hist(hist_a).get_data(as_text=True))

        self._login(rede_b["cpf_prof"])
        self.assertEqual(self._lista(rede_a["aluno_id"]).status_code, 403)
        self.assertEqual(self._boletim_hist(hist_a).status_code, 403)

        self._login(rede_a["inep"])
        status, ids = self._ids_lista(rede_a["aluno_id"])
        self.assertEqual(status, 200)
        self.assertEqual(ids, [hist_a])
        self.assertEqual(self._boletim_hist(hist_a).status_code, 200)
        html = self._html(self._boletim_hist(hist_a))
        self.assertIn("5º A", html)
        self.assertIn(rede_a["escola_nome"], html)
        self.assertNotIn("6º B Destino", html)

        self._login(rede_a["login_coord"])
        status, ids = self._ids_lista(rede_a["aluno_id"])
        self.assertEqual(status, 200)
        self.assertEqual(ids, [hist_a])
        self.assertEqual(self._boletim_hist(hist_a).status_code, 403)

        self._login("admin", SENHA_DEMO_ADMIN)
        status, ids = self._ids_lista(rede_a["aluno_id"])
        self.assertEqual(status, 200)
        self.assertIn(hist_a, ids)
        self.assertEqual(self._boletim_hist(hist_a).status_code, 200)

    def test_json_escola_id_nao_autoriza_historico(self):
        rede_a, hist_a, _ = self._fechar_fundamental(8, 8)
        rede_b = self._rede()
        self._matricular_em_outra_escola(rede_a["aluno_id"], rede_b)
        with self.app.app_context():
            from src import db
            from src.models import HistoricoAnual
            hist = db.session.get(HistoricoAnual, hist_a)
            bruto = json.loads(hist.dados_fechamento)
            bruto["escola"]["id"] = rede_b["escola_id"]
            hist.dados_fechamento = json.dumps(bruto)
            db.session.commit()
        self._login(rede_b["inep"])
        self.assertEqual(self._lista(rede_a["aluno_id"]).status_code, 403)
        self.assertEqual(self._boletim_hist(hist_a).status_code, 403)
        self._login(rede_a["inep"])
        self.assertEqual(self._boletim_hist(hist_a).status_code, 200)

