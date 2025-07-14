"""
Fix issues from code review report
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional
import zipfile

import requests
import typer
from fastcore.basics import AttrDict
from microcore import ui
from ghapi.all import GhApi
import git

from ..cli_base import app
from ..constants import JSON_REPORT_FILE_NAME, HTML_TEXT_ICON
from ..core import answer
from ..gh_api import post_gh_comment, resolve_gh_token
from ..project_config import ProjectConfig
from ..utils import extract_gh_owner_repo
from .fix import fix


@app.command(hidden=True)
def react_to_comment(
    comment_id: int = typer.Argument(),
    gh_token: str = typer.Option(
        "",
        "--gh-token",
        "--token",
        "-t",
        "--github-token",
        help="GitHub token for authentication",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-d", help="Only print changes without applying them"
    ),
):
    """
    Handles direct agent instructions from pull request comments.

    Note: Not for local usage. Designed for execution within GitHub Actions workflows.

    Fetches the PR comment by ID, parses agent directives, and executes the requested
    actions automatically to enable seamless code review workflow integration.
    """
    repo = git.Repo(".")  # Current directory
    owner, repo_name = extract_gh_owner_repo(repo)
    logging.info(f"Using repository: {ui.yellow}{owner}/{repo_name}{ui.reset}")
    gh_token = resolve_gh_token(gh_token)
    api = GhApi(owner=owner, repo=repo_name, token=gh_token)
    comment = api.issues.get_comment(comment_id=comment_id)
    logging.info(
        f"Comment by {ui.yellow('@' + comment.user.login)}: "
        f"{ui.green(comment.body)}\n"
        f"url: {comment.html_url}"
    )

    cfg = ProjectConfig.load_for_repo(repo)
    if not any(
        trigger.lower() in comment.body.lower() for trigger in cfg.mention_triggers
    ):
        ui.error("No mention trigger found in comment, no reaction added.")
        return
    try:
        logging.info("Comment contains mention trigger, reacting with 'eyes'.")
        api.reactions.create_for_issue_comment(comment_id=comment_id, content="eyes")
    except Exception as e:
        logging.error("Error reacting to comment with emoji: %s", str(e))
    pr = int(comment.issue_url.split("/")[-1])
    print(f"Processing comment for PR #{pr}...")

    issue_ids = extract_fix_args(comment.body)
    if issue_ids:
        logging.info(f"Extracted issue IDs: {ui.yellow(str(issue_ids))}")
        out_folder = "artifact"
        download_latest_code_review_artifact(
            api, pr_number=pr, gh_token=gh_token, out_folder=out_folder
        )
        fix(
            issue_ids[0],  # @todo: support multiple IDs
            report_path=Path(out_folder) / JSON_REPORT_FILE_NAME,
            dry_run=dry_run,
            commit=not dry_run,
            push=not dry_run,
        )
        logging.info("Fix applied successfully.")
    elif is_review_request(comment.body):
        ref = repo.active_branch.name
        logging.info(f"Triggering code-review workflow, ref='{ref}'")
        api.actions.create_workflow_dispatch(
            workflow_id="gito-code-review.yml",
            ref=ref,
            inputs={"pr_number": str(pr)},
        )
    else:
        if cfg.answer_github_comments:
            response = answer(comment.body, repo=repo)
            post_gh_comment(
                gh_repository=f"{owner}/{repo_name}",
                pr_or_issue_number=pr,
                gh_token=gh_token,
                text=HTML_TEXT_ICON+response,
            )
        else:
            ui.error("Can't identify target command in the text.")
            return


def last_code_review_run(api: GhApi, pr_number: int) -> AttrDict | None:
    pr = api.pulls.get(pr_number)
    sha = pr["head"]["sha"]  # noqa
    branch = pr["head"]["ref"]

    runs = api.actions.list_workflow_runs_for_repo(branch=branch)["workflow_runs"]
    # Find the run for this SHA
    run = next(
        (
            r
            for r in runs  # r['head_sha'] == sha and
            if (
                any(
                    marker in r["path"].lower()
                    for marker in ["code-review", "code_review", "cr"]
                )
                or "gito.yml" in r["name"].lower()
            )
            and r["status"] == "completed"
        ),
        None,
    )
    return run


def download_latest_code_review_artifact(
    api: GhApi, pr_number: int, gh_token: str, out_folder: Optional[str] = "artifact"
) -> tuple[str, dict] | None:
    run = last_code_review_run(api, pr_number)
    if not run:
        raise Exception("No workflow run found for this PR/SHA")

    artifacts = api.actions.list_workflow_run_artifacts(run["id"])["artifacts"]
    if not artifacts:
        raise Exception("No artifacts found for this workflow run")

    latest_artifact = artifacts[0]
    url = latest_artifact["archive_download_url"]
    print(f"Artifact: {latest_artifact['name']}, Download URL: {url}")
    headers = {"Authorization": f"token {gh_token}"} if gh_token else {}
    zip_path = "artifact.zip"
    try:
        with requests.get(url, headers=headers, stream=True) as r:
            r.raise_for_status()
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

        # Unpack to ./artifact
        os.makedirs("artifact", exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall("artifact")
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)

    print("Artifact unpacked to ./artifact")


def extract_fix_args(text: str) -> list[int]:
    pattern1 = r"fix\s+(?:issues?)?(?:\s+)?#?(\d+(?:\s*,\s*#?\d+)*)"
    match = re.search(pattern1, text)
    if match:
        numbers_str = match.group(1)
        numbers = re.findall(r"\d+", numbers_str)
        issue_numbers = [int(num) for num in numbers]
        return issue_numbers
    return []


def is_review_request(text: str) -> bool:
    text = text.lower().strip()
    trigger_words = ['review', 'run', 'code-review']
    if any(f"/{word}" in text for word in trigger_words):
        return True
    parts = text.split()
    if len(parts) == 2 and parts[1] in trigger_words:
        return True
    return False
