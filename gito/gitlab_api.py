"""
GitLab REST API helpers: MR notes, draft notes, discussions, diffs
and mapping of review issues to inline comment positions.
"""

import logging
import os
import re
from typing import List, Dict, Optional, Tuple

import requests

from .constants import HTML_INLINE_CR_COMMENT_MARKER
from .report_struct import Issue


def resolve_gl_token(token: str | None) -> Optional[str]:
    """Resolve GitLab access token from CLI flag or env var."""
    return (token or "").strip() or os.getenv("GITLAB_ACCESS_TOKEN") or os.getenv("GITLAB_TOKEN")


def _gl_base_url(base_url: Optional[str]) -> str:
    return (base_url or os.getenv("GITLAB_BASE_URL") or "https://gitlab.com").rstrip("/")


def _gl_headers(token: str) -> Dict:
    return {"PRIVATE-TOKEN": token}


def _gl_mr_url(project_id: str, merge_request_iid: int, base_url: Optional[str] = None) -> str:
    return (
        f"{_gl_base_url(base_url)}/api/v4/projects/{project_id}"
        f"/merge_requests/{merge_request_iid}"
    )


def _gl_paginated_get(
    resource: str,
    project_id: str,
    merge_request_iid: int,
    token: str,
    base_url: Optional[str] = None,
) -> List[Dict]:
    """Fetch all pages of an MR sub-resource (notes, diffs, discussions, ...)."""
    items: List[Dict] = []
    page = 1
    while True:
        url = (
            f"{_gl_mr_url(project_id, merge_request_iid, base_url)}/{resource}"
            f"?per_page=100&page={page}"
        )
        resp = requests.get(url, headers=_gl_headers(token), timeout=30)
        if resp.status_code != 200:
            logging.error(
                "Failed to list GitLab MR %s: %s %s", resource, resp.status_code, resp.text
            )
            break
        items.extend(resp.json() or [])
        next_page = resp.headers.get("X-Next-Page")
        if not next_page:
            break
        page = int(next_page)
    return items


def post_gl_comment(
    project_id: str,
    merge_request_iid: int,
    token: str,
    body: str,
    base_url: Optional[str] = None,
) -> bool:
    """Create a note on a GitLab Merge Request."""
    url = f"{_gl_mr_url(project_id, merge_request_iid, base_url)}/notes"
    resp = requests.post(url, headers=_gl_headers(token), json={"body": body}, timeout=30)
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
    """List *all* notes on a GitLab MR, sorted chronologically by creation time."""
    notes = _gl_paginated_get("notes", project_id, merge_request_iid, token, base_url)
    notes.sort(key=lambda n: n.get("created_at", ""))
    return notes


def update_gl_mr_note(
    project_id: str,
    merge_request_iid: int,
    note_id: int,
    token: str,
    new_body: str,
    base_url: Optional[str] = None,
) -> bool:
    """Update a GitLab MR note with new body content."""
    url = f"{_gl_mr_url(project_id, merge_request_iid, base_url)}/notes/{note_id}"
    resp = requests.put(url, headers=_gl_headers(token), json={"body": new_body}, timeout=30)
    if resp.status_code != 200:
        logging.error(
            "Failed to update GitLab MR note %s: %s %s",
            note_id,
            resp.status_code,
            resp.text,
        )
        return False
    return True


def get_gl_mr_info(
    project_id: str,
    merge_request_iid: int,
    token: str,
    base_url: Optional[str] = None,
) -> Optional[Dict]:
    """Fetch GitLab MR metadata (incl. diff_refs needed for inline comment positions)."""
    url = _gl_mr_url(project_id, merge_request_iid, base_url)
    resp = requests.get(url, headers=_gl_headers(token), timeout=30)
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
    """List all diffs of a GitLab MR."""
    return _gl_paginated_get("diffs", project_id, merge_request_iid, token, base_url)


def list_gl_mr_discussions(
    project_id: str,
    merge_request_iid: int,
    token: str,
    base_url: Optional[str] = None,
) -> List[Dict]:
    """List all discussions on a GitLab MR."""
    return _gl_paginated_get("discussions", project_id, merge_request_iid, token, base_url)


def create_gl_draft_note(
    project_id: str,
    merge_request_iid: int,
    token: str,
    body: str,
    position: Dict,
    base_url: Optional[str] = None,
) -> bool:
    """Create a draft (pending) inline note on a GitLab MR diff."""
    url = f"{_gl_mr_url(project_id, merge_request_iid, base_url)}/draft_notes"
    resp = requests.post(
        url,
        headers=_gl_headers(token),
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
    url = f"{_gl_mr_url(project_id, merge_request_iid, base_url)}/draft_notes/bulk_publish"
    resp = requests.post(url, headers=_gl_headers(token), timeout=30)
    if resp.status_code not in (200, 204):
        logging.error("Failed to publish GitLab draft notes: %s %s", resp.status_code, resp.text)
        return False
    return True


def resolve_gl_outdated_inline_discussions(
    project_id: str,
    merge_request_iid: int,
    token: str,
    base_url: Optional[str] = None,
) -> None:
    """Resolve inline discussions created by previous Gito review runs."""
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
            f"{_gl_mr_url(project_id, merge_request_iid, base_url)}"
            f"/discussions/{discussion['id']}"
        )
        resp = requests.put(url, headers=_gl_headers(token), json={"resolved": True}, timeout=30)
        if resp.status_code != 200:
            logging.warning(
                "Failed to resolve outdated Gito discussion %s: %s %s",
                discussion["id"],
                resp.status_code,
                resp.text,
            )


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
