from src import db, login_manager
from flask_login import UserMixin
from datetime import datetime
from sqlalchemy import Index, inspect, or_, text

# UNIQUE parciais: protegem lançamento moderno (matricula_id conhecido).
# SQLite e PostgreSQL: postgresql_where/sqlite_where expressam a mesma intenção.
# matricula_id NULL legado permanece permitido (fora do índice).
# disciplina_id NULL em UNIQUE: ver dívida C (NULLs distintos no PostgreSQL).
_WHERE_MATRICULA = text("matricula_id IS NOT NULL")
_WHERE_MATRICULA_AULA = text(
    "matricula_id IS NOT NULL AND registro_aula_id IS NOT NULL"
)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(Usuario, int(user_id))

class Usuario(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    login = db.Column(db.String(50), unique=True, nullable=False)
    nome = db.Column(db.String(150), nullable=True)
    senha_hash = db.Column(db.String(128), nullable=True, default=None)
    papel = db.Column(db.String(30), nullable=False)
    primeiro_acesso = db.Column(db.Boolean, default=True)
    escola_id = db.Column(db.Integer, db.ForeignKey('escola.id'), nullable=True)
    escola = db.relationship('Escola', backref=db.backref('usuarios', cascade="all, delete-orphan"))
    def get_id(self): return str(self.id)

class Escola(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    inep = db.Column(db.String(20), unique=True, nullable=False)
    
    # CONFIGURAÇÃO DE OFERTA (NOVO)
    oferta_infantil = db.Column(db.Boolean, default=True)
    oferta_fundamental = db.Column(db.Boolean, default=True)

class Professor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cpf = db.Column(db.String(20), unique=True, nullable=False)
    escola_id = db.Column(db.Integer, db.ForeignKey('escola.id'), nullable=False)
    escola = db.relationship('Escola', backref='professores_lista')
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), unique=True, nullable=False)
    usuario = db.relationship('Usuario', backref=db.backref('professor', uselist=False, cascade="all, delete-orphan"))

class Turma(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(50), nullable=False)
    ano_letivo = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(20), default='ativa') # ativa / concluida
    
    # NOVO: Define se é 'fundamental' (notas) ou 'infantil' (pareceres)
    etapa_ensino = db.Column(db.String(20), default='fundamental')
    
    escola_id = db.Column(db.Integer, db.ForeignKey('escola.id'), nullable=False)
    escola = db.relationship('Escola', backref=db.backref('turmas'))
    # NULL = sem limite (turmas já existentes). Valor positivo limita vagas de alunos ativos.
    capacidade = db.Column(db.Integer, nullable=True)
    # Educação Infantil: jornada e carga da turma (não por disciplina). Null = não configurado.
    tipo_jornada = db.Column(db.String(20), nullable=True)
    carga_horaria_diaria = db.Column(db.Float, nullable=True)
    carga_horaria_anual = db.Column(db.Float, nullable=True)

class Disciplina(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(80), nullable=False, unique=True)

class Alocacao(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    professor_id = db.Column(db.Integer, db.ForeignKey('professor.id'), nullable=False)
    turma_id = db.Column(db.Integer, db.ForeignKey('turma.id'), nullable=False)
    disciplina_id = db.Column(db.Integer, db.ForeignKey('disciplina.id'), nullable=True) # Pode ser null na infantil
    # Aulas previstas no ano para esta alocação. Null = ainda não informada (não inventar calendário).
    carga_horaria_prevista = db.Column(db.Integer, nullable=True)
    aulas_semanais = db.Column(db.Integer, nullable=True)
    duracao_aula_minutos = db.Column(db.Integer, nullable=True)
    professor = db.relationship('Professor', backref=db.backref('alocacoes', cascade="all, delete-orphan"))
    turma = db.relationship('Turma', backref=db.backref('alocacoes', cascade="all, delete-orphan"))
    disciplina = db.relationship('Disciplina')

class Aluno(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(150), nullable=False)
    cpf = db.Column(db.String(20), unique=True, nullable=False)
    
    # Dados Pessoais
    endereco = db.Column(db.String(200), nullable=True)
    telefone = db.Column(db.String(20), nullable=True)
    data_nascimento = db.Column(db.Date, nullable=True)
    local_nascimento = db.Column(db.String(100), nullable=True)
    nome_mae = db.Column(db.String(150), nullable=True)
    prof_mae = db.Column(db.String(100), nullable=True)
    nome_pai = db.Column(db.String(150), nullable=True)
    prof_pai = db.Column(db.String(100), nullable=True)
    tem_alergia = db.Column(db.String(200), nullable=True)
    tem_deficiencia = db.Column(db.String(200), nullable=True)
    rota_transporte = db.Column(db.String(100), nullable=True)
    nis_bolsa = db.Column(db.String(50), nullable=True)
    
    # Cadastral: ativo | inativo | transferido. Não confundir com a situação acadêmica.
    situacao = db.Column(db.String(20), default='ativo')

    # Escola de origem permanece mesmo se a turma for encerrada ou excluída.
    escola_id = db.Column(db.Integer, db.ForeignKey('escola.id'), nullable=True)
    escola = db.relationship('Escola', backref=db.backref('alunos_escola'))

    # Atalho de compatibilidade para a turma ATUAL. A fonte histórica do vínculo é Matricula.
    # Não confundir Aluno.situacao (cadastro) com Matricula.situacao (vínculo).
    turma_id = db.Column(db.Integer, db.ForeignKey('turma.id', ondelete='SET NULL'), nullable=True)
    turma = db.relationship('Turma', backref=db.backref('alunos'))


class Matricula(db.Model):
    """Vínculo Aluno + Turma. Ano letivo e escola vêm da Turma; não duplicar ano_letivo_id.

    situacao do vínculo (cursando/concluida/transferido/inativo) não se mistura com
    Aluno.situacao (cadastral). HistoricoAnual.situacao_final é coluna legada cadastral;
    o resultado acadêmico por disciplina fica no JSON do snapshot.
    Sem cascade de exclusão: apagar Aluno ou Turma não pode destruir o histórico de matrícula.
    """
    __tablename__ = "matricula"
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey("aluno.id"), nullable=False)
    turma_id = db.Column(db.Integer, db.ForeignKey("turma.id"), nullable=False)
    data_inicio = db.Column(db.Date, nullable=False)
    data_fim = db.Column(db.Date, nullable=True)
    situacao = db.Column(db.String(20), nullable=False)  # vínculo, não resultado acadêmico
    numero_chamada = db.Column(db.Integer, nullable=True)
    aluno = db.relationship("Aluno", backref=db.backref("matriculas"))
    turma = db.relationship("Turma", backref=db.backref("matriculas"))
    __table_args__ = (
        db.UniqueConstraint("aluno_id", "turma_id", name="uq_matricula_aluno_turma"),
    )

