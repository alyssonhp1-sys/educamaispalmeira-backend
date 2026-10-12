from src.uploads import extensao_permitida


def allowed_file(filename):
    return extensao_permitida(filename)
