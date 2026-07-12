from unittest.mock import MagicMock, patch

from gito.issue_trackers import IssueTrackerIssue
from gito.pipeline_steps.jira import fetch_associated_issue, fetch_issue


def make_jira_issue(summary="Issue summary", description="Issue description"):
    issue = MagicMock()
    issue.fields.summary = summary
    issue.fields.description = description
    return issue


def test_fetch_issue_uses_basic_auth_when_username_is_provided():
    jira_issue = make_jira_issue()
    with patch("gito.pipeline_steps.jira.JIRA") as jira_client:
        jira_client.return_value.issue.return_value = jira_issue

        result = fetch_issue("PROJ-123", "https://jira.example.com", "user@example.com", "token")

    jira_client.assert_called_once_with(
        "https://jira.example.com", basic_auth=("user@example.com", "token")
    )
    assert result == IssueTrackerIssue(
        title="Issue summary",
        description="Issue description",
        url="https://jira.example.com/browse/PROJ-123",
    )


def test_fetch_issue_uses_token_auth_when_username_is_omitted():
    jira_issue = make_jira_issue(description=None)
    with patch("gito.pipeline_steps.jira.JIRA") as jira_client:
        jira_client.return_value.issue.return_value = jira_issue

        result = fetch_issue("PROJ-123", "https://jira.example.com/", None, "token")

    jira_client.assert_called_once_with("https://jira.example.com/", token_auth="token")
    assert result == IssueTrackerIssue(
        title="Issue summary",
        description="",
        url="https://jira.example.com/browse/PROJ-123",
    )


def test_fetch_associated_issue_allows_token_only_configuration(monkeypatch):
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.setenv("JIRA_TOKEN", "token")
    monkeypatch.delenv("JIRA_USERNAME", raising=False)
    monkeypatch.delenv("JIRA_USER", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)

    with patch("gito.pipeline_steps.jira.resolve_issue_key", return_value="PROJ-123"), patch(
        "gito.pipeline_steps.jira.fetch_issue", return_value=MagicMock()
    ) as fetch:
        result = fetch_associated_issue(MagicMock())

    fetch.assert_called_once_with("PROJ-123", "https://jira.example.com", None, "token")
    assert "associated_issue" in result
