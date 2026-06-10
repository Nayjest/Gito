"""
Posting code review comments to GitLab Merge Requests.
"""

import logging
import os
import re
from time import sleep
from typing import List, Dict, Optional, Tuple

import requests
import typer
from microcore import ui

from ..cli_base import app
from ..constants import (
    GITHUB_MD_REPORT_FILE_NAME,
    JSON_REPORT_FILE_NAME,
    HTML_CR_COMMENT_MARKER,
    HTML_INLINE_CR_COMMENT_MARKER,
)
from ..project_config import ProjectConfig
from ..report_struct import Report, Issue
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
        logging.error("Failed to post GitLab MR note: %s %s", resp.status_code, resp.text)
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
            logging.error("Failed to list GitLab MR notes: %s %s", resp.status_code, resp.text)
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


def get_gl_mr_info(
    project_id: str,
    merge_request_iid: int,
    token: str,
    base_url: Optional[str] = None,
) -> Optional[Dict]:
    """Fetch GitLab MR metadata (incl. diff_refs needed for inline comment positions)."""
    url = (
        f"{_gl_base_url(base_url)}/api/v4/projects/{project_id}"
        f"/merge_requests/{merge_request_iid}"
    )
    resp = requests.get(url, headers={"PRIVATE-TOKEN": token}, timeout=30)
    if resp.status_code != 200:
        logging.error("Failed to fetch GitLab MR info: %s %s", resp.status_code, resp.text)
        return None
    return resp.json()


def get_gl_mr_diffs(
    project_id: str,
    merge_request_iid: int,
    token: str,
    base_url: Optional[str] = None,
) -> List[Dict]:
    """List all diffs of a GitLab MR (handles pagination)."""
    all_diffs: List[Dict] = []
    page = 1
    headers = {"PRIVATE-TOKEN": token}
    base = _gl_base_url(base_url)
    while True:
        url = (
            f"{base}/api/v4/projects/{project_id}/merge_requests/{merge_request_iid}/diffs"
            f"?per_page=100&page={page}"
        )
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logging.error("Failed to list GitLab MR diffs: %s %s", resp.status_code, resp.text)
            break
        all_diffs.extend(resp.json() or [])
        next_page = resp.headers.get("X-Next-Page")
        if not next_page:
            break
        page = int(next_page)
    return all_diffs


def parse_unified_diff_line_map(diff_text: str) -> Dict[int, Optional[int]]:
    """
    Map new-file line numbers commentable in this diff to old-file line numbers.

    Added lines map to None, unchanged (context) lines map to their old line number.
    Only lines present in the diff hunks are included (GitLab rejects positions
    pointing outside the diff).
    """
    line_map: Dict[int, Optional[int]] = {}
    old_ln = new_ln = 0
    in_hunk = False
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                old_ln, new_ln = int(match.group(1)), int(match.group(2))
                in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+"):
            line_map[new_ln] = None
            new_ln += 1
        elif line.startswith("-"):
            old_ln += 1
        elif line.startswith("\\"):  # "\ No newline at end of file"
            continue
        else:
            line_map[new_ln] = old_ln
            new_ln += 1
            old_ln += 1
    return line_map


def normalize_repo_path(path: str) -> str:
    return path.replace("\\", "/").removeprefix("./")


def build_gl_mr_line_maps(diffs: List[Dict]) -> Dict[str, Tuple[str, Dict[int, Optional[int]]]]:
    """Build {new_path: (old_path, line_map)} for all non-deleted files changed in the MR."""
    maps: Dict[str, Tuple[str, Dict[int, Optional[int]]]] = {}
    for d in diffs:
        if d.get("deleted_file") or not d.get("diff"):
            continue
        new_path = normalize_repo_path(d.get("new_path") or "")
        old_path = d.get("old_path") or new_path
        maps[new_path] = (old_path, parse_unified_diff_line_map(d["diff"]))
    return maps


def gl_issue_position(
    issue: Issue,
    line_maps: Dict[str, Tuple[str, Dict[int, Optional[int]]]],
    diff_refs: Dict,
) -> Optional[Dict]:
    """
    Build a GitLab text position for the issue, or None if none of its
    affected lines belong to the MR diff.
    """
    file = normalize_repo_path(issue.file)
    if file not in line_maps:
        return None
    old_path, line_map = line_maps[file]
    candidate_lines = [
        line
        for block in issue.affected_lines or []
        for line in (block.start_line, block.end_line)
        if line is not None
    ]
    for line in candidate_lines:
        if line in line_map:
            position = {
                "base_sha": diff_refs["base_sha"],
                "head_sha": diff_refs["head_sha"],
                "start_sha": diff_refs["start_sha"],
                "position_type": "text",
                "old_path": old_path,
                "new_path": file,
                "new_line": line,
            }
            # unchanged lines require old_line as well; added lines must omit it
            if (old_line := line_map[line]) is not None:
                position["old_line"] = old_line
            return position
    return None


