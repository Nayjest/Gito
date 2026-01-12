"""
Utilities for working with GitHub platform.
"""
import os
import re
from typing import Optional

import git

from ..env import Env


def is_running_in_github_action() -> bool:
    """
    Check if the code is running inside a GitHub Actions environment.
    Returns:
        bool: True if running in GitHub Actions, False otherwise.
    """
    return os.getenv("GITHUB_ACTIONS") == "true"


def extract_gh_owner_repo(repo: git.Repo) -> tuple[str, str]:
    """
    Extracts the GitHub owner and repository name.

    Returns:
        tuple[str, str]: A tuple containing the owner and repository name.
    """
    try:
        remote_url = repo.remotes.origin.url
    except Exception as e:
        raise ValueError("Could not get remote URL from the repository.") from e
    if remote_url.startswith('git@github.com:'):
        # SSH format: git@github.com:owner/repo.git
        repo_path = remote_url.split(':')[1].replace('.git', '')
    elif remote_url.startswith('https://github.com/'):
        # HTTPS format: https://github.com/owner/repo.git
        repo_path = remote_url.replace('https://github.com/', '').replace('.git', '')
    else:
        raise ValueError("Unsupported remote URL format")
    owner, repo_name = repo_path.split('/')
    return owner, repo_name


def get_gh_create_pr_link(repo: git.Repo, branch: str) -> Optional[str]:
    """
    Return a GitHub URL to create a pull request for the given branch.
    """
    try:
        owner, repo_name = extract_gh_owner_repo(repo)
        return f"https://github.com/{owner}/{repo_name}/compare/{branch}?expand=1"
    except ValueError:
        return None


def get_gh_secrets_link(repo: git.Repo) -> Optional[str]:
    """
    Return a GitHub URL to manage secrets.
    """
    try:
        owner, repo_name = extract_gh_owner_repo(repo)
        return f"https://github.com/{owner}/{repo_name}/settings/secrets/actions"
    except ValueError:
        return None


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
