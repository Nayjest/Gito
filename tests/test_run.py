import git
from gito.utils.git_platform.platform_types import PlatformType
from gito.report_struct import ReviewTarget
from typer.testing import CliRunner
from unittest.mock import AsyncMock
from gito.cli import app_no_subcommand
from gito.cli_base import app


runner = CliRunner()


def test_review_command_calls_review(monkeypatch):
    mock_review = AsyncMock()
    monkeypatch.setattr("gito.cli.review", mock_review)
    result = runner.invoke(
        app,
        ["review", "--what", "HEAD", "--against", "HEAD~1"],
    )
    assert result.exit_code == 0
    repo = git.Repo(".", search_parent_directories=True)
    commit_sha = repo.head.commit.hexsha
    try:
        active_branch = repo.active_branch.name
    except TypeError:
        active_branch = None
    review_target = ReviewTarget(
        git_platform_type=PlatformType.GITHUB,
        repo_url="https://github.com/Nayjest/Gito",
        what="HEAD",
        against="HEAD~1",
        commit_sha=commit_sha,
        filters="",
        pull_request_id=None,
        active_branch=active_branch,
    )
    mock_review.assert_awaited_once_with(
        repo=repo,
        target=review_target,
        out_folder=".",
    )


def test_calls_review(monkeypatch):
    mock_review = AsyncMock()
    monkeypatch.setattr("gito.cli.review", mock_review)
    result = runner.invoke(
        app_no_subcommand,
        ["HEAD", "--filters", "*.py,*.md"],
    )
    assert result.exit_code == 0
    repo = git.Repo(".", search_parent_directories=True)
    commit_sha = repo.head.commit.hexsha
    try:
        active_branch = repo.active_branch.name
    except TypeError:
        active_branch = None
    review_target = ReviewTarget(
        git_platform_type=PlatformType.GITHUB,
        repo_url="https://github.com/Nayjest/Gito",
        what="HEAD",
        against=None,
        commit_sha=commit_sha,
        filters="*.py,*.md",
        pull_request_id=None,
        active_branch=active_branch,
    )
    mock_review.assert_awaited_once_with(
        repo=repo,
        target=review_target,
        out_folder=".",
    )
