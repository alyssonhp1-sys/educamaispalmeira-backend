from src.http import err
from src.alunos import aluno_esta_ativo
from src.models import Alocacao


def municipal(user):
    return user.papel in ("dirigente", "secretaria")


def gestor(user):
    return user.papel == "gestor_escolar"


def coordenador(user):
    return user.papel == "coordenador_pedagogico"


def professor(user):
    return user.papel == "professor"


def recusa(mensagem="Acesso negado"):
    return err(mensagem, 403)


def escola_do_aluno(aluno):
    if aluno.escola_id:
        return aluno.escola_id
    if aluno.turma:
        return aluno.turma.escola_id
    return None


def alocado(user, turma_id, disciplina_id=None):
    if not professor(user) or not getattr(user, "professor", None):
        return False
    q = Alocacao.query.filter_by(professor_id=user.professor.id, turma_id=turma_id)
    if disciplina_id not in (None, 0):
        q = q.filter_by(disciplina_id=disciplina_id)
    return q.first() is not None


def ids_turmas_alocadas(user):
    if not professor(user) or not getattr(user, "professor", None):
        return []
    return list({a.turma_id for a in user.professor.alocacoes})


def pode_ver_turma(user, turma):
    if municipal(user):
        return True
    if user.papel in ("gestor_escolar", "coordenador_pedagogico"):
        return user.escola_id == turma.escola_id
    if professor(user):
        return alocado(user, turma.id)
    return False


def pode_ver_diario(user, turma, disciplina_id=None):
    if municipal(user):
        return True
    if user.papel in ("gestor_escolar", "coordenador_pedagogico"):
        return user.escola_id == turma.escola_id
    if professor(user):
        if turma.etapa_ensino == "infantil":
            return alocado(user, turma.id)
        return alocado(user, turma.id, disciplina_id)
    return False


def pode_lancar_notas(user, turma, disciplina_id=None):
    if not professor(user):
        return False
    if turma.status == "concluida":
        return False
    if turma.etapa_ensino == "infantil":
        return alocado(user, turma.id)
    return alocado(user, turma.id, disciplina_id)


def pode_ver_aluno(user, aluno):
    if municipal(user):
        return True
    eid = escola_do_aluno(aluno)
    if user.papel in ("gestor_escolar", "coordenador_pedagogico"):
        return eid is not None and user.escola_id == eid
    if professor(user):
        return bool(aluno.turma_id) and alocado(user, aluno.turma_id)
    return False


def pode_gerir_aluno(user, aluno):
    if municipal(user):
        return True
    if gestor(user):
        return escola_do_aluno(aluno) == user.escola_id
    return False


def pode_matricular(user, turma):
    if not gestor(user) or not user.escola_id:
        return False
    if turma:
        return user.escola_id == turma.escola_id
    return True


def pode_consultar_historico_matricula(user, aluno):
    """Histórico cadastral de vínculos. Não é acesso geral do professor pelo ID."""
    if not aluno:
        return False
    if municipal(user):
        return True
    eid = escola_do_aluno(aluno)
    if user.papel in ("gestor_escolar", "coordenador_pedagogico"):
        return eid is not None and user.escola_id == eid
    return False


def pode_enturmar(user, aluno, turma):
    if not aluno or not turma:
        return False
    if escola_do_aluno(aluno) != turma.escola_id:
        return False
    if municipal(user):
        return True
    return gestor(user) and user.escola_id == turma.escola_id


def pode_criar_turma(user):
    return gestor(user) and bool(user.escola_id)


def pode_encerrar_turma(user, turma):
    return gestor(user) and user.escola_id == turma.escola_id


def pode_excluir_turma(user, turma):
    if municipal(user):
        return True
    return gestor(user) and user.escola_id == turma.escola_id


def pode_gerir_estrutura_municipal(user):
    return municipal(user)


def pode_criar_professor(user, escola_id):
    if municipal(user):
        return True
    return gestor(user) and user.escola_id == escola_id


def pode_gerir_professor(user, prof):
    if municipal(user):
        return True
    return gestor(user) and user.escola_id == prof.escola_id


