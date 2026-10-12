from flask import Blueprint, request, current_app, send_file
from flask_login import login_required, current_user
from src import db
from src.http import ok, err, read_json_dict, MENSAGEM_ERRO_INTERNO
from src import authz
from src.passwords import gerar_senha_temporaria, hash_senha, verificar_senha
from src.models import Escola, Usuario, Professor, Turma, Aviso, Planejamento, RegistroNota, RegistroFalta, RegistroAula, Aluno, Alocacao, Disciplina, Matricula
from src.notas import (
    calcular_notas_matricula,
    payload_resumo_monitoramento,
    remover_nota_matricula,
    upsert_nota_matricula,
    validar_nota_0_a_10,
    garantir_configuracao_padrao,
    payload_configuracao,
)
from src.frequencia import (
    FALTA,
    PRESENTE,
    alunos_ativos_da_turma,
    alocacao_do_professor,
    aplicar_frequencia,
    disciplina_armazenada,
    normalizar_bimestre,
    normalizar_situacao,
    parse_data_chamada,
    query_chamada,
    resolver_matricula_da_chamada,
    upsert_falta_matricula,
)
from src.diario import (
    alunos_formulario,
    avaliar_registro_aula,
    criar_registro_aula,
    payload_registro,
    query_registros,
    query_registros_escola,
)
from src.calendario import aplicar_campos_carga, aplicar_jornada_turma
from src.indicadores import acompanhamento_escola, acompanhamento_rede
from src.alunos import (
    SITUACAO_ATIVO,
    aplicar_ficha, aluno_com_cpf, aluno_esta_ativo, cpf_valido, inativar_aluno,
    MSG_ALUNO_INATIVADO, normalizar_cpf,
    parse_capacidade, parse_data, tem_responsavel, turma_lotada,
)
from src.matriculas import (
    MSG_JA_CURSANDO,
    MSG_JA_NESTA_TURMA,
    MSG_SEM_MATRICULA_CURSANDO,
    VINCULO_CURSANDO,
    abrir_matricula_cursando,
    garantir_aluno_da_matricula,
    listar_historico_matriculas,
    matricula_cursando,
    matricula_cursando_na_turma,
    matriculas_da_turma,
)
from src.historico import (
    RESULTADO_JA_ENCERRADA,
    encerrar_turma_por_matriculas,
    historicos_visiveis_do_aluno,
)
from src.pareceres import (
    aplicar_parecer_matricula,
    payload_parecer,
    pareceres_da_matricula,
    resolver_matricula_parecer_turma,
)
from src.exclusao import HTTP_CONFLITO, MSG_GENERICO, motivo_bloqueio_exclusao
from src.integridade import MSG_INTEGRIDADE_DUPLICADA
from src.routes.spa import aluno_payload
from src.uploads import (
    MIME_POR_EXTENSAO,
    extensao_de,
    localizar_arquivo,
    nome_original_sanitizado,
    remover_silencioso,
    salvar_anexo,
)
from datetime import datetime
from sqlalchemy.exc import IntegrityError

api_bp = Blueprint('api', __name__)


def _usuario_primeiro_acesso(login, nome, papel, escola_id):
    senha = gerar_senha_temporaria()
    usuario = Usuario(
        login=login, nome=nome, papel=papel, escola_id=escola_id,
        primeiro_acesso=True, senha_hash=hash_senha(senha),
    )
    return usuario, senha


def _ok_conta_criada(mensagem, senha, extra=None):
    payload = {
        "mensagem": mensagem,
        "senha_temporaria": senha,
        "senhaTemporaria": senha,
        "avisoSenha": "Informe esta senha ao usuário. Ela não poderá ser consultada novamente.",
    }
    if extra:
        payload.update(extra)
    return ok(payload)


def _escopo_alunos():
    query = Aluno.query.outerjoin(Turma, Aluno.turma_id == Turma.id)
    if authz.gestor(current_user) or authz.coordenador(current_user):
        query = query.filter(
            (Aluno.escola_id == current_user.escola_id) | (Turma.escola_id == current_user.escola_id)
        )
    elif authz.professor(current_user):
        turmas_ids = authz.ids_turmas_alocadas(current_user)
        if not turmas_ids:
            return None
        query = query.filter(Aluno.turma_id.in_(turmas_ids))
    elif not authz.municipal(current_user):
        return None
    return query


def _aluno_busca_item(aluno):
    item = aluno_payload(aluno)
    item["turma"] = aluno.turma.nome if aluno.turma else "Sem Turma"
    return item


def _registrar_vinculo_aberto(acao, aluno, turma, situacao_anterior=None, turma_id_anterior=None, matricula_id_anterior=None):
    from src.auditoria import registrar_auditoria

    db.session.flush()
    mat = matricula_cursando(aluno)
    registrar_auditoria(
        acao,
        "Matricula",
        entidade_id=mat.id if mat is not None else None,
        escola_id=turma.escola_id if turma is not None else None,
        dados_anteriores={
            "aluno_id": aluno.id,
            "matricula_id": matricula_id_anterior,
            "turma_id": turma_id_anterior,
            "situacao_vinculo": situacao_anterior,
        },
        dados_novos={
            "aluno_id": aluno.id,
            "matricula_id": mat.id if mat is not None else None,
            "turma_id": turma.id if turma is not None else None,
            "situacao_vinculo": VINCULO_CURSANDO,
            "ano_letivo": turma.ano_letivo if turma is not None else None,
        },
    )


# --- NOVA ROTA: BUSCA INTELIGENTE DE ALUNOS (CORRIGIDA) ---
@api_bp.route('/api/alunos/busca', methods=['GET'])
@login_required
def buscar_alunos():
    termo = request.args.get('q', '').strip()
    if not termo or len(termo) < 2:
        return ok([])

    query = _escopo_alunos()
    if query is None:
        return ok([])

    alunos = query.filter(
        (Aluno.nome.ilike(f'%{termo}%')) | (Aluno.cpf.ilike(f'%{termo}%'))
    ).distinct().limit(20).all()
    return ok([_aluno_busca_item(a) for a in alunos])


@api_bp.route('/api/alunos', methods=['GET'])
@login_required
def listar_alunos():
    query = _escopo_alunos()
    if query is None:
        return authz.recusa()
    termo = (request.args.get('q') or '').strip()
    situacao = (request.args.get('situacao') or '').strip().lower()
    sem_turma = (request.args.get('sem_turma') or '').lower() in ('1', 'true', 'sim')
    if termo:
        query = query.filter(
            (Aluno.nome.ilike(f'%{termo}%')) | (Aluno.cpf.ilike(f'%{termo}%'))
        )
    if situacao:
        if situacao == SITUACAO_ATIVO:
            query = query.filter(Aluno.situacao.in_(('ativo', 'cursando')))
        else:
            query = query.filter(Aluno.situacao == situacao)
    if sem_turma:
        query = query.filter(Aluno.turma_id.is_(None))
        if not situacao:
            query = query.filter(Aluno.situacao.in_(('ativo', 'cursando')))
    alunos = query.distinct().order_by(Aluno.nome).all()
    return ok([_aluno_busca_item(a) for a in alunos])


