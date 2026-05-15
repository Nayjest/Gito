"""Bootstrap module for initializing the Gito application environment."""

import atexit
import os
import sys
import io
import logging
import tempfile
from datetime import datetime
from pathlib import Path

import microcore as mc

from .utils.git_platform.gitlab import is_running_in_gitlab_ci
from .utils.git_platform.github import is_running_in_github_action
from .constants import HOME_ENV_PATH, EXECUTABLE, PROJECT_GITO_FOLDER, DEFAULT_MAX_CONCURRENT_TASKS
from .env import Env


def _is_empty_sentinel(value: str | None) -> bool:
    """Return True when an env value is the string form of a missing value."""
    if value is None:
        return False
    return value.strip().strip("\"'").lower() in {"none", "null"}


def _sanitize_env_vars() -> None:
    """Remove invalid shell env values that should behave like 'unset'."""
    if _is_empty_sentinel(os.environ.get("LLM_API_PLATFORM")):
        logging.warning("Ignoring invalid LLM_API_PLATFORM shell value.")
        os.environ.pop("LLM_API_PLATFORM", None)


def _sanitized_home_env_path(home_env_path: Path) -> Path:
    """Return a temp env file path when persisted config contains invalid sentinels."""
    if not home_env_path.exists():
        return home_env_path

    changed = False
    sanitized_lines: list[str] = []
    for line in home_env_path.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("LLM_API_PLATFORM="):
            _, value = stripped.split("=", 1)
            if _is_empty_sentinel(value):
                changed = True
                continue
        sanitized_lines.append(line)

    if not changed:
        return home_env_path

    logging.warning("Ignoring invalid LLM_API_PLATFORM value in %s.", home_env_path)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".env", prefix="gito-", delete=False
    ) as temp_file:
        temp_file.writelines(sanitized_lines)
    atexit.register(lambda path=temp_file.name: os.path.exists(path) and os.unlink(path))
    return Path(temp_file.name)


def setup_logging(log_level: int = logging.INFO):
    """Setup custom CLI logging format with colored output."""

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

            formatted_message = f"{dt} {level_name}: {message}"
            if record.exc_info:
                formatted_message += "\n" + self.formatException(record.exc_info)
            return formatted_message

    handler = logging.StreamHandler()
    handler.setFormatter(CustomFormatter())
    logging.basicConfig(level=log_level, handlers=[handler])


def bootstrap(verbosity: int = 1):
    """Bootstrap the application with the environment configuration."""
    log_levels_by_verbosity = {
        0: logging.CRITICAL,
        1: logging.INFO,
        2: logging.INFO,
        3: logging.DEBUG,
    }
    Env.verbosity = verbosity
    Env.logging_level = log_levels_by_verbosity.get(verbosity, logging.INFO)
    setup_logging(Env.logging_level)
    logging.info(
        f"Bootstrapping Gito v{Env.gito_version}... " + mc.ui.gray(f"[verbosity={verbosity}]")
    )

    # cp1251 is used on Windows when redirecting output
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

    try:
        _sanitize_env_vars()
        mc.configure(
            DOT_ENV_FILE=_sanitized_home_env_path(HOME_ENV_PATH),
            USE_LOGGING=verbosity >= 1,
            EMBEDDING_DB_TYPE=mc.EmbeddingDbType.NONE,
            PROMPT_TEMPLATES_PATH=[PROJECT_GITO_FOLDER, Path(__file__).parent / "tpl"],
        )
        if mc.config().MAX_CONCURRENT_TASKS is None:
            mc.config().MAX_CONCURRENT_TASKS = DEFAULT_MAX_CONCURRENT_TASKS
        if verbosity > 1:
            mc.logging.LoggingConfig.STRIP_REQUEST_LINES = None
        else:
            mc.logging.LoggingConfig.STRIP_REQUEST_LINES = [300, 15]

    except mc.LLMConfigError as e:
        msg = str(e).strip()
        if not msg.endswith((".", "!", "?")):
            msg += "."
        if is_running_in_github_action():
            ref = os.getenv("GITHUB_WORKFLOW_REF", "")
            if ref:
                # example value: 'owner/repo/.github/workflows/ai-code-review.yml@refs/pull/1/merge'
                ref = ref.split("@")[0]
                ref = ref.split(".github/workflows/")[-1]
                ref = f" (.github/workflows/{ref})"
            msg += (
                f"\nPlease check your GitHub Action secrets "
                f"and `env` configuration section of the corresponding workflow step{ref}.\n"
                f"See https://github.com/Nayjest/Gito/blob/main/documentation/github_setup.md "
                f"for details."
            )
        elif is_running_in_gitlab_ci():
            # @TODO add link to GitLab setup article
            msg += (
                "\nPlease check your GitLab CI/CD Variables "
                "and `variables` configuration section of the corresponding job."
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
