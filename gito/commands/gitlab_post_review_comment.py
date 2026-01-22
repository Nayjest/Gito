"""
Posting code review comments to GitLab Merge Requests.
"""
import logging
import os
from time import sleep
from typing import List, Dict, Optional

import requests
import typer
from microcore import ui

from ..cli_base import app
from ..constants import GITHUB_MD_REPORT_FILE_NAME, HTML_CR_COMMENT_MARKER
from ..project_config import ProjectConfig
from ..utils.git_platform.gitlab import is_running_in_gitlab_ci


def resolve_gl_token(token: str | None) -> Optional[str]:
    """Resolve GitLab access token from CLI flag or env var."""
    return (token or "").strip() or os.getenv("GITLAB_ACCESS_TOKEN") or os.getenv("GITLAB_TOKEN")


def require_gl_token(token: str | None) -> str:
    """Resolve GitLab access token from CLI flag or env var, or exit."""
    if token := resolve_gl_token(token):
        return token
    hint = (
        "Add it to CI/CD variables (Settings → CI/CD → Variables)."
        if is_running_in_gitlab_ci()
        else "Pass --token or set GITLAB_ACCESS_TOKEN env var."
    )
    ui.error(
        f"GitLab access token is required.\n"
        f"{hint}\n"
        "Create a Project Access Token (role: Reporter, scope: api) at:\n"
        "Settings → Access Tokens"
    )
    raise typer.Exit(1)


def _gl_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.getenv("GITLAB_BASE_URL") or "https://gitlab.com").rstrip("/")


def post_gl_comment(
    project_id: str,
    merge_request_iid: int,
    token: str,
    body: str,
    base_url: Optional[str] = None,
) -> bool:
    """Create a note on a GitLab Merge Request."""
    base_url = _gl_base_url(base_url)
    url = f"{base_url}/api/v4/projects/{project_id}/merge_requests/{merge_request_iid}/notes"
    headers = {"PRIVATE-TOKEN": token}
    resp = requests.post(url, headers=headers, json={"body": body}, timeout=30)
    if resp.status_code != 201:
        logging.error(
            "Failed to post GitLab MR note: %s %s", resp.status_code, resp.text
        )
        return False
    return True


def list_gl_mr_notes(
    project_id: str,
    merge_request_iid: int,
    token: str,
    base_url: Optional[str] = None,
) -> List[Dict]:
    """List *all* notes on a GitLab MR (handles pagination)."""
    all_notes: List[Dict] = []
    page = 1
    headers = {"PRIVATE-TOKEN": token}
    base = _gl_base_url(base_url)

    while True:
        url = (
            f"{base}/api/v4/projects/{project_id}/merge_requests/{merge_request_iid}/notes"
            f"?per_page=100&page={page}"
        )
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logging.error(
                "Failed to list GitLab MR notes: %s %s", resp.status_code, resp.text
            )
            break
        notes = resp.json() or []
        all_notes.extend(notes)
        next_page = resp.headers.get("X-Next-Page")
        if not next_page:
            break
        page = int(next_page)

    # Ensure notes are sorted chronologically by creation time
    all_notes.sort(key=lambda n: n.get("created_at", ""))
    return all_notes


def update_gl_mr_note(
    project_id: str,
    merge_request_iid: int,
    note_id: int,
    token: str,
    new_body: str,
    base_url: Optional[str] = None,
) -> bool:
    """Update a GitLab MR note with new body content."""
    url = (
        f"{_gl_base_url(base_url)}/api/v4/projects/{project_id}/merge_requests/"
        f"{merge_request_iid}/notes/{note_id}"
    )
    headers = {"PRIVATE-TOKEN": token}
    resp = requests.put(url, headers=headers, json={"body": new_body}, timeout=30)
    if resp.status_code != 200:
        logging.error(
            "Failed to update GitLab MR note %s: %s %s",
            note_id,
            resp.status_code,
            resp.text,
        )
        return False
    return True