# --- ESCOLAS ---
@api_bp.route('/api/escolas', methods=['POST'])
@login_required
def api_criar_escola():
    if not authz.pode_gerir_estrutura_municipal(current_user):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    if not d.get('nome') or not d.get('inep'): return err('Preencha todos os campos')
    if Escola.query.filter_by(inep=d['inep']).first(): return err('INEP existe')
    
    inf = d.get('oferta_infantil', True); fund = d.get('oferta_fundamental', True)

    n = Escola(nome=d['nome'], inep=d['inep'], oferta_infantil=inf, oferta_fundamental=fund)
    db.session.add(n)
    db.session.flush()
    gestor, senha = _usuario_primeiro_acesso(
        d['inep'], 'Diretor(a)', 'gestor_escolar', n.id,
    )
    db.session.add(gestor)
    db.session.flush()
    from src.auditoria import ESCOLA_CRIADA, auditar_usuario_criado, registrar_auditoria, snapshot_escola
    registrar_auditoria(
        ESCOLA_CRIADA,
        "Escola",
        entidade_id=n.id,
        escola_id=n.id,
        dados_novos=snapshot_escola(n),
    )
    auditar_usuario_criado(gestor, senha_temporaria_gerada=True)
    db.session.commit()
    return _ok_conta_criada('Escola criada!', senha, extra={'id': n.id, 'login': d['inep']})

@api_bp.route('/api/escolas/<int:id>', methods=['PUT'])
@login_required
def api_editar_escola(id):
    if not authz.pode_gerir_estrutura_municipal(current_user):
        return authz.recusa()
    escola = db.session.get(Escola, id)
    if not escola: return err('Escola não encontrada', 404)
    
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    from src.auditoria import ESCOLA_ALTERADA, registrar_alteracao, snapshot_escola
    anterior = snapshot_escola(escola)
    if 'oferta_infantil' in d: escola.oferta_infantil = d['oferta_infantil']
    if 'oferta_fundamental' in d: escola.oferta_fundamental = d['oferta_fundamental']
    registrar_alteracao(
        ESCOLA_ALTERADA,
        "Escola",
        entidade_id=escola.id,
        escola_id=escola.id,
        anterior=anterior,
        novo=snapshot_escola(escola),
        identidade={"escola_id": escola.id},
    )
    db.session.commit()
    return ok({'mensagem': 'Configurações atualizadas!'})

@api_bp.route('/api/configuracao-academica', methods=['GET'])
@login_required
def api_get_configuracao_academica():
    cfg = garantir_configuracao_padrao()
    return ok(payload_configuracao(cfg))

@api_bp.route('/api/configuracao-academica', methods=['PUT'])
@login_required
def api_put_configuracao_academica():
    if not authz.pode_alterar_configuracao_academica(current_user):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    senha = d.get('senhaConfirmacao') or d.get('senha_confirmacao')
    if not verificar_senha(current_user.senha_hash, senha):
        return err('Confirme sua senha para aplicar a configuração.')
    bruto = d.get('media_minima', d.get('mediaMinima'))
    media, erro_nota = validar_nota_0_a_10(bruto)
    if erro_nota:
        return err('Média mínima inválida. Informe um número de 0 a 10.')
    cfg = garantir_configuracao_padrao()
    from src.auditoria import CONFIGURACAO_ACADEMICA_ALTERADA, registrar_auditoria, valores_iguais
    anterior = cfg.media_minima
    if not valores_iguais(anterior, media):
        cfg.media_minima = media
        registrar_auditoria(
            CONFIGURACAO_ACADEMICA_ALTERADA,
            "ConfiguracaoAcademica",
            entidade_id=cfg.id,
            dados_anteriores={
                "configuracao_id": cfg.id,
                "parametro": "media_minima",
                "valor": anterior,
                "escopo": "rede",
            },
            dados_novos={
                "configuracao_id": cfg.id,
                "parametro": "media_minima",
                "valor": media,
                "escopo": "rede",
            },
        )
    db.session.commit()
    return ok({**payload_configuracao(cfg), 'mensagem': 'Configuração acadêmica atualizada.'})

# --- TURMAS ---
@api_bp.route('/api/turmas', methods=['POST'])
@login_required
def api_criar_turma():
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    if not d.get('nome') or not d.get('ano_letivo'): return err('Nome e Ano são obrigatórios')
    if not authz.pode_criar_turma(current_user):
        return authz.recusa()
    
    etapa = d.get('etapa', 'fundamental')
    capacidade, erro_cap = parse_capacidade(d.get('capacidade'))
    if erro_cap:
        return err(erro_cap)
    turma = Turma(
        nome=d['nome'],
        ano_letivo=d['ano_letivo'],
        escola_id=current_user.escola_id,
        etapa_ensino=etapa,
        capacidade=capacidade,
    )
    mensagem = aplicar_jornada_turma(turma, d)
    if mensagem:
        return err(mensagem)
    db.session.add(turma)
    db.session.flush()
    from src.auditoria import TURMA_CRIADA, registrar_auditoria, snapshot_turma
    registrar_auditoria(
        TURMA_CRIADA,
        "Turma",
        entidade_id=turma.id,
        escola_id=turma.escola_id,
        dados_novos=snapshot_turma(turma),
    )
    db.session.commit()
    return ok({'mensagem': 'Turma criada!'})

@api_bp.route('/api/turmas/<int:id>/encerrar', methods=['POST'])
@login_required
def encerrar_turma(id):
    if current_user.papel != 'gestor_escolar': return err('Sem permissão', 403)
    turma = db.session.get(Turma, id)
    if not turma: return err('Turma não encontrada', 404)
    if not authz.pode_encerrar_turma(current_user, turma):
        return authz.recusa()

    try:
        resultado = encerrar_turma_por_matriculas(turma)
        db.session.commit()
    except Exception:
        db.session.rollback()
        return err(MENSAGEM_ERRO_INTERNO, 500)
    if resultado == RESULTADO_JA_ENCERRADA:
        return ok({'mensagem': 'Ano já encerrado e arquivado.'})
    return ok({'mensagem': 'Ano encerrado e arquivado.'})

