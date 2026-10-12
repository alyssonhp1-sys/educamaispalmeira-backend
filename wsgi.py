"""Entrypoint WSGI de produção.

Não executa migrations, não cria banco e não cria usuário demo.
A configuração vem do ambiente; falhas críticas encerram o processo.
"""
import os
import sys

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from src.config import ConfiguracaoInsegura
from src import create_app

try:
    app = create_app()
except ConfiguracaoInsegura as exc:
    raise SystemExit(str(exc)) from exc
