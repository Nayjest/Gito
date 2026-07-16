"""Publish Code Doctor review results to GitHub pull requests and GitLab
merge requests.

Tokens come from the server environment only (never from request payloads):

- GitHub: ``GITHUB_TOKEN`` or ``CODE_DOCTOR_GITHUB_TOKEN``
- GitLab: ``GITLAB_TOKEN`` or ``CODE_DOCTOR_GITLAB_TOKEN``
  (``GITLAB_BASE`` overrides the default ``https://gitlab.com`` for
  self-hosted instances)

Publishing is two-step by design: a ``dryRun`` request returns the exact
summary and line comments that would be posted, and only an explicit second
request performs the network call. GitHub gets a PR review with line comments
(falling back to a plain issue comment when line positions are outside the
diff); GitLab gets an MR note.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

MAX_LINE_COMMENTS = 25
MAX_SUMMARY_FINDINGS = 30
HTTP_TIMEOUT = 15

SEVERITY_LABELS = {1: "critical", 2: "major", 3: "minor", 4: "info", 5: "info"}

REMOTE_RE = re.compile(
    r"^(?:https?://|git@|ssh://git@)(?P<host>[^/:]+)[/:](?P<slug>.+?)(?:\.git)?/?$"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _github_token() -> str:
    return os.getenv("CODE_DOCTOR_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN") or ""


def _gitlab_token() -> str:
    return os.getenv("CODE_DOCTOR_GITLAB_TOKEN") or os.getenv("GITLAB_TOKEN") or ""


def gitlab_base() -> str:
    return (os.getenv("GITLAB_BASE") or "https://gitlab.com").rstrip("/")


def publish_config() -> dict[str, dict[str, Any]]:
    """Which platforms the server can publish to (no secrets leave the server)."""
    return {
        "github": {"configured": bool(_github_token())},
        "gitlab": {"configured": bool(_gitlab_token()), "base": gitlab_base()},
    }


def parse_remote(remote_url: str) -> dict[str, str] | None:
    """``https://github.com/o/r.git`` / ``git@gitlab.com:o/r.git`` → host + slug."""
    match = REMOTE_RE.match((remote_url or "").strip())
    if not match:
        return None
    host = match.group("host").lower()
    slug = match.group("slug").strip("/")
    platform = ""
    if "github" in host:
        platform = "github"
    elif "gitlab" in host:
        platform = "gitlab"
    return {"host": host, "slug": slug, "platform": platform}


