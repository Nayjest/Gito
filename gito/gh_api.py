import os
import logging

import requests
from fastcore.basics import AttrDict  # objects returned by ghapi


def resolve_gh_token(token_or_none: str | None = None) -> str | None:
    return token_or_none or os.getenv("GITHUB_TOKEN", None) or os.getenv("GH_TOKEN", None)


def post_gh_comment(
    gh_repository: str,  # e.g. "owner/repo"
    pr_or_issue_number: int,
    gh_token: str,
    text: str,
) -> bool:
    """
    Post a comment to a GitHub pull request or issue.
    Arguments:
        gh_repository (str): The GitHub repository in the format "owner/repo".
        pr_or_issue_number (int): The pull request or issue number.
        gh_token (str): GitHub personal access token with permissions to post comments.
        text (str): The comment text to post.
    Returns:
        True if the comment was posted successfully, False otherwise.
    """
    api_url = f"https://api.github.com/repos/{gh_repository}/issues/{pr_or_issue_number}/comments"
    headers = {
        "Authorization": f"token {gh_token}",
        "Accept": "application/vnd.github+json",
    }
    data = {"body": text}

    resp = requests.post(api_url, headers=headers, json=data)
    if 200 <= resp.status_code < 300:
        logging.info(f"Posted review comment to #{pr_or_issue_number} in {gh_repository}")
        return True
    else:
        logging.error(f"Failed to post comment: {resp.status_code} {resp.reason}\n{resp.text}")
        return False


def hide_gh_comment(
    comment: dict | str,
    token: str = None,
    reason: str = "OUTDATED"
) -> bool:
    """
    Hide a GitHub comment using GraphQL API with specified reason.
    Args:
        comment (dict | str):
            The comment to hide,
            either as a object returned from ghapi or a string node ID.
            note: comment.id is not the same as node_id.
        token (str): GitHub personal access token with permissions to minimize comments.
        reason (str): The reason for hiding the comment, e.g., "OUTDATED".
    """
    comment_node_id = comment.node_id if isinstance(comment, AttrDict) else comment
    token = resolve_gh_token(token)
    mutation = """
    mutation($commentId: ID!, $reason: ReportedContentClassifiers!) {
        minimizeComment(input: {subjectId: $commentId, classifier: $reason}) {
            minimizedComment { isMinimized }
        }
    }"""

    response = requests.post(
        "https://api.github.com/graphql",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "query": mutation,
            "variables": {"commentId": comment_node_id, "reason": reason}
        }
    )
    success = (
        response.status_code == 200
        and response.json().get("data", {}).get("minimizeComment") is not None
    )
    if not success:
        logging.error(
            f"Failed to hide comment {comment_node_id}: "
            f"{response.status_code} {response.reason}\n{response.text}"
        )
    return success
