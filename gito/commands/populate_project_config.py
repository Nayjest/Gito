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
def populate_project_config(
    force: bool = typer.Option(
        False, "--force", help="Overwrite an existing project configuration"
    ),
):
    repo = get_cwd_repo_or_fail()
    config_path = Path(repo.working_tree_dir) / PROJECT_CONFIG_FILE_PATH
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        # "xb" fails if the configuration already exists, "wb" overwrites it
        with config_path.open("wb" if force else "xb") as f:
            f.write(PROJECT_CONFIG_BUNDLED_DEFAULTS_FILE.read_bytes())
    except OSError as exc:
        hint = "\nUse --force to overwrite it." if config_path.is_file() else ""
        mc.ui.error(f"Can't write {mc.utils.file_link(config_path)}: {exc.strerror or exc}.{hint}")
        raise typer.Exit(code=1) from exc

    print(mc.ui.green("Project configuration created:"), mc.utils.file_link(config_path))
