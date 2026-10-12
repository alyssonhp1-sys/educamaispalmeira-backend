"""Trajetória escolar legada (anterior ao Educa+) e histórico por componente cursado."""
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from tests.support import SENHA_DEMO_ADMIN, SENHA_PESSOAL, concluir_primeiro_acesso, senha_temporaria

ARTEFATOS = Path(__file__).resolve().parent / "_pdf_artefatos"


def _json(resp):
    body = resp.get_json() or {}
    return body.get("data"), body.get("error")


class TrajetoriaLegadaTest(unittest.TestCase):
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

    def _login(self, login, senha=SENHA_PESSOAL):
        resp = self.client.post("/api/auth/login", json={"login": login, "senha": senha})
        self.assertEqual(resp.status_code, 200, _json(resp)[1])

    def _proximo(self):
        TrajetoriaLegadaTest._seq += 1
        return TrajetoriaLegadaTest._seq

    def _rede(self, nome_turma="9º A", ano_letivo=2026, nome_aluno=None, infantil=False, nome_escola=None):
        n = self._proximo()
        inep = f"9200{n:04d}"
        cpf_prof = f"9201{n:07d}"[:11]
        cpf_aluno = f"9202{n:07d}"[:11]
        etapa = "infantil" if infantil else "fundamental"
        nome_turma = nome_turma or ("Infantil V" if infantil else "9º A")
        escola_nome = nome_escola or f"Escola Legado {inep}"
        self._login("admin", SENHA_DEMO_ADMIN)
        resp = self.client.post("/api/escolas", json={
            "nome": escola_nome, "inep": inep,
            "oferta_infantil": True, "oferta_fundamental": True,
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_gestor = senha_temporaria(resp)
        dash = _json(self.client.get("/api/dashboard"))[0]
        escola_id = next(e["id"] for e in dash["escolas"] if e["inep"] == inep)
        login_coord = f"coordLeg{n}"
        resp = self.client.post(
            f"/api/escolas/{escola_id}/coordenadores",
            json={"login": login_coord, "nome": "Coord Legado"},
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        temp_coord = senha_temporaria(resp)
        resp = self.client.post("/api/professores", json={
            "nome": "Prof Legado", "cpf": cpf_prof, "escola_id": escola_id,
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
        prof_id = dash["professores"][0]["id"]
        resp = self.client.post("/api/alunos", json={
            "nome": nome_aluno or "João Legado",
            "cpf": cpf_aluno,
            "turma_id": turma_id,
            "nome_mae": "Maria Legado",
            "nome_pai": "José Legado",
            "data_nascimento": "2012-05-20",
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
            "inep": inep, "cpf_prof": cpf_prof, "cpf_aluno": cpf_aluno,
            "login_coord": login_coord, "turma_id": turma_id, "turma_nome": nome_turma,
            "escola_id": escola_id, "mat_id": mat_id, "aluno_id": aluno_id,
            "prof_id": prof_id, "ano_letivo": ano_letivo, "escola_nome": escola_nome,
        }

    def _legado(self, aluno_id, ano, serie, componentes=None, **extra):
        payload = {
            "ano_letivo": ano,
            "serie": serie,
            "estabelecimento": extra.get("estabelecimento", "Escola Municipal de Origem"),
            "municipio": extra.get("municipio", "Bom Jesus"),
            "uf": extra.get("uf", "PI"),
            "resultado": extra.get("resultado", "Aprovado"),
            "carga_horaria_total": extra.get("carga_horaria_total", 1000),
            "observacao": extra.get("observacao"),
            "componentes": componentes if componentes is not None else [
                {"nome": "Matemática", "nota_conceito": "7,0", "carga_horaria": 320},
            ],
        }
        if "carga_horaria_total" in extra and extra["carga_horaria_total"] is None:
            payload["carga_horaria_total"] = None
        return self.client.post(
            f"/api/alunos/{aluno_id}/trajetoria-legada", json=payload,
        )

    def _payload(self, aluno_id, para_transferencia=False):
        with self.app.app_context():
            from src.historico_escolar import montar_historico_escolar
            from src.models import Aluno
            from src import db
            aluno = db.session.get(Aluno, aluno_id)
            return montar_historico_escolar(aluno, para_transferencia=para_transferencia)

    def _assert_pdf(self, resp, cpf=None):
        self.assertEqual(resp.status_code, 200, resp.get_data()[:300])
        self.assertTrue((resp.content_type or "").startswith("application/pdf"), resp.content_type)
        self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
        data = resp.get_data()
        self.assertTrue(data.startswith(b"%PDF-"), data[:20])
        disp = resp.headers.get("Content-Disposition") or ""
        self.assertIn("filename=", disp)
        if cpf:
            digits = "".join(ch for ch in str(cpf) if ch.isdigit())
            self.assertNotIn(digits, disp)
        return data

    def _texto_pdf(self, data):
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

    def _info_pdf(self, data):
        from pypdf import PdfReader
        leitor = PdfReader(BytesIO(data))
        paginas = []
        for pagina in leitor.pages:
            box = pagina.mediabox
            w, h = float(box.width), float(box.height)
            paginas.append({
                "largura": w, "altura": h,
                "orientacao": "paisagem" if w > h else "retrato",
            })
        return paginas

    def _linhas(self, payload):
        return payload.get("componentes_cursados") or []

    def _linhas_ano(self, payload, ano):
        return [l for l in self._linhas(payload) if l.get("ano_letivo") == ano]

    def _compacto_pdf(self, data):
        compacto = " ".join(self._texto_pdf(data).replace("\n", " ").split()).upper()
        return (
            compacto.replace("Ê", "E").replace("É", "E").replace("Í", "I")
            .replace("Â", "A").replace("Ã", "A").replace("Ó", "O").replace("Ô", "O")
        )

    def _comps_legado(self, nota, situacao="Aprovado"):
        return [
            {"nome": "Língua Portuguesa", "nota_conceito": nota, "carga_horaria": 200, "situacao": situacao},
            {"nome": "Matemática", "nota_conceito": nota, "carga_horaria": 200, "situacao": situacao},
            {"nome": "Ciências", "nota_conceito": nota, "carga_horaria": 120, "situacao": situacao},
            {"nome": "História", "nota_conceito": nota, "carga_horaria": 80, "situacao": situacao},
            {"nome": "Geografia", "nota_conceito": nota, "carga_horaria": 80, "situacao": situacao},
            {"nome": "Arte", "nota_conceito": "MB", "carga_horaria": 40, "situacao": situacao},
            {"nome": "Educação Física", "nota_conceito": "APTO", "carga_horaria": 80, "situacao": "Cursado"},
            {"nome": "Ensino Religioso", "nota_conceito": "MB", "situacao": "Cursado"},
        ]

    def test_01_autorizado_cadastra_ano_legado(self):
        rede = self._rede()
        self._login(rede["inep"])
        resp = self._legado(rede["aluno_id"], 2024, 8, [
            {"nome": "Matemática", "nota_conceito": "6,0"},
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data, _ = _json(resp)
        self.assertEqual(data["ano_letivo"], 2024)
        self.assertEqual(data["serie"], 8)
        self.assertEqual(data["origem"], "LEGADO")
        self.assertEqual(data["componentes"][0]["nota_conceito"], "6,0")
        self.assertIsNone(data["componentes"][0]["carga_horaria"])
        self.assertIsNone(data["componentes"][0]["situacao"])

    def test_02_professor_403(self):
        rede = self._rede()
        self._login(rede["cpf_prof"])
        resp = self._legado(rede["aluno_id"], 2024, 8)
        self.assertEqual(resp.status_code, 403)
        self._login(rede["login_coord"])
        resp = self._legado(rede["aluno_id"], 2023, 7)
        self.assertEqual(resp.status_code, 403)
        self._login(rede["cpf_prof"])
        lista = self.client.get(f"/api/alunos/{rede['aluno_id']}/trajetoria-legada")
        self.assertEqual(lista.status_code, 403)

    def test_02b_secretaria_autorizada_e_csrf_no_write(self):
        rede = self._rede()
        login_sec = "secLeg%04d" % rede["aluno_id"]
        with self.app.app_context():
            from src import db
            from src.models import Usuario
            from src.passwords import hash_senha
            db.session.add(Usuario(
                login=login_sec,
                senha_hash=hash_senha(SENHA_PESSOAL),
                papel="secretaria",
                nome="Secretaria Municipal",
                primeiro_acesso=False,
            ))
            db.session.commit()
        self._login(login_sec)
        resp = self._legado(rede["aluno_id"], 2024, 8)
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        pdf = self.client.get(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self._assert_pdf(pdf, cpf=rede["cpf_aluno"])
        sem_csrf = self.client.post(
            f"/api/alunos/{rede['aluno_id']}/trajetoria-legada",
            json={
                "ano_letivo": 2023,
                "serie": 6,
                "estabelecimento": "Escola CSRF",
                "municipio": "Bom Jesus",
                "uf": "PI",
                "componentes": [{"nome": "História"}],
            },
            csrf=False,
        )
        self.assertEqual(sem_csrf.status_code, 400)
        self.assertIn("csrf", (_json(sem_csrf)[1] or "").lower())

    def test_03_gestor_nao_cadastra_aluno_outra_escola(self):
        rede_a = self._rede(nome_aluno="Aluno A")
        rede_b = self._rede(nome_aluno="Aluno B")
        self._login(rede_b["inep"])
        resp = self._legado(rede_a["aluno_id"], 2024, 8)
        self.assertEqual(resp.status_code, 403)
        self.assertIsNone(_json(resp)[0])
        lista = self.client.get(f"/api/alunos/{rede_a['aluno_id']}/trajetoria-legada")
        self.assertEqual(lista.status_code, 403)

    def test_04_edicao_funciona(self):
        rede = self._rede()
        self._login(rede["inep"])
        criado = _json(self._legado(rede["aluno_id"], 2023, 6))[0]
        resp = self.client.put(f"/api/trajetoria-legada/{criado['id']}", json={
            "ano_letivo": 2023,
            "serie": 6,
            "estabelecimento": "Escola Corrigida",
            "municipio": "Bom Jesus",
            "uf": "PI",
            "resultado": "Aprovado",
            "componentes": [{"nome": "Português", "nota_conceito": "MB", "situacao": "AP"}],
        })
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data = _json(resp)[0]
        self.assertEqual(data["estabelecimento"], "Escola Corrigida")
        self.assertEqual(data["componentes"][0]["nota_conceito"], "MB")
        self.assertEqual(data["componentes"][0]["situacao"], "AP")
        self.assertEqual(len(data["componentes"]), 1)

    def test_05_exclusao_com_auditoria(self):
        rede = self._rede()
        self._login(rede["inep"])
        criado = _json(self._legado(rede["aluno_id"], 2023, 6))[0]
        resp = self.client.delete(f"/api/trajetoria-legada/{criado['id']}")
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        lista = _json(self.client.get(f"/api/alunos/{rede['aluno_id']}/trajetoria-legada"))[0]
        self.assertEqual(lista["itens"], [])
        with self.app.app_context():
            from src.models import AuditLog
            log = (
                AuditLog.query.filter_by(acao="TRAJETORIA_LEGADA_REMOVIDA")
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(log)
            blob = json.dumps(log.dados_anteriores or {})
            self.assertIn("registro_legado_id", blob)
            self.assertNotIn("7,0", blob)
            self.assertNotIn(rede["cpf_aluno"], blob)
            self.assertNotIn("Maria Legado", blob)

    def test_06_duplicidade_ano_serie_bloqueada(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2024, 8).status_code, 200)
        resp = self._legado(rede["aluno_id"], 2024, 8)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("já existe", (_json(resp)[1] or "").lower())

    def test_07_conflito_historico_anual_educamais(self):
        rede = self._rede(nome_turma="8º A", ano_letivo=2024)
        self._login(rede["cpf_prof"])
        payload = []
        for a, b in ((11, 12), (21, 22), (31, 32), (41, 42)):
            payload.append({"aluno_id": rede["aluno_id"], "valor": "7", "tipo": "nota", "bimestre": a})
            payload.append({"aluno_id": rede["aluno_id"], "valor": "7", "tipo": "nota", "bimestre": b})
        self.assertEqual(self.client.post(
            f"/api/turmas/{rede['turma_id']}/disciplinas/{rede['mat_id']}/notas",
            json=payload,
        ).status_code, 200)
        self._login(rede["inep"])
        self.assertEqual(self.client.post(f"/api/turmas/{rede['turma_id']}/encerrar").status_code, 200)
        resp = self._legado(rede["aluno_id"], 2024, 8)
        self.assertEqual(resp.status_code, 400)
        erro = (_json(resp)[1] or "").lower()
        self.assertIn("educa", erro)

    def test_08_legado_2024_nao_recebe_matriz_2026(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2024, 8, [
            {"nome": "Matemática", "nota_conceito": "6,0"},
        ], carga_horaria_total=None).status_code, 200)
        payload = self._payload(rede["aluno_id"])
        ano_2024 = next(a for a in payload["anos_finais"] if a["ano_letivo"] == 2024)
        self.assertEqual(ano_2024["fonte"], "legado")
        self.assertFalse(ano_2024["matriz_aplicada"])
        nomes = [c["nome"] for c in ano_2024["componentes"]]
        self.assertEqual(nomes, ["Matemática"])
        self.assertNotIn("Cultura Digital", nomes)
        self.assertNotIn("Biologia", nomes)
        self.assertNotIn("Formação Humana", nomes)

    def test_09_conceito_textual_preservado(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2024, 8, [
            {"nome": "Arte", "nota_conceito": "MB"},
            {"nome": "Educação Física", "nota_conceito": "APTO"},
        ]).status_code, 200)
        payload = self._payload(rede["aluno_id"])
        ano = next(a for a in payload["anos_finais"] if a["ano_letivo"] == 2024)
        mapa = {c["nome"]: c for c in ano["componentes"]}
        self.assertEqual(mapa["Arte"]["nota_conceito"], "MB")
        self.assertIsNone(mapa["Arte"]["media"])
        self.assertEqual(mapa["Educação Física"]["nota_conceito"], "APTO")
        linhas = self._linhas_ano(payload, 2024)
        valores = {l["componente"]: l["nota_conceito"] for l in linhas}
        self.assertEqual(valores["Arte"], "MB")
        self.assertEqual(valores["Educação Física"], "APTO")

    def test_10_ch_desconhecida_permanece_none(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(self._legado(
            rede["aluno_id"], 2024, 8,
            [{"nome": "História", "nota_conceito": "8,0"}],
            carga_horaria_total=None,
        ).status_code, 200)
        payload = self._payload(rede["aluno_id"])
        ano = next(a for a in payload["anos_finais"] if a["ano_letivo"] == 2024)
        self.assertIsNone(ano["carga_horaria_total"])
        self.assertIsNone(ano["componentes"][0]["carga_horaria_anual"])
        traj = next(t for t in payload["trajetoria"] if t["ano_letivo"] == 2024)
        self.assertIsNone(traj["carga_horaria_total"])

    def test_11_pdf_combina_legado_com_educamais_2026(self):
        rede = self._rede(nome_turma="9º A", ano_letivo=2026, nome_aluno="Carla Trajetoria")
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2024, 7, [
            {"nome": "Ciências", "nota_conceito": "7,0", "categoria": "BASE_COMUM"},
            {"nome": "Matemática", "nota_conceito": "7,5", "categoria": "BASE_COMUM"},
        ]).status_code, 200)
        self.assertEqual(self._legado(rede["aluno_id"], 2025, 8, [
            {"nome": "Ciências", "nota_conceito": "8,0", "categoria": "BASE_COMUM"},
            {"nome": "Matemática", "nota_conceito": "8,2", "categoria": "BASE_COMUM"},
        ]).status_code, 200)
        payload = self._payload(rede["aluno_id"])
        anos = [t["ano_letivo"] for t in payload["trajetoria"]]
        self.assertEqual(anos, [2024, 2025, 2026])
        linhas = self._linhas(payload)
        nomes_2024 = [l["componente"] for l in linhas if l["ano_letivo"] == 2024]
        nomes_2026 = [l["componente"] for l in linhas if l["ano_letivo"] == 2026]
        self.assertTrue(any("Ciências" in n for n in nomes_2024))
        self.assertFalse(any("Biologia" in n for n in nomes_2024))
        self.assertTrue(any("Biologia" in n for n in nomes_2026))
        self.assertTrue(any("Química" in n for n in nomes_2026))
        self.assertTrue(any("Física" in n for n in nomes_2026))
        bio = next(l for l in linhas if l["componente"] == "Biologia")
        self.assertEqual(bio["ano_letivo"], 2026)
        self.assertIsNone(bio["nota_conceito"])
        self.assertNotEqual(bio["nota_conceito"], 0)
        ciencias_2026 = [l for l in linhas if l["ano_letivo"] == 2026 and "Ciências" in (l["componente"] or "")]
        self.assertEqual(ciencias_2026, [])
        self._login(rede["inep"])
        data = self._assert_pdf(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ), cpf=rede["cpf_aluno"])
        self.assertFalse(self._pdf_tem_secao_transferencia(data))

    def test_12_componentes_distintos_linhas_proprias_ausencia_traco(self):
        rede = self._rede(nome_turma="9º B")
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2024, 8, [
            {"nome": "Ciências", "nota_conceito": "7,0"},
        ]).status_code, 200)
        payload = self._payload(rede["aluno_id"])
        linhas = self._linhas(payload)
        nomes_2024 = [l["componente"] for l in linhas if l["ano_letivo"] == 2024]
        self.assertEqual(nomes_2024, ["Ciências"])
        self.assertNotIn("Biologia", nomes_2024)
        self.assertNotIn("Arte", nomes_2024)
        for linha in linhas:
            self.assertNotEqual(linha.get("nota_conceito"), 0)
            self.assertNotEqual(linha.get("nota_conceito"), 0.0)
            self.assertNotEqual(linha.get("nota_conceito"), "0")

    def test_13_nono_2026_biologia_quimica_fisica(self):
        rede = self._rede(nome_turma="9º C")
        payload = self._payload(rede["aluno_id"])
        nomes = [c["nome"] for c in payload["anos_finais"][0]["componentes"]]
        self.assertIn("Biologia", nomes)
        self.assertIn("Química", nomes)
        self.assertIn("Física", nomes)
        self.assertNotIn("Ciências", nomes)

    def test_14_historico_normal_sem_transferencia_e_transferencia_com(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2024, 8).status_code, 200)
        normal = self._payload(rede["aluno_id"])
        self.assertIsNone(normal["transferencia"])
        self.assertEqual(normal["tipo"], "historico_escolar")
        transf = self._payload(rede["aluno_id"], para_transferencia=True)
        self.assertIsNotNone(transf["transferencia"])
        self.assertEqual(transf["tipo"], "historico_transferencia")
        data_n = self._assert_pdf(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ))
        self.assertFalse(self._pdf_tem_secao_transferencia(data_n))
        data_t = self._assert_pdf(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        self.assertTrue(self._pdf_tem_secao_transferencia(data_t))

    def test_15_auditlog_sem_pii_academica(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2024, 8, [
            {"nome": "Matemática", "nota_conceito": "8,5"},
        ]).status_code, 200)
        with self.app.app_context():
            from src.models import AuditLog
            log = (
                AuditLog.query.filter_by(acao="TRAJETORIA_LEGADA_CRIADA")
                .order_by(AuditLog.id.desc())
                .first()
            )
            self.assertIsNotNone(log)
            blob = json.dumps({"a": log.dados_anteriores, "n": log.dados_novos})
            self.assertIn("aluno_id", blob)
            self.assertIn("ano_letivo", blob)
            self.assertIn("serie", blob)
            self.assertIn("registro_legado_id", blob)
            self.assertNotIn(rede["cpf_aluno"], blob)
            self.assertNotIn("Maria Legado", blob)
            self.assertNotIn("8,5", blob)
            self.assertNotIn("Matemática", blob)
            self.assertNotIn("2012-05-20", blob)

    def test_16_ei_fora_da_grade(self):
        rede = self._rede(infantil=True, nome_turma="Infantil V", ano_letivo=2026)
        self._login(rede["inep"])
        resp = self.client.get(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self.assertEqual(resp.status_code, 400)
        erro = (_json(resp)[1] or "").lower()
        self.assertTrue(
            "infantil" in erro or "parecer" in erro or "fundamental" in erro,
            erro,
        )

    def test_17_ano_automatico_nao_aceita_legado(self):
        rede = self._rede()
        self._login(rede["inep"])
        resp = self._legado(rede["aluno_id"], 2026, 9)
        self.assertEqual(resp.status_code, 400)
        erro = (_json(resp)[1] or "").lower()
        self.assertTrue("2026" in erro or "automatic" in erro or "educa" in erro)

    def test_18_pdf_application_filename_sem_cpf(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2023, 6).status_code, 200)
        resp = self.client.get(f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf")
        self._assert_pdf(resp, cpf=rede["cpf_aluno"])
        disp = resp.headers.get("Content-Disposition") or ""
        self.assertIn("historico-escolar", disp)

    def test_19_artefatos_visuais(self):
        so_legado = self._rede(nome_turma="9º D", nome_aluno="Ana Somente Legado")
        self._login(so_legado["inep"])
        self.assertEqual(self._legado(
            so_legado["aluno_id"], 2023, 6, [
                {"nome": "Língua Portuguesa", "nota_conceito": "7,0", "carga_horaria": 200, "situacao": "Aprovado"},
                {"nome": "Matemática", "nota_conceito": "7,5", "carga_horaria": 200, "situacao": "Aprovado"},
                {"nome": "Ciências", "nota_conceito": "MB", "carga_horaria": 120, "situacao": "Aprovado"},
            ],
            estabelecimento="Escola Municipal A", municipio="Bom Jesus", uf="PI",
        ).status_code, 200)
        self.assertEqual(self._legado(
            so_legado["aluno_id"], 2024, 7, [
                {"nome": "Língua Portuguesa", "nota_conceito": "8,0", "carga_horaria": 200, "situacao": "Aprovado"},
                {"nome": "Matemática", "nota_conceito": "7,5", "carga_horaria": 200, "situacao": "Aprovado"},
                {"nome": "História"},
            ],
            estabelecimento="Escola Municipal B", municipio="Cristino Castro", uf="PI",
            carga_horaria_total=None,
        ).status_code, 200)
        self.assertEqual(self._legado(
            so_legado["aluno_id"], 2025, 8, [
                {"nome": "Língua Portuguesa", "nota_conceito": "8,0", "carga_horaria": 200, "situacao": "Aprovado"},
                {"nome": "Matemática", "nota_conceito": "8,0", "carga_horaria": 200, "situacao": "Aprovado"},
            ],
            estabelecimento="Escola Municipal B", municipio="Cristino Castro", uf="PI",
        ).status_code, 200)
        a = self._assert_pdf(self.client.get(
            f"/api/alunos/{so_legado['aluno_id']}/documentos/historico.pdf"
        ))
        self.assertFalse(self._pdf_tem_secao_transferencia(a))
        compacto_a = self._compacto_pdf(a)
        self.assertIn("COMPONENTES CURRICULARES CURSADOS/CURSANDO", compacto_a)
        self.assertNotIn("GRADE CONSOLIDADA", compacto_a)
        self.assertNotIn("TRAJETORIA ESCOLAR", compacto_a)
        cab_a = compacto_a.replace(" ", "")
        self.assertIn("ANO/PERIODO", cab_a)
        self.assertIn("SERIE/ANO", cab_a)
        self.assertIn("CIDADE/UF", cab_a)
        self.assertIn("COMPONENTECURRICULAR", cab_a)
        self.assertNotIn("FALTAS", compacto_a)
        self.assertNotIn("FREQ.", compacto_a)
        self.assertNotIn("FREQ %", compacto_a)
        payload_so = self._payload(so_legado["aluno_id"])
        nomes_2023 = [l["componente"] for l in self._linhas_ano(payload_so, 2023)]
        nomes_2024 = [l["componente"] for l in self._linhas_ano(payload_so, 2024)]
        self.assertEqual(nomes_2023, ["Língua Portuguesa", "Matemática", "Ciências"])
        self.assertEqual(nomes_2024, ["Língua Portuguesa", "Matemática", "História"])
        self.assertNotIn("Geografia", nomes_2023)
        hist_2024 = next(l for l in self._linhas_ano(payload_so, 2024) if l["componente"] == "História")
        self.assertIsNone(hist_2024["carga_horaria"])
        self.assertIsNone(hist_2024["nota_conceito"])
        self.assertIsNone(hist_2024["situacao"])
        (ARTEFATOS / "historico_somente_legado.pdf").write_bytes(a)

        combo = self._rede(
            nome_turma="9º E", nome_aluno="Bruno Nono Trajetoria",
            nome_escola="Escola Municipal C",
        )
        self._login(combo["inep"])
        self.assertEqual(self._legado(
            combo["aluno_id"], 2023, 6, [
                {"nome": "Língua Portuguesa", "nota_conceito": "7,0", "carga_horaria": 200, "situacao": "Aprovado"},
                {"nome": "Matemática", "nota_conceito": "7,5", "carga_horaria": 200, "situacao": "Aprovado"},
                {"nome": "Ciências", "nota_conceito": "MB", "carga_horaria": 120, "situacao": "Aprovado"},
            ],
            estabelecimento="Escola Municipal A", municipio="Bom Jesus", uf="PI",
        ).status_code, 200)
        self.assertEqual(self._legado(
            combo["aluno_id"], 2024, 7, [
                {"nome": "Língua Portuguesa", "nota_conceito": "8,0", "carga_horaria": 200, "situacao": "Aprovado"},
                {"nome": "Matemática", "nota_conceito": "7,5", "carga_horaria": 200, "situacao": "Aprovado"},
                {"nome": "História"},
            ],
            estabelecimento="Escola Municipal B", municipio="Cristino Castro", uf="PI",
            carga_horaria_total=None,
        ).status_code, 200)
        self.assertEqual(self._legado(
            combo["aluno_id"], 2025, 8, [
                {"nome": "Língua Portuguesa", "nota_conceito": "8,2", "carga_horaria": 200, "situacao": "Aprovado"},
                {"nome": "Matemática", "nota_conceito": "8,0", "carga_horaria": 200, "situacao": "Aprovado"},
            ],
            estabelecimento="Escola Municipal B", municipio="Cristino Castro", uf="PI",
        ).status_code, 200)
        payload_combo = self._payload(combo["aluno_id"])
        por_ano_mat = {
            l["ano_letivo"]: l for l in self._linhas(payload_combo) if l["componente"] == "Matemática"
        }
        self.assertEqual(por_ano_mat[2023]["escola"], "Escola Municipal A")
        self.assertEqual(por_ano_mat[2023]["municipio_uf"], "Bom Jesus/PI")
        self.assertEqual(por_ano_mat[2023]["carga_horaria"], 200)
        self.assertEqual(por_ano_mat[2023]["nota_conceito"], "7,5")
        self.assertEqual(por_ano_mat[2023]["situacao"], "Aprovado")
        self.assertEqual(por_ano_mat[2024]["escola"], "Escola Municipal B")
        self.assertEqual(por_ano_mat[2024]["municipio_uf"], "Cristino Castro/PI")
        self.assertEqual(por_ano_mat[2025]["escola"], "Escola Municipal B")
        self.assertEqual(por_ano_mat[2026]["escola"], "Escola Municipal C")
        self.assertEqual(por_ano_mat[2026]["municipio_uf"], "Palmeira do Piauí/PI")
        self.assertEqual(por_ano_mat[2026]["situacao"], "CURSANDO")
        self.assertIsNone(por_ano_mat[2026]["nota_conceito"])
        ciencias_2023 = next(l for l in self._linhas_ano(payload_combo, 2023) if l["componente"] == "Ciências")
        self.assertEqual(ciencias_2023["carga_horaria"], 120)
        self.assertEqual(ciencias_2023["nota_conceito"], "MB")
        self.assertEqual(ciencias_2023["situacao"], "Aprovado")
        hist_combo = next(l for l in self._linhas_ano(payload_combo, 2024) if l["componente"] == "História")
        self.assertIsNone(hist_combo["carga_horaria"])
        self.assertIsNone(hist_combo["nota_conceito"])
        self.assertIsNone(hist_combo["situacao"])
        nomes_2023_combo = [l["componente"] for l in self._linhas_ano(payload_combo, 2023)]
        nomes_2024_combo = [l["componente"] for l in self._linhas_ano(payload_combo, 2024)]
        self.assertEqual(nomes_2023_combo, ["Língua Portuguesa", "Matemática", "Ciências"])
        self.assertEqual(nomes_2024_combo, ["Língua Portuguesa", "Matemática", "História"])
        self.assertFalse(any(l["ano_letivo"] == 2023 and l["componente"] == "Geografia" for l in self._linhas(payload_combo)))
        nomes_2026 = [l["componente"] for l in self._linhas_ano(payload_combo, 2026)]
        self.assertIn("Biologia", nomes_2026)
        self.assertIn("Química", nomes_2026)
        self.assertIn("Física", nomes_2026)
        for nome in ("Biologia", "Química", "Física"):
            linha = next(l for l in self._linhas_ano(payload_combo, 2026) if l["componente"] == nome)
            self.assertEqual(linha["situacao"], "CURSANDO")
            self.assertNotEqual(linha["nota_conceito"], 0)
        b = self._assert_pdf(self.client.get(
            f"/api/alunos/{combo['aluno_id']}/documentos/historico.pdf"
        ))
        compacto_b = self._compacto_pdf(b)
        self.assertIn("ESCOLA MUNICIPAL A", compacto_b)
        self.assertIn("ESCOLA MUNICIPAL B", compacto_b)
        self.assertIn("ESCOLA MUNICIPAL C", compacto_b)
        self.assertIn("BOM JESUS/PI", compacto_b)
        self.assertIn("CRISTINO CASTRO/PI", compacto_b)
        self.assertIn("PALMEIRA DO PIAUI/PI", compacto_b.replace("Í", "I"))
        self.assertIn("BIOLOGIA", compacto_b)
        self.assertIn("QUIMICA", compacto_b)
        self.assertIn("FISICA", compacto_b)
        self.assertIn("7,5", compacto_b)
        self.assertIn("200", compacto_b)
        self.assertIn("120", compacto_b)
        self.assertIn("MB", compacto_b)
        self.assertIn("APROVADO", compacto_b)
        self.assertIn("CURSANDO", compacto_b)
        self.assertNotIn("GRADE CONSOLIDADA", compacto_b)
        self.assertNotIn("TRAJETORIA ESCOLAR", compacto_b)
        self.assertNotIn("FALTAS", compacto_b)
        self.assertGreaterEqual(len(self._linhas(payload_combo)), 25)
        (ARTEFATOS / "historico_legado_e_nono_2026.pdf").write_bytes(b)

        iniciais = self._rede(nome_turma="5º A", ano_letivo=2026, nome_aluno="Clara Iniciais Legado")
        self._login(iniciais["inep"])
        self.assertEqual(self._legado(iniciais["aluno_id"], 2024, 3, [
            {"nome": "Matemática", "nota_conceito": "7,0", "carga_horaria": 200, "situacao": "Aprovado"},
            {"nome": "Língua Portuguesa", "nota_conceito": "7,5", "carga_horaria": 200, "situacao": "Aprovado"},
            {"nome": "Ciências", "nota_conceito": "MB", "carga_horaria": 120, "situacao": "Aprovado"},
        ], estabelecimento="Escola Municipal A", municipio="Bom Jesus", uf="PI").status_code, 200)
        self.assertEqual(self._legado(iniciais["aluno_id"], 2025, 4, [
            {"nome": "Matemática", "nota_conceito": "7,5", "carga_horaria": 200, "situacao": "Aprovado"},
            {"nome": "Língua Portuguesa", "nota_conceito": "8,0", "carga_horaria": 200, "situacao": "Aprovado"},
        ], estabelecimento="Escola Municipal B", municipio="Cristino Castro", uf="PI").status_code, 200)
        c = self._assert_pdf(self.client.get(
            f"/api/alunos/{iniciais['aluno_id']}/documentos/historico.pdf"
        ))
        (ARTEFATOS / "historico_iniciais_legado_e_2026.pdf").write_bytes(c)

        transf = self._rede(nome_turma="6º A", nome_aluno="Diego Transferencia Legado")
        self._login(transf["inep"])
        self.assertEqual(self._legado(transf["aluno_id"], 2024, 4, [
            {"nome": "Matemática", "nota_conceito": "6,5", "situacao": "Aprovado"},
        ]).status_code, 200)
        self.assertEqual(self._legado(transf["aluno_id"], 2025, 5, [
            {"nome": "Matemática", "nota_conceito": "7,0", "situacao": "Aprovado"},
        ]).status_code, 200)
        d_normal = self._assert_pdf(self.client.get(
            f"/api/alunos/{transf['aluno_id']}/documentos/historico.pdf"
        ))
        self.assertFalse(self._pdf_tem_secao_transferencia(d_normal))
        self.assertNotIn("FALTAS", self._compacto_pdf(d_normal))
        d = self._assert_pdf(self.client.get(
            f"/api/alunos/{transf['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        self.assertTrue(self._pdf_tem_secao_transferencia(d))
        (ARTEFATOS / "historico_transferencia_com_legado.pdf").write_bytes(d)
        info = self._info_pdf(b)
        self.assertGreaterEqual(len(info), 1)
        self.assertTrue(all(p["orientacao"] == "paisagem" for p in info))

    def test_20_configuracao_ano_inicio_centralizada(self):
        from src.trajetoria_legada import ano_inicio_historico_automatico, periodo_e_automatico
        with self.app.app_context():
            self.assertEqual(ano_inicio_historico_automatico(), 2026)
            self.assertFalse(periodo_e_automatico(2025))
            self.assertTrue(periodo_e_automatico(2026))
            self.assertTrue(periodo_e_automatico(2027))

    def test_21_legado_realista_2023_a_2026(self):
        rede = self._rede(nome_turma="9º R", nome_aluno="Helena Trajetoria Realista")
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2023, 6, [
            {"nome": "Língua Portuguesa", "nota_conceito": "7,5", "carga_horaria": 320, "categoria": "BASE_COMUM"},
            {"nome": "Matemática", "nota_conceito": "7,0", "categoria": "BASE_COMUM"},
            {"nome": "Ensino Religioso", "nota_conceito": "MB"},
            {"nome": "Ciências", "nota_conceito": "7,0", "carga_horaria": 160, "categoria": "BASE_COMUM"},
        ], carga_horaria_total=1000).status_code, 200)
        self.assertEqual(self._legado(rede["aluno_id"], 2024, 7, [
            {"nome": "Língua Portuguesa", "nota_conceito": "8,0", "carga_horaria": 320, "categoria": "BASE_COMUM"},
            {"nome": "Matemática", "nota_conceito": "7,5", "categoria": "BASE_COMUM"},
            {"nome": "Ciências", "nota_conceito": "8,0", "carga_horaria": 160, "categoria": "BASE_COMUM"},
        ], carga_horaria_total=1000).status_code, 200)
        self.assertEqual(self._legado(rede["aluno_id"], 2025, 8, [
            {"nome": "Língua Portuguesa", "nota_conceito": "8,2", "carga_horaria": 320, "categoria": "BASE_COMUM"},
            {"nome": "Matemática", "nota_conceito": "8,0", "categoria": "BASE_COMUM"},
            {"nome": "Ciências", "nota_conceito": "8,5", "carga_horaria": 160, "categoria": "BASE_COMUM"},
        ], carga_horaria_total=None).status_code, 200)

        payload = self._payload(rede["aluno_id"])
        anos = [t["ano_letivo"] for t in payload["trajetoria"]]
        self.assertEqual(anos, [2023, 2024, 2025, 2026])
        chs = {t["ano_letivo"]: t["carga_horaria_total"] for t in payload["trajetoria"]}
        self.assertEqual(chs[2023], 1000)
        self.assertEqual(chs[2024], 1000)
        self.assertIsNone(chs[2025])
        origens = {t["ano_letivo"]: t.get("origem") for t in payload["trajetoria"]}
        self.assertEqual(origens[2023], "LEGADO")
        self.assertEqual(origens[2024], "LEGADO")
        self.assertEqual(origens[2025], "LEGADO")
        self.assertEqual(origens[2026], "EDUCA+")

        ano_2023 = next(a for a in payload["anos_finais"] if a["ano_letivo"] == 2023)
        self.assertFalse(ano_2023.get("matriz_aplicada"))
        nomes_2023 = [c["nome"] for c in ano_2023["componentes"]]
        self.assertEqual(len(nomes_2023), 4)
        self.assertNotIn("Biologia", nomes_2023)
        self.assertNotIn("Química", nomes_2023)
        self.assertNotIn("Física", nomes_2023)
        mat_2023 = next(c for c in ano_2023["componentes"] if c["nome"] == "Matemática")
        self.assertEqual(mat_2023["nota_conceito"], "7,0")
        self.assertIsNone(mat_2023.get("carga_horaria_anual"))
        rel = next(c for c in ano_2023["componentes"] if "Religioso" in c["nome"])
        self.assertEqual(rel["nota_conceito"], "MB")

        linhas = self._linhas(payload)
        ciencias = [l for l in linhas if "Ciências" in (l["componente"] or "")]
        self.assertEqual(ciencias[0]["nota_conceito"], "7,0")
        self.assertEqual(ciencias[0]["ano_letivo"], 2023)
        self.assertFalse(any(l["ano_letivo"] == 2026 for l in ciencias))
        bio = [l for l in linhas if l["componente"] == "Biologia"]
        self.assertTrue(bio)
        self.assertTrue(all(l["ano_letivo"] == 2026 for l in bio))
        relig = next(l for l in linhas if "Religioso" in (l["componente"] or ""))
        self.assertEqual(relig["nota_conceito"], "MB")
        self.assertEqual(relig["ano_letivo"], 2023)

        self._login(rede["inep"])
        normal = self._assert_pdf(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ), cpf=rede["cpf_aluno"])
        self.assertFalse(self._pdf_tem_secao_transferencia(normal))
        texto = self._texto_pdf(normal)
        self.assertIn("MB", texto)
        self.assertNotIn("ANO 2024", texto.upper().replace(" ", ""))
        transf = self._assert_pdf(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico-transferencia.pdf"
        ))
        self.assertTrue(self._pdf_tem_secao_transferencia(transf))
        (ARTEFATOS / "historico_realista_2023_2026.pdf").write_bytes(normal)
        (ARTEFATOS / "historico_realista_transferencia.pdf").write_bytes(transf)

    def test_22_situacao_por_componente_textual_e_nula(self):
        rede = self._rede()
        self._login(rede["inep"])
        resp = self._legado(rede["aluno_id"], 2024, 8, [
            {"nome": "Matemática", "nota_conceito": "7,0", "situacao": "Aprovado"},
            {"nome": "Ciências", "nota_conceito": "MB", "situacao": "APTO"},
            {"nome": "Arte", "nota_conceito": "B"},
        ])
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        data = _json(resp)[0]
        mapa = {c["nome"]: c for c in data["componentes"]}
        self.assertEqual(mapa["Matemática"]["situacao"], "Aprovado")
        self.assertEqual(mapa["Ciências"]["situacao"], "APTO")
        self.assertIsNone(mapa["Arte"]["situacao"])
        payload = self._payload(rede["aluno_id"])
        linhas = {l["componente"]: l for l in self._linhas_ano(payload, 2024)}
        self.assertEqual(linhas["Matemática"]["situacao"], "Aprovado")
        self.assertEqual(linhas["Ciências"]["situacao"], "APTO")
        self.assertIsNone(linhas["Arte"]["situacao"])
        self.assertEqual(linhas["Arte"]["nota_conceito"], "B")
        self.assertEqual(linhas["Matemática"]["escola"], "Escola Municipal de Origem")
        self.assertEqual(linhas["Matemática"]["municipio_uf"], "Bom Jesus/PI")
        self.assertEqual(linhas["Matemática"]["serie_rotulo"], "8º ano")

    def test_23_ausentes_viram_traco_no_pdf(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2024, 8, [
            {"nome": "História"},
        ], carga_horaria_total=None).status_code, 200)
        payload = self._payload(rede["aluno_id"])
        linha = self._linhas_ano(payload, 2024)[0]
        self.assertIsNone(linha["carga_horaria"])
        self.assertIsNone(linha["nota_conceito"])
        self.assertIsNone(linha["situacao"])
        data = self._assert_pdf(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ))
        texto = self._texto_pdf(data)
        self.assertIn("—", texto)
        self.assertIn("História", texto)

    def test_24_escolas_diferentes_nao_viram_escola_atual(self):
        rede = self._rede(nome_turma="9º Z", nome_aluno="Lia Escolas Distintas")
        self._login(rede["inep"])
        self.assertEqual(self._legado(
            rede["aluno_id"], 2023, 6,
            [{"nome": "Matemática", "nota_conceito": "7,0", "situacao": "Aprovado"}],
            estabelecimento="Escola Municipal A", municipio="Bom Jesus", uf="PI",
        ).status_code, 200)
        self.assertEqual(self._legado(
            rede["aluno_id"], 2024, 7,
            [{"nome": "Matemática", "nota_conceito": "7,5", "situacao": "Aprovado"}],
            estabelecimento="Escola Municipal B", municipio="Cristino Castro", uf="PI",
        ).status_code, 200)
        self.assertEqual(self._legado(
            rede["aluno_id"], 2025, 8,
            [{"nome": "Matemática", "nota_conceito": "8,0", "situacao": "Aprovado"}],
            estabelecimento="Escola Municipal B", municipio="Cristino Castro", uf="PI",
        ).status_code, 200)
        payload = self._payload(rede["aluno_id"])
        por_ano = {l["ano_letivo"]: l for l in self._linhas(payload) if l["componente"] == "Matemática"}
        self.assertEqual(por_ano[2023]["escola"], "Escola Municipal A")
        self.assertEqual(por_ano[2023]["municipio_uf"], "Bom Jesus/PI")
        self.assertEqual(por_ano[2024]["escola"], "Escola Municipal B")
        self.assertEqual(por_ano[2024]["municipio_uf"], "Cristino Castro/PI")
        self.assertEqual(por_ano[2025]["escola"], "Escola Municipal B")
        atual = payload["escola"]["nome"]
        self.assertNotEqual(por_ano[2023]["escola"], atual)
        self.assertNotEqual(por_ano[2024]["municipio_uf"], "Palmeira do Piauí/PI")
        data = self._assert_pdf(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ))
        compacto = self._compacto_pdf(data)
        self.assertIn("ESCOLA MUNICIPAL A", compacto)
        self.assertIn("ESCOLA MUNICIPAL B", compacto)
        self.assertIn("BOM JESUS/PI", compacto)
        self.assertIn("CRISTINO CASTRO/PI", compacto)

    def test_25_ano_aberto_usa_cursando_e_nao_inventa_zero(self):
        rede = self._rede(nome_turma="9º Cursando")
        payload = self._payload(rede["aluno_id"])
        linhas_2026 = self._linhas_ano(payload, 2026)
        self.assertTrue(linhas_2026)
        for linha in linhas_2026:
            self.assertEqual(linha["situacao"], "CURSANDO")
            self.assertNotEqual(linha["nota_conceito"], 0)
            self.assertNotEqual(linha["nota_conceito"], "0")
        mat = next(l for l in linhas_2026 if l["componente"] == "Matemática")
        self.assertIsNone(mat["nota_conceito"])
        bio = next(l for l in linhas_2026 if l["componente"] == "Biologia")
        self.assertEqual(bio["situacao"], "CURSANDO")

    def test_26_pdf_modelo_definitivo_sem_frequencia(self):
        rede = self._rede()
        self._login(rede["inep"])
        self.assertEqual(self._legado(rede["aluno_id"], 2024, 8, [
            {"nome": "Matemática", "nota_conceito": "8,0", "carga_horaria": 200, "situacao": "Aprovado"},
        ]).status_code, 200)
        payload = self._payload(rede["aluno_id"])
        self.assertEqual(payload["titulo_tabela"], "COMPONENTES CURRICULARES CURSADOS/CURSANDO")
        self.assertNotIn("grades", payload)
        linha = self._linhas_ano(payload, 2024)[0]
        self.assertNotIn("faltas", linha)
        self.assertNotIn("frequencia", linha)
        data = self._assert_pdf(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf"
        ))
        compacto = self._compacto_pdf(data)
        self.assertIn("COMPONENTES CURRICULARES CURSADOS/CURSANDO", compacto)
        self.assertNotIn("GRADE CONSOLIDADA", compacto)
        self.assertNotIn("TRAJETORIA ESCOLAR", compacto)
        self.assertNotIn("FALTAS", compacto)
        self.assertNotIn("FREQUENCIA", compacto.replace("Ç", "C"))
        self.assertIn("MATEMATICA", compacto.replace("Á", "A"))
        self.assertIn("APROVADO", compacto)

    def _foto_academica(self):
        with self.app.app_context():
            from src import db
            from src.models import Alocacao, Disciplina, MatrizComponente, MatrizCurricular
            return {
                "matrizes": [
                    (m.id, m.nome, m.ano_inicio, m.ano_fim, m.ativa, m.carga_horaria_anual)
                    for m in MatrizCurricular.query.order_by(MatrizCurricular.id)
                ],
                "componentes": [
                    (c.id, c.matriz_id, c.nome, c.ano_serie, c.carga_horaria_anual, c.aulas_semanais)
                    for c in MatrizComponente.query.order_by(MatrizComponente.id)
                ],
                "disciplinas": [
                    (d.id, d.nome) for d in Disciplina.query.order_by(Disciplina.id)
                ],
                "alocacoes": db.session.query(Alocacao).count(),
            }

    def test_27_catalogo_de_componentes_sem_duplicidade_e_ordenado(self):
        with self.app.app_context():
            from src import db
            from src.matriz_curricular import normalizar_nome_componente
            from src.models import Disciplina, MatrizComponente, MatrizCurricular
            from src.trajetoria_legada import (
                CARGAS_HORARIAS_COMPONENTE_SUGERIDAS,
                cargas_componente_fora_da_sugestao,
                nomes_componentes_cadastrados,
            )
            nomes = nomes_componentes_cadastrados()
            chaves = [normalizar_nome_componente(n) for n in nomes]
            self.assertEqual(len(chaves), len(set(chaves)))
            self.assertEqual(nomes, sorted(nomes, key=normalizar_nome_componente))
            self.assertEqual(chaves.count("matematica"), 1)
            self.assertIn("Matemática", nomes)
            self.assertNotIn("Estudos Sociais", nomes)
            self.assertEqual(
                list(CARGAS_HORARIAS_COMPONENTE_SUGERIDAS),
                list(range(40, 441, 20)) + [450],
            )
            horas = {
                int(ch)
                for (ch,) in db.session.query(MatrizComponente.carga_horaria_anual).distinct()
                if ch is not None and 40 <= int(ch) <= 450
            }
            self.assertTrue(horas)
            self.assertTrue(horas <= set(CARGAS_HORARIAS_COMPONENTE_SUGERIDAS))
            self.assertEqual(cargas_componente_fora_da_sugestao(), [])

            matriz = MatrizCurricular.query.order_by(MatrizCurricular.id).first()
            extra = MatrizComponente(
                matriz_id=matriz.id,
                nome="matemática",
                categoria="BASE_COMUM",
                ano_serie=1,
                ordem=9000,
            )
            disc = Disciplina(nome="MATEMÁTICA")
            db.session.add(extra)
            db.session.add(disc)
            db.session.flush()
            try:
                nomes2 = nomes_componentes_cadastrados()
                chaves2 = [normalizar_nome_componente(n) for n in nomes2]
                self.assertEqual(chaves2.count("matematica"), 1)
                self.assertIn("Matemática", nomes2)
                self.assertNotIn("matemática", nomes2)
                self.assertNotIn("MATEMÁTICA", nomes2)
                self.assertEqual(nomes2, sorted(nomes2, key=normalizar_nome_componente))
            finally:
                db.session.delete(extra)
                db.session.delete(disc)
                db.session.commit()

    def test_28_componente_manual_ch_e_pdf_sem_alterar_matriz(self):
        rede = self._rede(nome_turma="9º Cat")
        antes = self._foto_academica()
        self._login(rede["inep"])
        lista = _json(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/trajetoria-legada",
        ))[0]
        self.assertIn("Matemática", lista["componentes_cadastrados"])
        self.assertNotIn("Estudos Sociais", lista["componentes_cadastrados"])
        self.assertEqual(lista["cargas_horarias_sugeridas"][0], 40)
        self.assertIn(200, lista["cargas_horarias_sugeridas"])
        self.assertEqual(lista["cargas_horarias_sugeridas"][-1], 450)
        self.assertEqual(lista["cargas_horarias_fora_da_sugestao"], [])

        componentes = [
            {"nome": "Matemática", "nota_conceito": "8,5", "carga_horaria": 40, "situacao": "Aprovado"},
            {"nome": "Língua Portuguesa", "nota_conceito": "8,0", "carga_horaria": 200, "situacao": "Aprovado"},
            {"nome": "Arte", "nota_conceito": "MB", "carga_horaria": 450, "situacao": "Aprovado"},
            {"nome": "História", "nota_conceito": "7,0", "carga_horaria": None, "situacao": "Aprovado"},
            {"nome": "Estudos Sociais", "nota_conceito": "MB", "carga_horaria": 75, "situacao": "Aprovado"},
        ]
        resp = self._legado(
            rede["aluno_id"], 2024, 8, componentes, carga_horaria_total=1000,
        )
        self.assertEqual(resp.status_code, 200, _json(resp)[1])
        criado = _json(resp)[0]
        por_nome = {c["nome"]: c for c in criado["componentes"]}
        self.assertEqual(por_nome["Matemática"]["carga_horaria"], 40)
        self.assertEqual(por_nome["Língua Portuguesa"]["carga_horaria"], 200)
        self.assertEqual(por_nome["Arte"]["carga_horaria"], 450)
        self.assertIsNone(por_nome["História"]["carga_horaria"])
        self.assertEqual(por_nome["Estudos Sociais"]["carga_horaria"], 75)
        self.assertEqual(criado["carga_horaria_total"], 1000)

        self.assertEqual(self._foto_academica(), antes)
        lista = _json(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/trajetoria-legada",
        ))[0]
        self.assertNotIn("Estudos Sociais", lista["componentes_cadastrados"])
        self.assertEqual(lista["cargas_horarias_fora_da_sugestao"], [75])
        self.assertIn("Matemática", lista["componentes_cadastrados"])

        corpo = {
            "ano_letivo": criado["ano_letivo"],
            "serie": criado["serie"],
            "estabelecimento": criado["estabelecimento"],
            "municipio": criado["municipio"],
            "uf": criado["uf"],
            "resultado": criado["resultado"],
            "carga_horaria_total": criado["carga_horaria_total"],
            "observacao": criado["observacao"],
            "componentes": [
                {
                    "nome": c["nome"],
                    "carga_horaria": c["carga_horaria"],
                    "nota_conceito": c["nota_conceito"],
                    "situacao": c["situacao"],
                    "categoria": c["categoria"],
                }
                for c in criado["componentes"]
            ],
        }
        editado = _json(self.client.put(
            f"/api/trajetoria-legada/{criado['id']}", json=corpo,
        ))[0]
        por_nome = {c["nome"]: c for c in editado["componentes"]}
        self.assertEqual(por_nome["Estudos Sociais"]["nome"], "Estudos Sociais")
        self.assertEqual(por_nome["Estudos Sociais"]["carga_horaria"], 75)
        self.assertEqual(por_nome["Estudos Sociais"]["nota_conceito"], "MB")
        self.assertEqual(editado["carga_horaria_total"], 1000)
        self.assertEqual(self._foto_academica(), antes)

        payload = self._payload(rede["aluno_id"])
        ano = next(a for a in payload["anos_finais"] if a["ano_letivo"] == 2024)
        self.assertEqual(ano["fonte"], "legado")
        self.assertFalse(ano["matriz_aplicada"])
        self.assertEqual(ano["carga_horaria_total"], 1000)
        linhas = {l["componente"]: l for l in self._linhas_ano(payload, 2024)}
        self.assertEqual(set(linhas), {
            "Matemática", "Língua Portuguesa", "Arte", "História", "Estudos Sociais",
        })
        self.assertEqual(linhas["Matemática"]["carga_horaria"], 40)
        self.assertEqual(linhas["Língua Portuguesa"]["carga_horaria"], 200)
        self.assertEqual(linhas["Arte"]["carga_horaria"], 450)
        self.assertIsNone(linhas["História"]["carga_horaria"])
        self.assertEqual(linhas["Estudos Sociais"]["carga_horaria"], 75)
        self.assertEqual(linhas["Estudos Sociais"]["nota_conceito"], "MB")
        self.assertEqual(linhas["Estudos Sociais"]["situacao"], "Aprovado")
        nomes_2026 = [l["componente"] for l in self._linhas_ano(payload, 2026)]
        self.assertIn("Biologia", nomes_2026)
        self.assertNotIn("Estudos Sociais", nomes_2026)

        data = self._assert_pdf(self.client.get(
            f"/api/alunos/{rede['aluno_id']}/documentos/historico.pdf",
        ), cpf=rede["cpf_aluno"])
        texto = self._texto_pdf(data)
        compacto = self._compacto_pdf(data).replace("Á", "A")
        self.assertIn("ESTUDOS SOCIAIS", compacto)
        self.assertIn("75", texto)
        self.assertIn("450", texto)
        self.assertIn("MATEMATICA", compacto)
        self.assertIn("HISTORIA", compacto)
        self.assertIn("—", texto)
        self.assertIn("MB", texto)
        self.assertEqual(self._foto_academica(), antes)


