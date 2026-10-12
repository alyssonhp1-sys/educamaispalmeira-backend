import sys
import os

# Adiciona o diretório atual ao caminho do Python
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from src.config import ConfiguracaoInsegura, flag_env, resolver_ambiente
from src import create_app

try:
    app = create_app()
except ConfiguracaoInsegura as exc:
    raise SystemExit(str(exc)) from exc

if __name__ == "__main__":
    ambiente = resolver_ambiente()
    if ambiente == "production":
        raise SystemExit(
            "Não use python app.py em produção. "
            "Use o servidor WSGI: python -m waitress --listen=127.0.0.1:8000 wsgi:app"
        )
    debug = flag_env("EDUCAMAIS_DEBUG") and ambiente != "production"
    port = int(os.environ.get("EDUCAMAIS_PORT", "5000"))
    app.run(host="127.0.0.1", port=port, debug=debug)
