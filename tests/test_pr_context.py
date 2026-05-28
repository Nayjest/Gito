from unittest.mock import MagicMock, patch

from gito.pr_context import _fetch_pr_context
from gito.report_struct import ReviewTarget
from gito.project_config import ProjectConfig


def test_fetch_pr_context_no_pr_id():
    """Should return empty string when no pull_request_id is set."""
    target = ReviewTarget(pull_request_id=None)
    config = ProjectConfig()
    result = _fetch_pr_context(target, MagicMock(), config)
    assert result == ""


def test_fetch_pr_context_disabled_by_config():
    """Should return empty string when include_pr_context is False."""
    target = ReviewTarget(pull_request_id="123")
    config = ProjectConfig(include_pr_context=False)
    result = _fetch_pr_context(target, MagicMock(), config)
    assert result == ""


def test_fetch_pr_context_success():
    """Should return formatted PR context when API calls succeed."""
    target = ReviewTarget(pull_request_id="42")
    config = ProjectConfig(include_pr_context=True)

    mock_pr = {
        "title": "Fix login bug",
        "body": "This PR fixes the login issue described in #100",
    }
    mock_comments = [
        {"user": {"login": "alice"}, "body": "Looks good!"},
        {"user": {"login": "bob"}, "body": "Please fix the typo"},
    ]

    mock_api = MagicMock()
    mock_api.pulls.get.return_value = mock_pr
    mock_api.issues.list_comments.return_value = mock_comments

    mock_repo = MagicMock()

    with patch("gito.pr_context.gh_api", return_value=mock_api):
        result = _fetch_pr_context(target, mock_repo, config)

    assert "PR Title: Fix login bug" in result
    assert "PR Description:" in result
    assert "This PR fixes the login issue" in result
    assert "@alice: Looks good!" in result
    assert "@bob: Please fix the typo" in result


def test_fetch_pr_context_api_failure():
    """Should return empty string when API call fails."""
    target = ReviewTarget(pull_request_id="42")
    config = ProjectConfig(include_pr_context=True)

    with patch("gito.pr_context.gh_api", side_effect=Exception("API error")):
        result = _fetch_pr_context(target, MagicMock(), config)

    assert result == ""


def test_fetch_pr_context_no_description():
    """Should handle PR with no description body."""
    target = ReviewTarget(pull_request_id="42")
    config = ProjectConfig(include_pr_context=True)

    mock_pr = {"title": "Update README", "body": ""}
    mock_comments = []

    mock_api = MagicMock()
    mock_api.pulls.get.return_value = mock_pr
    mock_api.issues.list_comments.return_value = mock_comments

    with patch("gito.pr_context.gh_api", return_value=mock_api):
        result = _fetch_pr_context(target, MagicMock(), config)

    assert "PR Title: Update README" in result
    assert "PR Description" not in result
