"""Regras de senha e lockout persistente. Nenhuma senha é persistida em texto puro."""
import secrets
import string
from datetime import datetime, timedelta

from flask import current_app, has_app_context
from sqlalchemy.exc import IntegrityError

from src import bcrypt, db

SENHA_MINIMA = 8
SENHA_TEMPORARIA_TAMANHO = 12

# Credencial conhecida SOMENTE com EDUCAMAIS_DEMO=1 (nunca em produção).
SENHA_DEMO_ADMIN = "admin123"

MAX_TENTATIVAS_LOGIN = 5
BLOQUEIO_SEGUNDOS = 300

_HASH_EQUALIZACAO = None

_MAIUSCULAS = string.ascii_uppercase
_MINUSCULAS = string.ascii_lowercase
_DIGITOS = string.digits
_ESPECIAIS = "!@#$%&*-_=+"
_ALFABETO = _MAIUSCULAS + _MINUSCULAS + _DIGITOS + _ESPECIAIS


def hash_senha(texto):
    return bcrypt.generate_password_hash(texto).decode("utf-8")


def gerar_senha_temporaria(tamanho=SENHA_TEMPORARIA_TAMANHO):
    """Senha aleatória de uso único. Não registrar o valor em log."""
    n = max(int(tamanho or SENHA_TEMPORARIA_TAMANHO), 4)
    obrigatorio = [
        secrets.choice(_MAIUSCULAS),
        secrets.choice(_MINUSCULAS),
        secrets.choice(_DIGITOS),
        secrets.choice(_ESPECIAIS),
    ]
    resto = [secrets.choice(_ALFABETO) for _ in range(n - 4)]
    chars = obrigatorio + resto
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def senha_atende_minimo(texto):
    return bool(texto) and len(texto) >= SENHA_MINIMA


def verificar_senha(hash_salvo, texto):
    if not hash_salvo or texto is None:
        return False
    try:
        return bcrypt.check_password_hash(hash_salvo, texto)
    except (ValueError, TypeError):
        return False


def hash_equalizacao_tempo():
    """Hash fixo em memória para equalizar o tempo quando o login não existe."""
    global _HASH_EQUALIZACAO
    if _HASH_EQUALIZACAO is None:
        _HASH_EQUALIZACAO = hash_senha("equalizacao-tempo-nao-e-senha")
    return _HASH_EQUALIZACAO


def completar_senhas_iniciais_ausentes():
    """Não preenche senha universal. Contas sem hash não autenticam."""
    return


def _chave_login(login):
    return (login or "").strip().lower()[:80]


def _max_tentativas():
    if has_app_context():
        return int(current_app.config.get("LOGIN_MAX_TENTATIVAS") or MAX_TENTATIVAS_LOGIN)
    return MAX_TENTATIVAS_LOGIN


def _bloqueio_segundos():
    if has_app_context():
        return int(current_app.config.get("LOGIN_BLOQUEIO_SEGUNDOS") or BLOQUEIO_SEGUNDOS)
    return BLOQUEIO_SEGUNDOS


def _agora():
    return datetime.utcnow()


def login_bloqueado(login):
    from src.models import TentativaLogin

    chave = _chave_login(login)
    if not chave:
        return False
    row = db.session.get(TentativaLogin, chave)
    if not row:
        return False
    limite = row.bloqueado_ate
    if limite and limite > _agora():
        return True
    if limite and limite <= _agora():
        db.session.delete(row)
        db.session.commit()
    return False


def registrar_falha_login(login):
    """Incrementa falhas no banco. True quando o bloqueio acaba de ser aplicado."""
    from src.models import TentativaLogin

    chave = _chave_login(login)
    if not chave:
        return False
    agora = _agora()
    row = (
        TentativaLogin.query.filter_by(login_normalizado=chave)
        .with_for_update()
        .first()
    )
    if row is None:
        row = TentativaLogin(
            login_normalizado=chave,
            falhas=1,
            bloqueado_ate=None,
            atualizado_em=agora,
        )
        db.session.add(row)
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            row = (
                TentativaLogin.query.filter_by(login_normalizado=chave)
                .with_for_update()
                .first()
            )
            if row is None:
                return False
            row.falhas = int(row.falhas or 0) + 1
            row.atualizado_em = agora
    else:
        row.falhas = int(row.falhas or 0) + 1
        row.atualizado_em = agora
    passou = row.falhas >= _max_tentativas()
    if passou:
        row.bloqueado_ate = agora + timedelta(seconds=_bloqueio_segundos())
    db.session.commit()
    return passou


def limpar_falhas_login(login):
    from src.models import TentativaLogin

    chave = _chave_login(login)
    if not chave:
        return
    row = db.session.get(TentativaLogin, chave)
    if row is not None:
        db.session.delete(row)
        db.session.commit()


def resetar_falhas_login_para_testes():
    """Limpa lockout persistente. Requer app context."""
    from src.models import TentativaLogin
    from flask import has_app_context as _has

    if not _has():
        return
    TentativaLogin.query.delete()
    db.session.commit()