# --- TABELAS OPERACIONAIS (Ano Vigente) ---

# Para Ensino Fundamental
class RegistroNota(db.Model):
    """Célula do boletim: a chave lógica é (matrícula, disciplina, bimestre).

    `bimestre` aqui é o código da célula (11/12/13 N1/N2/RP, 51/52 RS, 99 PF),
    não o número 1–4 isolado. `tipo` é rótulo (ex.: bimestral), não discrimina
    a célula: o motor usa o mapa {código: valor}.
    """
    id = db.Column(db.Integer, primary_key=True)
    valor = db.Column(db.Float)
    bimestre = db.Column(db.Integer, nullable=False)
    tipo = db.Column(db.String(50), nullable=False)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    disciplina_id = db.Column(db.Integer, db.ForeignKey('disciplina.id'), nullable=False)
    # Contexto acadêmico: a matrícula isola o ano. aluno_id fica só na transição.
    matricula_id = db.Column(db.Integer, db.ForeignKey('matricula.id'), nullable=True)
    # Sem cascade de exclusão: apagar o aluno ou a matrícula não pode levar as notas.
    aluno = db.relationship('Aluno', backref=db.backref('notas'))
    matricula = db.relationship('Matricula', backref=db.backref('notas'))
    __table_args__ = (
        Index(
            "uq_registro_nota_matricula_disc_bim",
            "matricula_id", "disciplina_id", "bimestre",
            unique=True,
            sqlite_where=_WHERE_MATRICULA,
            postgresql_where=_WHERE_MATRICULA,
        ),
    )

# NOVO: Para Educação Infantil
class RelatorioDescritivo(db.Model):
    """Parecer da matrícula da criança naquela turma/período.

    matricula_id é a fonte de verdade (nullable na transição).
    aluno_id fica como redundância de compatibilidade.
    Sem disciplina: Educação Infantil não lança parecer por componente.
    """
    id = db.Column(db.Integer, primary_key=True)
    eixo = db.Column(db.String(50), nullable=False) # Ex: "Linguagem", "Socioafetivo"
    bimestre = db.Column(db.Integer, nullable=False)
    avaliacao = db.Column(db.String(50), nullable=False) # "Em desenvolvimento", "Conquistado"
    observacao = db.Column(db.Text, nullable=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    # Contexto acadêmico: a matrícula isola o ano. aluno_id fica só na transição.
    matricula_id = db.Column(db.Integer, db.ForeignKey('matricula.id'), nullable=True)
    # Sem cascade de exclusão: pareceres são histórico; apagar matrícula não os remove.
    aluno = db.relationship('Aluno', backref=db.backref('relatorios_infantil'))
    matricula = db.relationship('Matricula', backref=db.backref('relatorios_infantil'))
    __table_args__ = (
        Index(
            "uq_relatorio_descritivo_matricula_eixo_bim",
            "matricula_id", "eixo", "bimestre",
            unique=True,
            sqlite_where=_WHERE_MATRICULA,
            postgresql_where=_WHERE_MATRICULA,
        ),
    )

class RegistroFalta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quantidade = db.Column(db.Integer)
    bimestre = db.Column(db.Integer, nullable=False)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    disciplina_id = db.Column(db.Integer, db.ForeignKey('disciplina.id'), nullable=True) # Null na infantil
    # Contexto acadêmico: a matrícula isola o ano. aluno_id fica só na transição.
    matricula_id = db.Column(db.Integer, db.ForeignKey('matricula.id'), nullable=True)
    # Sem cascade de exclusão: faltas bimestrais são histórico; apagar matrícula não as remove.
    aluno = db.relationship('Aluno', backref=db.backref('faltas'))
    matricula = db.relationship('Matricula', backref=db.backref('faltas'))
    __table_args__ = (
        Index(
            "uq_registro_falta_matricula_disc_bim",
            "matricula_id", "disciplina_id", "bimestre",
            unique=True,
            sqlite_where=_WHERE_MATRICULA,
            postgresql_where=_WHERE_MATRICULA,
        ),
    )


class RegistroAula(db.Model):
    """Registro de aula do diário de classe. Tabela nova; não altera Planejamento."""
    __tablename__ = "registro_aula"
    id = db.Column(db.Integer, primary_key=True)
    professor_id = db.Column(db.Integer, db.ForeignKey("professor.id"), nullable=False)
    turma_id = db.Column(db.Integer, db.ForeignKey("turma.id"), nullable=False)
    disciplina_id = db.Column(db.Integer, db.ForeignKey("disciplina.id"), nullable=True)
    alocacao_id = db.Column(db.Integer, db.ForeignKey("alocacao.id"), nullable=True)
    data = db.Column(db.Date, nullable=False)
    bimestre = db.Column(db.Integer, nullable=False)
    quantidade_aulas = db.Column(db.Integer, nullable=False)
    conteudo = db.Column(db.Text, nullable=False)
    metodologia = db.Column(db.Text, nullable=False)
    observacao = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="pendente")
    coordenador_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True)
    data_validacao = db.Column(db.DateTime, nullable=True)
    motivo_invalidacao = db.Column(db.Text, nullable=True)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    professor = db.relationship("Professor")
    turma = db.relationship("Turma")
    disciplina = db.relationship("Disciplina")
    alocacao = db.relationship("Alocacao")
    coordenador = db.relationship("Usuario", foreign_keys=[coordenador_id])
    __table_args__ = (
        db.Index("ix_aula_turma_disc_data", "turma_id", "disciplina_id", "data"),
        db.Index("ix_aula_professor", "professor_id"),
        db.Index("ix_aula_status", "status"),
    )