# --- NOTAS E PARECERES ---
@api_bp.route('/api/turmas/<int:turma_id>/disciplinas/<int:disciplina_id>/notas', methods=['POST'])
@login_required
def salvar_notas(turma_id, disciplina_id):
    turma = db.session.get(Turma, turma_id)
    if not turma:
        return err('Turma não encontrada', 404)
    dados = request.get_json(silent=True)
    if not isinstance(dados, list):
        return err('Envie uma lista de lançamentos em JSON')
    if not authz.pode_lancar_notas(current_user, turma, disciplina_id):
        return authz.recusa()

    if turma.etapa_ensino == 'fundamental':
        disciplina = db.session.get(Disciplina, disciplina_id)
        if not disciplina:
            return err('Disciplina não encontrada', 404)
        lancamentos = []
        for item in dados:
            if not isinstance(item, dict):
                return err('Cada lançamento deve ser um objeto JSON')
            if item.get('aluno_id') is None or not item.get('tipo') or item.get('bimestre') is None:
                return err('Campos obrigatórios ausentes no lançamento (aluno_id, valor, tipo, bimestre)')
            if 'valor' not in item:
                return err('Campos obrigatórios ausentes no lançamento (aluno_id, valor, tipo, bimestre)')
            aluno_item = db.session.get(Aluno, item['aluno_id'])
            if not aluno_item:
                return err('Aluno não encontrado', 404)
            try:
                int(item['bimestre'])
            except (TypeError, ValueError):
                return err('Bimestre inválido')
            if item.get('tipo') == 'status':
                continue
            bruto = item.get('valor')
            if bruto is not None and str(bruto).strip().upper() == 'T':
                continue
            mat = matricula_cursando_na_turma(aluno_item.id, turma.id)
            if mat is None:
                db.session.rollback()
                return err(MSG_SEM_MATRICULA_CURSANDO)
            if item.get('tipo') == 'falta':
                try:
                    qtd = int(float(str(item['valor']).replace(',', '.')))
                except (TypeError, ValueError):
                    return err('Quantidade de faltas inválida')
                if qtd < 0:
                    return err('Faltas não podem ser negativas')
                lancamentos.append((mat, 'falta', int(item['bimestre']), qtd))
                continue
            if item.get('tipo') != 'nota':
                continue
            if bruto is None or (isinstance(bruto, str) and bruto.strip() == ''):
                lancamentos.append((mat, 'nota', int(item['bimestre']), None))
                continue
            val, erro_nota = validar_nota_0_a_10(item['valor'])
            if erro_nota:
                db.session.rollback()
                return err(erro_nota)
            lancamentos.append((mat, 'nota', int(item['bimestre']), val))
        try:
            for mat, tipo, bimestre, val in lancamentos:
                if tipo == 'nota' and val is None:
                    remover_nota_matricula(mat, disciplina_id, bimestre)
                elif tipo == 'nota':
                    upsert_nota_matricula(mat, disciplina_id, bimestre, val)
                elif tipo == 'falta':
                    upsert_falta_matricula(mat, disciplina_id, bimestre, int(val))
        except ValueError as exc:
            db.session.rollback()
            return err(str(exc))
        except IntegrityError:
            db.session.rollback()
            return err(MSG_INTEGRIDADE_DUPLICADA, HTTP_CONFLITO)

    elif turma.etapa_ensino == 'infantil':
        lancamentos = []
        for item in dados:
            if not isinstance(item, dict):
                db.session.rollback()
                return err('Cada lançamento deve ser um objeto JSON')
            if item.get('aluno_id') is None or not item.get('eixo') or item.get('bimestre') is None:
                db.session.rollback()
                return err('Campos obrigatórios ausentes no parecer (aluno_id, eixo, bimestre)')
            if 'avaliacao' not in item:
                db.session.rollback()
                return err('Campos obrigatórios ausentes no parecer (aluno_id, eixo, bimestre, avaliacao)')
            aluno_item = db.session.get(Aluno, item['aluno_id'])
            if not aluno_item:
                db.session.rollback()
                return err('Aluno não encontrado', 404)
            try:
                bimestre = int(item['bimestre'])
            except (TypeError, ValueError):
                db.session.rollback()
                return err('Bimestre inválido')
            try:
                mat = resolver_matricula_parecer_turma(aluno_item.id, turma)
                garantir_aluno_da_matricula(aluno_item.id, mat)
            except ValueError as exc:
                db.session.rollback()
                return err(str(exc))
            # avaliacao null/"" = limpeza explícita desta célula.
            # Aluno/eixo/bimestre ausente da lista não é excluído.
            lancamentos.append((
                mat,
                item['eixo'],
                bimestre,
                item.get('avaliacao'),
                item.get('observacao', ''),
            ))
        try:
            for mat, eixo, bimestre, avaliacao, observacao in lancamentos:
                aplicar_parecer_matricula(mat, eixo, bimestre, avaliacao, observacao)
        except ValueError as exc:
            db.session.rollback()
            return err(str(exc))
        except IntegrityError:
            db.session.rollback()
            return err(MSG_INTEGRIDADE_DUPLICADA, HTTP_CONFLITO)

    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return err(MSG_INTEGRIDADE_DUPLICADA, HTTP_CONFLITO)
    return ok({'mensagem': 'Salvo!'})

# --- MONITORAMENTO (RETORNA DADOS PARA O DIÁRIO) ---
@api_bp.route('/api/monitoramento/turma/<int:turma_id>/disciplina/<int:disciplina_id>')
@login_required
def api_monitoramento(turma_id, disciplina_id):
    turma = db.session.get(Turma, turma_id)
    if not turma:
        return err('Turma não encontrada', 404)
    if not authz.pode_ver_diario(current_user, turma, disciplina_id):
        return authz.recusa()
    res = []
    
    if turma.etapa_ensino == 'infantil':
        for m in matriculas_da_turma(turma.id):
            a = m.aluno
            if a is None:
                continue
            rels = pareceres_da_matricula(m)
            res.append({
                'id': a.id, 'situacao': a.situacao,
                'matricula_id': m.id,
                'pareceres': [payload_parecer(r) for r in rels],
            })
    else:
        for m in matriculas_da_turma(turma.id):
            a = m.aluno
            if a is None:
                continue
            n = RegistroNota.query.filter_by(matricula_id=m.id, disciplina_id=disciplina_id).all()
            f = RegistroFalta.query.filter_by(matricula_id=m.id, disciplina_id=disciplina_id).all()
            resumo = calcular_notas_matricula(m.id, disciplina_id)
            res.append({
                'id': a.id, 'situacao': a.situacao,
                'matricula_id': m.id,
                'notas': [{'bimestre': x.bimestre, 'valor': x.valor} for x in n],
                'faltas': [{'bimestre': x.bimestre, 'quantidade': x.quantidade} for x in f],
                'resumo': payload_resumo_monitoramento(resumo),
            })
            
    return ok(res)


def _payload_chamada(turma, disciplina, disc_store, data, bimestre, pode_editar):
    registros = {r.aluno_id: r for r in query_chamada(turma.id, disc_store, data=data).all()}
    alunos = []
    presentes = 0
    faltosos = 0
    for aluno in sorted(alunos_ativos_da_turma(turma), key=lambda a: (a.nome or "").lower()):
        reg = registros.get(aluno.id)
        situacao = (reg.situacao if reg else PRESENTE)
        if situacao == FALTA:
            faltosos += 1
        else:
            presentes += 1
        alunos.append({"id": aluno.id, "nome": aluno.nome, "situacao": situacao})
    return {
        "data": data.isoformat(),
        "bimestre": bimestre,
        "turmaId": turma.id,
        "turmaNome": turma.nome,
        "disciplinaId": disc_store,
        "disciplinaNome": disciplina.nome if disciplina else ("Educação Infantil" if turma.etapa_ensino == "infantil" else None),
        "salva": bool(registros),
        "presentes": presentes,
        "faltosos": faltosos,
        "podeEditar": pode_editar,
        "alunos": alunos,
    }


