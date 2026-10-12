"""Ambiente padrão da suíte: testing + demo explícito + chave de teste."""
import os

from tests.support import preparar_ambiente_teste

preparar_ambiente_teste(demo=True)