def create_gl_draft_note(
    project_id: str,
    merge_request_iid: int,
    token: str,
    body: str,
    position: Dict,
    base_url: Optional[str] = None,
) -> bool:
    """Create a draft (pending) inline note on a GitLab MR diff."""
    url = (
        f"{_gl_base_url(base_url)}/api/v4/projects/{project_id}"
        f"/merge_requests/{merge_request_iid}/draft_notes"
    )
    resp = requests.post(
        url,
        headers={"PRIVATE-TOKEN": token},
        json={"note": body, "position": position},
        timeout=30,
    )
    if resp.status_code != 201:
        logging.error("Failed to create GitLab draft note: %s %s", resp.status_code, resp.text)
        return False
    return True


def publish_gl_draft_notes(
    project_id: str,
    merge_request_iid: int,
    token: str,
    base_url: Optional[str] = None,
) -> bool:
    """Publish all pending draft notes of a GitLab MR at once (single review)."""
    url = (
        f"{_gl_base_url(base_url)}/api/v4/projects/{project_id}"
        f"/merge_requests/{merge_request_iid}/draft_notes/bulk_publish"
    )
    resp = requests.post(url, headers={"PRIVATE-TOKEN": token}, timeout=30)
    if resp.status_code not in (200, 204):
        logging.error("Failed to publish GitLab draft notes: %s %s", resp.status_code, resp.text)
        return False
    return True


def list_gl_mr_discussions(
    project_id: str,
    merge_request_iid: int,
    token: str,
    base_url: Optional[str] = None,
) -> List[Dict]:
    """List all discussions on a GitLab MR (handles pagination)."""
    all_discussions: List[Dict] = []
    page = 1
    headers = {"PRIVATE-TOKEN": token}
    base = _gl_base_url(base_url)
    while True:
        url = (
            f"{base}/api/v4/projects/{project_id}/merge_requests/{merge_request_iid}/discussions"
            f"?per_page=100&page={page}"
        )
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            logging.error(
                "Failed to list GitLab MR discussions: %s %s", resp.status_code, resp.text
            )
            break
        all_discussions.extend(resp.json() or [])
        next_page = resp.headers.get("X-Next-Page")
        if not next_page:
            break
        page = int(next_page)
    return all_discussions


def resolve_gl_outdated_inline_discussions(
    project_id: str,
    merge_request_iid: int,
    token: str,
    base_url: Optional[str] = None,
) -> None:
    """Resolve inline discussions created by previous Gito review runs."""
    base = _gl_base_url(base_url)
    for discussion in list_gl_mr_discussions(project_id, merge_request_iid, token, base_url):
        notes = discussion.get("notes") or []
        if not notes:
            continue
        first = notes[0]
        if (
            HTML_INLINE_CR_COMMENT_MARKER not in (first.get("body") or "")
            or not first.get("resolvable")
            or first.get("resolved")
        ):
            continue
        url = (
            f"{base}/api/v4/projects/{project_id}/merge_requests/{merge_request_iid}"
            f"/discussions/{discussion['id']}"
        )
        resp = requests.put(
            url, headers={"PRIVATE-TOKEN": token}, json={"resolved": True}, timeout=30
        )
        if resp.status_code != 200:
            logging.warning(
                "Failed to resolve outdated Gito discussion %s: %s %s",
                discussion["id"],
                resp.status_code,
                resp.text,
            )


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
      gito gitlab-comment \
        --token $GITLAB_ACCESS_TOKEN \
        --project-id $CI_PROJECT_ID \
        --merge-request-iid $CI_MERGE_REQUEST_IID
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

        # Post the note
        if not post_gl_comment(project_id, merge_request_iid, token, body, base_url):
            raise typer.Exit(5)

    # Optionally collapse older comments
    if getattr(config, "collapse_previous_code_review_comments", False):
        sleep(1)
        collapse_gl_outdated_cr_comments(project_id, merge_request_iid, token, base_url)
