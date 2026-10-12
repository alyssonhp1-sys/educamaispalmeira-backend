from flask import Blueprint, request, session
from flask_login import login_user, logout_user, login_required, current_user
from src import db
from src.models import Usuario
from src.http import ok, err
from src.csrf import resposta_token_csrf
from src.routes.spa import user_payload
from src.passwords import (
    hash_senha,
    hash_equalizacao_tempo,
    senha_atende_minimo,
    verificar_senha,
    login_bloqueado,
    registrar_falha_login,
    limpar_falhas_login,
)

MSG_CREDENCIAL_INVALIDA = "Usuário ou senha inválidos."
MSG_BLOQUEIO_LOGIN = (
    "Acesso temporariamente bloqueado após várias tentativas. "
    "Tente novamente em alguns minutos."
)

auth_bp = Blueprint("auth", __name__)

_ROTAS_PRIMEIRO_ACESSO = {
    "/api/me",
    "/api/dashboard",
    "/api/auth/definir-senha",
    "/api/auth/logout",
    "/api/csrf-token",
    "/health",
}


@auth_bp.before_app_request
def exigir_definir_senha():
    if not getattr(current_user, "is_authenticated", False):
        return None
    if not getattr(current_user, "primeiro_acesso", False):
        return None
    path = request.path or ""
    if path in _ROTAS_PRIMEIRO_ACESSO:
        return None
    if path.startswith("/api/") or path.startswith("/documentos/") or path.startswith("/relatorio/"):
        return err("Defina sua senha para continuar.", 403)
    return None


@auth_bp.route("/api/csrf-token", methods=["GET"])
def csrf_token():
    return resposta_token_csrf()


@auth_bp.route("/api/auth/login", methods=["POST"])
def login():
    d = request.get_json(silent=True) or {}
    login_input = (d.get("login") or "").strip()
    senha = d.get("senha")
    if senha is None:
        senha = ""
    senha = str(senha)

    if not login_input:
        return err("Informe o usuário.")
    if not senha.strip():
        return err("Informe a senha.")

    if login_bloqueado(login_input):
        return err(MSG_BLOQUEIO_LOGIN, 403)

    usuario = Usuario.query.filter_by(login=login_input).first()
    if usuario:
        senha_ok = verificar_senha(usuario.senha_hash, senha)
    else:
        verificar_senha(hash_equalizacao_tempo(), senha)
        senha_ok = False

    if not senha_ok:
        bloquear = registrar_falha_login(login_input)
        if bloquear:
            from src.auditoria import LOGIN_BLOQUEADO, registrar_auditoria
            registrar_auditoria(
                LOGIN_BLOQUEADO,
                "Usuario",
                entidade_id=usuario.id if usuario else None,
                escola_id=usuario.escola_id if usuario else None,
                dados_novos={"login": login_input.strip().lower()[:80]},
            )
            db.session.commit()
        if bloquear or login_bloqueado(login_input):
            return err(MSG_BLOQUEIO_LOGIN, 403)
        return err(MSG_CREDENCIAL_INVALIDA)

    limpar_falhas_login(login_input)
    session.permanent = True
    login_user(usuario, remember=False)
    return ok(user_payload(usuario))


@auth_bp.route("/api/auth/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    return ok({"ok": True})


@auth_bp.route("/api/auth/definir-senha", methods=["POST"])
@login_required
def definir_senha():
    if not current_user.primeiro_acesso:
        return err("Senha já definida.")
    d = request.get_json(silent=True) or {}
    s1 = d.get("senha")
    s2 = d.get("confirmar")
    if not senha_atende_minimo(s1):
        return err("A senha deve ter no mínimo 8 caracteres.")
    if s1 != s2:
        return err("As senhas não conferem.")
    current_user.senha_hash = hash_senha(s1)
    current_user.primeiro_acesso = False
    db.session.commit()
    return ok(user_payload(current_user))


@auth_bp.route("/api/auth/alterar-senha", methods=["POST"])
@login_required
def alterar_senha():
    d = request.get_json(silent=True) or {}
    senha_atual = d.get("senhaAtual")
    nova_senha = d.get("novaSenha")
    confirmar_senha = d.get("confirmarSenha")

    if not verificar_senha(current_user.senha_hash, senha_atual):
        return err("Senha atual incorreta.")
    if nova_senha != confirmar_senha:
        return err("Novas senhas não conferem.")
    if not senha_atende_minimo(nova_senha):
        return err("A senha deve ter no mínimo 8 caracteres.")

    current_user.senha_hash = hash_senha(nova_senha)
    db.session.commit()
    return ok({"mensagem": "Senha alterada com sucesso!"})