class TrajetoriaLegadaMigrationTest(unittest.TestCase):
    def setUp(self):
        self._env = os.environ.copy()
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        self._db_path = tmp.name
        from src.config import Config
        from tests.support import apontar_banco_teste, preparar_ambiente_teste
        self._old_uri = Config.SQLALCHEMY_DATABASE_URI
        preparar_ambiente_teste(demo=True)
        apontar_banco_teste("sqlite:///" + Path(self._db_path).resolve().as_posix())

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        from src.config import Config
        from tests.support import preparar_ambiente_teste
        preparar_ambiente_teste(demo=True)
        Config.SQLALCHEMY_DATABASE_URI = self._old_uri
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def test_upgrade_downgrade_upgrade(self):
        from flask_migrate import downgrade, upgrade
        from sqlalchemy import inspect
        from src import create_app, db
        from src.migracoes import (
            REVISION_HEAD, REVISION_MATRIZ, REVISION_TRAJETORIA_LEGADA, revisao_atual,
        )

        app = create_app(preparar_schema=False)
        with app.app_context():
            upgrade()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            nomes = inspect(db.engine).get_table_names()
            self.assertIn("trajetoria_escolar_legada", nomes)
            self.assertIn("trajetoria_componente_legado", nomes)
            downgrade(revision=REVISION_MATRIZ)
            inspect(db.engine).clear_cache()
            self.assertEqual(revisao_atual(), REVISION_MATRIZ)
            nomes = inspect(db.engine).get_table_names()
            self.assertNotIn("trajetoria_escolar_legada", nomes)
            self.assertNotIn("trajetoria_componente_legado", nomes)
            upgrade()
            inspect(db.engine).clear_cache()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            nomes = inspect(db.engine).get_table_names()
            self.assertIn("trajetoria_escolar_legada", nomes)
            self.assertIn("matriz_curricular", nomes)
            colunas = {c["name"] for c in inspect(db.engine).get_columns("trajetoria_componente_legado")}
            self.assertIn("situacao", colunas)
            downgrade(revision=REVISION_TRAJETORIA_LEGADA)
            inspect(db.engine).clear_cache()
            self.assertEqual(revisao_atual(), REVISION_TRAJETORIA_LEGADA)
            colunas = {c["name"] for c in inspect(db.engine).get_columns("trajetoria_componente_legado")}
            self.assertNotIn("situacao", colunas)
            upgrade()
            inspect(db.engine).clear_cache()
            self.assertEqual(revisao_atual(), REVISION_HEAD)
            colunas = {c["name"] for c in inspect(db.engine).get_columns("trajetoria_componente_legado")}
            self.assertIn("situacao", colunas)
