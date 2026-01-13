"""Tests for Gito CI deployment."""
from pathlib import Path

import pytest
from git import Repo

from gito.commands.deploy import deploy
from gito.bootstrap import bootstrap
from gito.identify_git_provider import GitProvider


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


def test_deploy_github_creates_workflow_files(github_repo, monkeypatch):
    """Deploying to GitHub creates expected workflow files."""
    bootstrap()
    monkeypatch.setattr("builtins.input", lambda _: "")
    monkeypatch.setattr(
        "gito.commands.deploy.identify_git_provider",
        lambda _: GitProvider.GITLAB
    )

    deploy(api_type="anthropic", commit=False, model='claude-opus-4-5')

    workflow = Path(".github/workflows/gito-code-review.yml")
    assert workflow.exists()

    content = workflow.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" in content
    assert "gito" in content.lower()


def test_deploy_gitlab_creates_workflow_files(gitlab_repo, monkeypatch):
    """Deploying to GitLab creates expected workflow files."""
    bootstrap()
    monkeypatch.setattr("builtins.input", lambda _: "")

    deploy(api_type="anthropic", commit=False, model='claude-opus-4-5')

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
    deploy(api_type="anthropic", commit=False, model='claude-opus-4-5')

    # Second deploy should fail
    result = deploy(api_type="anthropic", commit=False, rewrite=False)

    assert result is False


def test_deploy_rewrite_overwrites_existing(github_repo, monkeypatch):
    """Deploying with --rewrite replaces existing workflow."""
    bootstrap()
    monkeypatch.setattr("builtins.input", lambda _: "")

    deploy(api_type="anthropic", commit=False, model='claude-opus-4-5')
    deploy(api_type="openai", commit=False, rewrite=True, model='gpt-3.5-turbo')

    workflow = Path(".github/workflows/gito-code-review.yml")
    content = workflow.read_text(encoding="utf-8")

    assert "OPENAI_API_KEY" in content
    assert "gpt-3.5-turbo" in content