def collapse_gl_outdated_cr_comments(
    project_id: str,
    merge_request_iid: int,
    token: Optional[str] = None,
    base_url: Optional[str] = None,
) -> None:
    """
    Collapse previous CR comments on a GitLab MR by wrapping them in <details>…</details>.

    We detect our comments using HTML_CR_COMMENT_MARKER. All but the most recent
    matching note are collapsed. GitLab doesn't support "minimize" via API for normal
    MR notes, so we edit the note bodies in-place.
    """
    logging.info(
        "Collapsing outdated comments in GitLab project %s MR !%s...",
        project_id,
        merge_request_iid,
    )
    token = require_gl_token(token)

    notes = list_gl_mr_notes(project_id, merge_request_iid, token, base_url)

    review_marker = HTML_CR_COMMENT_MARKER
    collapsed_title = "🗑️ Outdated Code Review by Gito"
    collapsed_marker = f"<summary>{collapsed_title}</summary>"

    candidates = [
        n
        for n in notes
        if (n.get("body") and (review_marker in n["body"]) and (collapsed_marker not in n["body"]))
    ]

    # Exclude the most recent matching note
    outdated = candidates[:-1] if candidates else []
    if not outdated:
        logging.info("No outdated comments found")
        return

    for n in outdated:
        note_id = n["id"]
        logging.info("Collapsing GitLab MR note %s...", note_id)
        new_body = f"<details>\n<summary>{collapsed_title}</summary>\n\n{n['body']}\n</details>"
        update_gl_mr_note(project_id, merge_request_iid, note_id, token, new_body, base_url)

    logging.info("All outdated comments collapsed successfully.")


@app.command(name="gitlab-comment", help="Leave a GitLab MR comment with the review.")
@app.command(name="post-gitlab-comment", hidden=True)
def post_gitlab_cr_comment(
    md_report_file: str = typer.Option(
        default=None,
        help=(
            "Path to the markdown review file. "
            "Gito's standard report file will be used by default."
        ),
    ),
    project_id: str = typer.Option(
        default=None, help="GitLab project ID (numeric) or URL-encoded path"
    ),
    merge_request_iid: int = typer.Option(default=None, help="Merge Request IID"),
    token: str = typer.Option(
        "", help="GitLab access token (or set GITLAB_ACCESS_TOKEN env var)"
    ),
    base_url: Optional[str] = typer.Option(
        default=None, help="GitLab base URL (default env GITLAB_BASE_URL or https://gitlab.com)"
    ),
):
    """
    Leaves a comment with the review on the current GitLab merge request.

    Requires a Project Access Token with 'api' scope.
    The default $CI_JOB_TOKEN does not have write access to merge requests.

    Examples:
      ```bash
      gito gitlab-comment \
        --token $GITLAB_ACCESS_TOKEN \
        --project-id $CI_PROJECT_ID \
        --merge-request-iid $CI_MERGE_REQUEST_IID
      ```
    """
    file = md_report_file or GITHUB_MD_REPORT_FILE_NAME
    if not os.path.exists(file):
        logging.error(f"Review file not found: {file}, comment will not be posted.")
        raise typer.Exit(4)

    with open(file, "r", encoding="utf-8") as f:
        body = f.read()

    token = require_gl_token(token)

    # Resolve project and MR IID from flags or common CI env vars
    project_id = project_id or os.getenv("CI_PROJECT_ID")
    mr_env_val = os.getenv("CI_MERGE_REQUEST_IID")
    if not merge_request_iid and mr_env_val:
        try:
            merge_request_iid = int(mr_env_val)
        except ValueError:
            pass

    if not project_id:
        logging.error("Could not resolve GitLab project_id (flag or CI_PROJECT_ID env var).")
        raise typer.Exit(3)

    if not merge_request_iid:
        logging.error(
            "Could not resolve GitLab merge_request_iid (flag or CI_MERGE_REQUEST_IID env var)."
        )
        raise typer.Exit(3)

    # Post the note
    if not post_gl_comment(project_id, merge_request_iid, token, body, base_url):
        raise typer.Exit(5)

    # Optionally collapse older comments
    config = ProjectConfig.load()
    if getattr(config, "collapse_previous_code_review_comments", False):
        sleep(1)
        collapse_gl_outdated_cr_comments(project_id, merge_request_iid, token, base_url)