class RegistroFrequencia(db.Model):
    """Chamada diária (PRESENTE/FALTA). Contexto acadêmico: Matricula.

    RegistroAula permanece fato da Turma (sem matricula_id).
    aluno_id e turma_id ficam só na transição e devem coincidir com a matrícula.
    matricula_id é nullable: legado ambíguo não é atribuído por chute.
    """
    __tablename__ = "registro_frequencia"
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(db.Integer, db.ForeignKey("aluno.id"), nullable=False)
    turma_id = db.Column(db.Integer, db.ForeignKey("turma.id"), nullable=False)
    # Contexto acadêmico: a matrícula isola o ano. aluno_id/turma_id ficam só na transição.
    matricula_id = db.Column(db.Integer, db.ForeignKey("matricula.id"), nullable=True)
    disciplina_id = db.Column(db.Integer, db.ForeignKey("disciplina.id"), nullable=True)
    professor_id = db.Column(db.Integer, db.ForeignKey("professor.id"), nullable=True)
    alocacao_id = db.Column(db.Integer, db.ForeignKey("alocacao.id"), nullable=True)
    registro_aula_id = db.Column(db.Integer, db.ForeignKey("registro_aula.id"), nullable=True)
    data = db.Column(db.Date, nullable=False)
    bimestre = db.Column(db.Integer, nullable=False)
    situacao = db.Column(db.String(20), nullable=False)
    # Sem cascade de exclusão: frequência diária é histórico; apagar matrícula não a remove.
    aluno = db.relationship("Aluno", backref=db.backref("frequencias"))
    turma = db.relationship("Turma")
    matricula = db.relationship("Matricula", backref=db.backref("frequencias"))
    disciplina = db.relationship("Disciplina")
    professor = db.relationship("Professor")
    alocacao = db.relationship("Alocacao")
    # Aula + chamada do mesmo lançamento: objeto operacional conjunto, sem valor histórico isolado.
    registro_aula = db.relationship("RegistroAula", backref=db.backref("frequencias", cascade="all, delete-orphan"))
    __table_args__ = (
        db.Index("ix_freq_turma_disc_data", "turma_id", "disciplina_id", "data"),
        db.Index("ix_freq_aluno_data", "aluno_id", "disciplina_id", "data"),
        db.Index("ix_freq_matricula", "matricula_id"),
        # Unidade da chamada moderna: um P/F por matrícula por RegistroAula.
        # quantidade_aulas > 1 continua um único lançamento. Chamada legada
        # (registro_aula_id NULL) não entra neste UNIQUE (dívida consciente).
        Index(
            "uq_registro_frequencia_matricula_aula",
            "matricula_id", "registro_aula_id",
            unique=True,
            sqlite_where=_WHERE_MATRICULA_AULA,
            postgresql_where=_WHERE_MATRICULA_AULA,
        ),
    )