@api_bp.route("/api/turmas/<int:turma_id>/disciplinas/<int:disciplina_id>/chamada", methods=["GET", "POST"])
@login_required
def api_chamada(turma_id, disciplina_id):
    turma = db.session.get(Turma, turma_id)
    if not turma:
        return err("Turma não encontrada", 404)
    if turma.etapa_ensino != "infantil":
        if not disciplina_id:
            return err("Informe a disciplina")
        disciplina = db.session.get(Disciplina, disciplina_id)
        if not disciplina:
            return err("Disciplina não encontrada", 404)
    else:
        disciplina = db.session.get(Disciplina, disciplina_id) if disciplina_id else None

    if request.method == "GET":
        if not authz.pode_ver_chamada(current_user, turma, disciplina_id or None):
            return authz.recusa()
        data = parse_data_chamada(request.args.get("data"))
        bimestre = normalizar_bimestre(request.args.get("bimestre") or 1)
        if not data:
            return err("Informe a data da aula (AAAA-MM-DD)")
        if not bimestre:
            return err("Informe o bimestre (1 a 4)")
        disc_store = disciplina_armazenada(turma, disciplina_id)
        pode_editar = authz.pode_lancar_chamada(current_user, turma, disciplina_id or None)
        return ok(_payload_chamada(turma, disciplina, disc_store, data, bimestre, pode_editar))

    if not authz.pode_lancar_chamada(current_user, turma, disciplina_id or None):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err("JSON inválido ou ausente")
    data = parse_data_chamada(d.get("data"))
    bimestre = normalizar_bimestre(d.get("bimestre"))
    if not data:
        return err("Informe a data da aula (AAAA-MM-DD)")
    if not bimestre:
        return err("Informe o bimestre (1 a 4)")
    lancamentos = d.get("lancamentos")
    if not isinstance(lancamentos, list) or not lancamentos:
        return err("Envie a lista de alunos da chamada")

    ativos = {a.id: a for a in alunos_ativos_da_turma(turma)}
    disc_store = disciplina_armazenada(turma, disciplina_id)
    professor = getattr(current_user, "professor", None)
    aloc = alocacao_do_professor(professor.id, turma, disc_store) if professor else None

    vistos = set()
    normalizados = []
    for item in lancamentos:
        if not isinstance(item, dict):
            return err("Cada lançamento deve ser um objeto JSON")
        try:
            aluno_id = int(item.get("aluno_id"))
        except (TypeError, ValueError):
            return err("Aluno inválido na chamada")
        if aluno_id in vistos:
            continue
        vistos.add(aluno_id)
        aluno = ativos.get(aluno_id)
        if not aluno:
            return err("Aluno inativo ou fora desta turma não entra na chamada")
        situacao = normalizar_situacao(item.get("situacao"))
        if not situacao:
            return err("Situação inválida. Use PRESENTE ou FALTA.")
        normalizados.append((aluno, situacao))

    pares = []
    for aluno, situacao in normalizados:
        mat = resolver_matricula_da_chamada(aluno.id, turma.id)
        if mat is None:
            db.session.rollback()
            return err(MSG_SEM_MATRICULA_CURSANDO)
        pares.append((aluno, mat, situacao))

    try:
        for aluno, mat, situacao in pares:
            aplicar_frequencia(
                mat,
                disc_store,
                data,
                bimestre,
                situacao,
                professor_id=professor.id if professor else None,
                alocacao_id=aloc.id if aloc else None,
                aluno_id=aluno.id,
                turma_id=turma.id,
            )
        db.session.commit()
    except ValueError as exc:
        db.session.rollback()
        return err(str(exc))
    except IntegrityError:
        db.session.rollback()
        return err(MSG_INTEGRIDADE_DUPLICADA, HTTP_CONFLITO)
    except Exception:
        db.session.rollback()
        raise
    pode_editar = authz.pode_lancar_chamada(current_user, turma, disciplina_id or None)
    payload = _payload_chamada(turma, disciplina, disc_store, data, bimestre, pode_editar)
    payload["mensagem"] = "Chamada salva."
    return ok(payload)


def _resolver_turma_disciplina(turma_id, disciplina_id):
    turma = db.session.get(Turma, turma_id)
    if not turma:
        return None, None, err("Turma não encontrada", 404)
    if turma.etapa_ensino != "infantil":
        if not disciplina_id:
            return None, None, err("Informe a disciplina")
        disciplina = db.session.get(Disciplina, disciplina_id)
        if not disciplina:
            return None, None, err("Disciplina não encontrada", 404)
    else:
        disciplina = db.session.get(Disciplina, disciplina_id) if disciplina_id else None
    return turma, disciplina, None


@api_bp.route("/api/turmas/<int:turma_id>/disciplinas/<int:disciplina_id>/registros-aula", methods=["GET", "POST"])
@login_required
def api_registros_aula(turma_id, disciplina_id):
    turma, disciplina, erro_res = _resolver_turma_disciplina(turma_id, disciplina_id)
    if erro_res:
        return erro_res
    disc_store = disciplina_armazenada(turma, disciplina_id)

    if request.method == "GET":
        if not authz.pode_ver_diario(current_user, turma, disciplina_id or None):
            return authz.recusa()
        alunos, presentes, faltosos = alunos_formulario(turma)
        registros = [payload_registro(a) for a in query_registros(turma.id, disc_store).all()]
        return ok({
            "turmaId": turma.id,
            "turmaNome": turma.nome,
            "disciplinaId": disc_store,
            "disciplinaNome": disciplina.nome if disciplina else (
                "Educação Infantil" if turma.etapa_ensino == "infantil" else None
            ),
            "podeRegistrar": authz.pode_registrar_aula(current_user, turma, disciplina_id or None),
            "alunos": alunos,
            "presentes": presentes,
            "faltosos": faltosos,
            "registros": registros,
        })

    if not authz.pode_registrar_aula(current_user, turma, disciplina_id or None):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err("JSON inválido ou ausente")
    aula, mensagem, status = criar_registro_aula(current_user, turma, disciplina_id, d)
    if mensagem:
        return err(mensagem, status)
    payload = payload_registro(aula, incluir_frequencia=True)
    payload["mensagem"] = "Aula e frequência registradas."
    return ok(payload)


@api_bp.route("/api/registros-aula/<int:aula_id>", methods=["GET"])
@login_required
def api_registro_aula_detalhe(aula_id):
    aula = db.session.get(RegistroAula, aula_id)
    if not aula:
        return err("Registro de aula não encontrado", 404)
    if not authz.pode_ver_registro_aula(current_user, aula):
        return authz.recusa()
    return ok(payload_registro(aula, incluir_frequencia=True))


@api_bp.route("/api/coordenacao/registros-aula/<int:aula_id>", methods=["POST"])
@login_required
def api_avaliar_registro_aula(aula_id):
    aula = db.session.get(RegistroAula, aula_id)
    if not aula:
        return err("Registro de aula não encontrado", 404)
    if not authz.pode_avaliar_registro_aula(current_user, aula):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err("JSON inválido ou ausente")
    aula, mensagem, status = avaliar_registro_aula(aula, current_user, d)
    if mensagem:
        return err(mensagem, status)
    payload = payload_registro(aula, incluir_frequencia=True)
    payload["mensagem"] = "Registro de aula atualizado."
    return ok(payload)


