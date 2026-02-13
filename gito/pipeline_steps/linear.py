import logging
import os
import requests

import git

from gito.issue_trackers import IssueTrackerIssue, resolve_issue_key


def fetch_issue(issue_key: str, api_key: str = None) -> IssueTrackerIssue | None:
    """
    Fetch a Linear issue using GraphQL API.
    """
    api_key = api_key or os.getenv("LINEAR_API_KEY")
    try:
        url = "https://api.linear.app/graphql"
        headers = {"Authorization": f"{api_key}", "Content-Type": "application/json"}

        query = """
            query Issues($teamKey: String!, $issueNumber: Float) {
                issues(filter: {team: {key: {eq: $teamKey}}, number: {eq: $issueNumber}}) {
                    nodes {
                        id
                        identifier
                        title
                        description
                        url
                    }
                }
            }
        """
        team_key, issue_number = issue_key.split("-")
        response = requests.post(
            url,
            json={
                "query": query,
                "variables": {"teamKey": team_key, "issueNumber": int(issue_number)},
            },
            headers=headers,
        )
        response.raise_for_status()
        data = response.json()

        if "errors" in data:
            logging.error(f"Linear API error: {data['errors']}")
            return None

        nodes = data.get("data", {}).get("issues", {}).get("nodes", [])
        if not nodes:
            logging.error(f"Linear issue {issue_key} not found")
            return None

        issue = nodes[0]
        return IssueTrackerIssue(
            title=issue["title"], description=issue.get("description") or "", url=issue["url"]
        )

    except requests.HTTPError as e:
        logging.error(f"Failed to fetch Linear issue {issue_key}: {e}")
        logging.error(f"Response body: {response.text}")
        return None


def fetch_associated_issue(repo: git.Repo, api_key=None, **kwargs):
    """
    Pipeline step to fetch a Linear issue based on the current branch name.
    """
    api_key = api_key or os.getenv("LINEAR_API_KEY")
    if not api_key:
        logging.error("LINEAR_API_KEY environment variable is not set")
        return

    issue_key = resolve_issue_key(repo)
    return dict(associated_issue=fetch_issue(issue_key, api_key)) if issue_key else None
