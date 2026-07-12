"""
Posting code review comments to GitLab Merge Requests.
"""

import logging
import os
from time import sleep
from typing import List, Dict, Optional, Tuple

import typer
from microcore import ui

from ..cli_base import app, runs_without_llm
from ..constants import (
    GITHUB_MD_REPORT_FILE_NAME,
    JSON_REPORT_FILE_NAME,
    HTML_CR_COMMENT_MARKER,
)
from ..gitlab_api import (
    resolve_gl_token,
    post_gl_comment,
    list_gl_mr_notes,
    update_gl_mr_note,
    get_gl_mr_info,
    get_gl_mr_diffs,
    build_gl_mr_line_maps,
    gl_issue_position,
    create_gl_draft_note,
    publish_gl_draft_notes,
    resolve_gl_outdated_inline_discussions,
)
from ..project_config import ProjectConfig
from ..report_struct import Report, Issue
from ..utils.git_platform.gitlab import is_running_in_gitlab_ci


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


def post_gl_inline_review(
    project_id: str,
    merge_request_iid: int,
    token: str,
    report: Report,
    config: ProjectConfig,
    base_url: Optional[str] = None,
) -> bool:
    """
    Post the review as an overview note + one inline diff comment per issue.

    Inline comments are created as draft notes anchored to the MR diff and published
    together as a single review. Unlike the GitLab Code Quality artifact, this works
    on all GitLab tiers. Issues that cannot be anchored to changed lines are included
    in the overview comment instead.
    """
    mr = get_gl_mr_info(project_id, merge_request_iid, token, base_url)
    diff_refs = (mr or {}).get("diff_refs") or {}
    if not all(diff_refs.get(key) for key in ("base_sha", "head_sha", "start_sha")):
        logging.error("Could not resolve MR diff_refs; unable to post inline comments.")
        return False
    diffs = get_gl_mr_diffs(project_id, merge_request_iid, token, base_url)
    line_maps = build_gl_mr_line_maps(diffs)

    anchored: List[Tuple[Issue, Dict]] = []
    unanchored: List[Issue] = []
    for issue in report.plain_issues:
        if position := gl_issue_position(issue, line_maps, diff_refs):
            anchored.append((issue, position))
        else:
            unanchored.append(issue)

    overview = report.render(
        config,
        Report.Format.MARKDOWN,
        include_issues=False,
        unanchored_issues=unanchored,
    )
    if not post_gl_comment(project_id, merge_request_iid, token, overview, base_url):
        return False

    resolve_gl_outdated_inline_discussions(project_id, merge_request_iid, token, base_url)

    drafts_created = 0
    for issue, position in anchored:
        body = report.render(config, Report.Format.GITLAB_MR_INLINE_ISSUE, issue=issue)
        if create_gl_draft_note(project_id, merge_request_iid, token, body, position, base_url):
            drafts_created += 1
        else:
            logging.warning(
                "Falling back to a regular MR note for issue #%s (%s)", issue.id, issue.file
            )
            post_gl_comment(project_id, merge_request_iid, token, body, base_url)
    if drafts_created:
        publish_gl_draft_notes(project_id, merge_request_iid, token, base_url)
    logging.info(
        "Posted %s inline comment(s); %s issue(s) included in the overview comment.",
        drafts_created,
        len(unanchored),
    )
    return True


@app.command(name="gitlab-comment", help="Leave a GitLab MR comment with the review.")
@app.command(name="post-gitlab-comment", hidden=True)
@runs_without_llm
def post_gitlab_cr_comment(
    md_report_file: str = typer.Option(
        default=None,
        help=(
            "Path to the markdown review file. "
            "Gito's standard report file will be used by default."
        ),
    ),
    json_report_file: str = typer.Option(
        default=None,
        help=(
            "Path to the JSON review report (used with --inline). "
            "Gito's standard report file will be used by default."
        ),
    ),
    project_id: str = typer.Option(
        default=None, help="GitLab project ID (numeric) or URL-encoded path"
    ),
    merge_request_iid: int = typer.Option(default=None, help="Merge Request IID"),
    token: str = typer.Option("", help="GitLab access token (or set GITLAB_ACCESS_TOKEN env var)"),
    base_url: Optional[str] = typer.Option(
        default=None, help="GitLab base URL (default env GITLAB_BASE_URL or https://gitlab.com)"
    ),
    inline: bool = typer.Option(
        False,
        "--inline",
        help=(
            "Post each issue as a separate inline comment on the MR diff "
            "(works on all GitLab tiers); "
            "the main comment then contains only the review overview."
        ),
    ),
):
    """
    Leaves a comment with the review on the current GitLab merge request.

    With --inline, posts an overview comment first, then each issue as a separate
    inline comment anchored to the affected diff lines (published together as one review).

    Requires a Project Access Token with 'api' scope.
    The default $CI_JOB_TOKEN does not have write access to merge requests.

    Examples:
      ```bash
      gito gitlab-comment --token $GITLAB_ACCESS_TOKEN
      --project-id $CI_PROJECT_ID --merge-request-iid $CI_MERGE_REQUEST_IID
      ```
    """
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

    config = ProjectConfig.load()

    if inline:
        report_file = json_report_file or JSON_REPORT_FILE_NAME
        if not os.path.exists(report_file):
            logging.error(f"Review report not found: {report_file}, comments will not be posted.")
            raise typer.Exit(4)
        report = Report.load(report_file)
        if not post_gl_inline_review(
            project_id, merge_request_iid, token, report, config, base_url
        ):
            raise typer.Exit(5)
    else:
        file = md_report_file or GITHUB_MD_REPORT_FILE_NAME
        if not os.path.exists(file):
            logging.error(f"Review file not found: {file}, comment will not be posted.")
            raise typer.Exit(4)

        with open(file, "r", encoding="utf-8") as f:
            body = f.read()

        if not post_gl_comment(project_id, merge_request_iid, token, body, base_url):
            raise typer.Exit(5)

    # Optionally collapse older comments
    if getattr(config, "collapse_previous_code_review_comments", False):
        sleep(1)
        collapse_gl_outdated_cr_comments(project_id, merge_request_iid, token, base_url)
