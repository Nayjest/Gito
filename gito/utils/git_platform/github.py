"""
Utilities for working with GitHub platform.
"""
import os
import re
from typing import Optional

import git
from urllib.parse import quote

from ...env import Env
from .shared import get_repo_web_url


def is_running_in_github_action() -> bool:
    """
    Check if the code is running inside a GitHub Actions environment.
    Returns:
        bool: True if running in GitHub Actions, False otherwise.
    """
    return os.getenv("GITHUB_ACTIONS") == "true"


def get_gh_create_pr_link(repo_or_base_url: git.Repo | str, branch: str) -> Optional[str]:
    """
    Return a GitHub URL to create a pull request for the given branch.
    """
    branch = quote(branch, safe='')
    return get_repo_web_url(repo_or_base_url, f"/compare/{branch}?expand=1")


def get_gh_secrets_link(repo_or_base_url: git.Repo | str) -> Optional[str]:
    """
    Return a GitHub URL to manage secrets.
    """
    return get_repo_web_url(repo_or_base_url, "/settings/secrets/actions")


def gh_ci_src_branch() -> Optional[str]:
    """
    Try to get the current branch name from GitHub Actions environment variables.
    Returns:
        Optional[str]: The branch name if available, None otherwise.
    """
    # https://docs.github.com/en/actions/reference/workflows-and-actions/variables

    if branch_name := os.getenv('GITHUB_HEAD_REF'):
        # GITHUB_HEAD_REF — The source branch of a pull request
        # Push: empty
        # PR: feature-branch (just the branch name, no refs/heads/ prefix)
        return branch_name

    ref = os.getenv("GITHUB_REF", "")
    if ref.startswith("refs/heads/"):
        return ref[len("refs/heads/"):]
    return None


def get_gh_file_link(
    repo_or_base_url: git.Repo | str,
    file: str,
    branch="main",
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> Optional[str]:
    branch, file = quote(branch, safe=''), quote(file, safe='/')
    url_path = f"/blob/{branch}/{file}"
    if start_line:
        url_path += f"#L{start_line}"
        if end_line and end_line != start_line:
            url_path += f"-L{end_line}"
    return get_repo_web_url(repo_or_base_url, url_path)


def detect_github_env() -> dict:
    """
    Try to detect GitHub repository/PR info from environment variables (for GitHub Actions).
    Returns a dict with github_repo, github_pr_sha, github_pr_number, github_ref, etc.
    """
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr_sha = os.environ.get("GITHUB_SHA", "")
    pr_number = os.environ.get("GITHUB_REF", "")
    branch = ""
    ref = os.environ.get("GITHUB_REF", "")
    # Try to resolve PR head SHA if available.
    # On PRs, GITHUB_HEAD_REF/BASE_REF contain branch names.
    if "GITHUB_HEAD_REF" in os.environ:
        branch = os.environ["GITHUB_HEAD_REF"]
    elif ref.startswith("refs/heads/"):
        branch = ref[len("refs/heads/"):]
    elif ref.startswith("refs/pull/"):
        # for pull_request events
        branch = ref

    d = {
        "github_repo": repo,
        "github_pr_sha": pr_sha,
        "github_pr_number": pr_number,
        "github_branch": branch,
        "github_ref": ref,
    }
    # Fallback for local usage: try to get from git
    if not repo or repo == "octocat/Hello-World":
        git_repo = None
        try:
            git_repo = git.Repo(Env.working_folder, search_parent_directories=True)
            origin = git_repo.remotes.origin.url
            # e.g. git@github.com:Nayjest/ai-code-review.git -> Nayjest/ai-code-review
            match = re.search(r"[:/]([\w\-]+)/([\w\-\.]+?)(\.git)?$", origin)
            if match:
                d["github_repo"] = f"{match.group(1)}/{match.group(2)}"
            d["github_pr_sha"] = git_repo.head.commit.hexsha
            d["github_branch"] = (
                git_repo.active_branch.name if hasattr(git_repo, "active_branch") else ""
            )
        except Exception:
            pass
        finally:
            if git_repo:
                try:
                    git_repo.close()
                except Exception:
                    pass
    # If branch is not a commit SHA, prefer branch for links
    if d["github_branch"]:
        d["github_pr_sha_or_branch"] = d["github_branch"]
    elif d["github_pr_sha"]:
        d["github_pr_sha_or_branch"] = d["github_pr_sha"]
    else:
        d["github_pr_sha_or_branch"] = "main"
    return d
