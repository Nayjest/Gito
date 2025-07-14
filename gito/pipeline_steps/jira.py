import logging
import os

import git
from jira import JIRA, JIRAError

from gito.issue_trackers import IssueTrackerIssue, resolve_issue_key


def fetch_issue(issue_key, jira_url, username, api_token) -> IssueTrackerIssue | None:
    try:
        jira = JIRA(jira_url, basic_auth=(username, api_token))
        issue = jira.issue(issue_key)
        return IssueTrackerIssue(
            title=issue.fields.summary,
            description=issue.fields.description or "",
            url=f"{jira_url.rstrip('/')}/browse/{issue_key}"
        )
    except JIRAError as e:
        logging.error(
            f"Failed to fetch Jira issue {issue_key}: code {e.status_code} :: {e.text}"
        )
        return None
    except Exception as e:
        logging.error(f"Failed to fetch Jira issue {issue_key}: {e}")
        return None


def fetch_associated_issue(
    repo: git.Repo,
    jira_url=None,
    jira_username=None,
    jira_api_token=None,
    **kwargs
):
    """
    Pipeline step to fetch a Jira issue based on the current branch name.
    """
    jira_url = jira_url or os.getenv("JIRA_URL")
    jira_username = (
        jira_username
        or os.getenv("JIRA_USERNAME")
        or os.getenv("JIRA_USER")
        or os.getenv("JIRA_EMAIL")
    )
    jira_token = (
        jira_api_token
        or os.getenv("JIRA_API_TOKEN")
        or os.getenv("JIRA_API_KEY")
        or os.getenv("JIRA_TOKEN")
    )
    try:
        assert jira_url, "JIRA_URL is not set"
        assert jira_username, "JIRA_USERNAME is not set"
        assert jira_token, "JIRA_API_TOKEN is not set"
    except AssertionError as e:
        logging.error(f"Jira configuration error: {e}")
        return None
    issue_key = resolve_issue_key(repo)
    return dict(
        associated_issue=fetch_issue(issue_key, jira_url, jira_username, jira_token)
    ) if issue_key else None