@api_bp.route("/api/coordenacao/registros-aula", methods=["GET"])
@login_required
def api_listar_registros_coordenacao():
    if not authz.coordenador(current_user) or not current_user.escola_id:
        return authz.recusa()
    registros = [payload_registro(a) for a in query_registros_escola(current_user.escola_id).all()]
    return ok({"registros": registros})


@api_bp.route("/api/acompanhamento-pedagogico", methods=["GET"])
@login_required
def api_acompanhamento_pedagogico():
    escola_id = request.args.get("escola_id", type=int)
    data_ref = parse_data(request.args.get("data"))
    if not authz.pode_ver_acompanhamento(current_user, escola_id):
        return authz.recusa()
    if authz.municipal(current_user):
        if escola_id:
            escola = db.session.get(Escola, escola_id)
            if not escola:
                return err("Escola não encontrada", 404)
            payload = acompanhamento_escola(escola_id, data_ref=data_ref)
            payload["escolaNome"] = escola.nome
            payload["escopo"] = "escola"
            return ok(payload)
        payload = acompanhamento_rede(data_ref=data_ref)
        payload["escopo"] = "rede"
        return ok(payload)
    payload = acompanhamento_escola(current_user.escola_id, data_ref=data_ref)
    payload["escolaNome"] = current_user.escola.nome if current_user.escola else None
    payload["escopo"] = "escola"
    return ok(payload)


# --- CADASTROS E OPERAÇÕES GERAIS ---

@api_bp.route('/api/professores', methods=['POST'])
@login_required
def api_criar_professor():
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    if not d.get('nome') or not d.get('cpf'):
        return err('Nome e CPF são obrigatórios')
    if authz.gestor(current_user):
        eid = current_user.escola_id
    elif authz.municipal(current_user):
        eid = d.get('escola_id')
    else:
        return authz.recusa()
    if not eid:
        return err('Informe a escola')
    if not db.session.get(Escola, eid):
        return err('Escola não encontrada', 404)
    if not authz.pode_criar_professor(current_user, eid):
        return authz.recusa()
    if Usuario.query.filter_by(login=d['cpf']).first():
        return err('CPF existe')
    u, senha = _usuario_primeiro_acesso(d['cpf'], d['nome'], 'professor', eid)
    db.session.add(u)
    db.session.flush()
    db.session.add(Professor(nome=d['nome'], cpf=d['cpf'], escola_id=eid, usuario_id=u.id))
    from src.auditoria import auditar_usuario_criado
    auditar_usuario_criado(u, senha_temporaria_gerada=True)
    db.session.commit()
    return _ok_conta_criada('Criado!', senha, extra={'login': d['cpf']})

@api_bp.route('/api/escolas/<int:escola_id>/coordenadores', methods=['POST'])
@login_required
def adicionar_coordenador(escola_id):
    if not db.session.get(Escola, escola_id):
        return err('Escola não encontrada', 404)
    if not authz.pode_criar_coordenador(current_user, escola_id):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    if not d.get('login') or not d.get('nome'):
        return err('Nome e login são obrigatórios')
    if Usuario.query.filter_by(login=d['login']).first():
        return err('Login existe')
    if Usuario.query.filter_by(escola_id=escola_id, papel='coordenador_pedagogico').count() >= 2:
        return err('Limite de 2 atingido')
    coord, senha = _usuario_primeiro_acesso(
        d['login'], d['nome'], 'coordenador_pedagogico', escola_id,
    )
    db.session.add(coord)
    from src.auditoria import auditar_usuario_criado
    auditar_usuario_criado(coord, senha_temporaria_gerada=True)
    db.session.commit()
    return _ok_conta_criada('Criado!', senha, extra={'login': d['login']})

@api_bp.route('/api/escolas/<int:escola_id>/diretor', methods=['POST'])
@login_required
def api_criar_diretor(escola_id):
    e = db.session.get(Escola, escola_id)
    if not e:
        return err('Escola não encontrada', 404)
    if not authz.pode_criar_diretor(current_user):
        return authz.recusa()
    if Usuario.query.filter_by(login=e.inep).first():
        return err('Diretor existe')
    diretor, senha = _usuario_primeiro_acesso(e.inep, 'Diretor(a)', 'gestor_escolar', e.id)
    db.session.add(diretor)
    from src.auditoria import auditar_usuario_criado
    auditar_usuario_criado(diretor, senha_temporaria_gerada=True)
    db.session.commit()
    return _ok_conta_criada('Criado!', senha, extra={'login': e.inep})

@api_bp.route('/api/disciplinas', methods=['POST'])
@login_required
def api_criar_disciplina():
    if not authz.pode_criar_disciplina(current_user):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    if not d.get('nome'):
        return err('Informe o nome da disciplina')
    if Disciplina.query.filter_by(nome=d['nome']).first():
        return err('Já existe')
    disc = Disciplina(nome=d['nome'])
    db.session.add(disc)
    db.session.flush()
    from src.auditoria import DISCIPLINA_CRIADA, registrar_auditoria, snapshot_disciplina
    registrar_auditoria(
        DISCIPLINA_CRIADA,
        "Disciplina",
        entidade_id=disc.id,
        dados_novos=snapshot_disciplina(disc),
    )
    db.session.commit()
    return ok({'mensagem': 'Criada!'})

@api_bp.route('/api/alunos', methods=['POST'])
@login_required
def matricular_aluno():
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    if not d.get('nome') or not d.get('cpf'):
        return err('Nome e CPF são obrigatórios')
    cpf = normalizar_cpf(d.get('cpf'))
    if not cpf_valido(cpf):
        return err('CPF inválido. Informe 11 dígitos.')
    if aluno_com_cpf(cpf):
        return err('Já existe um aluno com este CPF.')
    if not tem_responsavel(d):
        return err('Informe pelo menos um responsável legal (mãe ou pai).')
    turma = None
    if d.get('turma_id'):
        turma = db.session.get(Turma, d.get('turma_id'))
        if not turma:
            return err('Turma não encontrada', 404)
        if turma.status != 'ativa':
            return err('Só é possível enturmar em turma ativa.')
    if not authz.pode_matricular(current_user, turma):
        return authz.recusa()
    if turma and turma_lotada(turma):
        return err('Turma sem vagas. Capacidade máxima atingida.')
    escola_id = turma.escola_id if turma else current_user.escola_id
    novo_aluno = Aluno(
        situacao=SITUACAO_ATIVO,
        turma_id=turma.id if turma else None,
        escola_id=escola_id,
    )
    aplicar_ficha(novo_aluno, d)
    novo_aluno.cpf = cpf
    db.session.add(novo_aluno)
    if turma:
        db.session.flush()
        erro_mat = abrir_matricula_cursando(novo_aluno, turma)
        if erro_mat:
            db.session.rollback()
            codigo = HTTP_CONFLITO if erro_mat in (MSG_JA_CURSANDO, MSG_JA_NESTA_TURMA) else 400
            return err(erro_mat, codigo)
        from src.auditoria import MATRICULA_CRIADA
        _registrar_vinculo_aberto(MATRICULA_CRIADA, novo_aluno, turma)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return err(MSG_JA_NESTA_TURMA, HTTP_CONFLITO)
    return ok({'mensagem': 'Matriculado!', 'id': novo_aluno.id})


