import logging

from gito.gh_api import gh_api


def _fetch_pr_context(target, repo, config) -> str:
    """
    Fetch PR description and comments to include as review context.
    Returns empty string if not a PR review or if fetching fails.
    """
    if not target.pull_request_id:
        return ""

    if not getattr(config, "include_pr_context", True):
        return ""

    try:
        api = gh_api(repo=repo)
        pr_number = int(target.pull_request_id)

        pr = api.pulls.get(pr_number)
        pr_description = (pr.get("body") or "").strip()
        pr_title = pr.get("title") or ""

        comments_resp = api.issues.list_comments(pr_number)
        comment_lines = []
        for c in comments_resp:
            body = (c.get("body") or "").strip()
            if body:
                user = c.get("user", {}).get("login", "unknown")
                comment_lines.append(f"@{user}: {body}")

        parts = []
        if pr_title:
            parts.append("PR Title: " + pr_title)
        if pr_description:
            parts.append("PR Description:\n" + pr_description)
        if comment_lines:
            parts.append("PR Comments:\n" + "\n".join(comment_lines))

        return "\n\n".join(parts) if parts else ""

    except Exception as e:
        logging.warning(
            "Failed to fetch PR context for #%s: %s", target.pull_request_id, e
        )
        return ""