# --- TABELA DE ARQUIVO MORTO (HISTÓRICO) ---
# Snapshot do encerramento de UMA Matrícula. aluno_id/ano_letivo ficam na transição.
class HistoricoAnual(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ano_letivo = db.Column(db.Integer, nullable=False)
    aluno_id = db.Column(db.Integer, db.ForeignKey('aluno.id'), nullable=False)
    # Fonte de verdade do vínculo. Nullable na transição (legado sem associação segura).
    matricula_id = db.Column(db.Integer, db.ForeignKey('matricula.id'), nullable=True)
    escola_nome = db.Column(db.String(100))
    turma_nome = db.Column(db.String(50))
    etapa = db.Column(db.String(20)) # infantil ou fundamental

    # JSON do encerramento. Chaves legadas: notas, faltas, frequencias, pareceres.
    # Snapshots novos acrescentam metadados congelados e resultado por disciplina.
    dados_fechamento = db.Column(db.Text)

    # Coluna legada: Aluno.situacao cadastral no fechamento (ativo/inativo/transferido).
    # Não é resultado acadêmico global: o sistema não possui essa regra oficial.
    # Resultado por disciplina e a distinção cadastral/acadêmica ficam no JSON.
    situacao_final = db.Column(db.String(20))
    aluno = db.relationship('Aluno', backref='historico_escolar')
    # Sem cascade: apagar matrícula não apaga o snapshot anual.
    matricula = db.relationship('Matricula', backref=db.backref('historicos_anuais'))
    __table_args__ = (
        Index(
            "uq_historico_anual_matricula",
            "matricula_id",
            unique=True,
            sqlite_where=_WHERE_MATRICULA,
            postgresql_where=_WHERE_MATRICULA,
        ),
    )

class Aviso(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    titulo = db.Column(db.String(100), nullable=False)
    conteudo = db.Column(db.Text, nullable=False)
    data_criacao = db.Column(db.DateTime, default=datetime.utcnow)
    autor_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    escola_destino_id = db.Column(db.Integer, db.ForeignKey('escola.id'), nullable=True)
    autor = db.relationship('Usuario', foreign_keys=[autor_id])
    escola_destino = db.relationship('Escola', foreign_keys=[escola_destino_id])

class Planejamento(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    professor_id = db.Column(db.Integer, db.ForeignKey('professor.id'), nullable=False)
    turma_id = db.Column(db.Integer, db.ForeignKey('turma.id'), nullable=False)
    data_envio = db.Column(db.DateTime, default=datetime.utcnow)
    conteudo = db.Column(db.Text, nullable=False)
    arquivo = db.Column(db.String(300), nullable=True)
    arquivo_original = db.Column(db.String(300), nullable=True)
    feedback_coordenacao = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='pendente')
    arquivado = db.Column(db.Boolean, default=False)
    professor = db.relationship('Professor')
    turma = db.relationship('Turma')


TIPOS_DIA_LETIVO = frozenset({"LETIVO", "REPOSICAO"})
TIPOS_DIA_CALENDARIO = frozenset({"LETIVO", "FERIADO", "RECESSO", "NAO_LETIVO", "REPOSICAO"})
TIPOS_JORNADA = frozenset({"PARCIAL", "INTEGRAL"})
NOMES_PERIODO_PADRAO = {
    1: "1º Bimestre",
    2: "2º Bimestre",
    3: "3º Bimestre",
    4: "4º Bimestre",
}


class AnoLetivo(db.Model):
    """Ano civil da rede. Turma.ano_letivo (inteiro) permanece; não há FK obrigatória."""
    __tablename__ = "ano_letivo"
    id = db.Column(db.Integer, primary_key=True)
    ano = db.Column(db.Integer, nullable=False, unique=True)
    data_inicio = db.Column(db.Date, nullable=False)
    data_fim = db.Column(db.Date, nullable=False)
    dias_letivos_previstos = db.Column(db.Integer, nullable=True)
    ativo = db.Column(db.Boolean, default=False, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    periodos = db.relationship("PeriodoLetivo", backref="ano_letivo_ref", cascade="all, delete-orphan")
    dias = db.relationship("DiaCalendario", backref="ano_letivo_ref", cascade="all, delete-orphan")


class PeriodoLetivo(db.Model):
    __tablename__ = "periodo_letivo"
    id = db.Column(db.Integer, primary_key=True)
    ano_letivo_id = db.Column(db.Integer, db.ForeignKey("ano_letivo.id"), nullable=False)
    numero = db.Column(db.Integer, nullable=False)
    nome = db.Column(db.String(80), nullable=False)
    data_inicio = db.Column(db.Date, nullable=False)
    data_fim = db.Column(db.Date, nullable=False)
    __table_args__ = (
        db.UniqueConstraint("ano_letivo_id", "numero", name="uq_periodo_ano_numero"),
        db.Index("ix_periodo_ano", "ano_letivo_id"),
    )


class DiaCalendario(db.Model):
    __tablename__ = "dia_calendario"
    id = db.Column(db.Integer, primary_key=True)
    ano_letivo_id = db.Column(db.Integer, db.ForeignKey("ano_letivo.id"), nullable=False)
    data = db.Column(db.Date, nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    descricao = db.Column(db.String(200), nullable=True)
    __table_args__ = (
        db.UniqueConstraint("ano_letivo_id", "data", name="uq_dia_calendario_ano_data"),
        db.Index("ix_dia_cal_ano_data", "ano_letivo_id", "data"),
    )


class HorarioAlocacao(db.Model):
    """Grade semanal mínima: dia da semana + quantidade de aulas. 0=segunda … 6=domingo."""
    __tablename__ = "horario_alocacao"
    id = db.Column(db.Integer, primary_key=True)
    alocacao_id = db.Column(db.Integer, db.ForeignKey("alocacao.id"), nullable=False)
    dia_semana = db.Column(db.Integer, nullable=False)
    quantidade_aulas = db.Column(db.Integer, nullable=False)
    alocacao = db.relationship("Alocacao", backref=db.backref("horarios", cascade="all, delete-orphan"))
    __table_args__ = (
        db.UniqueConstraint("alocacao_id", "dia_semana", name="uq_horario_aloc_dia"),
        db.Index("ix_horario_aloc", "alocacao_id"),
    )


STATUS_OCORRENCIA_ATIVA = "ATIVA"
STATUS_OCORRENCIA_CANCELADA = "CANCELADA"
STATUS_OCORRENCIA = frozenset({STATUS_OCORRENCIA_ATIVA, STATUS_OCORRENCIA_CANCELADA})
MOTIVO_FALTA_PROFESSOR = "FALTA_DO_PROFESSOR"
_WHERE_OCORRENCIA_ATIVA = text("status = 'ATIVA'")


class OcorrenciaAulaNaoMinistrada(db.Model):
    """Aula prevista que a gestão registrou como não ocorrida. Não é RegistroAula.

    Identidade da aula prevista: (horario_alocacao_id, data). HorarioAlocacao é
    o padrão semanal da alocação (um registro por dia da semana, com
    quantidade_aulas). Não há 1ª/2ª aula no modelo; quantidade_aulas é o
    feixe do dia. Ausência de diário não gera esta ocorrência automaticamente.
    """
    __tablename__ = "ocorrencia_aula_nao_ministrada"
    id = db.Column(db.Integer, primary_key=True)
    escola_id = db.Column(db.Integer, db.ForeignKey("escola.id"), nullable=False)
    turma_id = db.Column(db.Integer, db.ForeignKey("turma.id"), nullable=False)
    disciplina_id = db.Column(db.Integer, db.ForeignKey("disciplina.id"), nullable=True)
    professor_id = db.Column(db.Integer, db.ForeignKey("professor.id"), nullable=False)
    alocacao_id = db.Column(db.Integer, db.ForeignKey("alocacao.id"), nullable=False)
    horario_alocacao_id = db.Column(
        db.Integer, db.ForeignKey("horario_alocacao.id"), nullable=False,
    )
    data = db.Column(db.Date, nullable=False)
    quantidade_aulas = db.Column(db.Integer, nullable=False)
    motivo = db.Column(db.String(40), nullable=False, default=MOTIVO_FALTA_PROFESSOR)
    observacao = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default=STATUS_OCORRENCIA_ATIVA)
    registrado_por_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=False)
    registrado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    cancelado_por_id = db.Column(db.Integer, db.ForeignKey("usuario.id"), nullable=True)
    cancelado_em = db.Column(db.DateTime, nullable=True)
    motivo_cancelamento = db.Column(db.Text, nullable=True)
    escola = db.relationship("Escola", foreign_keys=[escola_id])
    turma = db.relationship("Turma", foreign_keys=[turma_id])
    disciplina = db.relationship("Disciplina", foreign_keys=[disciplina_id])
    professor = db.relationship("Professor", foreign_keys=[professor_id])
    alocacao = db.relationship("Alocacao", foreign_keys=[alocacao_id])
    horario = db.relationship("HorarioAlocacao", foreign_keys=[horario_alocacao_id])
    registrado_por = db.relationship("Usuario", foreign_keys=[registrado_por_id])
    cancelado_por = db.relationship("Usuario", foreign_keys=[cancelado_por_id])
    __table_args__ = (
        Index(
            "uq_ocorrencia_nao_ministrada_horario_data_ativa",
            "horario_alocacao_id", "data",
            unique=True,
            sqlite_where=_WHERE_OCORRENCIA_ATIVA,
            postgresql_where=_WHERE_OCORRENCIA_ATIVA,
        ),
        db.Index("ix_ocorrencia_nao_min_escola_data", "escola_id", "data"),
        db.Index("ix_ocorrencia_nao_min_professor", "professor_id"),
        db.Index("ix_ocorrencia_nao_min_status", "status"),
    )


MEDIA_MINIMA_PADRAO = 6.0


class ConfiguracaoAcademica(db.Model):
    """Uma linha vigente para a rede. Schema vem das migrations (não do startup)."""
    __tablename__ = "configuracao_academica"
    id = db.Column(db.Integer, primary_key=True)
    media_minima = db.Column(db.Float, nullable=False, default=MEDIA_MINIMA_PADRAO)
    atualizado_em = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


ETAPAS_MATRIZ = frozenset({
    "fundamental_iniciais",
    "fundamental_finais",
})
CATEGORIAS_MATRIZ = frozenset({"BASE_COMUM", "BASE_DIVERSIFICADA"})
ORIGEM_TRAJETORIA_LEGADA = "LEGADO"
ORIGENS_TRAJETORIA_LEGADA = frozenset({ORIGEM_TRAJETORIA_LEGADA})


class MatrizCurricular(db.Model):
    """Matriz oficial por vigência. Não substitui snapshot acadêmico antigo.

    ano_fim NULL = ainda vigente. Um ano letivo só usa a matriz se
    ano_inicio <= ano <= (ano_fim ou infinito). Nunca preenche ano anterior.
    """
    __tablename__ = "matriz_curricular"
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(160), nullable=False)
    ano_inicio = db.Column(db.Integer, nullable=False)
    ano_fim = db.Column(db.Integer, nullable=True)
    etapa = db.Column(db.String(40), nullable=False)
    carga_horaria_semanal = db.Column(db.Integer, nullable=True)
    dias_letivos = db.Column(db.Integer, nullable=True)
    carga_horaria_anual = db.Column(db.Integer, nullable=True)
    ativa = db.Column(db.Boolean, default=True, nullable=False)
    componentes = db.relationship(
        "MatrizComponente", backref="matriz", cascade="all, delete-orphan",
    )
    __table_args__ = (
        db.UniqueConstraint("nome", "ano_inicio", "etapa", name="uq_matriz_nome_inicio_etapa"),
        db.Index("ix_matriz_vigencia", "ano_inicio", "ano_fim", "etapa"),
    )


class MatrizComponente(db.Model):
    """Componente de uma matriz vigente, por ano/série. CH só a da própria matriz."""
    __tablename__ = "matriz_componente"
    id = db.Column(db.Integer, primary_key=True)
    matriz_id = db.Column(
        db.Integer, db.ForeignKey("matriz_curricular.id"), nullable=False,
    )
    nome = db.Column(db.String(160), nullable=False)
    categoria = db.Column(db.String(40), nullable=False)
    area = db.Column(db.String(80), nullable=True)
    ano_serie = db.Column(db.Integer, nullable=False)
    aulas_semanais = db.Column(db.Integer, nullable=True)
    carga_horaria_anual = db.Column(db.Integer, nullable=True)
    ordem = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (
        db.UniqueConstraint(
            "matriz_id", "ano_serie", "nome", name="uq_matriz_comp_serie_nome",
        ),
        db.Index("ix_matriz_comp_matriz", "matriz_id"),
    )


class TrajetoriaEscolarLegada(db.Model):
    """Trajetória anterior à adoção do Educa+. Não é HistoricoAnual.

    Preenchimento manual a partir do documento apresentado. Não recebe
    matriz vigente nem notas do motor acadêmico.
    """
    __tablename__ = "trajetoria_escolar_legada"
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(
        db.Integer,
        db.ForeignKey("aluno.id", name="fk_trajetoria_legada_aluno_id"),
        nullable=False,
    )
    ano_letivo = db.Column(db.Integer, nullable=False)
    serie = db.Column(db.Integer, nullable=False)
    etapa = db.Column(db.String(40), nullable=True)
    estabelecimento = db.Column(db.String(160), nullable=True)
    municipio = db.Column(db.String(100), nullable=True)
    uf = db.Column(db.String(2), nullable=True)
    resultado = db.Column(db.String(80), nullable=True)
    carga_horaria_total = db.Column(db.Integer, nullable=True)
    observacao = db.Column(db.Text, nullable=True)
    origem = db.Column(db.String(40), nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )
    aluno = db.relationship("Aluno", backref=db.backref("trajetorias_legadas"))
    componentes = db.relationship(
        "TrajetoriaComponenteLegado",
        backref="trajetoria",
        cascade="all, delete-orphan",
        order_by="TrajetoriaComponenteLegado.ordem",
    )
    __table_args__ = (
        db.UniqueConstraint(
            "aluno_id", "ano_letivo", "serie",
            name="uq_trajetoria_legada_aluno_ano_serie",
        ),
        db.Index("ix_trajetoria_legada_aluno", "aluno_id"),
    )


class TrajetoriaComponenteLegado(db.Model):
    """Componente transcrito do documento de origem. CH/nota só se informadas."""
    __tablename__ = "trajetoria_componente_legado"
    id = db.Column(db.Integer, primary_key=True)
    trajetoria_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "trajetoria_escolar_legada.id",
            name="fk_trajetoria_comp_legado_trajetoria_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    nome = db.Column(db.String(160), nullable=False)
    carga_horaria = db.Column(db.Integer, nullable=True)
    nota_conceito = db.Column(db.String(40), nullable=True)
    situacao = db.Column(db.String(80), nullable=True)
    faltas = db.Column(db.Integer, nullable=True)
    frequencia = db.Column(db.String(40), nullable=True)
    categoria = db.Column(db.String(40), nullable=True)
    ordem = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (
        db.Index("ix_trajetoria_comp_legado", "trajetoria_id"),
    )


class ComplementoTransferencia(db.Model):
    """Transcrição do ano corrente só para o histórico de transferência.

    Não é HistoricoAnual, RegistroNota nem RegistroFalta. Não encerra o ano.
    """
    __tablename__ = "complemento_transferencia"
    id = db.Column(db.Integer, primary_key=True)
    aluno_id = db.Column(
        db.Integer,
        db.ForeignKey("aluno.id", name="fk_compl_transf_aluno_id"),
        nullable=False,
    )
    matricula_id = db.Column(
        db.Integer,
        db.ForeignKey("matricula.id", name="fk_compl_transf_matricula_id"),
        nullable=False,
    )
    escola_id = db.Column(
        db.Integer,
        db.ForeignKey("escola.id", name="fk_compl_transf_escola_id"),
        nullable=True,
    )
    ano_letivo = db.Column(db.Integer, nullable=False)
    criado_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    atualizado_em = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
    )
    aluno = db.relationship("Aluno", backref=db.backref("complementos_transferencia"))
    matricula = db.relationship("Matricula", backref=db.backref("complemento_transferencia", uselist=False))
    periodos = db.relationship(
        "ComplementoTransferenciaPeriodo",
        backref="complemento",
        cascade="all, delete-orphan",
        order_by="ComplementoTransferenciaPeriodo.periodo",
    )
    __table_args__ = (
        db.UniqueConstraint("matricula_id", name="uq_compl_transf_matricula"),
        db.Index("ix_compl_transf_aluno", "aluno_id"),
        db.Index("ix_compl_transf_escola", "escola_id"),
    )