@api_bp.route('/api/alunos/<int:id>', methods=['GET'])
@login_required
def api_get_aluno(id):
    aluno = db.session.get(Aluno, id)
    if not aluno:
        return err('Aluno não encontrado', 404)
    if not authz.pode_ver_aluno(current_user, aluno):
        return authz.recusa()
    return ok(aluno_payload(aluno))


@api_bp.route('/api/alunos/<int:id>', methods=['PUT'])
@login_required
def api_editar_aluno(id):
    aluno = db.session.get(Aluno, id)
    if not aluno:
        return err('Aluno não encontrado', 404)
    if not authz.pode_gerir_aluno(current_user, aluno):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    if not d.get('nome') or not d.get('cpf'):
        return err('Nome e CPF são obrigatórios')
    cpf = normalizar_cpf(d.get('cpf'))
    if not cpf_valido(cpf):
        return err('CPF inválido. Informe 11 dígitos.')
    if aluno_com_cpf(cpf, ignorar_id=aluno.id):
        return err('Já existe um aluno com este CPF.')
    if not tem_responsavel(d, aluno):
        return err('Informe pelo menos um responsável legal (mãe ou pai).')
    aplicar_ficha(aluno, d)
    aluno.cpf = cpf
    db.session.commit()
    return ok({'mensagem': 'Dados do aluno atualizados.', 'id': aluno.id, **aluno_payload(aluno)})


@api_bp.route('/api/alunos/<int:id>/inativar', methods=['POST'])
@login_required
def api_inativar_aluno(id):
    aluno = db.session.get(Aluno, id)
    if not aluno:
        return err('Aluno não encontrado', 404)
    if not authz.pode_gerir_aluno(current_user, aluno):
        return authz.recusa()
    inativar_aluno(aluno)
    db.session.commit()
    return ok({'mensagem': MSG_ALUNO_INATIVADO, 'id': aluno.id})


@api_bp.route('/api/alunos/<int:id>/enturmar', methods=['POST'])
@login_required
def api_enturmar_aluno(id):
    aluno = db.session.get(Aluno, id)
    if not aluno:
        return err('Aluno não encontrado', 404)
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    turma = db.session.get(Turma, d.get('turma_id'))
    if not turma:
        return err('Turma não encontrada', 404)
    if not authz.pode_enturmar(current_user, aluno, turma):
        return authz.recusa()
    if not aluno_esta_ativo(aluno):
        return err('Somente aluno ativo pode ser enturmado.')
    if matricula_cursando(aluno):
        return err(MSG_JA_CURSANDO, HTTP_CONFLITO)
    if aluno.turma_id:
        return err('Este aluno já está em uma turma. Encerre ou remova o vínculo atual antes de uma nova enturmação.')
    if turma.status != 'ativa':
        return err('Só é possível enturmar em turma ativa.')
    if authz.escola_do_aluno(aluno) != turma.escola_id:
        return err('Aluno e turma devem pertencer à mesma escola.')
    if turma_lotada(turma):
        return err('Turma sem vagas. Capacidade máxima atingida.')
    from src.auditoria import ALUNO_ENTURMADO, ALUNO_REMATRICULADO
    vinculo_anterior = (
        Matricula.query.filter_by(aluno_id=aluno.id)
        .order_by(Matricula.id.desc())
        .first()
    )
    rematricula = vinculo_anterior is not None
    erro_mat = abrir_matricula_cursando(aluno, turma)
    if erro_mat:
        codigo = HTTP_CONFLITO if erro_mat in (MSG_JA_CURSANDO, MSG_JA_NESTA_TURMA) else 400
        return err(erro_mat, codigo)
    if not aluno.escola_id:
        aluno.escola_id = turma.escola_id
    _registrar_vinculo_aberto(
        ALUNO_REMATRICULADO if rematricula else ALUNO_ENTURMADO,
        aluno,
        turma,
        situacao_anterior=vinculo_anterior.situacao if vinculo_anterior else None,
        turma_id_anterior=vinculo_anterior.turma_id if vinculo_anterior else None,
        matricula_id_anterior=vinculo_anterior.id if vinculo_anterior else None,
    )
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return err(MSG_JA_NESTA_TURMA, HTTP_CONFLITO)
    return ok({'mensagem': 'Aluno enturmado.', 'id': aluno.id, 'turmaId': turma.id})


@api_bp.route('/api/alunos/<int:id>/matriculas', methods=['GET'])
@login_required
def api_historico_matriculas_aluno(id):
    aluno = db.session.get(Aluno, id)
    if not aluno:
        return err('Aluno não encontrado', 404)
    if not authz.pode_consultar_historico_matricula(current_user, aluno):
        return authz.recusa()
    return ok(listar_historico_matriculas(aluno))


@api_bp.route('/api/alunos/<int:id>/historicos', methods=['GET'])
@login_required
def api_historicos_anuais_aluno(id):
    aluno = db.session.get(Aluno, id)
    if not aluno:
        return err('Aluno não encontrado', 404)
    itens, autorizado = historicos_visiveis_do_aluno(current_user, aluno)
    if not autorizado:
        return authz.recusa()
    return ok(itens)

