import logging
from importlib.metadata import version, PackageNotFoundError


def CRACK_version() -> str:
    """
    Retrieve the current version of CRACK.bot package.
    Returns:
        str: The version string of the CRACK.bot package, or "{Dev}" if not found.
    """
    try:
        return version("CRACK.bot")
    except PackageNotFoundError:
        logging.warning("Could not retrieve CRACK.bot version.")
        return "{Dev}"


class Env:
    logging_level: int = 1
    verbosity: int = 1
    CRACK_version: str = CRACK_version()
    working_folder = "."
