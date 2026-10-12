"""Proteção contra exclusão física de cadastros com histórico acadêmico.

Não substitui inativação de aluno nem encerramento de turma.
Operações de correção de lançamento (salvar notas/faltas/parecer) ficam
fora daqui: não são DELETE administrativo de cadastro.
"""
from src.models import (
    Alocacao,
    Aviso,
    Disciplina,
    Escola,
    HistoricoAnual,
    Matricula,
    OcorrenciaAulaNaoMinistrada,
    Planejamento,
    Professor,
    RegistroAula,
    RegistroFalta,
    RegistroFrequencia,
    RegistroNota,
    RelatorioDescritivo,
    TrajetoriaEscolarLegada,
    Turma,
    Usuario,
    alunos_da_escola,
)

HTTP_CONFLITO = 409

MSG_GENERICO = "Este registro possui dados acadêmicos vinculados e não pode ser excluído."
MSG_ESCOLA = "Esta escola possui dados vinculados e não pode ser excluída."
MSG_TURMA = (
    "Esta turma possui histórico acadêmico e não pode ser excluída. "
    "Utilize o encerramento."
)
MSG_PROFESSOR = (
    "Este professor possui registros de diário ou dados acadêmicos vinculados "
    "e não pode ser excluído."
)
MSG_USUARIO = "Esta conta possui dados acadêmicos vinculados e não pode ser excluída."
MSG_DISCIPLINA = "Esta disciplina possui dados acadêmicos vinculados e não pode ser excluída."


def _existe(query):
    return query.first() is not None


def matricula_possui_historico_academico(matricula):
    """Matrícula com nota, falta, frequência, parecer EI ou snapshot anual.

    Não existe endpoint DELETE de matrícula; este helper reconhece a
    relationship nova para proteção de histórico. Sem cascade delete.
    """
    if matricula is None or matricula.id is None:
        return False
    if _existe(RegistroNota.query.filter_by(matricula_id=matricula.id)):
        return True
    if _existe(RegistroFalta.query.filter_by(matricula_id=matricula.id)):
        return True
    if _existe(RegistroFrequencia.query.filter_by(matricula_id=matricula.id)):
        return True
    if _existe(RelatorioDescritivo.query.filter_by(matricula_id=matricula.id)):
        return True
    if _existe(HistoricoAnual.query.filter_by(matricula_id=matricula.id)):
        return True
    return False


def aluno_possui_registro_academico(aluno_id):
    if _existe(RegistroNota.query.filter_by(aluno_id=aluno_id)):
        return True
    if _existe(RegistroFalta.query.filter_by(aluno_id=aluno_id)):
        return True
    if _existe(RegistroFrequencia.query.filter_by(aluno_id=aluno_id)):
        return True
    mats = Matricula.query.filter_by(aluno_id=aluno_id).all()
    if any(matricula_possui_historico_academico(m) for m in mats):
        return True
    if _existe(HistoricoAnual.query.filter_by(aluno_id=aluno_id)):
        return True
    if _existe(RelatorioDescritivo.query.filter_by(aluno_id=aluno_id)):
        return True
    if _existe(TrajetoriaEscolarLegada.query.filter_by(aluno_id=aluno_id)):
        return True
    if mats:
        return True
    return False


