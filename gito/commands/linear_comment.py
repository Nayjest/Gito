import os
import sys
import logging

import requests
import typer

from ..cli_base import app, arg_refs
from ..issue_trackers import resolve_issue_key
from ..utils.git import get_cwd_repo_or_fail


def post_linear_comment(issue_key: str, text: str, api_key: str):
    """
    Post a comment to a Linear issue using the Linear API.
    Args:
        issue_key (str): The ID of the Linear issue to comment on.
        text (str): The comment text to post.
        api_key (str): The Linear API key for authentication.
    Returns:
        dict: The JSON response from the Linear API.
    """
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
    return response.json()


@app.command(help="Post a comment with specified text to the associated Linear issue.")
def linear_comment(
    text: str = typer.Argument(None),
    refs: str = arg_refs(),
):
    if text is None or text == "-":
        # Read from stdin if no text provided
        text = sys.stdin.read()

    if not text or not text.strip():
        typer.echo("Error: No comment text provided.", err=True)
        raise typer.Exit(code=1)

    api_key = os.getenv("LINEAR_API_KEY")
    if not api_key:
        logging.error("LINEAR_API_KEY environment variable is not set")
        return

    repo = get_cwd_repo_or_fail()
    key = resolve_issue_key(repo)
    post_linear_comment(key, text, api_key)
    logging.info("Comment posted to Linear issue %s", key)
