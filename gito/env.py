import logging
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import tomllib


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
        # Fallback: try to read version from local pyproject.toml (useful when
        # running with system Python that doesn't have the package installed).
        try:
            p = Path(__file__).resolve().parent.parent
            for _ in range(5):
                cfg = p / "pyproject.toml"
                if cfg.exists():
                    with open(cfg, "rb") as f:
                        data = tomllib.load(f)
                        # poetry stores version under [tool.poetry]
                        ver = data.get("tool", {}).get("poetry", {}).get("version")
                        if ver:
                            return ver
                p = p.parent
        except Exception:
            pass
        return "{Dev}"


class Env:
    logging_level: int = 1
    verbosity: int = 1
    gito_version: str = gito_version()
    working_folder = "."
