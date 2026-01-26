import os
import sys
import logging

import requests
import typer

from ..cli_base import app
from ..issue_trackers import resolve_issue_key
from ..utils.git import get_cwd_repo_or_fail


class LinearAPIError(Exception):
    """Custom exception for Linear API errors."""


def post_linear_comment(issue_key: str, text: str, api_key: str) -> dict:
    """
    Post a comment to a Linear issue using the Linear API.
    Args:
        issue_key (str): The ID of the Linear issue to comment on.
        text (str): The comment text to post.
        api_key (str): The Linear API key for authentication.
    Returns:
        dict: The JSON response from the Linear API.
    Raises:
        LinearAPIError: If the API request fails for any reason.
    """
    try:
        response = requests.post(
            'https://api.linear.app/graphql',
            headers={'Authorization': api_key, 'Content-Type': 'application/json'},
            json={
                'query': '''
                    mutation($issueId: String!, $body: String!) {
                        commentCreate(input: {issueId: $issueId, body: $body}) {
                            comment { id }
                        }
                    }
                ''',
                'variables': {'issueId': issue_key, 'body': text}
            }
        )
        response.raise_for_status()
        data = response.json()

        # Check for GraphQL-level errors
        if 'errors' in data:
            raise LinearAPIError(f"GraphQL error: {data['errors']}")

        return data

    except requests.exceptions.RequestException as e:
        raise LinearAPIError(f"Request failed: {e}") from e


def _process_text_input(text: str | None) -> str:
    if text == "-":
        # Explicit stdin request
        text = sys.stdin.read()
    elif text is None:
        if not sys.stdin.isatty():
            # Data is being piped in
            text = sys.stdin.read()
    if not text or not text.strip():
        raise typer.BadParameter(
            "Comment text is required. Provide text as argument or pipe from stdin."
        )

    text = text.replace('\\n', '\n').replace('\\t', '\t')
    return text


@app.command(help="Post a comment with specified text to the associated Linear issue.")
def linear_comment(
    text: str = typer.Argument(
        default=None,
        callback=_process_text_input,
        help="Comment text (supports Markdown). Use '-' to read from stdin.",
    ),
    issue_key: str = typer.Option(
        None,
        "--issue-key",
        "-k",
        help="Linear issue key (if not provided, will be resolved from the current repo branch)",
    ),
):
    api_key = os.getenv("LINEAR_API_KEY")
    if not api_key:
        logging.error("LINEAR_API_KEY environment variable is not set")
        raise typer.Exit(code=1)

    repo = get_cwd_repo_or_fail()
    key = issue_key or resolve_issue_key(repo)
    if not key:
        logging.error("Could not determine Linear issue key from the current branch or argument")
        raise typer.Exit(code=1)
    try:
        post_linear_comment(key, text, api_key)
    except LinearAPIError as e:
        logging.error("Failed to post comment to Linear issue %s: %s", key, str(e))
        raise typer.Exit(code=1)
    logging.info("Comment posted to Linear issue %s", key)
