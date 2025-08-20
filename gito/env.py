from importlib.metadata import version


class Env:
    logging_level: int = 1
    verbosity: int = 1
    gito_version: str = version("gito.bot")
    working_folder = "."
