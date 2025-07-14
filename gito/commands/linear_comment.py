import os
import sys
import logging

import typer
from git import Repo

from ..cli_base import app, arg_refs
from ..issue_trackers import resolve_issue_key

import requests


def post_linear_comment(issue_key, text, api_key):
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


@app.command()
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

    repo = Repo(".")
    key = resolve_issue_key(repo)
    post_linear_comment(key, text, api_key)
    logging.info("Comment posted to Linear issue %s", key)
