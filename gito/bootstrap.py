import os
import sys
import io
import logging
from datetime import datetime
from pathlib import Path

import microcore as mc

from .utils import is_running_in_github_action
from .constants import HOME_ENV_PATH, EXECUTABLE, PROJECT_GITO_FOLDER
from .env import Env


def setup_logging(log_level: int = logging.INFO):
    class CustomFormatter(logging.Formatter):
        def format(self, record):
            dt = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
            message, level_name = record.getMessage(), record.levelname
            if record.levelno == logging.WARNING:
                message = mc.ui.yellow(message)
                level_name = mc.ui.yellow(level_name)
            if record.levelno >= logging.ERROR:
                message = mc.ui.red(message)
                level_name = mc.ui.red(level_name)
            return f"{dt} {level_name}: {message}"

    handler = logging.StreamHandler()
    handler.setFormatter(CustomFormatter())
    logging.basicConfig(level=log_level, handlers=[handler])


def bootstrap(verbosity: int = 1):
    """Bootstrap the application with the environment configuration."""
    log_levels_by_verbosity = {
        0: logging.CRITICAL,
        1: logging.INFO,
        2: logging.DEBUG,
        3: logging.DEBUG,
    }
    Env.verbosity = verbosity
    Env.logging_level = log_levels_by_verbosity.get(verbosity, logging.INFO)
    setup_logging(Env.logging_level)
    logging.info("Bootstrapping... "+mc.ui.gray(f"[verbosity={verbosity}]"))

    # cp1251 is used on Windows when redirecting output
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    try:
        mc.configure(
            DOT_ENV_FILE=HOME_ENV_PATH,
            USE_LOGGING=verbosity >= 1,
            EMBEDDING_DB_TYPE=mc.EmbeddingDbType.NONE,
            PROMPT_TEMPLATES_PATH=[
                PROJECT_GITO_FOLDER,
                Path(__file__).parent / "tpl"
            ],
        )
        if verbosity > 1:
            mc.logging.LoggingConfig.STRIP_REQUEST_LINES = None
        else:
            mc.logging.LoggingConfig.STRIP_REQUEST_LINES = [300, 15]

    except mc.LLMConfigError as e:
        msg = str(e)
        if is_running_in_github_action():
            ref = os.getenv("GITHUB_WORKFLOW_REF", "")
            if ref:
                # example value: 'owner/repo/.github/workflows/ai-code-review.yml@refs/pull/1/merge'
                ref = ref.split("@")[0]
                ref = ref.split(".github/workflows/")[-1]
                ref = f" (.github/workflows/{ref})"
            msg += (
                f"\nPlease check your GitHub Action Secrets "
                f"and `env` configuration section of the corresponding workflow step{ref}."
            )
        else:
            msg += (
                f"\nPlease run '{EXECUTABLE} setup' "
                "to configure LLM API access (API keys, model, etc)."
            )
        print(mc.ui.red(msg))
        raise SystemExit(2)
    except Exception as e:
        logging.error(f"Unexpected configuration error: {e}")
        raise SystemExit(3)