@api_bp.route('/api/alocacoes', methods=['POST'])
@login_required
def alocar_professor():
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    if not d.get('turma_id') or not d.get('professor_id'):
        return err('Professor e turma são obrigatórios')
    turma = db.session.get(Turma, d['turma_id'])
    if not turma:
        return err('Turma não encontrada', 404)
    prof = db.session.get(Professor, d['professor_id'])
    if not prof:
        return err('Professor não encontrado', 404)
    infantil = turma.etapa_ensino == 'infantil'
    disc_id = d.get('disciplina_id', d.get('disciplinaId'))
    if not infantil and not disc_id:
        return err('Professor, turma e disciplina são obrigatórios')
    if disc_id in (None, '', 0, '0'):
        disc_id = None
    else:
        try:
            disc_id = int(disc_id)
        except (TypeError, ValueError):
            return err('Disciplina não encontrada', 404)
        if not db.session.get(Disciplina, disc_id):
            return err('Disciplina não encontrada', 404)
    if not authz.pode_alocar(current_user, turma, prof):
        return authz.recusa()
    if disc_id is None:
        ex = Alocacao.query.filter_by(
            turma_id=d['turma_id'], disciplina_id=None, professor_id=d['professor_id'],
        ).first()
        if not ex:
            ex = Alocacao.query.filter_by(turma_id=d['turma_id'], disciplina_id=None).first()
    else:
        ex = Alocacao.query.filter_by(turma_id=d['turma_id'], disciplina_id=disc_id).first()
    from src.auditoria import (
        ALOCACAO_ALTERADA,
        ALOCACAO_CRIADA,
        registrar_alteracao,
        registrar_auditoria,
        snapshot_alocacao,
    )
    criando = ex is None
    if ex:
        anterior = snapshot_alocacao(ex)
        ex.professor_id = d['professor_id']
        alvo = ex
    else:
        anterior = None
        alvo = Alocacao(
            professor_id=d['professor_id'],
            turma_id=d['turma_id'],
            disciplina_id=disc_id,
        )
        db.session.add(alvo)
        db.session.flush()
    mensagem = aplicar_campos_carga(alvo, d)
    if mensagem:
        return err(mensagem)
    db.session.flush()
    escola_id = turma.escola_id
    if criando:
        registrar_auditoria(
            ALOCACAO_CRIADA,
            "Alocacao",
            entidade_id=alvo.id,
            escola_id=escola_id,
            dados_novos=snapshot_alocacao(alvo),
        )
    else:
        registrar_alteracao(
            ALOCACAO_ALTERADA,
            "Alocacao",
            entidade_id=alvo.id,
            escola_id=escola_id,
            anterior=anterior,
            novo=snapshot_alocacao(alvo),
            identidade={
                "alocacao_id": alvo.id,
                "professor_id": alvo.professor_id,
                "turma_id": alvo.turma_id,
                "disciplina_id": alvo.disciplina_id,
            },
        )
    db.session.commit()
    return ok({'mensagem': 'Alocado!'})

@api_bp.route('/api/excluir/<tipo>/<int:id>', methods=['DELETE'])
@login_required
def excluir_item(tipo, id):
    if tipo == 'aviso':
        item = db.session.get(Aviso, id)
        if not item:
            return err('Aviso não encontrado', 404)
        if not authz.pode_excluir_aviso(current_user, item):
            return authz.recusa()
        db.session.delete(item)
        db.session.commit()
        return ok({'mensagem': 'Excluído'})

    model_map = {'turma': Turma, 'aluno': Aluno, 'professor': Professor, 'escola': Escola, 'usuario': Usuario, 'disciplina': Disciplina}
    model = model_map.get(tipo)
    if not model:
        return err('Tipo inválido')
    item = db.session.get(model, id)
    if not item:
        return err('Item não encontrado', 404)

    if tipo == 'escola':
        if not authz.pode_gerir_estrutura_municipal(current_user):
            return authz.recusa()
    elif tipo == 'turma':
        if not authz.pode_excluir_turma(current_user, item):
            return authz.recusa()
    elif tipo == 'aluno':
        if not authz.pode_gerir_aluno(current_user, item):
            return authz.recusa()
    elif tipo == 'professor':
        if not authz.pode_gerir_professor(current_user, item):
            return authz.recusa()
    elif tipo == 'usuario':
        if not authz.pode_gerir_usuario(current_user, item):
            return authz.recusa()
    elif tipo == 'disciplina':
        if not authz.pode_criar_disciplina(current_user):
            return authz.recusa()
    else:
        return err('Tipo inválido')

    if tipo == 'aluno':
        inativar_aluno(item)
        db.session.commit()
        return ok({'mensagem': MSG_ALUNO_INATIVADO, 'id': item.id})

    motivo = motivo_bloqueio_exclusao(tipo, item)
    if motivo:
        return err(motivo, HTTP_CONFLITO)

    try:
        from src.auditoria import (
            DISCIPLINA_REMOVIDA,
            ESCOLA_REMOVIDA,
            TURMA_REMOVIDA,
            auditar_usuario_removido,
            registrar_auditoria,
            snapshot_disciplina,
            snapshot_escola,
            snapshot_turma,
        )
        if tipo == 'escola':
            for usuario_escola in list(item.usuarios or []):
                auditar_usuario_removido(usuario_escola)
            registrar_auditoria(
                ESCOLA_REMOVIDA,
                "Escola",
                entidade_id=item.id,
                escola_id=item.id,
                dados_anteriores=snapshot_escola(item),
            )
        elif tipo == 'turma':
            registrar_auditoria(
                TURMA_REMOVIDA,
                "Turma",
                entidade_id=item.id,
                escola_id=item.escola_id,
                dados_anteriores=snapshot_turma(item),
            )
        elif tipo == 'disciplina':
            registrar_auditoria(
                DISCIPLINA_REMOVIDA,
                "Disciplina",
                entidade_id=item.id,
                dados_anteriores=snapshot_disciplina(item),
            )
        elif tipo == 'usuario':
            auditar_usuario_removido(item)
        elif tipo == 'professor' and item.usuario:
            auditar_usuario_removido(item.usuario)

        if tipo == 'turma':
            turma = item
            for aluno in list(turma.alunos):
                if not aluno.escola_id:
                    aluno.escola_id = turma.escola_id
                aluno.turma_id = None
            db.session.flush()
        if tipo == 'professor' and item.usuario:
            db.session.delete(item.usuario)
        db.session.delete(item)
        db.session.commit()
        return ok({'mensagem': 'Excluído'})
    except IntegrityError:
        db.session.rollback()
        return err(MSG_GENERICO, HTTP_CONFLITO)
    except Exception:
        db.session.rollback()
        return err('Erro interno ao excluir.', 500)


@api_bp.route('/api/editar/<tipo>/<int:id>', methods=['PUT'])
@login_required
def editar_item(tipo, id):
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    if tipo == 'usuario':
        i = db.session.get(Usuario, id)
        if not i:
            return err('Usuário não encontrado', 404)
        if not authz.pode_gerir_usuario(current_user, i):
            return authz.recusa()
        if not d.get('login'):
            return err('Informe o login')
        from src.auditoria import USUARIO_ALTERADO, registrar_alteracao, snapshot_usuario
        anterior = snapshot_usuario(i)
        i.login = d['login']
        i.nome = d.get('nome')
        registrar_alteracao(
            USUARIO_ALTERADO,
            "Usuario",
            entidade_id=i.id,
            escola_id=i.escola_id,
            anterior=anterior,
            novo=snapshot_usuario(i),
            identidade={"usuario_alvo_id": i.id},
        )
    elif tipo == 'professor':
        i = db.session.get(Professor, id)
        if not i:
            return err('Professor não encontrado', 404)
        if not authz.pode_gerir_professor(current_user, i):
            return authz.recusa()
        if not d.get('nome') or not d.get('cpf'):
            return err('Nome e CPF são obrigatórios')
        if not i.usuario:
            return err('Professor sem usuário vinculado')
        from src.auditoria import USUARIO_ALTERADO, registrar_alteracao, snapshot_usuario
        anterior = snapshot_usuario(i.usuario)
        i.nome = d['nome']
        i.cpf = d['cpf']
        i.usuario.login = d['cpf']
        registrar_alteracao(
            USUARIO_ALTERADO,
            "Usuario",
            entidade_id=i.usuario.id,
            escola_id=i.usuario.escola_id,
            anterior=anterior,
            novo=snapshot_usuario(i.usuario),
            identidade={"usuario_alvo_id": i.usuario.id},
        )
    else:
        return err('Tipo inválido')
    db.session.commit()
    return ok({'mensagem': 'Editado'})


