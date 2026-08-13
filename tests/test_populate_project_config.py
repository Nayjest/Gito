"""Tests for populating a repository's project configuration."""

from git import Repo
from typer.testing import CliRunner

import gito.cli  # noqa: F401  # registers CLI commands on the shared app
from gito.cli_base import app
from gito.constants import PROJECT_CONFIG_BUNDLED_DEFAULTS_FILE, PROJECT_CONFIG_FILE_PATH

runner = CliRunner()


def test_populate_project_config_copies_bundled_defaults(tmp_path, monkeypatch):
    """The command creates an exact copy of the bundled configuration."""
    Repo.init(tmp_path)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["populate-project-config"])

    config_path = tmp_path / PROJECT_CONFIG_FILE_PATH
    assert result.exit_code == 0
    assert config_path.read_bytes() == PROJECT_CONFIG_BUNDLED_DEFAULTS_FILE.read_bytes()
    assert "Project configuration created" in result.stdout


def test_populate_project_config_does_not_overwrite_existing(tmp_path, monkeypatch):
    """An existing project configuration is preserved."""
    Repo.init(tmp_path)
    monkeypatch.chdir(tmp_path)
    config_path = tmp_path / PROJECT_CONFIG_FILE_PATH
    config_path.parent.mkdir()
    config_path.write_text("retries = 7\n", encoding="utf-8")

    result = runner.invoke(app, ["populate-project-config"])

    assert result.exit_code == 1
    assert config_path.read_text(encoding="utf-8") == "retries = 7\n"
    assert "Project configuration already exists" in result.stdout


def test_populate_project_config_requires_repository(tmp_path, monkeypatch):
    """The command does not write outside a repository root."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["populate-project-config"])

    assert result.exit_code == 2
    assert not (tmp_path / PROJECT_CONFIG_FILE_PATH).exists()
    assert "Current folder is not a Git repository" in result.stdout
