from pathlib import Path
from .env import Env

PROJECT_GITO_FOLDER = ".gito"
PROJECT_CONFIG_FILE_NAME = "config.toml"
PROJECT_CONFIG_FILE_PATH = Path(".gito") / PROJECT_CONFIG_FILE_NAME
PROJECT_CONFIG_BUNDLED_DEFAULTS_FILE = Path(__file__).resolve().parent / PROJECT_CONFIG_FILE_NAME
HOME_ENV_PATH = Path("~/.gito/.env").expanduser()
JSON_REPORT_FILE_NAME = "code-review-report.json"
GITHUB_MD_REPORT_FILE_NAME = "code-review-report.md"
EXECUTABLE = "gito"
TEXT_ICON_URL = 'https://raw.githubusercontent.com/Nayjest/Gito/main/press-kit/logo/gito-bot-1_64top.png'  # noqa: E501
HTML_TEXT_ICON = f'<a href="https://github.com/Nayjest/Gito"><img src="{TEXT_ICON_URL}" align="left" width=64 height=50 title="Gito v{Env.gito_version}"/></a>'  # noqa: E501
HTML_CR_COMMENT_MARKER = '<!-- GITO_COMMENT:CODE_REVIEW_REPORT -->'
REFS_VALUE_ALL = '!all'
