"""Tests for Gito CI deployment."""

from pathlib import Path

import click
import microcore as mc
import pytest
from git import Repo
from typer.main import get_command

import gito.cli  # noqa: F401  # registers CLI commands on the shared app
from gito.cli_base import app, command_requires_llm
from gito.commands.deploy import _configure_llm, deploy
from gito.bootstrap import bootstrap
from gito.utils.git_platform.platform_types import PlatformType


@pytest.mark.parametrize(
    "subcommand, requires_llm",
    [
        ("deploy", False),  # marked @runs_without_llm
        ("init", False),  # alias shares the marker
        ("version", False),  # another marked command
        ("review", True),  # performs inference
        ("repl", True),  # exposes live LLM access
        (None, True),  # bare invocation
    ],
)
def test_command_requires_llm_marker(subcommand, requires_llm):
    """@runs_without_llm commands report no LLM requirement; others do (issue #288)."""
    ctx = click.Context(get_command(app))
    ctx.invoked_subcommand = subcommand
    assert command_requires_llm(ctx) is requires_llm


@pytest.fixture
def github_repo(tmp_path, monkeypatch):
    """Create a minimal GitHub repository."""
    repo = Repo.init(tmp_path)
    repo.create_remote("origin", "https://github.com/test/repo.git")

    # Initial commit (required for branch operations)
    readme = tmp_path / "README.md"
    readme.write_text("# Test Repo\n")
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")

    monkeypatch.chdir(tmp_path)
    yield repo


@pytest.fixture
def gitlab_repo(tmp_path, monkeypatch):
    """Create a minimal GitLab repository."""
    repo = Repo.init(tmp_path)
    repo.create_remote("origin", "https://gitlab.com/test/repo.git")

    readme = tmp_path / "README.md"
    readme.write_text("# Test Repo\n")
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")

    monkeypatch.chdir(tmp_path)
    yield repo


@pytest.fixture
def no_llm_config(tmp_path, monkeypatch):
    """Simulate an environment without any LLM API credentials configured."""
    monkeypatch.setattr("gito.bootstrap.HOME_ENV_PATH", str(tmp_path / "nonexistent.env"))
    for var in (
        "LLM_API_KEY",
        "LLM_API_TYPE",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_bootstrap_requires_llm_config_by_default(no_llm_config):
    """Without LLM credentials, the default bootstrap aborts (LLM is required for review)."""
    with pytest.raises(SystemExit):
        bootstrap()


def test_bootstrap_skips_llm_config_for_deploy(no_llm_config):
    """`gito deploy` must bootstrap without LLM credentials (issue #288)."""
    bootstrap(require_llm_config=False)  # must not raise SystemExit


def test_bootstrap_ignores_llm_cli_for_non_llm_command(monkeypatch):
    """Inference-free commands must not warn about an unused CLI LLM config (#297)."""
    monkeypatch.setenv("LLM_API_TYPE", "cli")
    monkeypatch.setenv("LLM_CLI", "echo mocked")
    bootstrap(require_llm_config=False)

    assert mc.config().LLM_API_TYPE == mc.ApiType.NONE
    assert mc.config().LLM_CLI is None


def test_deploy_uses_current_openai_default_model():
    """`--model default` selects the current recommended OpenAI model."""
    api_type, secret_name, model = _configure_llm("openai", model="default")

    assert api_type == mc.ApiType.OPENAI
    assert secret_name == "OPENAI_API_KEY"
    assert model == "gpt-5.6"


def test_deploy_github_creates_workflow_files(github_repo, monkeypatch):
    """Deploying to GitHub creates expected workflow files."""
    bootstrap()
    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr("gito.commands.deploy.identify_git_platform", lambda _: PlatformType.GITHUB)

    deploy(api_type="anthropic", commit=False, model="claude-opus-4-6")

    workflow = Path(".github/workflows/gito-code-review.yml")
    assert workflow.exists()

    content = workflow.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" in content
    assert "gito" in content.lower()


def test_deploy_gitlab_creates_workflow_files(gitlab_repo, monkeypatch):
    """Deploying to GitLab creates expected workflow files."""
    bootstrap()
    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr("gito.commands.deploy.identify_git_platform", lambda _: PlatformType.GITLAB)

    deploy(api_type="anthropic", commit=False, model="claude-opus-4-6")

    workflow = Path(".gitlab/ci/gito-code-review.yml")
    gitlab_ci = Path(".gitlab-ci.yml")

    assert workflow.exists()
    assert gitlab_ci.exists()

    content = workflow.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" in content
    assert "GITLAB_ACCESS_TOKEN" in content


def test_deploy_does_not_overwrite_existing(github_repo, monkeypatch):
    """Deploying fails if workflow already exists (without --rewrite)."""
    bootstrap()
    monkeypatch.setattr("builtins.input", lambda _: "")

    # First deploy
    deploy(api_type="anthropic", commit=False, model="claude-opus-4-6")

    # Second deploy should fail
    result = deploy(api_type="anthropic", commit=False, rewrite=False)

    assert result is False


def test_deploy_rewrite_overwrites_existing(github_repo, monkeypatch):
    """Deploying with --rewrite replaces existing workflow."""
    bootstrap()
    monkeypatch.setattr("builtins.input", lambda _: "")

    deploy(api_type="anthropic", commit=False, model="claude-opus-4-6")
    deploy(api_type="openai", commit=False, rewrite=True, model="gpt-3.5-turbo")

    workflow = Path(".github/workflows/gito-code-review.yml")
    content = workflow.read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" in content
    assert "gpt-3.5-turbo" in content
