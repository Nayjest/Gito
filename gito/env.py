import logging
from importlib.metadata import version, PackageNotFoundError


def gito_version() -> str:
    """
    Retrieve the current version of gito.bot package.
    Returns:
        str: The version string of the gito.bot package, or "{Dev}" if not found.
    """
    try:
        return version("gito.bot")
    except PackageNotFoundError:
        logging.warning("Could not retrieve gito.bot version.")
        return "{Dev}"


class Env:
    logging_level: int = 1
    verbosity: int = 1
    gito_version: str = gito_version()
    working_folder = "."