def turma_possui_registro_academico(turma):
    if _existe(Matricula.query.filter_by(turma_id=turma.id)):
        return True
    if (turma.status or "").lower() == "concluida":
        return True
    if _existe(RegistroAula.query.filter_by(turma_id=turma.id)):
        return True
    if _existe(OcorrenciaAulaNaoMinistrada.query.filter_by(turma_id=turma.id)):
        return True
    if _existe(RegistroFrequencia.query.filter_by(turma_id=turma.id)):
        return True
    ids_mat = [m.id for m in Matricula.query.filter_by(turma_id=turma.id).all()]
    if ids_mat and _existe(RegistroFrequencia.query.filter(
        RegistroFrequencia.matricula_id.in_(ids_mat)
    )):
        return True
    if ids_mat and _existe(RelatorioDescritivo.query.filter(
        RelatorioDescritivo.matricula_id.in_(ids_mat)
    )):
        return True
    if ids_mat and _existe(HistoricoAnual.query.filter(
        HistoricoAnual.matricula_id.in_(ids_mat)
    )):
        return True
    if _existe(Planejamento.query.filter_by(turma_id=turma.id)):
        return True
    escola_nome = turma.escola.nome if turma.escola else None
    if _existe(HistoricoAnual.query.filter_by(
        ano_letivo=turma.ano_letivo,
        turma_nome=turma.nome,
        escola_nome=escola_nome,
    )):
        return True
    ids = [a.id for a in turma.alunos]
    if not ids:
        return False
    if _existe(RegistroNota.query.filter(RegistroNota.aluno_id.in_(ids))):
        return True
    if _existe(RegistroFalta.query.filter(RegistroFalta.aluno_id.in_(ids))):
        return True
    if _existe(RelatorioDescritivo.query.filter(RelatorioDescritivo.aluno_id.in_(ids))):
        return True
    return False


def escola_possui_vinculos(escola):
    if _existe(Turma.query.filter_by(escola_id=escola.id)):
        return True
    if _existe(Professor.query.filter_by(escola_id=escola.id)):
        return True
    if alunos_da_escola(escola.id):
        return True
    if _existe(HistoricoAnual.query.filter_by(escola_nome=escola.nome)):
        return True
    return False


def professor_possui_registro_academico(professor):
    if _existe(RegistroAula.query.filter_by(professor_id=professor.id)):
        return True
    if _existe(OcorrenciaAulaNaoMinistrada.query.filter_by(professor_id=professor.id)):
        return True
    if _existe(RegistroFrequencia.query.filter_by(professor_id=professor.id)):
        return True
    if _existe(Planejamento.query.filter_by(professor_id=professor.id)):
        return True
    return False


def usuario_possui_registro_academico(usuario):
    prof = getattr(usuario, "professor", None)
    if prof is not None and professor_possui_registro_academico(prof):
        return True
    if _existe(RegistroAula.query.filter_by(coordenador_id=usuario.id)):
        return True
    if _existe(OcorrenciaAulaNaoMinistrada.query.filter_by(registrado_por_id=usuario.id)):
        return True
    if _existe(OcorrenciaAulaNaoMinistrada.query.filter_by(cancelado_por_id=usuario.id)):
        return True
    if _existe(Aviso.query.filter_by(autor_id=usuario.id)):
        return True
    return False


def disciplina_possui_vinculos(disciplina):
    if _existe(RegistroNota.query.filter_by(disciplina_id=disciplina.id)):
        return True
    if _existe(RegistroFalta.query.filter_by(disciplina_id=disciplina.id)):
        return True
    if _existe(RegistroAula.query.filter_by(disciplina_id=disciplina.id)):
        return True
    if _existe(OcorrenciaAulaNaoMinistrada.query.filter_by(disciplina_id=disciplina.id)):
        return True
    if _existe(RegistroFrequencia.query.filter_by(disciplina_id=disciplina.id)):
        return True
    if _existe(Alocacao.query.filter_by(disciplina_id=disciplina.id)):
        return True
    return False


def motivo_bloqueio_exclusao(tipo, item):
    """Retorna mensagem se a exclusão física deve ser recusada; senão None."""
    if tipo == "escola" and isinstance(item, Escola) and escola_possui_vinculos(item):
        return MSG_ESCOLA
    if tipo == "turma" and isinstance(item, Turma) and turma_possui_registro_academico(item):
        return MSG_TURMA
    if tipo == "professor" and isinstance(item, Professor) and professor_possui_registro_academico(item):
        return MSG_PROFESSOR
    if tipo == "usuario" and isinstance(item, Usuario) and usuario_possui_registro_academico(item):
        return MSG_USUARIO
    if tipo == "disciplina" and isinstance(item, Disciplina) and disciplina_possui_vinculos(item):
        return MSG_DISCIPLINA
    return None