def _flatten_issues(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    issues = []
    for file, file_issues in ((report or {}).get("issues") or {}).items():
        for issue in file_issues:
            issues.append(issue | {"file": issue.get("file") or file})
    issues.sort(key=lambda issue: (issue.get("severity") or 9, str(issue.get("file"))))
    return issues


def _issue_line(issue: dict[str, Any]) -> int | None:
    blocks = issue.get("affected_lines") or []
    if blocks and isinstance(blocks[0], dict):
        line = blocks[0].get("start_line")
        if isinstance(line, int) and line > 0:
            return line
    return None


def build_summary_markdown(
    meta: dict[str, Any], report: dict[str, Any] | None, stats: dict[str, Any]
) -> str:
    issues = _flatten_issues(report)
    verification = stats.get("verification") or {}
    lines = [
        "## 🩺 Code Doctor review",
        "",
        f"**Gate:** `{stats.get('gate', 'n/a')}` · **Risk score:** {stats.get('risk_score', 0)} "
        f"· **Findings:** {len(issues)} "
        f"(static {stats.get('static_issues', 0)}, cross-file {stats.get('cross_file_issues', 0)})",
    ]
    if verification:
        lines.append(
            f"**Verification pass:** {verification.get('confirmed', 0)} confirmed, "
            f"{stats.get('rejected_issues', 0)} auto-rejected, "
            f"{verification.get('uncertain', 0)} uncertain"
        )
    if stats.get("summary"):
        lines.extend(["", str(stats["summary"]).strip()])
    if issues:
        lines.extend(["", "| Severity | File | Finding |", "| --- | --- | --- |"])
        for issue in issues[:MAX_SUMMARY_FINDINGS]:
            severity = SEVERITY_LABELS.get(issue.get("severity"), "info")
            location = issue.get("file", "")
            line = _issue_line(issue)
            if line:
                location = f"{location}:{line}"
            title = str(issue.get("title", "")).replace("|", "\\|")
            verified = " ✓" if issue.get("verified") else ""
            lines.append(f"| {severity}{verified} | `{location}` | {title} |")
        if len(issues) > MAX_SUMMARY_FINDINGS:
            lines.append(f"| … | | +{len(issues) - MAX_SUMMARY_FINDINGS} more |")
    lines.extend(
        [
            "",
            f"_Reviewed by Code Doctor (model: {meta.get('model', 'local')}, "
            f"run `{meta.get('id', '')}`). Code never left the reviewer's machine._",
        ]
    )
    return "\n".join(lines)


def build_line_comments(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    comments = []
    for issue in _flatten_issues(report):
        line = _issue_line(issue)
        if not line or issue.get("suppressed"):
            continue
        severity = SEVERITY_LABELS.get(issue.get("severity"), "info")
        body = f"**Code Doctor ({severity}):** {issue.get('title', '')}"
        details = str(issue.get("details") or "").strip()
        if details:
            body += f"\n\n{details[:1200]}"
        proposal = ""
        blocks = issue.get("affected_lines") or []
        if blocks and isinstance(blocks[0], dict):
            proposal = str(blocks[0].get("proposal") or "").strip()
        if proposal:
            body += f"\n\nSuggested fix:\n```\n{proposal[:800]}\n```"
        comments.append({"path": issue.get("file", ""), "line": line, "body": body})
        if len(comments) >= MAX_LINE_COMMENTS:
            break
    return comments


def _request_json(
    url: str, token_header: dict[str, str], payload: dict[str, Any]
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "code-doctor",
            **token_header,
        },
        method="POST",
    )
    with urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _publish_github(
    slug: str, number: int, summary: str, comments: list[dict[str, Any]]
) -> dict[str, Any]:
    token = _github_token()
    if not token:
        raise ValueError("GitHub publishing is not configured. Set GITHUB_TOKEN on the server.")
    headers = {
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    api = f"https://api.github.com/repos/{slug}"
    if comments:
        try:
            review = _request_json(
                f"{api}/pulls/{number}/reviews",
                headers,
                {
                    "event": "COMMENT",
                    "body": summary,
                    "comments": [
                        {
                            "path": comment["path"],
                            "line": comment["line"],
                            "side": "RIGHT",
                            "body": comment["body"],
                        }
                        for comment in comments
                    ],
                },
            )
            return {
                "mode": "review_with_line_comments",
                "url": review.get("html_url", ""),
                "comments_posted": len(comments),
            }
        except HTTPError as exc:
            if exc.code != 422:
                raise
            # Line positions outside the PR diff → fall back to a summary comment.
    comment = _request_json(f"{api}/issues/{number}/comments", headers, {"body": summary})
    return {"mode": "summary_comment", "url": comment.get("html_url", ""), "comments_posted": 0}


def _publish_gitlab(slug: str, number: int, summary: str) -> dict[str, Any]:
    token = _gitlab_token()
    if not token:
        raise ValueError("GitLab publishing is not configured. Set GITLAB_TOKEN on the server.")
    api = f"{gitlab_base()}/api/v4/projects/{quote(slug, safe='')}/merge_requests/{number}/notes"
    note = _request_json(api, {"PRIVATE-TOKEN": token}, {"body": summary})
    return {"mode": "mr_note", "url": "", "note_id": note.get("id"), "comments_posted": 0}


GATE_STATUS_STATES = {
    "github": {"block": "failure", "review": "success", "pass": "success"},
    "gitlab": {"block": "failed", "review": "success", "pass": "success"},
}
GATE_STATUS_DESCRIPTIONS = {
    "block": "Blocking findings — fix before merge",
    "review": "Completed — needs human review",
    "pass": "No blocking findings",
}


def post_commit_status(
    platform: str, slug: str, sha: str, gate: str, target_url: str = ""
) -> dict[str, Any]:
    """Post the review gate as a commit status (context ``code-doctor/gate``)."""
    platform = str(platform or "").strip().lower()
    gate = str(gate or "pass").strip().lower()
    if platform not in GATE_STATUS_STATES:
        raise ValueError("Unsupported platform. Use 'github' or 'gitlab'.")
    if not slug or not sha:
        raise ValueError("Commit status needs a repository slug and a commit sha.")
    state = GATE_STATUS_STATES[platform].get(gate, "success")
    description = f"Code Doctor: {GATE_STATUS_DESCRIPTIONS.get(gate, gate)}"

    if platform == "github":
        token = _github_token()
        if not token:
            raise ValueError("GitHub publishing is not configured. Set GITHUB_TOKEN on the server.")
        url = f"https://api.github.com/repos/{slug}/statuses/{sha}"
        headers = {"Authorization": f"Bearer {token}", "X-GitHub-Api-Version": "2022-11-28"}
        payload: dict[str, Any] = {
            "state": state,
            "context": "code-doctor/gate",
            "description": description,
        }
    else:
        token = _gitlab_token()
        if not token:
            raise ValueError("GitLab publishing is not configured. Set GITLAB_TOKEN on the server.")
        url = f"{gitlab_base()}/api/v4/projects/{quote(slug, safe='')}/statuses/{sha}"
        headers = {"PRIVATE-TOKEN": token}
        payload = {"state": state, "name": "code-doctor/gate", "description": description}
    if target_url:
        payload["target_url"] = target_url

    try:
        _request_json(url, headers, payload)
    except HTTPError as exc:
        raise ValueError(f"{platform} status API returned {exc.code}: {exc.reason}") from exc
    except URLError as exc:
        raise ValueError(f"Could not reach the {platform} API: {exc.reason}") from exc
    return {"platform": platform, "sha": sha, "state": state, "description": description}


def publish_review(
    meta: dict[str, Any],
    report: dict[str, Any] | None,
    stats: dict[str, Any],
    payload: dict[str, Any],
    remote_url: str = "",
) -> dict[str, Any]:
    """Build (and on non-dry runs, post) the review for a PR/MR.

    ``payload``: platform (github|gitlab, defaults from the repo remote),
    repo (owner/name slug, defaults from the remote), pr (number, required),
    dryRun (default True — the caller must explicitly pass false to post),
    lineComments (default True, GitHub only).
    """
    remote = parse_remote(remote_url) if remote_url else None
    platform = str(payload.get("platform") or (remote or {}).get("platform") or "").strip().lower()
    if platform not in {"github", "gitlab"}:
        raise ValueError("Unsupported platform. Use 'github' or 'gitlab'.")
    slug = str(payload.get("repo") or (remote or {}).get("slug") or "").strip().strip("/")
    if not slug:
        raise ValueError("No repository slug. Pass 'repo' (owner/name) or add an origin remote.")
    try:
        number = int(payload.get("pr") or payload.get("mr") or 0)
    except (TypeError, ValueError):
        number = 0
    if number <= 0:
        raise ValueError("Pass 'pr' — the pull/merge request number to publish to.")

    summary = build_summary_markdown(meta, report, stats)
    comments = build_line_comments(report) if payload.get("lineComments") is not False else []
    preview = {
        "platform": platform,
        "target": f"{slug}#{number}",
        "summary_markdown": summary,
        "line_comments": comments if platform == "github" else [],
        "dry_run": payload.get("dryRun") is not False,
    }
    if preview["dry_run"]:
        return preview

    try:
        if platform == "github":
            posted = _publish_github(slug, number, summary, comments)
        else:
            posted = _publish_gitlab(slug, number, summary)
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")[:400]
        except Exception:  # noqa: BLE001 - best-effort error body
            pass
        raise ValueError(f"{platform} API returned {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise ValueError(f"Could not reach the {platform} API: {exc.reason}") from exc

    return {**preview, "posted": posted, "published_at": _utc_now()}
