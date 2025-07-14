import logging
import os
from time import sleep

import typer
from ghapi.core import GhApi

from ..cli_base import app
from ..constants import GITHUB_MD_REPORT_FILE_NAME, HTML_CR_COMMENT_MARKER
from ..gh_api import (
    post_gh_comment,
    resolve_gh_token,
    hide_gh_comment,
)
from ..project_config import ProjectConfig


@app.command(name="github-comment", help="Leave a GitHub PR comment with the review.")
def post_github_cr_comment(
    md_report_file: str = typer.Option(default=None),
    pr: int = typer.Option(default=None),
    gh_repo: str = typer.Option(default=None, help="owner/repo"),
    token: str = typer.Option(
        "", help="GitHub token (or set GITHUB_TOKEN env var)"
    ),
):
    """
    Leaves a comment with the review on the current GitHub pull request.
    """
    file = md_report_file or GITHUB_MD_REPORT_FILE_NAME
    if not os.path.exists(file):
        logging.error(f"Review file not found: {file}, comment will not be posted.")
        raise typer.Exit(4)

    with open(file, "r", encoding="utf-8") as f:
        body = f.read()

    token = resolve_gh_token(token)
    if not token:
        print("GitHub token is required (--token or GITHUB_TOKEN env var).")
        raise typer.Exit(1)
    config = ProjectConfig.load()
    gh_env = config.prompt_vars["github_env"]
    gh_repo = gh_repo or gh_env.get("github_repo", "")
    pr_env_val = gh_env.get("github_pr_number", "")
    logging.info(f"github_pr_number = {pr_env_val}")

    if not pr:
        # e.g. could be "refs/pull/123/merge" or a direct number
        if "/" in pr_env_val and "pull" in pr_env_val:
            # refs/pull/123/merge
            try:
                pr_num_candidate = pr_env_val.strip("/").split("/")
                idx = pr_num_candidate.index("pull")
                pr = int(pr_num_candidate[idx + 1])
            except Exception:
                pass
        else:
            try:
                pr = int(pr_env_val)
            except ValueError:
                pass
    if not pr:
        if pr_str := os.getenv("PR_NUMBER_FROM_WORKFLOW_DISPATCH"):
            try:
                pr = int(pr_str)
            except ValueError:
                pass
    if not pr:
        logging.error("Could not resolve PR number from environment variables.")
        raise typer.Exit(3)

    if not post_gh_comment(gh_repo, pr, token, body):
        raise typer.Exit(5)

    if config.collapse_previous_code_review_comments:
        sleep(1)
        collapse_gh_outdated_cr_comments(gh_repo, pr, token)


def collapse_gh_outdated_cr_comments(
    gh_repository: str,
    pr_or_issue_number: int,
    token: str = None
):
    """
    Collapse outdated code review comments in a GitHub pull request or issue.
    """
    logging.info(f"Collapsing outdated comments in {gh_repository} #{pr_or_issue_number}...")

    token = resolve_gh_token(token)
    owner, repo = gh_repository.split('/')
    api = GhApi(owner, repo, token=token)

    comments = api.issues.list_comments(pr_or_issue_number)
    review_marker = HTML_CR_COMMENT_MARKER
    collapsed_title = "🗑️ Outdated Code Review by Gito"
    collapsed_marker = f"<summary>{collapsed_title}</summary>"
    outdated_comments = [
        c for c in comments
        if c.body and review_marker in c.body and collapsed_marker not in c.body
    ][:-1]
    if not outdated_comments:
        logging.info("No outdated comments found")
        return
    for comment in outdated_comments:
        logging.info(f"Collapsing comment {comment.id}...")
        new_body = f"<details>\n<summary>{collapsed_title}</summary>\n\n{comment.body}\n</details>"
        api.issues.update_comment(comment.id, new_body)
        hide_gh_comment(comment.node_id, token)
    logging.info("All outdated comments collapsed successfully.")