@api_bp.route('/api/usuarios/<int:id>/resetar-senha', methods=['POST'])
@login_required
def api_resetar_senha(id):
    alvo = db.session.get(Usuario, id)
    if not alvo:
        return err('Usuário não encontrado', 404)
    if not authz.pode_resetar_senha(current_user, alvo):
        return authz.recusa()
    senha = gerar_senha_temporaria()
    alvo.senha_hash = hash_senha(senha)
    alvo.primeiro_acesso = True
    from src.passwords import limpar_falhas_login
    limpar_falhas_login(alvo.login)
    from src.auditoria import auditar_senha_resetada
    auditar_senha_resetada(alvo)
    db.session.commit()
    return _ok_conta_criada(
        'Senha redefinida. Informe a senha temporária ao usuário.',
        senha,
        extra={'id': alvo.id, 'login': alvo.login},
    )

@api_bp.route('/api/alunos/<int:id>/reverter-transferencia', methods=['POST'])
@login_required
def reverter_transf(id):
    a = db.session.get(Aluno, id)
    if not a:
        return err('Aluno não encontrado', 404)
    if not authz.pode_reverter_transferencia(current_user, a):
        return authz.recusa()
    a.situacao = 'ativo'
    db.session.commit()
    return ok({'mensagem': 'Revertido'})

@api_bp.route('/api/planejamento/enviar', methods=['POST'])
@api_bp.route('/planejamento/enviar', methods=['POST'])
@login_required
def enviar_plan():
    if not getattr(current_user, 'professor', None):
        return err('Somente professores podem enviar planejamento', 403)
    turma_id = request.form.get('turma_id')
    if not turma_id:
        return err('Informe a turma')
    turma = db.session.get(Turma, turma_id)
    if not turma:
        return err('Turma não encontrada', 404)
    if not authz.pode_enviar_plano(current_user, turma):
        return authz.recusa()
    conteudo = request.form.get('conteudo')
    if not conteudo:
        return err('Informe o conteúdo do planejamento')
    f = request.files.get('arquivo')
    anexo = None
    if f and (f.filename or '').strip():
        pasta = current_app.config['UPLOAD_FOLDER']
        limite = current_app.config.get('MAX_CONTENT_LENGTH')
        anexo, falha = salvar_anexo(f, pasta, limite=limite)
        if falha:
            return falha
    try:
        db.session.add(Planejamento(
            professor_id=current_user.professor.id,
            turma_id=turma.id,
            conteudo=conteudo,
            arquivo=anexo['fisico'] if anexo else None,
            arquivo_original=anexo['original'] if anexo else None,
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        if anexo:
            remover_silencioso(anexo.get('caminho'))
        current_app.logger.exception('Falha ao gravar planejamento')
        return err('Não foi possível salvar o planejamento.', 500)
    return ok({'mensagem': 'Enviado'})


@api_bp.route('/api/planejamentos/<int:id>/arquivo', methods=['GET'])
@login_required
def baixar_arquivo_plan(id):
    p = db.session.get(Planejamento, id)
    if not p:
        return err('Planejamento não encontrado', 404)
    if not authz.pode_baixar_arquivo_plano(current_user, p):
        return authz.recusa()
    if not p.arquivo:
        return err('Arquivo não encontrado', 404)
    pasta = current_app.config['UPLOAD_FOLDER']
    caminho = localizar_arquivo(pasta, p.arquivo)
    if not caminho:
        return err('Arquivo não encontrado', 404)
    ext = extensao_de(p.arquivo_original or p.arquivo) or extensao_de(p.arquivo)
    mimes = MIME_POR_EXTENSAO.get(ext or '', frozenset())
    mimetype = next(iter(mimes), 'application/octet-stream')
    nome_download = nome_original_sanitizado(p.arquivo_original or 'anexo', ext or 'bin')
    resp = send_file(
        caminho,
        as_attachment=True,
        download_name=nome_download,
        mimetype=mimetype,
        max_age=0,
    )
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    return resp

@api_bp.route('/api/planejamento/<int:id>/arquivar', methods=['POST'])
@login_required
def arq_plan(id):
    p = db.session.get(Planejamento, id)
    if not p:
        return err('Planejamento não encontrado', 404)
    if not authz.pode_arquivar_plano(current_user, p):
        return authz.recusa()
    p.arquivado = not p.arquivado
    db.session.commit()
    return ok({'ok': True, 'arquivado': p.arquivado})


@api_bp.route('/api/coordenacao/planejamento/<int:id>', methods=['POST'])
@api_bp.route('/coordenacao/planejamento/<int:id>', methods=['POST'])
@login_required
def aval_plan(id):
    p = db.session.get(Planejamento, id)
    if not p:
        return err('Planejamento não encontrado', 404)
    if not authz.pode_avaliar_plano(current_user, p):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    status = (d.get('status') or '').strip().lower()
    if status not in ('aprovado', 'revisar'):
        return err('Informe o status: aprovado ou revisar.')
    if status == 'revisar':
        feedback = (d.get('feedback') or '').strip()
        if not feedback:
            return err('Informe o feedback obrigatório para devolver o planejamento à revisão.')
        p.feedback_coordenacao = feedback
    elif d.get('feedback') is not None:
        p.feedback_coordenacao = (d.get('feedback') or '').strip() or None
    p.status = status
    db.session.commit()
    return ok({'ok': True, 'status': p.status})


@api_bp.route('/api/planejamento/<int:id>/reenviar', methods=['POST'])
@login_required
def reenviar_plan(id):
    p = db.session.get(Planejamento, id)
    if not p:
        return err('Planejamento não encontrado', 404)
    if not authz.pode_reenviar_plano(current_user, p):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    conteudo = (d.get('conteudo') or '').strip()
    if not conteudo:
        return err('Informe o conteúdo do planejamento')
    p.conteudo = conteudo
    p.status = 'pendente'
    p.data_envio = datetime.utcnow()
    db.session.commit()
    return ok({'mensagem': 'Planejamento reenviado para análise.', 'status': p.status})

@api_bp.route('/api/avisos', methods=['POST'])
@api_bp.route('/avisos/novo', methods=['POST'])
@login_required
def criar_aviso_api():
    if not authz.pode_criar_aviso(current_user):
        return authz.recusa()
    d = read_json_dict()
    if d is None:
        return err('JSON inválido ou ausente')
    if not d.get('titulo') or not d.get('conteudo'):
        return err('Título e conteúdo são obrigatórios')
    if authz.municipal(current_user):
        dest = d.get('escola_id') if d.get('escola_id') != 'todos' else None
        try:
            dest_id = int(dest) if dest else None
        except (TypeError, ValueError):
            return err('Escola de destino inválida')
    else:
        dest_id = current_user.escola_id
    db.session.add(Aviso(
        titulo=d['titulo'],
        conteudo=d['conteudo'],
        autor_id=current_user.id,
        escola_destino_id=dest_id
    ))
    db.session.commit()
    return ok({'ok': True})
