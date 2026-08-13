"""Create an editable project configuration from Gito's bundled defaults."""

from pathlib import Path

import microcore as mc
import typer

from ..cli_base import app, runs_without_llm
from ..constants import PROJECT_CONFIG_BUNDLED_DEFAULTS_FILE, PROJECT_CONFIG_FILE_PATH
from ..utils.git import get_cwd_repo_or_fail


@app.command(
    name="populate-project-config",
    help="Copy Gito's bundled configuration to .gito/config.toml in the current repository.",
)
@runs_without_llm
def populate_project_config() -> Path:
    """Copy the bundled defaults into the current repository without overwriting a config."""
    repo = get_cwd_repo_or_fail()
    config_path = Path(repo.working_tree_dir) / PROJECT_CONFIG_FILE_PATH

    config_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        bundled_config = PROJECT_CONFIG_BUNDLED_DEFAULTS_FILE.read_bytes()
        with config_path.open("xb") as project_config:
            project_config.write(bundled_config)
    except FileExistsError as exc:
        mc.ui.error(
            f"Project configuration already exists at {mc.utils.file_link(config_path)}.\n"
            "Remove or rename it before populating a fresh copy."
        )
        raise typer.Exit(code=1) from exc

    print(mc.ui.green("Project configuration created:"), mc.utils.file_link(config_path))
    return config_path