def pode_alocar(user, turma, prof):
    if turma.escola_id != prof.escola_id:
        return False
    if municipal(user):
        return True
    return gestor(user) and user.escola_id == turma.escola_id


def pode_criar_disciplina(user):
    return municipal(user) or gestor(user)


def pode_enviar_plano(user, turma):
    return professor(user) and alocado(user, turma.id)


def pode_avaliar_plano(user, plan):
    if not coordenador(user) or not plan.turma:
        return False
    return user.escola_id == plan.turma.escola_id


def pode_reenviar_plano(user, plan):
    if not professor(user) or not getattr(user, "professor", None):
        return False
    if plan.arquivado or plan.status != "revisar":
        return False
    return plan.professor_id == user.professor.id


def pode_arquivar_plano(user, plan):
    if professor(user) and getattr(user, "professor", None):
        return plan.professor_id == user.professor.id
    if coordenador(user) and plan.turma:
        return user.escola_id == plan.turma.escola_id
    return False


def pode_baixar_arquivo_plano(user, plan):
    """Mesmo escopo de quem já vê o planejamento no fluxo atual, mais gestor/municipal da escola/rede."""
    if not plan:
        return False
    if municipal(user):
        return True
    if professor(user) and getattr(user, "professor", None):
        return plan.professor_id == user.professor.id
    if not plan.turma:
        return False
    if coordenador(user) or gestor(user):
        return user.escola_id == plan.turma.escola_id
    return False


def pode_alterar_configuracao_academica(user):
    return municipal(user)


def pode_lancar_chamada(user, turma, disciplina_id=None):
    return pode_lancar_notas(user, turma, disciplina_id)


def pode_ver_chamada(user, turma, disciplina_id=None):
    return pode_ver_diario(user, turma, disciplina_id)


def pode_emitir_documento_administrativo(user, aluno):
    """Boletim individual, declarações e comprovante/ficha de matrícula."""
    if municipal(user):
        return True
    if gestor(user):
        eid = escola_do_aluno(aluno)
        return eid is not None and user.escola_id == eid
    return False


def pode_emitir_historico_escolar(user, aluno):
    """Histórico completo: dirigente/secretaria e gestor da escola do aluno.

    Professor não emite. Coordenador não emite. Recorte igual ao boletim administrativo.
    """
    if professor(user) or coordenador(user):
        return False
    return pode_emitir_documento_administrativo(user, aluno)


def pode_gerir_trajetoria_legada(user, aluno):
    """Cadastro de trajetória anterior ao Educa+. Somente administrativo.

    Dirigente/secretaria: rede. Gestor: alunos da própria escola.
    Professor e coordenador não editam histórico administrativo.
    """
    if professor(user) or coordenador(user):
        return False
    return pode_gerir_aluno(user, aluno)


def pode_emitir_parecer(user, aluno):
    if not aluno:
        return False
    eid = escola_do_aluno(aluno)
    infantil = bool(aluno.turma and aluno.turma.etapa_ensino == "infantil")
    if municipal(user):
        return True
    if gestor(user):
        return eid is not None and user.escola_id == eid
    if coordenador(user):
        return eid is not None and user.escola_id == eid
    if professor(user):
        if not infantil:
            return False
        if not aluno_esta_ativo(aluno):
            return False
        return alocado(user, aluno.turma_id)
    return False


def pode_ver_historico_anual(user, escola_id):
    """ACL pela escola da matrícula histórica (Matricula → Turma → Escola).

    escola_id deve vir de escola_id_para_acl(hist), nunca da escola/turma atuais do aluno.
    Professor não acessa histórico anual.
    """
    if municipal(user):
        return True
    if user.papel in ("gestor_escolar", "coordenador_pedagogico"):
        return escola_id is not None and user.escola_id == escola_id
    return False


def pode_emitir_boletim_historico(user, escola_id):
    """Mesmo recorte do boletim atual: municipal e gestor. Sem professor/coordenador."""
    if not pode_ver_historico_anual(user, escola_id):
        return False
    return municipal(user) or gestor(user)


