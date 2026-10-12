"""Prepara a base FICTÍCIA da Demo 1.0 do Educa+ Palmeira.

NÃO é executado na inicialização do sistema.
NÃO altera o banco padrão (instance/educamais.db) sem flags explícitas.
NÃO roda em produção.

Procedimento (na raiz do projeto):

  python scripts/gerar_dados_demo.py --destino instance/educamais_demo.db --confirmar

  python -m flask educamais preparar-demo --confirmar --destino instance/educamais_demo.db

  PowerShell:
    $env:EDUCAMAIS_ENV = "development"
    $env:EDUCAMAIS_DATABASE_URI = "sqlite:///instance/educamais_demo.db"
    python app.py

Se o arquivo de destino já existir, acrescente --substituir.
Nunca use o banco padrão sem --permitir-banco-padrao.
Todos os nomes, INEP, CPF e contatos deste script são fictícios.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

SENHA_DEMO = "demo1234"
INEP_SOL = "88000001"
INEP_LUA = "88000002"
INEP_CAMINHOS = "88000003"
INEP_SERRA = "88000004"
INEP_CRECHE = "88000005"

NOME_SOL = "EMEF Demo Sol Nascente"
NOME_LUA = "EMEF Demo Lua Clara"
NOME_CAMINHOS = "Escola Demo Caminhos do Saber"
NOME_SERRA = "Escola Demo Serra Azul"
NOME_CRECHE = "Creche Demo Pequeno Aprendiz"

LOGIN_MARIA = "88011111111"
LOGIN_JOAO = "88022222222"
LOGIN_CARLOS = "88033333333"
LOGIN_HELENA = "88100000001"
LOGIN_COORD_SOL = "coord.sol.demo"
LOGIN_COORD_LUA = "coord.lua.demo"
LOGIN_COORD_CAMINHOS = "coord.caminhos.demo"
LOGIN_COORD_SERRA = "coord.serra.demo"
LOGIN_COORD_CRECHE = "coord.creche.demo"

ANO_DEMO = 2026
DATA_INICIO_ANO = date(2026, 2, 2)
DATA_FIM_ANO = date(2026, 12, 18)
DATA_FALTA_ATIVA = date(2026, 9, 1)
DATA_FALTA_CANCELADA = date(2026, 8, 11)
DATA_AULA_PENDENTE_MARIA = date(2026, 8, 31)
DATA_AULA_PENDENTE_JOAO = date(2026, 8, 18)
DATA_AULA_PENDENTE_HELENA = date(2026, 8, 24)
DATA_AULA_VALIDADA_TODOS = date(2026, 8, 10)
DATA_AULA_VALIDADA_FALTAS = date(2026, 8, 17)
DATA_AULA_VALIDADA_JOAO = date(2026, 8, 4)
DATA_AULA_INVALIDADA = date(2026, 8, 14)
DATA_AULA_CARLOS = date(2026, 8, 10)
DATA_REPOSICAO = date(2026, 9, 12)
DATA_FERIADO_INDEPENDENCIA = date(2026, 9, 7)
DATA_FERIADO_TIRADENTES = date(2026, 4, 21)

DISCIPLINAS_PADRAO = [
    "Matemática", "Português", "História", "Geografia", "Ciências", "Artes", "Ed. Física",
]
EIXO_EI = "Eu, o outro e o nós"

PRENOMES = [
    "Alice", "Bernardo", "Cecília", "Davi", "Elisa", "Felipe", "Giovana", "Heitor",
    "Isadora", "Jonas", "Larissa", "Murilo", "Natália", "Otávio", "Pietra", "Rafael",
    "Sofia", "Thiago", "Valentina", "Yago", "Amanda", "Bruno", "Camila", "Daniel",
    "Eduarda", "Fernando", "Gabriela", "Henrique", "Ingrid", "Júlia", "Kaique", "Lara",
    "Mateus", "Nicole", "Otto", "Paula", "Renan", "Sabrina", "Tainá", "Vitor",
]
SOBRENOMES = [
    "Prado", "Lima", "Nogueira", "Azevedo", "Barros", "Castro", "Duarte", "Farias",
    "Gomes", "Henriques", "Ibrahim", "Jardim", "Klein", "Lopes", "Mendes", "Nunes",
]


def banco_padrao():
    from src.config import Config
    return Path(Config.ROOT_DIR, "instance", "educamais.db").resolve()


def recusar_producao():
    """Recusa só se o ambiente estiver explicitamente em produção.

    Sem EDUCAMAIS_ENV o processo Flask assume produção (fail-closed). O script
    de arquivo demo, porém, define development em seguida — desde que ninguém
    tenha pedido produção de propósito.
    """
    v = (os.environ.get("EDUCAMAIS_ENV") or "").strip().lower()
    if v in ("production", "prod"):
        return (
            "Recusa: a preparação da demo não é permitida em produção. "
            "Os dados são fictícios e este comando só funciona fora de produção."
        )
    return None


def recusar_producao_app():
    from flask import current_app, has_app_context
    if has_app_context() and current_app.config.get("EDUCAMAIS_ENV") == "production":
        return (
            "Recusa: a preparação da demo não é permitida em produção. "
            "Os dados são fictícios e este comando só funciona fora de produção."
        )
    return recusar_producao()


def validar_destino(destino, confirmar, substituir, permitir_padrao):
    erro_prod = recusar_producao()
    if erro_prod:
        return None, erro_prod
    path = Path(destino).resolve()
    padrao = banco_padrao()
    if not confirmar:
        return None, "Recusa: informe --confirmar. Este comando nunca roda sozinho sobre um banco existente."
    if path == padrao and not permitir_padrao:
        return None, (
            "Recusa: o destino é o banco padrão (instance/educamais.db). "
            "Use outro arquivo (ex.: instance/educamais_demo.db) ou --permitir-banco-padrao."
        )
    if path.exists() and not substituir:
        return None, f"Recusa: o arquivo já existe ({path}). Passe --substituir para apagá-lo e gerar de novo."
    return path, None


def _path_sqlite_da_uri(uri):
    if not uri or not str(uri).startswith("sqlite:///"):
        return None
    resto = str(uri)[len("sqlite:///"):]
    if resto.startswith("/") and len(resto) >= 3 and resto[2] == ":":
        resto = resto.lstrip("/")
    return Path(resto).resolve()


def _disc(nome):
    from src.models import Disciplina
    d = Disciplina.query.filter_by(nome=nome).first()
    if not d:
        raise RuntimeError("Disciplina ausente: %s" % nome)
    return d


def _cpf_seq(inicio=88200000001):
    n = inicio
    while True:
        yield "%011d" % n
        n += 1


def _nome_bulk(indice):
    prenome = PRENOMES[indice % len(PRENOMES)]
    sobrenome = SOBRENOMES[(indice // len(PRENOMES)) % len(SOBRENOMES)]
    extra = SOBRENOMES[(indice + 3) % len(SOBRENOMES)]
    return "%s %s %s" % (prenome, sobrenome, extra)


def _periodos_2026():
    return [
        {"numero": 1, "nome": "1º Bimestre", "data_inicio": "2026-02-02", "data_fim": "2026-04-17"},
        {"numero": 2, "nome": "2º Bimestre", "data_inicio": "2026-04-20", "data_fim": "2026-06-30"},
        {"numero": 3, "nome": "3º Bimestre", "data_inicio": "2026-08-03", "data_fim": "2026-10-09"},
        {"numero": 4, "nome": "4º Bimestre", "data_inicio": "2026-10-13", "data_fim": "2026-12-18"},
    ]


def _garantir_admin_e_disciplinas():
    from src import db
    from src.models import Disciplina, Usuario
    from src.passwords import SENHA_DEMO_ADMIN, hash_senha

    if not Usuario.query.filter_by(login="admin").first():
        db.session.add(Usuario(
            login="admin",
            senha_hash=hash_senha(SENHA_DEMO_ADMIN),
            papel="dirigente",
            nome="Administrador",
            primeiro_acesso=False,
        ))
    for nome in DISCIPLINAS_PADRAO:
        if not Disciplina.query.filter_by(nome=nome).first():
            db.session.add(Disciplina(nome=nome))
    db.session.flush()


def _escola(nome, inep, infantil, fundamental):
    from src import db
    from src.models import Escola
    e = Escola.query.filter_by(inep=inep).first()
    if e:
        return e
    e = Escola(
        nome=nome, inep=inep,
        oferta_infantil=infantil, oferta_fundamental=fundamental,
    )
    db.session.add(e)
    db.session.flush()
    return e


def _usuario(login, nome, papel, escola_id, senha_hash):
    from src.models import Usuario
    u = Usuario.query.filter_by(login=login).first()
    if u:
        return u
    u = Usuario(
        login=login, nome=nome, papel=papel, escola_id=escola_id,
        primeiro_acesso=False, senha_hash=senha_hash,
    )
    from src import db
    db.session.add(u)
    db.session.flush()
    return u


def _professor(nome, cpf, escola_id, usuario):
    from src import db
    from src.models import Professor
    p = Professor.query.filter_by(cpf=cpf).first()
    if p:
        return p
    p = Professor(nome=nome, cpf=cpf, escola_id=escola_id, usuario_id=usuario.id)
    db.session.add(p)
    db.session.flush()
    return p


def _turma(nome, escola_id, etapa, capacidade, jornada=None, ch_diaria=None, ch_anual=None, ano=ANO_DEMO, status="ativa"):
    from src import db
    from src.models import Turma
    t = Turma.query.filter_by(nome=nome, escola_id=escola_id, ano_letivo=ano).first()
    if t:
        return t
    t = Turma(
        nome=nome, ano_letivo=ano, status=status, etapa_ensino=etapa,
        escola_id=escola_id, capacidade=capacidade,
        tipo_jornada=jornada, carga_horaria_diaria=ch_diaria, carga_horaria_anual=ch_anual,
    )
    db.session.add(t)
    db.session.flush()
    return t


def _alocar(professor, turma, disciplina, weekday, aulas_dia=2, carga=160):
    from src import db
    from src.models import Alocacao, HorarioAlocacao
    disc_id = disciplina.id if disciplina is not None else None
    aloc = Alocacao.query.filter_by(
        professor_id=professor.id, turma_id=turma.id, disciplina_id=disc_id,
    ).first()
    if not aloc:
        aloc = Alocacao(
            professor_id=professor.id,
            turma_id=turma.id,
            disciplina_id=disc_id,
            carga_horaria_prevista=carga,
            aulas_semanais=aulas_dia,
            duracao_aula_minutos=50,
        )
        db.session.add(aloc)
        db.session.flush()
    else:
        aloc.carga_horaria_prevista = aloc.carga_horaria_prevista or carga
        aloc.aulas_semanais = aloc.aulas_semanais or aulas_dia
        aloc.duracao_aula_minutos = aloc.duracao_aula_minutos or 50
    existente = HorarioAlocacao.query.filter_by(alocacao_id=aloc.id, dia_semana=weekday).first()
    if not existente:
        db.session.add(HorarioAlocacao(
            alocacao_id=aloc.id, dia_semana=weekday, quantidade_aulas=aulas_dia,
        ))
        db.session.flush()
    return aloc


def _aluno_demo(nome, cpf, escola_id, turma, mae, nasc=None, endereco=None, situacao="ativo"):
    from src import db
    from src.models import Aluno
    a = Aluno.query.filter_by(cpf=cpf).first()
    if a:
        return a
    a = Aluno(
        nome=nome, cpf=cpf, situacao=situacao, turma_id=turma.id if turma else None,
        escola_id=escola_id, nome_mae=mae, data_nascimento=nasc,
        endereco=endereco or "Rua das Acácias, 20 — Setor Demo",
        telefone="8990000000", local_nascimento="Palmeira do Piauí (fictício)",
    )
    db.session.add(a)
    db.session.flush()
    return a


def _matricular(aluno, turma, data_inicio=None):
    from src.matriculas import abrir_matricula_cursando, matricula_cursando
    if turma is None:
        return None
    ja = matricula_cursando(aluno)
    if ja:
        return ja
    erro = abrir_matricula_cursando(aluno, turma, data_inicio=data_inicio or date(ANO_DEMO, 2, 2))
    if erro:
        raise RuntimeError("%s: %s" % (aluno.nome, erro))
    from src.matriculas import matricula_cursando as _mc
    return _mc(aluno)


def _preencher_turma(turma, quantidade, cpfs, indice_base, escola_id):
    criados = []
    from src.models import Aluno
    ja = Aluno.query.filter_by(turma_id=turma.id, situacao="ativo").count()
    falta = max(0, quantidade - ja)
    i = 0
    while len(criados) < falta:
        nome = _nome_bulk(indice_base + i)
        i += 1
        if Aluno.query.filter_by(nome=nome).first():
            continue
        cpf = next(cpfs)
        aluno = _aluno_demo(
            nome, cpf, escola_id, turma,
            mae="Responsável de %s (Demo)" % nome.split()[0],
            nasc=date(2016, 3, 10) if turma.etapa_ensino == "fundamental" else date(2021, 5, 8),
        )
        _matricular(aluno, turma)
        criados.append(aluno)
    return criados


def _celulas_ano(n1, n2, rp=None, rs=None, pf=None):
    mapa = {}
    for bim in (11, 21, 31, 41):
        mapa[bim] = n1
        mapa[bim + 1] = n2
        if rp is not None:
            mapa[bim + 2] = rp
    if rs is not None:
        mapa[51] = rs
        mapa[52] = rs
    if pf is not None:
        mapa[99] = pf
    return mapa


def _lancar_notas(aluno, disciplina_id, celulas):
    from src.matriculas import matricula_cursando
    from src.notas import upsert_nota_matricula
    mat = matricula_cursando(aluno)
    if not mat:
        raise RuntimeError("Sem matrícula para lançar notas: %s" % aluno.nome)
    for codigo, valor in celulas.items():
        tipo = "recuperacao" if codigo in (13, 23, 33, 43, 51, 52) else ("final" if codigo == 99 else "bimestral")
        upsert_nota_matricula(mat, disciplina_id, codigo, valor, tipo=tipo)


def _faltas_bimestrais(aluno, disciplina_id, qtd=(2, 1, 3, 0)):
    from src import db
    from src.models import RegistroFalta
    from src.matriculas import matricula_cursando
    mat = matricula_cursando(aluno)
    for bim, n in enumerate(qtd, start=1):
        if RegistroFalta.query.filter_by(matricula_id=mat.id if mat else None, disciplina_id=disciplina_id, bimestre=bim).first():
            continue
        db.session.add(RegistroFalta(
            aluno_id=aluno.id, matricula_id=mat.id if mat else None,
            disciplina_id=disciplina_id, bimestre=bim, quantidade=n,
        ))


def _parecer(aluno, eixo, bimestre, avaliacao, observacao):
    from src.matriculas import matricula_cursando
    from src.pareceres import upsert_parecer_matricula
    mat = matricula_cursando(aluno)
    if not mat:
        raise RuntimeError("Sem matrícula para parecer: %s" % aluno.nome)
    upsert_parecer_matricula(mat, eixo, bimestre, avaliacao, observacao)


def _notas_minimas_da_turma(turma, disciplina):
    from src.models import Aluno, RegistroNota
    from src.matriculas import matricula_cursando
    for aluno in Aluno.query.filter_by(turma_id=turma.id, situacao="ativo").all():
        mat = matricula_cursando(aluno)
        if not mat:
            continue
        if RegistroNota.query.filter_by(matricula_id=mat.id, disciplina_id=disciplina.id).first():
            continue
        _lancar_notas(
            aluno, disciplina.id,
            {11: 7.0, 12: 7.0, 21: 7.0, 22: 7.0, 31: 7.0, 32: 7.0, 41: 7.0, 42: 7.0},
        )


def _pareceres_minimos(turma):
    from src.models import Aluno, RelatorioDescritivo
    from src.matriculas import matricula_cursando
    for aluno in Aluno.query.filter_by(turma_id=turma.id, situacao="ativo").all():
        mat = matricula_cursando(aluno)
        if not mat:
            continue
        if RelatorioDescritivo.query.filter_by(matricula_id=mat.id).first():
            continue
        _parecer(
            aluno, EIXO_EI, 1, "Em Desenvolvimento",
            "Participa das atividades propostas (registro fictício de demonstração).",
        )


def _registrar_aula(usuario, turma, disciplina_id, data, conteudo, metodologia, faltosos=None, quantidade=1, bimestre=3):
    from src.diario import criar_registro_aula
    from src.frequencia import FALTA, PRESENTE, alunos_ativos_da_turma
    faltosos = set(faltosos or [])
    lancamentos = []
    for aluno in alunos_ativos_da_turma(turma):
        lancamentos.append({
            "aluno_id": aluno.id,
            "situacao": FALTA if aluno.id in faltosos else PRESENTE,
        })
    aula, erro, _codigo = criar_registro_aula(usuario, turma, disciplina_id, {
        "data": data.isoformat() if hasattr(data, "isoformat") else data,
        "bimestre": bimestre,
        "quantidade_aulas": quantidade,
        "conteudo": conteudo,
        "metodologia": metodologia,
        "lancamentos": lancamentos,
    })
    if erro:
        raise RuntimeError("Falha ao registrar aula demo (%s): %s" % (data, erro))
    return aula


def _avaliar_aula(coord, aula, status, motivo=None):
    from src.diario import avaliar_registro_aula
    dados = {"status": status}
    if motivo:
        dados["motivo"] = motivo
    aula, erro, _codigo = avaliar_registro_aula(aula, coord, dados)
    if erro:
        raise RuntimeError("Falha ao avaliar aula demo: %s" % erro)
    return aula


def demo_ja_preparada():
    from src.models import Escola
    return Escola.query.filter_by(inep=INEP_SOL).first() is not None


def montar_credenciais():
    from src.models import Escola, Usuario
    sol = Escola.query.filter_by(inep=INEP_SOL).first()
    lua = Escola.query.filter_by(inep=INEP_LUA).first()
    diretor_sol = Usuario.query.filter_by(login=INEP_SOL).first()
    diretor_lua = Usuario.query.filter_by(login=INEP_LUA).first()
    coord_sol = Usuario.query.filter_by(login=LOGIN_COORD_SOL).first()
    coord_lua = Usuario.query.filter_by(login=LOGIN_COORD_LUA).first()
    maria = Usuario.query.filter_by(login=LOGIN_MARIA).first()
    joao = Usuario.query.filter_by(login=LOGIN_JOAO).first()
    carlos = Usuario.query.filter_by(login=LOGIN_CARLOS).first()
    helena = Usuario.query.filter_by(login=LOGIN_HELENA).first()
    return {
        "senha": SENHA_DEMO,
        "ficticio": True,
        "aviso": "Dados 100% fictícios. Não usar em produção.",
        "dirigente": {"login": "admin", "senha": "admin123"},
        "escola_principal": NOME_SOL,
        "professor_principal": {"login": LOGIN_MARIA, "nome": maria.nome if maria else "Maria Demo"},
        "coordenador_principal": {
            "login": LOGIN_COORD_SOL,
            "nome": coord_sol.nome if coord_sol else "Cibele Demo (Coordenadora)",
        },
        "gestores": [
            {"login": INEP_SOL, "nome": diretor_sol.nome if diretor_sol else "", "escola": sol.nome if sol else NOME_SOL},
            {"login": INEP_LUA, "nome": diretor_lua.nome if diretor_lua else "", "escola": lua.nome if lua else NOME_LUA},
            {"login": INEP_CAMINHOS, "nome": "Raquel Demo (Gestora)", "escola": NOME_CAMINHOS},
            {"login": INEP_SERRA, "nome": "Sérgio Demo (Gestor)", "escola": NOME_SERRA},
            {"login": INEP_CRECHE, "nome": "Tânia Demo (Gestora)", "escola": NOME_CRECHE},
        ],
        "coordenadores": [
            {"login": LOGIN_COORD_SOL, "nome": coord_sol.nome if coord_sol else "Cibele Demo (Coordenadora)"},
            {"login": LOGIN_COORD_LUA, "nome": coord_lua.nome if coord_lua else "Paulo Demo (Coordenador)"},
            {"login": LOGIN_COORD_CAMINHOS, "nome": "Nádia Demo (Coordenadora)"},
            {"login": LOGIN_COORD_SERRA, "nome": "Oscar Demo (Coordenador)"},
            {"login": LOGIN_COORD_CRECHE, "nome": "Pilar Demo (Coordenadora)"},
        ],
        "professores": [
            {"login": LOGIN_MARIA, "nome": maria.nome if maria else "Maria Demo"},
            {"login": LOGIN_JOAO, "nome": joao.nome if joao else "João Demo"},
            {"login": LOGIN_CARLOS, "nome": carlos.nome if carlos else "Carlos Demo"},
            {"login": LOGIN_HELENA, "nome": helena.nome if helena else "Helena Viana Demo"},
        ],
        "escolas": 5,
    }


def _popular_calendario():
    from src import db
    from src.calendario import criar_ano_letivo, definir_dia
    from src.models import AnoLetivo

    ano = AnoLetivo.query.filter_by(ano=ANO_DEMO).first()
    if not ano:
        ano, erro = criar_ano_letivo({
            "ano": ANO_DEMO,
            "data_inicio": DATA_INICIO_ANO.isoformat(),
            "data_fim": DATA_FIM_ANO.isoformat(),
            "ativo": True,
            "periodos": _periodos_2026(),
            "gerar_calendario": True,
        })
        if erro:
            raise RuntimeError(erro)
    definir_dia(ano, DATA_FERIADO_INDEPENDENCIA, "FERIADO", "Independência (demo)", persistir=False)
    definir_dia(ano, DATA_FERIADO_TIRADENTES, "FERIADO", "Tiradentes (demo)", persistir=False)
    definir_dia(ano, DATA_REPOSICAO, "REPOSICAO", "Sábado letivo de reposição (demo)", persistir=False)
    recesso = date(2026, 7, 13)
    while recesso <= date(2026, 7, 31):
        if recesso.weekday() < 5:
            definir_dia(ano, recesso, "RECESSO", "Recesso escolar (demo)", persistir=False)
        recesso += timedelta(days=1)
    definir_dia(ano, date(2026, 6, 12), "NAO_LETIVO", "Ponto facultativo municipal (demo)", persistir=False)
    db.session.commit()
    return ano


def _popular_historico_e_legado(sol, t5, mat_disc, cpfs):
    from src import db
    from src.historico import encerrar_turma_por_matriculas
    from src.models import Aluno
    from src.trajetoria_legada import criar

    t2025 = _turma("4º Ano A 2025 Demo", sol.id, "fundamental", 30, ano=2025, status="ativa")
    livia = Aluno.query.filter_by(nome="Lívia Rematrícula Demo").first()
    if not livia:
        livia = _aluno_demo(
            "Lívia Rematrícula Demo", next(cpfs), sol.id, t2025,
            mae="Mãe Lívia Demo", nasc=date(2015, 11, 2),
        )
        _matricular(livia, t2025, data_inicio=date(2025, 2, 3))
        _lancar_notas(livia, mat_disc.id, _celulas_ano(7.0, 7.5))
        db.session.flush()
    if (t2025.status or "").lower() != "concluida":
        encerrar_turma_por_matriculas(t2025, data_fim=date(2025, 12, 15))
        db.session.commit()
    from src.matriculas import matricula_cursando
    if matricula_cursando(livia) is None or livia.turma_id != t5.id:
        livia.turma_id = None
        db.session.flush()
        _matricular(livia, t5, data_inicio=date(ANO_DEMO, 2, 2))
        db.session.flush()

    ana = Aluno.query.filter_by(nome="Ana Aprovada Demo").first()
    if ana and not (ana.trajetorias_legadas or []):
        criar(ana, {
            "ano_letivo": 2024,
            "serie": 3,
            "estabelecimento": "EMEF Demo Recanto das Palmeiras",
            "municipio": "Palmeira do Piauí",
            "uf": "PI",
            "resultado": "Aprovado",
            "carga_horaria_total": 800,
            "origem": "LEGADO",
            "observacao": "Trajetória anterior fictícia, transcrita para demonstração.",
            "componentes": [
                {"nome": "Língua Portuguesa", "carga_horaria": 160, "nota_conceito": "8,0", "situacao": "Aprovado", "categoria": "BASE_COMUM"},
                {"nome": "Matemática", "carga_horaria": 160, "nota_conceito": "7,5", "situacao": "Aprovado", "categoria": "BASE_COMUM"},
            ],
        }, sol.id)
        db.session.flush()
    return livia


def popular_dados_demo():
    """Preenche a base atual. Idempotente: se a rede demo já existe, não duplica."""
    from src import db
    from src.alunos import inativar_aluno
    from src.faltas_docentes import cancelar_ocorrencia, criar_ocorrencia
    from src.models import (
        Alocacao,
        Aluno,
        Escola,
        HorarioAlocacao,
        Planejamento,
        RegistroAula,
    )
    from src.notas import garantir_configuracao_padrao
    from src.passwords import hash_senha

    garantir_configuracao_padrao()
    _garantir_admin_e_disciplinas()

    from src.models import AnoLetivo, OcorrenciaAulaNaoMinistrada
    if (
        Escola.query.filter_by(inep=INEP_SOL).first()
        and Escola.query.count() >= 5
        and AnoLetivo.query.filter_by(ano=ANO_DEMO).first()
        and RegistroAula.query.filter_by(status="pendente").first()
        and OcorrenciaAulaNaoMinistrada.query.filter_by(status="ATIVA").first()
        and OcorrenciaAulaNaoMinistrada.query.filter_by(status="CANCELADA").first()
    ):
        return montar_credenciais()

    senha = hash_senha(SENHA_DEMO)
    mat = _disc("Matemática")
    port = _disc("Português")
    hist = _disc("História")
    cpfs = _cpf_seq()

    sol = _escola(NOME_SOL, INEP_SOL, True, True)
    lua = _escola(NOME_LUA, INEP_LUA, True, True)
    caminhos = _escola(NOME_CAMINHOS, INEP_CAMINHOS, False, True)
    serra = _escola(NOME_SERRA, INEP_SERRA, False, True)
    creche = _escola(NOME_CRECHE, INEP_CRECHE, True, False)

    diretor_sol = _usuario(INEP_SOL, "André Demo (Gestor)", "gestor_escolar", sol.id, senha)
    _usuario(INEP_LUA, "Beatriz Demo (Gestora)", "gestor_escolar", lua.id, senha)
    _usuario(INEP_CAMINHOS, "Raquel Demo (Gestora)", "gestor_escolar", caminhos.id, senha)
    _usuario(INEP_SERRA, "Sérgio Demo (Gestor)", "gestor_escolar", serra.id, senha)
    _usuario(INEP_CRECHE, "Tânia Demo (Gestora)", "gestor_escolar", creche.id, senha)
    coord_sol = _usuario(LOGIN_COORD_SOL, "Cibele Demo (Coordenadora)", "coordenador_pedagogico", sol.id, senha)
    _usuario(LOGIN_COORD_LUA, "Paulo Demo (Coordenador)", "coordenador_pedagogico", lua.id, senha)
    _usuario(LOGIN_COORD_CAMINHOS, "Nádia Demo (Coordenadora)", "coordenador_pedagogico", caminhos.id, senha)
    _usuario(LOGIN_COORD_SERRA, "Oscar Demo (Coordenador)", "coordenador_pedagogico", serra.id, senha)
    _usuario(LOGIN_COORD_CRECHE, "Pilar Demo (Coordenadora)", "coordenador_pedagogico", creche.id, senha)

    u_maria = _usuario(LOGIN_MARIA, "Maria Demo", "professor", sol.id, senha)
    u_joao = _usuario(LOGIN_JOAO, "João Demo", "professor", sol.id, senha)
    u_carlos = _usuario(LOGIN_CARLOS, "Carlos Demo", "professor", lua.id, senha)
    u_helena = _usuario(LOGIN_HELENA, "Helena Viana Demo", "professor", sol.id, senha)
    u_sofia = _usuario("88100000002", "Sofia Ramos Demo", "professor", sol.id, senha)
    u_teresa = _usuario("88100000003", "Teresa Alves Demo", "professor", lua.id, senha)
    u_rita = _usuario("88100000004", "Rita Campos Demo", "professor", caminhos.id, senha)
    u_otavio = _usuario("88100000005", "Otávio Borges Demo", "professor", caminhos.id, senha)
    u_pedro = _usuario("88100000006", "Pedro Sales Demo", "professor", serra.id, senha)
    u_lucia = _usuario("88100000007", "Lúcia Pires Demo", "professor", creche.id, senha)

    maria = _professor("Maria Demo", LOGIN_MARIA, sol.id, u_maria)
    joao = _professor("João Demo", LOGIN_JOAO, sol.id, u_joao)
    carlos = _professor("Carlos Demo", LOGIN_CARLOS, lua.id, u_carlos)
    helena = _professor("Helena Viana Demo", LOGIN_HELENA, sol.id, u_helena)
    sofia = _professor("Sofia Ramos Demo", "88100000002", sol.id, u_sofia)
    teresa = _professor("Teresa Alves Demo", "88100000003", lua.id, u_teresa)
    rita = _professor("Rita Campos Demo", "88100000004", caminhos.id, u_rita)
    otavio = _professor("Otávio Borges Demo", "88100000005", caminhos.id, u_otavio)
    pedro = _professor("Pedro Sales Demo", "88100000006", serra.id, u_pedro)
    lucia = _professor("Lúcia Pires Demo", "88100000007", creche.id, u_lucia)

    t5 = _turma("5º Ano A Demo", sol.id, "fundamental", 30)
    t4 = _turma("4º Ano A Demo", sol.id, "fundamental", 30)
    t3 = _turma("3º Ano A Demo", sol.id, "fundamental", 28)
    t1 = _turma("1º Ano A Demo", sol.id, "fundamental", 25)
    tpre = _turma("Pré II Demo", sol.id, "infantil", 20, jornada="PARCIAL", ch_diaria=4, ch_anual=800)
    tlua6 = _turma("6º Ano A Demo", lua.id, "fundamental", 30)
    tlua2 = _turma("2º Ano A Demo", lua.id, "fundamental", 25)
    tlua7 = _turma("7º Ano A Demo", lua.id, "fundamental", 30)
    tcam4 = _turma("4º Ano B Demo", caminhos.id, "fundamental", 28)
    tcam8 = _turma("8º Ano A Demo", caminhos.id, "fundamental", 30)
    tser3 = _turma("3º Ano B Demo", serra.id, "fundamental", 28)
    tser5 = _turma("5º Ano B Demo", serra.id, "fundamental", 30)
    tmaternal = _turma("Maternal II Demo", creche.id, "infantil", 18, jornada="INTEGRAL", ch_diaria=8, ch_anual=1600)
    tpre1 = _turma("Pré I Demo", creche.id, "infantil", 20, jornada="PARCIAL", ch_diaria=4, ch_anual=800)

    aloc_maria_port5 = _alocar(maria, t5, port, 0, aulas_dia=2, carga=160)
    _alocar(maria, t5, hist, 4, aulas_dia=2, carga=80)
    _alocar(maria, t4, port, 2, aulas_dia=2, carga=160)
    aloc_joao_mat5 = _alocar(joao, t5, mat, 1, aulas_dia=2, carga=160)
    _alocar(joao, t4, mat, 3, aulas_dia=2, carga=160)
    _alocar(helena, tpre, None, 0, aulas_dia=4, carga=800)
    _alocar(sofia, t3, port, 0, aulas_dia=2, carga=160)
    _alocar(sofia, t3, mat, 1, aulas_dia=2, carga=160)
    _alocar(sofia, t1, port, 2, aulas_dia=2, carga=160)
    _alocar(sofia, t1, mat, 3, aulas_dia=2, carga=160)
    _alocar(carlos, tlua6, mat, 0, aulas_dia=2, carga=160)
    _alocar(carlos, tlua7, mat, 2, aulas_dia=2, carga=160)
    _alocar(teresa, tlua6, port, 1, aulas_dia=2, carga=160)
    _alocar(teresa, tlua2, port, 3, aulas_dia=2, carga=160)
    _alocar(teresa, tlua2, mat, 4, aulas_dia=2, carga=160)
    _alocar(rita, tcam4, port, 1, aulas_dia=2, carga=160)
    _alocar(rita, tcam8, port, 3, aulas_dia=2, carga=160)
    _alocar(otavio, tcam4, mat, 0, aulas_dia=2, carga=160)
    _alocar(otavio, tcam8, mat, 2, aulas_dia=2, carga=160)
    _alocar(pedro, tser3, port, 0, aulas_dia=2, carga=160)
    _alocar(pedro, tser3, mat, 1, aulas_dia=2, carga=160)
    _alocar(pedro, tser5, port, 2, aulas_dia=2, carga=160)
    _alocar(pedro, tser5, mat, 3, aulas_dia=2, carga=160)
    _alocar(lucia, tmaternal, None, 0, aulas_dia=4, carga=1600)
    _alocar(lucia, tpre1, None, 1, aulas_dia=4, carga=800)
    db.session.flush()

    ana = _aluno_demo("Ana Aprovada Demo", "88044444444", sol.id, t5, "Mãe Ana Demo", date(2015, 3, 12), "Rua das Palmeiras, 10 — Demo")
    bruno = _aluno_demo("Bruno Recuperação Demo", "88055555555", sol.id, t5, "Mãe Bruno Demo", date(2015, 7, 4))
    diego = _aluno_demo("Diego Cursando Demo", "88066666666", sol.id, t5, "Mãe Diego Demo")
    carla = _aluno_demo("Carla Sem Turma Demo", "88077777777", sol.id, None, "Mãe Carla Demo")
    elena = _aluno_demo("Elena Infantil Demo", "88088888888", sol.id, tpre, "Mãe Elena Demo")
    fabio = _aluno_demo("Fábio Lua Demo", "88099999999", lua.id, tlua6, "Mãe Fábio Demo")
    clara = _aluno_demo("Clara Mediana Demo", next(cpfs), sol.id, t5, "Mãe Clara Demo", date(2015, 4, 9))
    igor = _aluno_demo("Igor Limítrofe Demo", next(cpfs), sol.id, t5, "Mãe Igor Demo", date(2015, 8, 21))
    renata = _aluno_demo("Renata Risco Demo", next(cpfs), sol.id, t5, "Mãe Renata Demo", date(2015, 1, 30))
    paulo_al = _aluno_demo("Paulo Paralela Demo", next(cpfs), sol.id, t5, "Mãe Paulo Demo", date(2015, 6, 15))
    sem_turma_2 = _aluno_demo("Caio Aguardando Demo", next(cpfs), sol.id, None, "Mãe Caio Demo")
    inativo = _aluno_demo("Marta Inativa Demo", next(cpfs), sol.id, t1, "Mãe Marta Demo", date(2018, 2, 2))

    for aluno, turma in (
        (ana, t5), (bruno, t5), (diego, t5), (elena, tpre), (fabio, tlua6),
        (clara, t5), (igor, t5), (renata, t5), (paulo_al, t5), (inativo, t1),
    ):
        _matricular(aluno, turma)

    _preencher_turma(t5, 12, cpfs, 0, sol.id)
    _preencher_turma(t4, 8, cpfs, 20, sol.id)
    _preencher_turma(t3, 6, cpfs, 40, sol.id)
    _preencher_turma(t1, 6, cpfs, 50, sol.id)
    _preencher_turma(tpre, 8, cpfs, 60, sol.id)
    _preencher_turma(tlua6, 10, cpfs, 80, lua.id)
    _preencher_turma(tlua2, 8, cpfs, 100, lua.id)
    _preencher_turma(tlua7, 6, cpfs, 120, lua.id)
    _preencher_turma(tcam4, 10, cpfs, 140, caminhos.id)
    _preencher_turma(tcam8, 8, cpfs, 160, caminhos.id)
    _preencher_turma(tser3, 8, cpfs, 180, serra.id)
    _preencher_turma(tser5, 8, cpfs, 200, serra.id)
    _preencher_turma(tmaternal, 8, cpfs, 220, creche.id)
    _preencher_turma(tpre1, 8, cpfs, 240, creche.id)
    db.session.flush()

    _lancar_notas(ana, mat.id, _celulas_ano(8.0, 8.0))
    _lancar_notas(ana, port.id, _celulas_ano(8.5, 8.0))
    _lancar_notas(bruno, mat.id, _celulas_ano(4.0, 4.0))
    _lancar_notas(bruno, port.id, _celulas_ano(5.0, 4.5))
    _faltas_bimestrais(bruno, mat.id, (4, 3, 2, 1))
    from src.matriculas import matricula_cursando
    from src.models import RegistroNota
    mat_diego = matricula_cursando(diego)
    if not RegistroNota.query.filter_by(matricula_id=mat_diego.id, disciplina_id=mat.id).first():
        db.session.add(RegistroNota(
            aluno_id=diego.id, matricula_id=mat_diego.id, disciplina_id=mat.id,
            bimestre=11, valor=6.0, tipo="bimestral",
        ))
        db.session.add(RegistroNota(
            aluno_id=diego.id, matricula_id=mat_diego.id, disciplina_id=mat.id,
            bimestre=12, valor=7.0, tipo="bimestral",
        ))
    _lancar_notas(clara, mat.id, _celulas_ano(7.0, 7.0))
    _lancar_notas(igor, mat.id, _celulas_ano(6.0, 6.0))
    _lancar_notas(renata, mat.id, _celulas_ano(3.0, 3.5, pf=4.0))
    _lancar_notas(paulo_al, mat.id, _celulas_ano(4.0, 5.0, rp=7.5))
    _lancar_notas(fabio, mat.id, _celulas_ano(7.0, 7.0))

    _parecer(
        elena, EIXO_EI, 1, "Em Desenvolvimento",
        "Participa das rodas de conversa (dado fictício de demonstração).",
    )

    if not Planejamento.query.filter_by(status="pendente").first():
        db.session.add_all([
            Planejamento(
                professor_id=maria.id, turma_id=t5.id,
                conteudo="Plano pendente Demo: leitura e produção de texto no 5º ano (fictício).",
                status="pendente",
            ),
            Planejamento(
                professor_id=joao.id, turma_id=t5.id,
                conteudo="Plano aprovado Demo: operações com números decimais (fictício).",
                status="aprovado",
                feedback_coordenacao="Plano alinhado à BNCC. Seguir com os exercícios propostos.",
            ),
            Planejamento(
                professor_id=maria.id, turma_id=t5.id,
                conteudo="Plano devolvido Demo: precisa detalhar a avaliação (fictício).",
                status="revisar",
                feedback_coordenacao="Inclua os critérios de avaliação da semana e reenvie.",
            ),
        ])
    db.session.commit()

    _popular_historico_e_legado(sol, t5, mat, cpfs)
    _popular_calendario()

    for aloc in Alocacao.query.all():
        turma = aloc.turma
        if not turma or turma.status != "ativa":
            continue
        if turma.etapa_ensino == "infantil":
            _pareceres_minimos(turma)
        elif aloc.disciplina_id:
            _notas_minimas_da_turma(turma, aloc.disciplina)
    db.session.commit()

    inativar_aluno(inativo)
    db.session.commit()

    u_maria = u_maria if u_maria.professor else maria.usuario
    u_joao = joao.usuario
    u_helena = helena.usuario
    u_carlos = carlos.usuario

    if not RegistroAula.query.filter_by(turma_id=t5.id).first():
        aula_todos = _registrar_aula(
            u_maria, t5, port.id, DATA_AULA_VALIDADA_TODOS,
            "Leitura compartilhada de crônica e identificação do tema.",
            "Roda de leitura e registro no caderno.",
            faltosos=[], quantidade=2,
        )
        _avaliar_aula(coord_sol, aula_todos, "validada")

        faltosos_freq = [bruno.id, renata.id]
        aula_faltas = _registrar_aula(
            u_maria, t5, port.id, DATA_AULA_VALIDADA_FALTAS,
            "Produção de parágrafo narrativo com reescrita orientada.",
            "Escrita individual e socialização de trechos.",
            faltosos=faltosos_freq, quantidade=2,
        )
        _avaliar_aula(coord_sol, aula_faltas, "validada")

        aula_inv = _registrar_aula(
            u_maria, t5, hist.id, DATA_AULA_INVALIDADA,
            "Conteúdo incompleto — precisa reapresentar a sequência didática.",
            "Aula expositiva sem registro de atividade dos alunos.",
            faltosos=[], quantidade=1,
        )
        _avaliar_aula(
            coord_sol, aula_inv, "invalidada",
            "O registro não descreve o conteúdo efetivamente trabalhado nem a participação da turma.",
        )

        _registrar_aula(
            u_maria, t5, port.id, DATA_AULA_PENDENTE_MARIA,
            "Revisão de pontuação e leitura de poema para o mural da turma.",
            "Leitura em voz alta e exercício curto no quadro.",
            faltosos=[diego.id], quantidade=2,
        )

        aula_joao_ok = _registrar_aula(
            u_joao, t5, mat.id, DATA_AULA_VALIDADA_JOAO,
            "Números decimais: comparação e ordenação.",
            "Situações-problema com material dourado e quadro.",
            faltosos=[bruno.id], quantidade=2,
        )
        _avaliar_aula(coord_sol, aula_joao_ok, "validada")

        _registrar_aula(
            u_joao, t5, mat.id, DATA_AULA_PENDENTE_JOAO,
            "Adição e subtração com decimais em contextos do cotidiano.",
            "Resolução em duplas e correção coletiva.",
            faltosos=[], quantidade=2,
        )

        aula_ei_ok = _registrar_aula(
            u_helena, tpre, None, DATA_AULA_VALIDADA_TODOS,
            "Roda de conversa sobre o cuidado com as plantas do pátio.",
            "Exploração no jardim e desenho de observação.",
            faltosos=[], quantidade=1,
        )
        _avaliar_aula(coord_sol, aula_ei_ok, "validada")

        _registrar_aula(
            u_helena, tpre, None, DATA_AULA_PENDENTE_HELENA,
            "Brincadeira de faz de conta na casinha e organização da sala.",
            "Mediação em pequenos grupos e registro fotográfico interno.",
            faltosos=[], quantidade=1,
        )

        aula_carlos = _registrar_aula(
            u_carlos, tlua6, mat.id, DATA_AULA_CARLOS,
            "Frações equivalentes com apoio de material concreto.",
            "Atividade em grupos e socialização dos resultados.",
            faltosos=[], quantidade=2,
        )
        coord_lua = _usuario(LOGIN_COORD_LUA, "Paulo Demo (Coordenador)", "coordenador_pedagogico", lua.id, senha)
        _avaliar_aula(coord_lua, aula_carlos, "validada")

    horario_joao_terca = HorarioAlocacao.query.filter_by(
        alocacao_id=aloc_joao_mat5.id, dia_semana=1,
    ).first()
    if horario_joao_terca:
        from src.models import OcorrenciaAulaNaoMinistrada
        if not OcorrenciaAulaNaoMinistrada.query.filter_by(data=DATA_FALTA_ATIVA).first():
            occ_ativa, erro, _c = criar_ocorrencia(diretor_sol, {
                "data": DATA_FALTA_ATIVA.isoformat(),
                "horario_alocacao_id": horario_joao_terca.id,
                "observacao": "Professor não compareceu neste horário (registro fictício de demonstração).",
            }, sol.id)
            if erro:
                raise RuntimeError("Falha ao criar falta docente ativa: %s" % erro)
        if not OcorrenciaAulaNaoMinistrada.query.filter_by(data=DATA_FALTA_CANCELADA).first():
            occ_canc, erro, _c = criar_ocorrencia(diretor_sol, {
                "data": DATA_FALTA_CANCELADA.isoformat(),
                "horario_alocacao_id": horario_joao_terca.id,
                "observacao": "Lançamento provisório depois confirmado como equívoco de data (demo).",
            }, sol.id)
            if erro:
                raise RuntimeError("Falha ao criar falta docente cancelável: %s" % erro)
            cancelar_ocorrencia(occ_canc, diretor_sol, {
                "motivo": "A aula ocorreu normalmente; o lançamento foi feito na data errada.",
            })

    _ = (carla, sem_turma_2, aloc_maria_port5)
    db.session.commit()
    return montar_credenciais()


def gerar_arquivo(destino, confirmar, substituir=False, permitir_padrao=False):
    path, erro = validar_destino(destino, confirmar, substituir, permitir_padrao)
    if erro:
        return None, erro
    if path.exists():
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    uri = "sqlite:///" + path.as_posix()
    from src.config import Config
    Config.SQLALCHEMY_DATABASE_URI = uri
    os.environ["EDUCAMAIS_DATABASE_URI"] = uri
    os.environ.setdefault("EDUCAMAIS_ENV", "development")
    os.environ["EDUCAMAIS_DEMO"] = "1"
    from flask_migrate import upgrade
    from src import create_app
    app = create_app(preparar_schema=False)
    with app.app_context():
        upgrade()
    app = create_app()
    with app.app_context():
        credenciais = popular_dados_demo()
    credenciais["arquivo"] = str(path)
    credenciais["uri"] = uri
    return credenciais, None


def executar_preparar_demo(confirmar=False, destino=None, substituir=False, permitir_padrao=False):
    """Ponto único para a CLI Flask e o script. Recusa produção."""
    if destino:
        return gerar_arquivo(destino, confirmar, substituir, permitir_padrao)
    erro_prod = recusar_producao_app()
    if erro_prod:
        return None, erro_prod
    if not confirmar:
        return None, "Recusa: informe --confirmar. Este comando nunca roda sozinho sobre um banco existente."
    if substituir:
        return None, (
            "Recusa: --substituir só apaga arquivo SQLite informado em --destino. "
            "Não há reset silencioso do banco atual."
        )
    from flask import current_app
    from src.config import uri_e_sqlite
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    if uri_e_sqlite(uri):
        path = _path_sqlite_da_uri(uri)
        if path is not None and path == banco_padrao() and not permitir_padrao:
            return None, (
                "Recusa: o banco atual é o padrão (instance/educamais.db). "
                "Use --destino instance/educamais_demo.db ou --permitir-banco-padrao."
            )
    os.environ["EDUCAMAIS_DEMO"] = "1"
    credenciais = popular_dados_demo()
    credenciais["uri"] = uri
    return credenciais, None


def imprimir_credenciais(credenciais):
    print("ATENÇÃO: dados 100% fictícios. Não usar em produção.")
    if credenciais.get("arquivo"):
        print("Base de demonstração criada em:", credenciais["arquivo"])
    if credenciais.get("uri"):
        print("URI:", credenciais["uri"])
    print("Senha dos perfis de escola/docentes (fictícia):", credenciais["senha"])
    print("Dirigente municipal: admin / admin123")
    print("Escola principal da apresentação:", credenciais.get("escola_principal"))
    print(
        "Professor principal:",
        credenciais["professor_principal"]["login"], "/", credenciais["senha"],
        "-", credenciais["professor_principal"]["nome"],
    )
    print(
        "Coordenador principal:",
        credenciais["coordenador_principal"]["login"], "/", credenciais["senha"],
        "-", credenciais["coordenador_principal"]["nome"],
    )
    for g in credenciais["gestores"]:
        print("Gestor:", g["login"], "/", credenciais["senha"], "-", g["nome"])
    for c in credenciais["coordenadores"]:
        print("Coordenador:", c["login"], "/", credenciais["senha"], "-", c["nome"])
    for p in credenciais["professores"]:
        print("Professor:", p["login"], "/", credenciais["senha"], "-", p["nome"])
    print("Para usar a base SQLite: EDUCAMAIS_ENV=development, EDUCAMAIS_DATABASE_URI e python app.py")
    print("Roteiro: docs/DEMO_COMERCIAL.md")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Gera uma base FICTÍCIA de demonstração. Não executa sozinho sobre o banco real.",
    )
    parser.add_argument(
        "--destino",
        default=str(Path("instance") / "educamais_demo.db"),
        help="Arquivo SQLite de destino (padrão: instance/educamais_demo.db)",
    )
    parser.add_argument("--confirmar", action="store_true", help="Obrigatório. Confirma a geração.")
    parser.add_argument("--substituir", action="store_true", help="Apaga o arquivo de destino se já existir.")
    parser.add_argument(
        "--permitir-banco-padrao",
        action="store_true",
        help="Permite gravar em instance/educamais.db (use só se tiver certeza).",
    )
    args = parser.parse_args(argv)
    credenciais, erro = gerar_arquivo(
        args.destino, args.confirmar, args.substituir, args.permitir_banco_padrao,
    )
    if erro:
        print(erro, file=sys.stderr)
        return 2
    imprimir_credenciais(credenciais)
    return 0


if __name__ == "__main__":
    sys.exit(main())