class ComplementoTransferenciaPeriodo(db.Model):
    """Nota ou falta transcrita de um período ainda ausente no Educa+."""
    __tablename__ = "complemento_transferencia_periodo"
    id = db.Column(db.Integer, primary_key=True)
    complemento_id = db.Column(
        db.Integer,
        db.ForeignKey(
            "complemento_transferencia.id",
            name="fk_compl_transf_per_complemento_id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    disciplina_id = db.Column(
        db.Integer,
        db.ForeignKey("disciplina.id", name="fk_compl_transf_per_disciplina_id"),
        nullable=True,
    )
    nome_componente = db.Column(db.String(160), nullable=False)
    nome_chave = db.Column(db.String(160), nullable=False)
    periodo = db.Column(db.Integer, nullable=False)
    nota = db.Column(db.Float, nullable=True)
    faltas = db.Column(db.Integer, nullable=True)
    __table_args__ = (
        db.UniqueConstraint(
            "complemento_id", "nome_chave", "periodo",
            name="uq_compl_transf_per_comp_periodo",
        ),
        db.Index("ix_compl_transf_per_complemento", "complemento_id"),
    )


class AuditLog(db.Model):
    """Trilha de auditoria append-only. Sem fluxo de edição ou exclusão.

    usuario_id e escola_id são nullable (operação de sistema / rede municipal).
    data_hora é UTC ingênuo, no mesmo padrão de Aviso e RegistroAula.
    dados_* são JSON (SQLite e PostgreSQL); o chamador passa o recorte já
    escolhido — a sanitização remove senha/token/secret, não o objeto inteiro.
    """
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(
        db.Integer,
        db.ForeignKey("usuario.id", name="fk_audit_log_usuario_id", ondelete="SET NULL"),
        nullable=True,
    )
    acao = db.Column(db.String(80), nullable=False)
    entidade = db.Column(db.String(80), nullable=False)
    entidade_id = db.Column(db.Integer, nullable=True)
    escola_id = db.Column(
        db.Integer,
        db.ForeignKey("escola.id", name="fk_audit_log_escola_id", ondelete="SET NULL"),
        nullable=True,
    )
    data_hora = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    request_id = db.Column(db.String(36), nullable=True)
    ip = db.Column(db.String(45), nullable=True)
    dados_anteriores = db.Column(db.JSON, nullable=True)
    dados_novos = db.Column(db.JSON, nullable=True)
    usuario = db.relationship("Usuario", foreign_keys=[usuario_id])
    escola = db.relationship("Escola", foreign_keys=[escola_id])
    __table_args__ = (
        db.Index("ix_audit_log_usuario_id", "usuario_id"),
        db.Index("ix_audit_log_escola_id", "escola_id"),
        db.Index("ix_audit_log_data_hora", "data_hora"),
        db.Index("ix_audit_log_entidade_id", "entidade", "entidade_id"),
    )


class TentativaLogin(db.Model):
    """Lockout persistente por identificador de login. Sem senha, sem enumeração extra."""
    __tablename__ = "tentativa_login"
    login_normalizado = db.Column(db.String(80), primary_key=True)
    falhas = db.Column(db.Integer, nullable=False)
    bloqueado_ate = db.Column(db.DateTime, nullable=True)
    atualizado_em = db.Column(db.DateTime, nullable=False)


def alunos_da_escola(escola_id):
    """Alunos da escola, inclusive os que estão sem turma (turma_id nulo)."""
    return (
        Aluno.query.outerjoin(Turma, Aluno.turma_id == Turma.id)
        .filter(or_(Aluno.escola_id == escola_id, Turma.escola_id == escola_id))
        .distinct()
        .all()
    )


def _add_index_if_missing(tabelas, table, name, ddl):
    if table not in tabelas:
        return
    insp = inspect(db.engine)
    nomes = {i["name"] for i in insp.get_indexes(table) if i.get("name")}
    if name not in nomes:
        db.session.execute(text(ddl))
        db.session.commit()


def _add_column_if_missing(tabelas, table, column, ddl):
    if table not in tabelas:
        return
    insp = inspect(db.engine)
    nomes = {c["name"] for c in insp.get_columns(table)}
    if column not in nomes:
        db.session.execute(text(ddl))
        db.session.commit()


def ensure_colunas_legado():
    """ALTER TABLE ADD COLUMN se faltar. Dívida C. Não usar no startup comercial.

    Banco novo usa flask db upgrade. Este trecho só atende testes e o comando
    explícito `flask educamais alinhar-schema-legado`.
    """
    insp = inspect(db.engine)
    tabelas = insp.get_table_names()
    if "aluno" not in tabelas:
        return False
    colunas = {c["name"] for c in insp.get_columns("aluno")}
    if "escola_id" not in colunas:
        db.session.execute(text("ALTER TABLE aluno ADD COLUMN escola_id INTEGER"))
        db.session.commit()

    _add_column_if_missing(tabelas, "turma", "capacidade", "ALTER TABLE turma ADD COLUMN capacidade INTEGER")
    _add_column_if_missing(tabelas, "turma", "tipo_jornada", "ALTER TABLE turma ADD COLUMN tipo_jornada VARCHAR(20)")
    _add_column_if_missing(tabelas, "turma", "carga_horaria_diaria", "ALTER TABLE turma ADD COLUMN carga_horaria_diaria FLOAT")
    _add_column_if_missing(tabelas, "turma", "carga_horaria_anual", "ALTER TABLE turma ADD COLUMN carga_horaria_anual FLOAT")
    _add_column_if_missing(tabelas, "alocacao", "carga_horaria_prevista", "ALTER TABLE alocacao ADD COLUMN carga_horaria_prevista INTEGER")
    _add_column_if_missing(tabelas, "alocacao", "aulas_semanais", "ALTER TABLE alocacao ADD COLUMN aulas_semanais INTEGER")
    _add_column_if_missing(tabelas, "alocacao", "duracao_aula_minutos", "ALTER TABLE alocacao ADD COLUMN duracao_aula_minutos INTEGER")
    _add_column_if_missing(tabelas, "registro_frequencia", "registro_aula_id", "ALTER TABLE registro_frequencia ADD COLUMN registro_aula_id INTEGER")
    _add_column_if_missing(tabelas, "planejamento", "arquivo_original", "ALTER TABLE planejamento ADD COLUMN arquivo_original VARCHAR(300)")
    _add_column_if_missing(tabelas, "registro_nota", "matricula_id", "ALTER TABLE registro_nota ADD COLUMN matricula_id INTEGER")
    _add_column_if_missing(tabelas, "registro_falta", "matricula_id", "ALTER TABLE registro_falta ADD COLUMN matricula_id INTEGER")
    _add_column_if_missing(tabelas, "registro_frequencia", "matricula_id", "ALTER TABLE registro_frequencia ADD COLUMN matricula_id INTEGER")
    _add_column_if_missing(
        tabelas,
        RelatorioDescritivo.__tablename__,
        "matricula_id",
        "ALTER TABLE {} ADD COLUMN matricula_id INTEGER".format(RelatorioDescritivo.__tablename__),
    )
    _add_column_if_missing(
        tabelas,
        "historico_anual",
        "matricula_id",
        "ALTER TABLE historico_anual ADD COLUMN matricula_id INTEGER",
    )
    _add_column_if_missing(
        tabelas,
        "trajetoria_componente_legado",
        "situacao",
        "ALTER TABLE trajetoria_componente_legado ADD COLUMN situacao VARCHAR(80)",
    )
    _add_index_if_missing(
        tabelas,
        "registro_frequencia",
        "ix_freq_matricula",
        "CREATE INDEX IF NOT EXISTS ix_freq_matricula ON registro_frequencia (matricula_id)",
    )
    return True


def executar_backfills_legado():
    """Backfills conservadores e idempotentes. Não rodam no startup comercial.

    Matricula e matricula_id: só vínculo inequívoco; não inventa matrícula.
    """
    Aluno.query.filter(Aluno.situacao == "cursando").update(
        {Aluno.situacao: "ativo"}, synchronize_session=False
    )
    db.session.commit()

    pendentes = Aluno.query.filter(Aluno.escola_id.is_(None)).all()
    if pendentes:
        for aluno in pendentes:
            if aluno.turma is not None:
                aluno.escola_id = aluno.turma.escola_id
                continue
            hist = (
                HistoricoAnual.query.filter_by(aluno_id=aluno.id)
                .order_by(HistoricoAnual.id.desc())
                .first()
            )
            if hist and hist.escola_nome:
                escola = Escola.query.filter_by(nome=hist.escola_nome).first()
                if escola:
                    aluno.escola_id = escola.id
        db.session.commit()

    from src.matriculas import (
        backfill_matriculas_atuais,
        backfill_registros_academicos,
        garantir_unicidade_lancamentos_matricula,
    )
    from src.frequencia import auditar_duplicidades_frequencia, backfill_frequencias
    from src.pareceres import backfill_relatorios_descritivos
    from src.historico import backfill_historicos_anuais
    backfill_matriculas_atuais()
    backfill_registros_academicos()
    garantir_unicidade_lancamentos_matricula()
    backfill_frequencias()
    auditar_duplicidades_frequencia()
    backfill_relatorios_descritivos()
    backfill_historicos_anuais()


def ensure_schema():
    """Testes, fixtures e comando alinhar. Não é mecanismo de produção.

    Não executa DROP nem recria o banco. Produção/development usam Alembic.
    """
    if ensure_colunas_legado() is False:
        return
    executar_backfills_legado()
