import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria

ARTEFATOS = Path(__file__).resolve().parent / "_pdf_artefatos"


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class DocumentosPdfTest(unittest.TestCase):
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
        ARTEFATOS.mkdir(exist_ok=True)

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
        DocumentosPdfTest._seq += 1
        return DocumentosPdfTest._seq

    def _rede(self, infantil=False, nome_turma=None, ano_letivo=2026, nome_aluno=None):
        n = self._proximo()
        inep = f"8100{n:04d}"
        cpf_prof = f"8101{n:07d}"[:11]
        cpf_joao = f"8102{n:07d}"[:11]
        etapa = "infantil" if infantil else "fundamental"
        nome_turma = nome_turma or ("Infantil V" if infantil else "5º A")
        escola_nome = f"Escola PDF {inep}"
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": escola_nome, "inep": inep,
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        login_coord = f"coordPdf{n}"
        resp = self.client.post(
            f"/api/escolas/{escola_id}/coordenadores",
            json={"login": login_coord, "nome": "Coord PDF"},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_coord = senha_temporaria(resp)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof PDF", "cpf": cpf_prof, "escola_id": escola_id,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_prof = senha_temporaria(resp)
        concluir_primeiro_acesso(self.client, inep, temp_gestor)
        self.client.post("/api/turmas", json={
            "nome": nome_turma, "ano_letivo": ano_letivo, "etapa": etapa,
        })
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_id = next(t["id"] for t in dash["turmas"] if t["nome"] == nome_turma)
        mat_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Matemática")
        port_id = next(d["id"] for d in dash["disciplinas"] if d["nome"] == "Português")
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": nome_aluno or ("Ana" if infantil else "João da Silva"),
            "cpf": cpf_joao,
            "turma_id": turma_id,
            "nome_mae": "Maria da Silva",
            "nome_pai": "José da Silva",
            "data_nascimento": "2014-03-15",
            "local_nascimento": "Palmeira do Piauí",
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        aluno_id = _json(resp)[0]["id"]
        aloc = {"professor_id": prof_id, "turma_id": turma_id}
        if not infantil:
            aloc["disciplina_id"] = mat_id
        self.assertEqual(self.client.post("/api/alocacoes", json=aloc).status_code, 200)
        concluir_primeiro_acesso(self.client, cpf_prof, temp_prof)
        self._login("admin", SENHA_DEMO_ADMIN)
        concluir_primeiro_acesso(self.client, login_coord, temp_coord)
        return {
            "inep": inep, "cpf_prof": cpf_prof, "cpf_aluno": cpf_joao,
            "login_coord": login_coord, "turma_id": turma_id, "turma_nome": nome_turma,
            "escola_id": escola_id, "escola_nome": escola_nome,
            "mat_id": mat_id, "port_id": port_id, "aluno_id": aluno_id,
            "prof_id": prof_id, "infantil": infantil, "ano_letivo": ano_letivo,
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
            f"/api/turmas/{turma_id}/disciplinas/{disc_id}/notas", json=payload,
        )

    def _encerrar(self, turma_id, inep):
        self._login(inep)
        resp = self.client.post(f"/api/turmas/{turma_id}/encerrar")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _pdf(self, url):
        return self.client.get(url)

    def _assert_pdf(self, resp, cpf=None):
        self.assertEqual(resp.status_code, 200, resp.get_data()[:300])
        self.assertTrue(
            (resp.content_type or "").startswith("application/pdf"),
            resp.content_type,
        )
        data = resp.get_data()
        self.assertTrue(data.startswith(b"%PDF-"), data[:20])
        disp = resp.headers.get("Content-Disposition") or ""
        self.assertIn("filename=", disp)
        if cpf:
            digits = "".join(ch for ch in str(cpf) if ch.isdigit())
            self.assertNotIn(digits, disp)
        return data

    def _payload(self, aluno_id, para_transferencia=False):
        with self.app.app_context():
            from src.historico_escolar import montar_historico_escolar
            from src.models import Aluno
            from src import db
            aluno = db.session.get(Aluno, aluno_id)
            return montar_historico_escolar(aluno, para_transferencia=para_transferencia)

    def _texto_pdf(self, data):
        from io import BytesIO
        try:
            from pypdf import PdfReader
            leitor = PdfReader(BytesIO(data))
            return "\n".join((pagina.extract_text() or "") for pagina in leitor.pages)
        except Exception:
            return data.decode("latin-1", errors="ignore")

    def _pdf_tem_secao_transferencia(self, data):
        compacto = " ".join(self._texto_pdf(data).replace("\n", " ").split()).upper()
        compacto = (
            compacto.replace("Ê", "E").replace("É", "E").replace("Í", "I")
            .replace("Â", "A").replace("Ã", "A")
        )
        return "TRANSFERENCIA DURANTE" in compacto

    def _compacto_pdf(self, data):
        compacto = " ".join(self._texto_pdf(data).replace("\n", " ").split()).upper()
        return (
            compacto.replace("Ê", "E").replace("É", "E").replace("Í", "I")
            .replace("Â", "A").replace("Ã", "A").replace("Ó", "O").replace("Ô", "O")
            .replace("Ç", "C")
        )

    def _tem_autenticacao(self, data):
        return "RESERVADO PARA AUTENTIC" in self._compacto_pdf(data)

    def _tamanho_brasao(self):
        from PIL import Image as PILImage
        from src.documentos_institucionais import caminho_brasao
        path = caminho_brasao()
        self.assertTrue(path, "brasao-palmeira-piaui.png ausente")
        with PILImage.open(path) as im:
            return im.size

    def _imagens_por_pagina(self, data):
        from io import BytesIO
        from pypdf import PdfReader
        from PIL import Image as PILImage
        reader = PdfReader(BytesIO(data))
        paginas = []
        for page in reader.pages:
            tamanhos = []
            try:
                imagens = list(page.images or [])
            except Exception:
                imagens = []
            for img in imagens:
                try:
                    im = PILImage.open(BytesIO(img.data))
                    tamanhos.append(im.size)
                except Exception:
                    continue
            paginas.append(tamanhos)
        return paginas

    def _pdf_tem_marca_dagua(self, data):
        alvo = self._tamanho_brasao()
        paginas = self._imagens_por_pagina(data)
        if not paginas:
            return False
        return all(alvo in tamanhos for tamanhos in paginas)

    def _n_paginas(self, data):
        from io import BytesIO
        from pypdf import PdfReader
        return len(PdfReader(BytesIO(data)).pages)

    def _definir_periodos_2026(self):
        with self.app.app_context():
            from src import db
            from src.models import AnoLetivo, PeriodoLetivo
            ano = AnoLetivo.query.filter_by(ano=2026).first()
            if ano is None:
                ano = AnoLetivo(
                    ano=2026, data_inicio=date(2026, 2, 1),
                    data_fim=date(2026, 12, 20), ativo=True,
                )
                db.session.add(ano)
                db.session.flush()
            else:
                PeriodoLetivo.query.filter_by(ano_letivo_id=ano.id).delete()
            db.session.add(PeriodoLetivo(
                ano_letivo_id=ano.id, numero=1, nome="1º Bimestre",
                data_inicio=date(2026, 2, 1), data_fim=date(2026, 4, 30),
            ))
            db.session.add(PeriodoLetivo(
                ano_letivo_id=ano.id, numero=2, nome="2º Bimestre",
                data_inicio=date(2026, 5, 1), data_fim=date(2026, 7, 15),
            ))
            db.session.add(PeriodoLetivo(
                ano_letivo_id=ano.id, numero=3, nome="3º Bimestre",
                data_inicio=date(2026, 8, 1), data_fim=date(2026, 9, 30),
            ))
            db.session.add(PeriodoLetivo(
                ano_letivo_id=ano.id, numero=4, nome="4º Bimestre",
                data_inicio=date(2026, 10, 1), data_fim=date(2026, 12, 20),
            ))
            db.session.commit()

    def test_01_pdf_historico_content_type_e_assinatura(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_id"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 8, 8),
        ).status_code, 200)
        self._login(rede["inep"])
        resp = self._pdf(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self._assert_pdf(resp, cpf=rede["cpf_aluno"])

    def test_02_autorizado_emite_e_professor_403(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self._salvar_notas(
            rede["turma_id"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 7, 7),
        )
        self._login(rede["cpf_prof"])
        resp = self._pdf(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self.assertEqual(resp.status_code, 403)
        self._login(rede["login_coord"])
        resp = self._pdf(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self.assertEqual(resp.status_code, 403)
        self._login(rede["inep"])
        resp = self._pdf(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self._assert_pdf(resp)
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._pdf(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self._assert_pdf(resp)
        self._login(rede["cpf_prof"])
        resp = self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        )
        self.assertEqual(resp.status_code, 403)
        self._login(rede["login_coord"])
        resp = self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        )
        self.assertEqual(resp.status_code, 403)
        self._login(rede["inep"])
        resp = self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        )
        self._assert_pdf(resp)

    def test_03_idor_gestor_outra_escola(self):
        rede_a = self._rede()
        rede_b = self._rede()
        self._login(rede_b["inep"])
        resp = self._pdf(f"/api/alunos/{rede_a['aluno_id']}/documentos/historico.pdf")
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(_json(resp)[0])
        resp = self._pdf(
            f"/api/alunos/{rede_a['aluno_id']}/documentos/historico-transferencia.pdf"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(_json(resp)[0])

    def test_04_aluno_inexistente_404(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self._pdf("/api/alunos/99999/documentos/historico.pdf")
        self.assertEqual(resp.status_code, 404)
        self.assertIsNotNone(_json(resp)[1])
        resp = self._pdf("/api/alunos/99999/documentos/historico-transferencia.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_05_snapshot_nao_recalcula_media_minima_nem_renomeia_disciplina(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self._salvar_notas(
            rede["turma_id"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 8, 8),
        )
        self._encerrar(rede["turma_id"], rede["inep"])
        self._definir_media(9.5)
        with self.app.app_context():
            from src import db
            from src.models import Disciplina
            disc = db.session.get(Disciplina, rede["mat_id"])
            original = disc.nome
            disc.nome = "Matemática Geral Extraordinária"
            db.session.commit()
        try:
            payload = self._payload(rede["aluno_id"])
            ano = payload["anos_iniciais"][0]
            self.assertEqual(ano["fonte"], "snapshot")
            comps = {c["nome"]: c for c in ano["componentes"]}
            self.assertIn("Matemática", comps)
            self.assertNotIn("Matemática Geral Extraordinária", comps)
            self.assertEqual(comps["Matemática"]["resultado"], "APROVADO")
            self.assertAlmostEqual(float(ano["media_minima"]), 6.0)
        finally:
            with self.app.app_context():
                from src import db
                from src.models import Disciplina
                db.session.get(Disciplina, rede["mat_id"]).nome = original
                db.session.commit()

    def test_06_matriz_2026_nao_entra_em_ano_anterior(self):
        rede = self._rede(nome_turma="5º A", ano_letivo=2024)
        self._login(rede["cpf_prof"])
        self._salvar_notas(
            rede["turma_id"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 7, 7),
        )
        self._encerrar(rede["turma_id"], rede["inep"])
        payload = self._payload(rede["aluno_id"])
        ano = payload["anos_iniciais"][0]
        self.assertEqual(ano["ano_letivo"], 2024)
        self.assertFalse(ano["matriz_aplicada"])
        nomes = [c["nome"] for c in ano["componentes"]]
        self.assertIn("Matemática", nomes)
        self.assertNotIn("Cultura Digital", nomes)
        self.assertNotIn("Formação Humana", nomes)
        self.assertNotIn("Biologia", nomes)

    def test_07_nono_2026_suporta_biologia_quimica_fisica(self):
        rede = self._rede(nome_turma="9º A", ano_letivo=2026, nome_aluno="Carla Nono")
        payload = self._payload(rede["aluno_id"])
        ano = payload["anos_finais"][0]
        self.assertTrue(ano["matriz_aplicada"])
        nomes = [c["nome"] for c in ano["componentes"]]
        self.assertIn("Biologia", nomes)
        self.assertIn("Química", nomes)
        self.assertIn("Física", nomes)
        self.assertNotIn("Ciências", nomes)
        bio = next(c for c in ano["componentes"] if c["nome"] == "Biologia")
        self.assertIsNone(bio["media"])
        self.assertIsNone(bio["faltas"])
        self.assertEqual(bio["fonte"], "matriz")

    def test_08_sexto_a_oitavo_2026_tem_ciencias(self):
        rede = self._rede(nome_turma="7º B", ano_letivo=2026)
        payload = self._payload(rede["aluno_id"])
        nomes = [c["nome"] for c in payload["anos_finais"][0]["componentes"]]
        self.assertIn("Ciências", nomes)
        self.assertNotIn("Biologia", nomes)

    def test_09_transferencia_nao_inventa_periodo_futuro_nem_zero(self):
        rede = self._rede(nome_turma="6º A", ano_letivo=2026)
        self._definir_periodos_2026()
        self._login(rede["cpf_prof"])
        self._salvar_notas(rede["turma_id"], rede["mat_id"], [
            self._nota(rede["aluno_id"], "8", 11),
            self._nota(rede["aluno_id"], "8", 12),
        ])
        normal = self._payload(rede["aluno_id"])
        self.assertIsNone(normal["transferencia"])
        self.assertEqual(normal["tipo"], "historico_escolar")
        payload = self._payload(rede["aluno_id"], para_transferencia=True)
        transf = payload["transferencia"]
        self.assertIsNotNone(transf)
        self.assertEqual(payload["tipo"], "historico_transferencia")
        nomes_periodo = [p["nome"] for p in transf["periodos"]]
        self.assertEqual(nomes_periodo, ["1º Bimestre", "2º Bimestre", "3º Bimestre", "4º Bimestre"])
        futuro = [p for p in transf["periodos"] if p["numero"] == 4][0]
        self.assertTrue(futuro["futuro"])
        mat = next(
            c for c in transf["componentes"] if c["nome"] == "Matemática"
        )
        p4 = next(p for p in mat["periodos"] if p["numero"] == 4)
        self.assertTrue(p4["futuro"])
        self.assertIsNone(p4["nota"])
        self.assertIsNone(p4["faltas"])
        p1 = next(p for p in mat["periodos"] if p["numero"] == 1)
        self.assertIsNotNone(p1["nota"])
        cultura = next(
            c for c in transf["componentes"] if c["nome"] == "Cultura Digital"
        )
        p1c = next(p for p in cultura["periodos"] if p["numero"] == 1)
        self.assertIsNone(p1c["nota"])
        self.assertNotEqual(p1c["nota"], 0)
        self.assertIsNone(p1c["faltas"])
        for p in cultura["periodos"]:
            self.assertIsNone(p["faltas"])
            if p.get("futuro"):
                self.assertIsNone(p["nota"])
        self.assertNotIn("faltas", mat)
        self.assertNotIn("total_faltas", mat)
        with self.app.app_context():
            from src.models import Aluno
            from src import db
            situacao = (db.session.get(Aluno, rede["aluno_id"]).situacao or "").lower()
        self.assertNotEqual(situacao, "transferido")

    def test_10_filename_sem_cpf_e_auditlog_sem_pii(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self._salvar_notas(
            rede["turma_id"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 8, 8),
        )
        self._login(rede["inep"])
        resp = self._pdf(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self._assert_pdf(resp, cpf=rede["cpf_aluno"])
        disp = resp.headers.get("Content-Disposition") or ""
        self.assertIn("historico-escolar", disp)
        self.assertNotIn(rede["cpf_aluno"], disp)
        with self.app.app_context():
            from src.models import AuditLog
            log = (
                AuditLog.query.filter_by(acao="DOCUMENTO_HISTORICO_EMITIDO")
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(log)
            blob = json.dumps(log.dados_novos or {})
            self.assertIn("aluno_id", blob)
            self.assertNotIn(rede["cpf_aluno"], blob)
            self.assertNotIn("Maria da Silva", blob)
            self.assertNotIn("2014-03-15", blob)
            self.assertNotIn("8.0", blob)
            self.assertNotIn("8,0", blob)
            novos = log.dados_novos
            if isinstance(novos, str):
                novos = json.loads(novos)
            self.assertEqual((novos or {}).get("tipo"), "historico_escolar")

    def test_11_erro_geracao_nao_vaza_stack(self):
        rede = self._rede()
        self._login(rede["inep"])
        with patch(
            "src.documentos_pdf.montar_pdf_historico",
            side_effect=RuntimeError("STACK_SECRETA_XYZ"),
        ):
            resp = self._pdf(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self.assertEqual(resp.status_code, 500)
        texto = resp.get_data(as_text=True)
        self.assertNotIn("STACK_SECRETA_XYZ", texto)
        self.assertNotIn("Traceback", texto)

    def test_12_rotas_html_removidas(self):
        rede = self._rede()
        self._login(rede["inep"])
        for url in (
            f"/documentos/boletim/{rede['aluno_id']}",
            f"/documentos/declaracao/cursando/{rede['aluno_id']}",
            f"/documentos/declaracao/conclusao/{rede['aluno_id']}",
            f"/documentos/parecer/{rede['aluno_id']}",
            f"/documentos/comprovante-matricula/{rede['aluno_id']}",
            f"/documentos/historico/1/boletim",
            f"/documentos/historico/1/parecer",
            f"/relatorio/turma/{rede['turma_id']}/disciplina/{rede['mat_id']}",
        ):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 404, url)
            self.assertNotIn("text/html", (resp.content_type or ""), url)
        pdf = self._pdf(f"/api/alunos/{rede['aluno_id']}/documentos/boletim.pdf")
        self._assert_pdf(pdf, cpf=rede["cpf_aluno"])
        dec = self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/declaracao/cursando.pdf"
        )
        self._assert_pdf(dec, cpf=rede["cpf_aluno"])
        ficha = self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/comprovante-matricula.pdf"
        )
        self._assert_pdf(ficha, cpf=rede["cpf_aluno"])

    def test_13_artefatos_visuais(self):
        iniciais = self._rede(nome_turma="3º A", ano_letivo=2026, nome_aluno="Lucas Iniciais")
        self._login(iniciais["cpf_prof"])
        self._salvar_notas(
            iniciais["turma_id"], iniciais["mat_id"],
            self._notas_ano(iniciais["aluno_id"], 8, 7),
        )
        self._login(iniciais["inep"])
        a = self._assert_pdf(self._pdf(
            f"/api/alunos/{iniciais['aluno_id']}/documentos/historico.pdf"
        ))
        self.assertFalse(self._pdf_tem_secao_transferencia(a))
        (ARTEFATOS / "historico_anos_iniciais.pdf").write_bytes(a)

        nono = self._rede(nome_turma="9º A", ano_letivo=2026, nome_aluno="Beatriz Nono")
        self._login(nono["inep"])
        b = self._assert_pdf(self._pdf(
            f"/api/alunos/{nono['aluno_id']}/documentos/historico.pdf"
        ))
        self.assertFalse(self._pdf_tem_secao_transferencia(b))
        (ARTEFATOS / "historico_nono_ano.pdf").write_bytes(b)

        legado = self._rede(nome_turma="8º A", ano_letivo=2024, nome_aluno="Pedro Legado")
        self._login(legado["cpf_prof"])
        self._salvar_notas(
            legado["turma_id"], legado["mat_id"],
            self._notas_ano(legado["aluno_id"], 6, 6),
        )
        self._encerrar(legado["turma_id"], legado["inep"])
        self._login(legado["inep"])
        self.assertEqual(self.client.post("/api/turmas", json={
            "nome": "9º C", "ano_letivo": 2026, "etapa": "fundamental",
        }).status_code, 200)
        dash = _json(self.client.get("/api/dashboard"))[0]
        turma_2026 = next(t["id"] for t in dash["turmas"] if t["nome"] == "9º C")
        self.assertEqual(self.client.post(
            f"/api/alunos/{legado['aluno_id']}/enturmar", json={"turma_id": turma_2026},
        ).status_code, 200)
        c = self._assert_pdf(self._pdf(
            f"/api/alunos/{legado['aluno_id']}/documentos/historico.pdf"
        ))
        (ARTEFATOS / "historico_trajetoria_pre_2026.pdf").write_bytes(c)
        payload = self._payload(legado["aluno_id"])
        anos_2024 = [x for x in payload["anos_finais"] if x["ano_letivo"] == 2024]
        self.assertTrue(anos_2024)
        self.assertNotIn("Cultura Digital", [k["nome"] for k in anos_2024[0]["componentes"]])

        transf = self._rede(nome_turma="4º A", ano_letivo=2026, nome_aluno="Helena Transferência")
        self._login(transf["inep"])
        d_normal = self._assert_pdf(self._pdf(
            f"/api/alunos/{transf['aluno_id']}/documentos/historico.pdf"
        ))
        self.assertFalse(self._pdf_tem_secao_transferencia(d_normal))
        d = self._assert_pdf(self._pdf(
            f"/api/alunos/{transf['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        self.assertTrue(self._pdf_tem_secao_transferencia(d))
        (ARTEFATOS / "historico_transferencia.pdf").write_bytes(d)
        self.assertGreater(len(d), 1000)
        disp = self._pdf(
            f"/api/alunos/{transf['aluno_id']}/documentos/historico-transferencia.pdf"
        ).headers.get("Content-Disposition") or ""
        self.assertIn("historico-transferencia", disp)

    def test_14_historico_normal_turma_aberta_sem_secao_transferencia(self):
        rede = self._rede(nome_turma="5º B", ano_letivo=2026)
        payload = self._payload(rede["aluno_id"])
        self.assertIsNone(payload["transferencia"])
        self.assertEqual(payload["tipo"], "historico_escolar")
        self.assertTrue(payload["anos_iniciais"] or payload["anos_finais"])
        self._login(rede["inep"])
        data = self._assert_pdf(self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ))
        self.assertFalse(self._pdf_tem_secao_transferencia(data))

    def test_15_auditlog_transferencia_diferencia_tipo_sem_pii(self):
        rede = self._rede(nome_aluno="Helena Audit")
        self._login(rede["inep"])
        resp = self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        )
        self._assert_pdf(resp, cpf=rede["cpf_aluno"])
        disp = resp.headers.get("Content-Disposition") or ""
        self.assertIn("historico-transferencia", disp)
        with self.app.app_context():
            from src.models import AuditLog
            log = (
                AuditLog.query.filter_by(acao="DOCUMENTO_HISTORICO_TRANSFERENCIA_EMITIDO")
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(log)
            novos = log.dados_novos
            if isinstance(novos, str):
                novos = json.loads(novos)
            self.assertEqual((novos or {}).get("tipo"), "historico_transferencia")
            self.assertEqual((novos or {}).get("formato"), "pdf")
            blob = json.dumps(novos or {})
            self.assertNotIn(rede["cpf_aluno"], blob)
            self.assertNotIn("Maria da Silva", blob)
            self.assertNotIn("2014-03-15", blob)
            self.assertNotIn("Helena Audit", blob)

    def test_16_aluno_encerrado_emite_normal_e_recusa_transferencia(self):
        rede = self._rede(nome_turma="5º C", ano_letivo=2025)
        self._login(rede["cpf_prof"])
        self._salvar_notas(
            rede["turma_id"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 7, 7),
        )
        self._encerrar(rede["turma_id"], rede["inep"])
        payload = self._payload(rede["aluno_id"])
        self.assertIsNone(payload["transferencia"])
        self.assertTrue(payload["anos_iniciais"])
        self._login(rede["inep"])
        data = self._assert_pdf(self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ))
        self.assertFalse(self._pdf_tem_secao_transferencia(data))
        resp = self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("andamento", (_json(resp)[1] or "").lower())

    def test_17_ei_nao_emite_historico_numerico_nem_transferencia(self):
        rede = self._rede(infantil=True)
        self._login(rede["inep"])
        resp = self._pdf(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse((resp.content_type or "").startswith("application/pdf"))
        erro = (_json(resp)[1] or "").lower()
        self.assertTrue(
            "infantil" in erro or "parecer" in erro or "fundamental" in erro,
            erro,
        )
        resp = self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse((resp.content_type or "").startswith("application/pdf"))
        with self.app.app_context():
            from src.historico_escolar import DocumentoErro, montar_historico_escolar
            from src.models import Aluno
            from src import db
            aluno = db.session.get(Aluno, rede["aluno_id"])
            with self.assertRaises(DocumentoErro):
                montar_historico_escolar(aluno)
            with self.assertRaises(DocumentoErro):
                montar_historico_escolar(aluno, para_transferencia=True)

    def test_18_historico_linhas_por_componente_sem_grade(self):
        rede = self._rede(nome_turma="9º A", ano_letivo=2026)
        payload = self._payload(rede["aluno_id"])
        self.assertEqual(payload["titulo_tabela"], "COMPONENTES CURRICULARES CURSADOS/CURSANDO")
        self.assertNotIn("grades", payload)
        linhas = payload["componentes_cursados"]
        self.assertTrue(linhas)
        nomes = [l["componente"] for l in linhas]
        self.assertIn("Biologia", nomes)
        self.assertIn("Química", nomes)
        self.assertIn("Física", nomes)
        for linha in linhas:
            self.assertEqual(linha["ano_letivo"], 2026)
            self.assertEqual(linha["situacao"], "CURSANDO")
            self.assertNotEqual(linha["nota_conceito"], 0)
        self._login(rede["inep"])
        data = self._assert_pdf(self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ))
        compacto = self._compacto_pdf(data)
        self.assertIn("COMPONENTES CURRICULARES CURSADOS/CURSANDO", compacto)
        self.assertNotIn("GRADE CONSOLIDADA", compacto)
        self.assertNotIn("TRAJETORIA ESCOLAR", compacto)
        self.assertNotIn("FALTAS", compacto)
        self.assertFalse(self._pdf_tem_secao_transferencia(data))

    def test_19_sexto_2026_ciencias_sem_bio(self):
        rede = self._rede(nome_turma="7º B", ano_letivo=2026)
        linhas = self._payload(rede["aluno_id"])["componentes_cursados"]
        nomes = [l["componente"] for l in linhas]
        self.assertIn("Ciências", nomes)
        self.assertNotIn("Biologia", nomes)
        self.assertTrue(all(l["situacao"] == "CURSANDO" for l in linhas))

    def test_20_snapshot_fechado_nao_recalcula_situacao(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        self._salvar_notas(
            rede["turma_id"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 8, 8),
        )
        self._encerrar(rede["turma_id"], rede["inep"])
        self._definir_media(9.5)
        payload = self._payload(rede["aluno_id"])
        mat = next(
            l for l in payload["componentes_cursados"]
            if l["componente"] == "Matemática"
        )
        self.assertEqual(mat["situacao"], "APROVADO")
        self.assertNotEqual(mat["nota_conceito"], 0)

    def test_21_documentos_oficiais_respondem_pdf(self):
        fund = self._rede()
        ei = self._rede(infantil=True)
        self._login(fund["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            fund["turma_id"], fund["mat_id"], self._notas_ano(fund["aluno_id"], 8, 7),
        ).status_code, 200)
        self._login(fund["inep"])
        boletim = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/boletim.pdf"
        ))
        declaracao = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/declaracao/cursando.pdf"
        ))
        ficha = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/comprovante-matricula.pdf"
        ))
        historico = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/historico.pdf"
        ))
        transf = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        rendimento = self._assert_pdf(self._pdf(
            f"/api/turmas/{fund['turma_id']}/documentos/rendimento/{fund['mat_id']}.pdf"
        ))
        self._login(ei["inep"])
        parecer = self._assert_pdf(self._pdf(
            f"/api/alunos/{ei['aluno_id']}/documentos/parecer.pdf"
        ))
        (ARTEFATOS / "inspecao_boletim.pdf").write_bytes(boletim)
        (ARTEFATOS / "inspecao_declaracao.pdf").write_bytes(declaracao)
        (ARTEFATOS / "inspecao_ficha_matricula.pdf").write_bytes(ficha)
        (ARTEFATOS / "inspecao_historico.pdf").write_bytes(historico)
        (ARTEFATOS / "inspecao_transferencia.pdf").write_bytes(transf)
        (ARTEFATOS / "inspecao_parecer_ei.pdf").write_bytes(parecer)
        (ARTEFATOS / "inspecao_rendimento.pdf").write_bytes(rendimento)

    def test_22_parecer_ei_assinaturas_e_sem_autenticacao(self):
        rede = self._rede(infantil=True)
        self._login(rede["cpf_prof"])
        resp = self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/0/notas",
            json=[{
                "aluno_id": rede["aluno_id"],
                "eixo": "Eu, o outro e o nós",
                "bimestre": 1,
                "avaliacao": "Em Desenvolvimento",
                "observacao": "Participa das atividades propostas.",
            }],
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self._login(rede["inep"])
        data = self._assert_pdf(self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/parecer.pdf"
        ))
        compacto = self._compacto_pdf(data)
        self.assertIn("PROFESSOR(A) TITULAR", compacto)
        self.assertIn("COORDENACAO PEDAGOGICA", compacto)
        self.assertIn("PROF PDF", compacto)
        self.assertIn("COORD PDF", compacto)
        self.assertNotIn("SECRETARIO(A)", compacto)
        self.assertNotIn("DIRETOR(A)", compacto)
        self.assertFalse(self._tem_autenticacao(data))
        self.assertFalse(self._pdf_tem_marca_dagua(data))
        (ARTEFATOS / "inspecao_parecer_ei.pdf").write_bytes(data)

    def test_23_autenticacao_somente_no_historico(self):
        fund = self._rede()
        ei = self._rede(infantil=True)
        self._login(fund["inep"])
        historico = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/historico.pdf"
        ))
        transf = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        boletim = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/boletim.pdf"
        ))
        declaracao = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/declaracao/cursando.pdf"
        ))
        conclusao_tipo = self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/declaracao/conclusao.pdf"
        )
        ficha = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/comprovante-matricula.pdf"
        ))
        self._login(ei["inep"])
        parecer = self._assert_pdf(self._pdf(
            f"/api/alunos/{ei['aluno_id']}/documentos/parecer.pdf"
        ))
        self.assertTrue(self._tem_autenticacao(historico))
        for data in (transf, boletim, declaracao, ficha, parecer):
            self.assertFalse(self._tem_autenticacao(data))
        if conclusao_tipo.status_code == 200:
            self.assertFalse(self._tem_autenticacao(conclusao_tipo.get_data()))
        else:
            self.assertEqual(conclusao_tipo.status_code, 400)

    def test_24_marca_dagua_so_historico_e_transferencia(self):
        fund = self._rede()
        ei = self._rede(infantil=True)
        self._login(fund["inep"])
        historico = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/historico.pdf"
        ))
        transf = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        boletim = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/boletim.pdf"
        ))
        declaracao = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/declaracao/cursando.pdf"
        ))
        ficha = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/comprovante-matricula.pdf"
        ))
        rendimento = self._assert_pdf(self._pdf(
            f"/api/turmas/{fund['turma_id']}/documentos/rendimento/{fund['mat_id']}.pdf"
        ))
        self._login(ei["inep"])
        parecer = self._assert_pdf(self._pdf(
            f"/api/alunos/{ei['aluno_id']}/documentos/parecer.pdf"
        ))
        self.assertTrue(self._pdf_tem_marca_dagua(historico))
        self.assertTrue(self._pdf_tem_marca_dagua(transf))
        self.assertGreaterEqual(self._n_paginas(transf), 2)
        for data in (boletim, declaracao, ficha, rendimento, parecer):
            self.assertFalse(self._pdf_tem_marca_dagua(data))

    def test_25_marca_dagua_em_todas_as_paginas_do_historico(self):
        from src.pdf_oficial import montar_pdf_historico
        linhas = []
        for ano in range(2014, 2027):
            for disc in (
                "Português", "Matemática", "História", "Geografia",
                "Ciências", "Arte", "Educação Física", "Ensino Religioso",
            ):
                linhas.append({
                    "ano_letivo": ano,
                    "serie": 5,
                    "serie_rotulo": "5º ano",
                    "escola": "Escola Municipal Ficticia",
                    "municipio_uf": "Palmeira do Piauí/PI",
                    "componente": disc,
                    "carga_horaria": 160,
                    "nota_conceito": 7.5,
                    "situacao": "APROVADO",
                })
        payload = {
            "tipo": "historico_escolar",
            "aluno": {
                "nome": "Aluno Ficticio",
                "data_nascimento": "2014-03-15",
                "local_nascimento": "Palmeira do Piauí",
                "nome_mae": "Mae Ficticia",
                "nome_pai": "Pai Ficticio",
            },
            "escola": {"nome": "Escola Municipal Ficticia", "inep": "12345678"},
            "titulo_tabela": "COMPONENTES CURRICULARES CURSADOS/CURSANDO",
            "componentes_cursados": linhas,
            "trajetoria": [],
            "observacoes": "",
        }
        data = montar_pdf_historico(payload)
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertGreaterEqual(self._n_paginas(data), 2)
        self.assertTrue(self._pdf_tem_marca_dagua(data))
        self.assertTrue(self._tem_autenticacao(data))
        (ARTEFATOS / "inspecao_historico_multipagina.pdf").write_bytes(data)

    def test_26_helper_marca_dagua_nao_e_padrao_global(self):
        import inspect
        from src import pdf_oficial
        self.assertIn("marca_dagua=False", inspect.getsource(pdf_oficial._build_pdf))
        for nome in (
            "montar_pdf_boletim",
            "montar_pdf_declaracao",
            "montar_pdf_parecer",
            "montar_pdf_matricula",
            "montar_pdf_rendimento",
        ):
            src = inspect.getsource(getattr(pdf_oficial, nome))
            self.assertNotIn("marca_dagua=True", src, nome)
            self.assertNotIn("desenhar_marca_dagua_brasao", src, nome)
        src_h = inspect.getsource(pdf_oficial.montar_pdf_historico)
        self.assertIn("TIPOS_COM_MARCA_DAGUA", src_h)
        self.assertIn("desenhar_marca_dagua_brasao", inspect.getsource(pdf_oficial._build_pdf))

    def test_27_acl_documentos_pdf_permanece(self):
        fund = self._rede()
        ei = self._rede(infantil=True)
        outra = self._rede()
        self._login(fund["cpf_prof"])
        self.assertEqual(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/boletim.pdf"
        ).status_code, 403)
        self.assertEqual(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/comprovante-matricula.pdf"
        ).status_code, 403)
        self._login(outra["inep"])
        self.assertEqual(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/historico.pdf"
        ).status_code, 403)
        self.assertEqual(self._pdf(
            f"/api/alunos/{ei['aluno_id']}/documentos/parecer.pdf"
        ).status_code, 403)
        self._login(ei["cpf_prof"])
        self._assert_pdf(self._pdf(
            f"/api/alunos/{ei['aluno_id']}/documentos/parecer.pdf"
        ))
        self._login(fund["inep"])
        self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/boletim.pdf"
        ))

    def test_28_documento_inexistente_nao_gera_500(self):
        self._login("admin", SENHA_DEMO_ADMIN)
        for url in (
            "/api/alunos/99999/documentos/historico.pdf",
            "/api/alunos/99999/documentos/historico-transferencia.pdf",
            "/api/alunos/99999/documentos/boletim.pdf",
            "/api/alunos/99999/documentos/declaracao/cursando.pdf",
            "/api/alunos/99999/documentos/parecer.pdf",
            "/api/alunos/99999/documentos/comprovante-matricula.pdf",
            "/api/historicos/99999/boletim.pdf",
            "/api/historicos/99999/parecer.pdf",
            "/api/turmas/99999/documentos/rendimento/1.pdf",
        ):
            resp = self._pdf(url)
            self.assertNotEqual(resp.status_code, 500, url)
            self.assertEqual(resp.status_code, 404, url)
            self.assertFalse((resp.content_type or "").startswith("application/pdf"))
        fund = self._rede()
        self._login(fund["inep"])
        resp = self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/declaracao/inexistente.pdf"
        )
        self.assertNotEqual(resp.status_code, 500)
        self.assertEqual(resp.status_code, 400)

    def test_29_ficha_matricula_assinaturas_do_responsavel(self):
        import inspect
        from src import pdf_oficial

        src_ficha = inspect.getsource(pdf_oficial.montar_pdf_matricula)
        self.assertIn("Pai/Mãe ou Responsável", src_ficha)
        self.assertIn("Responsável pela Matrícula", src_ficha)
        self.assertNotIn("Secretário(a)", src_ficha)
        self.assertNotIn("Diretor(a)", src_ficha)

        src_padrao = inspect.getsource(pdf_oficial._assinaturas)
        self.assertIn('("Secretário(a)", None), ("Diretor(a)", None)', src_padrao)

        src_parecer = inspect.getsource(pdf_oficial.montar_pdf_parecer)
        self.assertIn("Professor(a) Titular", src_parecer)
        self.assertIn("Coordenação Pedagógica", src_parecer)
        self.assertNotIn("Pai/Mãe ou Responsável", src_parecer)

        for nome in (
            "montar_pdf_boletim",
            "montar_pdf_declaracao",
            "montar_pdf_historico",
            "montar_pdf_rendimento",
        ):
            src = inspect.getsource(getattr(pdf_oficial, nome))
            self.assertNotIn("Pai/Mãe ou Responsável", src, nome)
            self.assertNotIn("Responsável pela Matrícula", src, nome)

        fund = self._rede()
        ei = self._rede(infantil=True)
        self._login(fund["inep"])
        ficha = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/comprovante-matricula.pdf"
        ))
        boletim = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/boletim.pdf"
        ))
        declaracao = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/declaracao/cursando.pdf"
        ))
        historico = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/historico.pdf"
        ))
        transf = self._assert_pdf(self._pdf(
            f"/api/alunos/{fund['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        self._login(ei["inep"])
        parecer = self._assert_pdf(self._pdf(
            f"/api/alunos/{ei['aluno_id']}/documentos/parecer.pdf"
        ))

        def _norm(data):
            return self._compacto_pdf(data).replace("Á", "A").replace("À", "A")

        ficha_txt = _norm(ficha)
        self.assertIn("PAI/MAE OU RESPONSAVEL", ficha_txt)
        self.assertIn("RESPONSAVEL PELA MATRICULA", ficha_txt)
        self.assertNotIn("SECRETARIO(A)", ficha_txt)
        self.assertNotIn("DIRETOR(A)", ficha_txt)
        self.assertFalse(self._tem_autenticacao(ficha))

        for data, rotulo in (
            (boletim, "boletim"),
            (declaracao, "declaracao"),
            (historico, "historico"),
            (transf, "transferencia"),
        ):
            txt = _norm(data)
            self.assertIn("SECRETARIO(A)", txt, rotulo)
            self.assertIn("DIRETOR(A)", txt, rotulo)
            self.assertNotIn("PAI/MAE OU RESPONSAVEL", txt, rotulo)
            self.assertNotIn("RESPONSAVEL PELA MATRICULA", txt, rotulo)

        parecer_txt = _norm(parecer)
        self.assertIn("PROFESSOR(A) TITULAR", parecer_txt)
        self.assertIn("COORDENACAO PEDAGOGICA", parecer_txt)
        self.assertNotIn("PAI/MAE OU RESPONSAVEL", parecer_txt)
        self.assertNotIn("SECRETARIO(A)", parecer_txt)
        self.assertNotIn("DIRETOR(A)", parecer_txt)

        (ARTEFATOS / "inspecao_ficha_matricula.pdf").write_bytes(ficha)

    def _assert_dados_timbre_centralizados(self):
        import inspect
        from src import pdf_oficial
        from src.documentos_institucionais import dados_timbre

        t = dados_timbre()
        self.assertEqual(t["prefeitura"], "PREFEITURA MUNICIPAL DE PALMEIRA DO PIAUÍ")
        self.assertEqual(t["secretaria"], "SECRETARIA MUNICIPAL DE EDUCAÇÃO")
        self.assertEqual(t["cnpj"], "06.077.363/0001-01")
        self.assertEqual(t["cnpj_linha"], "CNPJ: 06.077.363/0001-01")
        self.assertEqual(t["municipio_uf"], "PALMEIRA DO PIAUÍ - PI")
        self.assertEqual(
            t["autorizacao"],
            "AUTORIZADO PELA RESOLUÇÃO C.E.E/PI Nº 133/2026",
        )
        src = inspect.getsource(pdf_oficial)
        self.assertNotIn("06.077.363/0001-01", src)
        self.assertNotIn("133/2026", src)
        self.assertNotIn("88000001", src)
        self.assertIn("dados_timbre()", inspect.getsource(pdf_oficial._timbre_flowable))

    def _assert_cabecalho(self, data, escola_nome, inep, rotulo):
        self.assertTrue(data.startswith(b"%PDF-"), rotulo)
        texto = " ".join(self._texto_pdf(data).replace("\n", " ").split())
        pos = -1
        for trecho in (
            "PREFEITURA MUNICIPAL DE PALMEIRA DO PIAUÍ",
            "SECRETARIA MUNICIPAL DE EDUCAÇÃO",
            "CNPJ: 06.077.363/0001-01",
            "PALMEIRA DO PIAUÍ - PI",
            "AUTORIZADO PELA RESOLUÇÃO C.E.E/PI Nº 133/2026",
        ):
            idx = texto.find(trecho)
            self.assertGreater(
                idx, pos, "%s ausente ou fora de ordem em %s" % (trecho, rotulo),
            )
            pos = idx
        linha = "%s (INEP: %s)" % (escola_nome, inep)
        idx_escola = texto.find(linha)
        self.assertGreater(idx_escola, pos, "%s: escola deve ser a última linha do timbre" % rotulo)
        self.assertNotIn("EMEF Demo Sol Nascente", texto, rotulo)
        self.assertNotIn("88000001", texto, rotulo)

    def _assert_logo_presente(self, data):
        from PIL import Image as PILImage
        from src.documentos_institucionais import caminho_logo
        path = caminho_logo()
        self.assertTrue(path, "logo2.png ausente")
        with PILImage.open(path) as im:
            alvo = im.size
        paginas = self._imagens_por_pagina(data)
        self.assertTrue(paginas and paginas[0], "logo não embutida na primeira página")
        self.assertIn(alvo, paginas[0])

    def test_30_cabecalho_institucional(self):
        from src.pdf_oficial import montar_pdf_boletim, montar_pdf_parecer

        self._assert_dados_timbre_centralizados()
        fund = self._rede(nome_aluno="Aluno Cabecalho Fund")
        outra = self._rede(nome_aluno="Aluno Cabecalho Outra")
        ei = self._rede(infantil=True, nome_aluno="Aluno Cabecalho EI")
        self.assertNotEqual(fund["escola_nome"], outra["escola_nome"])
        self.assertNotEqual(fund["inep"], outra["inep"])
        self.assertNotEqual(fund["escola_nome"], ei["escola_nome"])

        self._login(fund["inep"])
        urls = {
            "ficha": f"/api/alunos/{fund['aluno_id']}/documentos/comprovante-matricula.pdf",
            "boletim": f"/api/alunos/{fund['aluno_id']}/documentos/boletim.pdf",
            "declaracao": f"/api/alunos/{fund['aluno_id']}/documentos/declaracao/cursando.pdf",
            "historico": f"/api/alunos/{fund['aluno_id']}/documentos/historico.pdf",
            "transferencia": f"/api/alunos/{fund['aluno_id']}/documentos/historico-transferencia.pdf",
            "rendimento": f"/api/turmas/{fund['turma_id']}/documentos/rendimento/{fund['mat_id']}.pdf",
        }
        gerados = {}
        for nome, url in urls.items():
            resp = self._pdf(url)
            data = self._assert_pdf(resp)
            self.assertTrue((resp.content_type or "").startswith("application/pdf"), nome)
            self._assert_cabecalho(data, fund["escola_nome"], fund["inep"], nome)
            texto = self._texto_pdf(data)
            self.assertNotIn(outra["escola_nome"], texto, nome)
            self.assertNotIn(outra["inep"], texto, nome)
            gerados[nome] = data

        self._login(outra["inep"])
        ficha_b = self._assert_pdf(self._pdf(
            f"/api/alunos/{outra['aluno_id']}/documentos/comprovante-matricula.pdf"
        ))
        self._assert_cabecalho(ficha_b, outra["escola_nome"], outra["inep"], "ficha_outra_escola")
        self.assertNotIn(fund["escola_nome"], self._texto_pdf(ficha_b))
        self.assertNotIn(fund["inep"], self._texto_pdf(ficha_b))

        self._login(ei["inep"])
        parecer = self._assert_pdf(self._pdf(
            f"/api/alunos/{ei['aluno_id']}/documentos/parecer.pdf"
        ))
        self._assert_cabecalho(parecer, ei["escola_nome"], ei["inep"], "parecer")

        escola_snap = {"nome": "Unidade Historica Snapshot", "inep": "99001122"}
        boletim_hist = montar_pdf_boletim({
            "escola": escola_snap,
            "aluno_nome": "Aluno Snapshot",
            "linhas": [],
            "titulo": "BOLETIM ESCOLAR",
        })
        parecer_hist = montar_pdf_parecer({
            "escola": escola_snap,
            "aluno_nome": "Crianca Snapshot",
            "pareceres": {},
            "titulo": "RELATÓRIO DESCRITIVO DE DESENVOLVIMENTO",
        })
        self.assertTrue(boletim_hist.startswith(b"%PDF-"))
        self.assertTrue(parecer_hist.startswith(b"%PDF-"))
        self._assert_cabecalho(
            boletim_hist, escola_snap["nome"], escola_snap["inep"], "boletim_historico",
        )
        self._assert_cabecalho(
            parecer_hist, escola_snap["nome"], escola_snap["inep"], "parecer_historico",
        )
        self.assertNotIn(fund["escola_nome"], self._texto_pdf(boletim_hist))
        self.assertNotIn(ei["escola_nome"], self._texto_pdf(parecer_hist))

        self.assertTrue(self._tem_autenticacao(gerados["historico"]))
        for nome in ("transferencia", "boletim", "declaracao", "ficha", "rendimento"):
            self.assertFalse(self._tem_autenticacao(gerados[nome]), nome)
        self.assertFalse(self._tem_autenticacao(parecer))
        self.assertFalse(self._tem_autenticacao(boletim_hist))
        self.assertFalse(self._tem_autenticacao(parecer_hist))
        self.assertTrue(self._pdf_tem_marca_dagua(gerados["historico"]))
        self.assertTrue(self._pdf_tem_marca_dagua(gerados["transferencia"]))
        for nome in ("boletim", "declaracao", "ficha", "rendimento"):
            self.assertFalse(self._pdf_tem_marca_dagua(gerados[nome]), nome)
        self.assertFalse(self._pdf_tem_marca_dagua(parecer))
        self.assertFalse(self._pdf_tem_marca_dagua(boletim_hist))
        self.assertFalse(self._pdf_tem_marca_dagua(parecer_hist))

        ficha_txt = self._compacto_pdf(gerados["ficha"]).replace("Á", "A").replace("À", "A")
        self.assertIn("PAI/MAE OU RESPONSAVEL", ficha_txt)
        self.assertIn("RESPONSAVEL PELA MATRICULA", ficha_txt)
        parecer_txt = self._compacto_pdf(parecer).replace("Á", "A").replace("À", "A")
        self.assertIn("PROFESSOR(A) TITULAR", parecer_txt)
        self.assertIn("COORDENACAO PEDAGOGICA", parecer_txt)

        self._assert_logo_presente(gerados["ficha"])
        self._assert_logo_presente(gerados["historico"])
        self._assert_logo_presente(parecer)

        (ARTEFATOS / "cabecalho_ficha.pdf").write_bytes(gerados["ficha"])
        (ARTEFATOS / "cabecalho_parecer.pdf").write_bytes(parecer)
        (ARTEFATOS / "cabecalho_boletim.pdf").write_bytes(gerados["boletim"])
        (ARTEFATOS / "cabecalho_historico.pdf").write_bytes(gerados["historico"])
        (ARTEFATOS / "cabecalho_transferencia.pdf").write_bytes(gerados["transferencia"])

    def _url_complemento(self, aluno_id):
        return f"/api/alunos/{aluno_id}/documentos/historico-transferencia/complemento"

    def _contar(self, modelo, **filtro):
        with self.app.app_context():
            from src import db
            return db.session.query(modelo).filter_by(**filtro).count()

    def test_33_complemento_transferencia_preenche_so_o_que_falta(self):
        from src.models import AuditLog, RegistroFalta, RegistroNota

        rede = self._rede()
        self._login(rede["inep"])
        antes_notas = self._contar(RegistroNota, aluno_id=rede["aluno_id"])
        antes_faltas = self._contar(RegistroFalta, aluno_id=rede["aluno_id"])
        resp = self.client.get(self._url_complemento(rede["aluno_id"]))
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        form, _ = _json(resp)
        self.assertFalse(form["completo"])
        abertos = []
        for comp in form["componentes"]:
            if comp["nome"] != "Matemática":
                continue
            for periodo in comp["periodos"]:
                if periodo["futuro"]:
                    continue
                if periodo["editavel_nota"]:
                    abertos.append(periodo["numero"])
        self.assertTrue(abertos)
        periodo = abertos[0]
        resp = self.client.put(self._url_complemento(rede["aluno_id"]), json={
            "itens": [{
                "nome": "Matemática",
                "disciplina_id": rede["mat_id"],
                "periodo": periodo,
                "nota": "9,5",
                "faltas": 4,
            }],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.assertEqual(self._contar(RegistroNota, aluno_id=rede["aluno_id"]), antes_notas)
        self.assertEqual(self._contar(RegistroFalta, aluno_id=rede["aluno_id"]), antes_faltas)
        pdf = self._assert_pdf(self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        texto = self._texto_pdf(pdf)
        self.assertIn("9,5", texto)
        self.assertIn("4", texto)
        self.assertNotIn("digitado manualmente", texto.lower())
        historico = self._assert_pdf(self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ))
        self.assertNotIn("9,5", self._texto_pdf(historico))
        with self.app.app_context():
            from src import db
            log = (
                AuditLog.query.filter_by(acao="COMPLEMENTO_TRANSFERENCIA_CRIADO")
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(log)
            bruto = json.dumps(log.dados_novos or {}, ensure_ascii=False)
            self.assertIn(str(rede["aluno_id"]), bruto)
            self.assertNotIn("9.5", bruto)
            self.assertNotIn("9,5", bruto)
            self.assertNotIn(rede["cpf_aluno"], bruto)
            self.assertNotIn("nome_mae", bruto)
            self.assertNotIn("\"faltas\"", bruto)
            self.assertNotIn("\"nota\"", bruto)

    def test_34_dado_oficial_precede_complemento_e_periodo_futuro_fica_em_branco(self):
        from src.models import RegistroFalta, RegistroNota

        rede = self._rede(nome_turma="5º B", ano_letivo=2026)
        self._definir_periodos_2026()
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_id"], rede["mat_id"],
            [
                self._nota(rede["aluno_id"], "8", 11),
                self._nota(rede["aluno_id"], "8", 12),
            ],
        ).status_code, 200)
        notas_antes = self._contar(RegistroNota, aluno_id=rede["aluno_id"])
        faltas_antes = self._contar(RegistroFalta, aluno_id=rede["aluno_id"])
        self._login(rede["inep"])
        resp = self.client.put(self._url_complemento(rede["aluno_id"]), json={
            "itens": [
                {"nome": "Matemática", "periodo": 1, "nota": "1,5", "faltas": 9},
                {"nome": "Matemática", "periodo": 4, "nota": "3,3", "faltas": 7},
            ],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        form, _ = _json(resp)
        mat = next(c for c in form["componentes"] if c["nome"] == "Matemática")
        p1 = next(p for p in mat["periodos"] if p["numero"] == 1)
        p4 = next(p for p in mat["periodos"] if p["numero"] == 4)
        self.assertIsNotNone(p1["nota_oficial"])
        self.assertFalse(p1["editavel_nota"])
        self.assertTrue(p4["futuro"])
        self.assertIsNone(p4["nota_complementar"])
        pdf = self._assert_pdf(self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        texto = self._texto_pdf(pdf)
        self.assertNotIn("1,5", texto)
        self.assertNotIn("3,3", texto)
        self.assertEqual(self._contar(RegistroNota, aluno_id=rede["aluno_id"]), notas_antes)
        self.assertEqual(self._contar(RegistroFalta, aluno_id=rede["aluno_id"]), faltas_antes)

    def test_35_acl_complemento_transferencia(self):
        rede = self._rede()
        outra = self._rede()
        self._login("admin", SENHA_DEMO_ADMIN)
        self.assertEqual(self.client.get(self._url_complemento(rede["aluno_id"])).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(self.client.get(self._url_complemento(rede["aluno_id"])).status_code, 200)
        self._login(outra["inep"])
        self.assertEqual(self.client.get(self._url_complemento(rede["aluno_id"])).status_code, 403)
        self._login(rede["cpf_prof"])
        self.assertEqual(self.client.put(
            self._url_complemento(rede["aluno_id"]), json={"itens": []},
        ).status_code, 403)
        self._login(rede["login_coord"])
        self.assertEqual(self.client.get(self._url_complemento(rede["aluno_id"])).status_code, 403)
        self.assertEqual(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        ).status_code, 403)

    def _faixas_pdf(self, data, texto):
        from tests.test_cor_notas_pdf import faixas, trechos_coloridos

        self._trechos = trechos_coloridos(data)
        return faixas(data, texto)

    def _assert_faixa_pdf(self, data, texto, faixa):
        achados = self._faixas_pdf(data, texto)
        self.assertTrue(achados, "texto ausente no PDF: %s" % texto)
        self.assertTrue(all(item == faixa for item in achados), (texto, achados))

    def test_36_boletim_rendimento_e_transferencia_colorem_pela_media_vigente(self):
        from src.models import RegistroFalta, RegistroNota

        rede = self._rede(nome_turma="5º C", ano_letivo=2026, nome_aluno="Clara Notas")
        self._definir_media(7.0)
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(rede["turma_id"], rede["mat_id"], [
            self._nota(rede["aluno_id"], "6,9", 11),
            self._nota(rede["aluno_id"], "6,9", 12),
            self._nota(rede["aluno_id"], "7,0", 21),
            self._nota(rede["aluno_id"], "7,0", 22),
            self._nota(rede["aluno_id"], "8,5", 31),
            self._nota(rede["aluno_id"], "8,5", 32),
        ]).status_code, 200)
        self._login(rede["inep"])
        boletim = self._assert_pdf(self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/boletim.pdf"
        ))
        self._assert_faixa_pdf(boletim, "6,9", "abaixo")
        self.assertIn("atingida", self._faixas_pdf(boletim, "7,0"))
        self.assertIn(None, self._faixas_pdf(boletim, "7,0"))
        self._assert_faixa_pdf(boletim, "8,5", "atingida")
        self._assert_faixa_pdf(boletim, "0", None)
        self._assert_faixa_pdf(boletim, "CURSANDO", None)
        texto_boletim = self._texto_pdf(boletim)
        self.assertIn("6,9", texto_boletim)
        self.assertIn("7,0", texto_boletim)
        self.assertIn("8,5", texto_boletim)
        self.assertNotIn("6.9", texto_boletim)

        rendimento = self._assert_pdf(self._pdf(
            f"/api/turmas/{rede['turma_id']}/documentos/rendimento/{rede['mat_id']}.pdf"
        ))
        self._assert_faixa_pdf(rendimento, "6,9", "abaixo")
        self._assert_faixa_pdf(rendimento, "8,5", "atingida")
        self.assertIn("atingida", self._faixas_pdf(rendimento, "7,0"))
        self.assertNotIn(None, self._faixas_pdf(rendimento, "7,0"))
        self._assert_faixa_pdf(rendimento, "CURSANDO", None)

        notas_antes = self._contar(RegistroNota, aluno_id=rede["aluno_id"])
        faltas_antes = self._contar(RegistroFalta, aluno_id=rede["aluno_id"])
        self._definir_periodos_2026()
        form, _ = _json(self.client.get(self._url_complemento(rede["aluno_id"])))
        alvo = None
        for comp in form["componentes"]:
            if comp["nome"] == "Matemática":
                continue
            for periodo in comp["periodos"]:
                if periodo.get("editavel_nota") and not periodo.get("futuro"):
                    alvo = (comp["nome"], periodo["numero"])
                    break
            if alvo:
                break
        self.assertIsNotNone(alvo)
        resp = self.client.put(self._url_complemento(rede["aluno_id"]), json={
            "itens": [
                {"nome": "Matemática", "periodo": 1, "nota": "1,0", "faltas": 9},
                {"nome": alvo[0], "periodo": alvo[1], "nota": "6,5", "faltas": 3},
            ],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        self.assertEqual(self._contar(RegistroNota, aluno_id=rede["aluno_id"]), notas_antes)
        self.assertEqual(self._contar(RegistroFalta, aluno_id=rede["aluno_id"]), faltas_antes)
        transf = self._assert_pdf(self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        texto = self._texto_pdf(transf)
        self.assertIn("6,9", texto)
        self.assertIn("6,5", texto)
        self.assertNotIn("1,0", texto)
        self.assertNotIn("1.0", texto)
        self._assert_faixa_pdf(transf, "6,9", "abaixo")
        self._assert_faixa_pdf(transf, "6,5", "abaixo")
        self._assert_faixa_pdf(transf, "8,5", "atingida")
        self.assertIn("atingida", self._faixas_pdf(transf, "7,0"))
        self._assert_faixa_pdf(transf, "3", None)
        self._assert_faixa_pdf(transf, "CURSANDO", None)
        self.assertTrue(self._pdf_tem_marca_dagua(transf))
        from tests.test_cor_notas_pdf import _NOTA_NUMERICA
        for trecho, faixa in self._trechos:
            if faixa is not None:
                self.assertRegex(trecho, _NOTA_NUMERICA)

    def test_37_historico_congelado_colore_e_legado_sem_regra_nao(self):
        rede = self._rede(nome_turma="5º D", ano_letivo=2026, nome_aluno="Davi Historico")
        self._definir_media(7.0)
        self._login(rede["cpf_prof"])
        self.assertEqual(self._salvar_notas(
            rede["turma_id"], rede["mat_id"], self._notas_ano(rede["aluno_id"], 8, 8),
        ).status_code, 200)
        self._encerrar(rede["turma_id"], rede["inep"])
        self._definir_media(9.5)
        self._login(rede["inep"])
        self.assertEqual(self.client.post(
            f"/api/alunos/{rede['aluno_id']}/trajetoria-legada",
            json={
                "ano_letivo": 2024,
                "serie": 4,
                "estabelecimento": "Escola Municipal de Origem",
                "municipio": "Bom Jesus",
                "uf": "PI",
                "resultado": "Aprovado",
                "carga_horaria_total": 800,
                "componentes": [
                    {"nome": "Matemática", "nota_conceito": "8,5", "carga_horaria": 200, "situacao": "Aprovado"},
                    {"nome": "Arte", "nota_conceito": "MB", "carga_horaria": 40, "situacao": "Cursado"},
                    {"nome": "Educação Física", "nota_conceito": "APTO", "carga_horaria": 40, "situacao": "Cursado"},
                ],
            },
        ).status_code, 200)
        payload = self._payload(rede["aluno_id"])
        legado = next(
            linha for linha in payload["componentes_cursados"]
            if linha["ano_letivo"] == 2024 and linha["componente"] == "Matemática"
        )
        oficial = next(
            linha for linha in payload["componentes_cursados"]
            if linha["ano_letivo"] == 2026 and linha["componente"] == "Matemática"
        )
        self.assertIsNone(legado["media_minima"])
        self.assertEqual(legado["nota_conceito"], "8,5")
        self.assertAlmostEqual(float(oficial["media_minima"]), 7.0)
        self.assertEqual(oficial["situacao"], "APROVADO")

        historico = self._assert_pdf(self._pdf(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ))
        texto = self._texto_pdf(historico)
        self.assertIn("8,0", texto)
        self.assertIn("8,5", texto)
        self.assertIn("MB", texto)
        self.assertIn("APTO", texto)
        self.assertNotIn("9,5", texto)
        self._assert_faixa_pdf(historico, "8,0", "atingida")
        self._assert_faixa_pdf(historico, "8,5", None)
        self._assert_faixa_pdf(historico, "MB", None)
        self._assert_faixa_pdf(historico, "APTO", None)
        self._assert_faixa_pdf(historico, "APROVADO", None)
        self.assertTrue(self._pdf_tem_marca_dagua(historico))

        with self.app.app_context():
            from src.models import HistoricoAnual
            hist = HistoricoAnual.query.filter_by(aluno_id=rede["aluno_id"]).one()
            hist_id = hist.id
        boletim = self._assert_pdf(self._pdf(
            f"/api/historicos/{hist_id}/boletim.pdf"
        ))
        texto_b = self._texto_pdf(boletim)
        self.assertIn("8,0", texto_b)
        self.assertIn("7,0", texto_b)
        self.assertNotIn("9,5", texto_b)
        self._assert_faixa_pdf(boletim, "8,0", "atingida")
        self._assert_faixa_pdf(boletim, "7,0", None)
        self._assert_faixa_pdf(boletim, "APROVADO", None)
        self._assert_faixa_pdf(boletim, "0", None)