def pode_emitir_parecer_historico(user, escola_id):
    """Parecer histórico: municipal, gestor e coordenador da escola congelada.

    Professor não é habilitado: a alocação atual não equivale a acesso a anos encerrados.
    """
    if professor(user):
        return False
    if not pode_ver_historico_anual(user, escola_id):
        return False
    return municipal(user) or gestor(user) or coordenador(user)


def pode_relatorio(user, turma, disciplina_id=None):
    return pode_ver_diario(user, turma, disciplina_id)


def pode_registrar_aula(user, turma, disciplina_id=None):
    return pode_lancar_chamada(user, turma, disciplina_id)


def pode_ver_registro_aula(user, aula):
    if not aula or not aula.turma:
        return False
    return pode_ver_diario(user, aula.turma, aula.disciplina_id)


def pode_avaliar_registro_aula(user, aula):
    if not coordenador(user) or not aula or not aula.turma:
        return False
    return user.escola_id == aula.turma.escola_id


def pode_ver_acompanhamento(user, escola_id=None):
    if municipal(user):
        return True
    if gestor(user) and user.escola_id:
        if escola_id is None:
            return True
        return user.escola_id == escola_id
    return False


def pode_consultar_calendario(user):
    return municipal(user) or gestor(user) or coordenador(user) or professor(user)


def pode_alterar_calendario(user):
    return municipal(user)


def pode_editar_turma(user, turma):
    if not turma:
        return False
    if municipal(user):
        return True
    return gestor(user) and user.escola_id == turma.escola_id


def pode_configurar_carga(user, turma=None, aloc=None):
    if municipal(user):
        return True
    alvo = turma
    if alvo is None and aloc is not None:
        alvo = aloc.turma
    if not gestor(user) or not user.escola_id or not alvo:
        return False
    return user.escola_id == alvo.escola_id


def pode_criar_coordenador(user, escola_id):
    if municipal(user):
        return True
    return gestor(user) and user.escola_id == escola_id


def pode_criar_diretor(user):
    return municipal(user)


def pode_gerir_usuario(user, alvo):
    if municipal(user):
        return True
    if not gestor(user) or not user.escola_id:
        return False
    if alvo.papel in ("dirigente", "secretaria"):
        return False
    return alvo.escola_id == user.escola_id


def pode_resetar_senha(user, alvo):
    """Reset administrativo. Professor/coordenador não resetam terceiros. Sem auto-reset."""
    if not user or not alvo:
        return False
    if getattr(user, "id", None) == getattr(alvo, "id", None):
        return False
    if professor(user) or coordenador(user):
        return False
    return pode_gerir_usuario(user, alvo)


def pode_reverter_transferencia(user, aluno):
    return pode_gerir_aluno(user, aluno)


def pode_criar_aviso(user):
    return municipal(user) or gestor(user)


def pode_excluir_aviso(user, aviso):
    if municipal(user):
        return True
    if aviso.autor_id == user.id:
        return True
    if gestor(user):
        return aviso.escola_destino_id in (None, user.escola_id)
    return False


def pode_registrar_falta_docente(user, escola_id=None):
    """Coordenador ou gestor da própria escola. Secretaria visualiza; não registra."""
    if not user or not user.escola_id:
        return False
    if not (coordenador(user) or gestor(user)):
        return False
    if escola_id is None:
        return True
    return user.escola_id == escola_id


def pode_cancelar_falta_docente(user, occ):
    if not occ:
        return False
    return pode_registrar_falta_docente(user, occ.escola_id)


def pode_ver_faltas_docentes(user, escola_id=None):
    """Consulta de ocorrências. Professor não acessa o recorte administrativo."""
    if municipal(user):
        return True
    if coordenador(user) or gestor(user):
        if not user.escola_id:
            return False
        if escola_id is None:
            return True
        return user.escola_id == escola_id
    return False


def pode_relatorio_faltas_docentes(user, escola_id=None):
    return pode_ver_faltas_docentes(user, escola_id)


def pode_consultar_auditoria(user):
    """Consulta somente leitura do AuditLog. Professor e coordenador não acessam."""
    if municipal(user):
        return True
    return gestor(user) and bool(user.escola_id)


def pode_ver_ip_auditoria(user):
    """IP só para dirigente/secretaria. Gestor escolar não recebe o campo."""
    return municipal(user)
