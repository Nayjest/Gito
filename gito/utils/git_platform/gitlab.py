"""
Utilities for working with GitLab platform.
"""
import os
from typing import Optional

import git
from urllib.parse import quote

from .shared import get_repo_web_url


def is_running_in_gitlab_ci() -> bool:
    """
    Check if the code is running inside a GitLab CI/CD environment.

    Returns:
        bool: True if running in GitLab CI, False otherwise.

    See: https://docs.gitlab.com/ci/variables/predefined_variables/
    """
    return os.getenv("GITLAB_CI") == "true"


def extract_gitlab_owner_repo(repo: git.Repo) -> tuple[str, str]:
    """
    Extracts the GitLab owner and repository name.

    Returns:
        tuple[str, str]: A tuple containing the owner and repository name.
    """
    try:
        remote_url = repo.remotes.origin.url
    except Exception as e:
        raise ValueError("Could not get remote URL from the repository.") from e
    if remote_url.startswith('git@gitlab.com:'):
        # SSH format: git@gitlab.com:owner/repo.git
        repo_path = remote_url.split(':')[1].replace('.git', '')
    elif remote_url.startswith('https://gitlab.com/'):
        # HTTPS format: https://gitlab.com/owner/repo.git
        repo_path = remote_url.replace('https://gitlab.com/', '').replace('.git', '')
    else:
        raise ValueError("Unsupported remote URL format")
    parts = repo_path.rsplit('/', 1)
    if len(parts) != 2:
        raise ValueError("Unsupported gitlab repository path format")
    owner, repo_name = parts
    return owner, repo_name


def get_gitlab_create_mr_link(repo_or_base_url: git.Repo | str, branch: str) -> Optional[str]:
    """
    Return a GitLab URL to create a merge request for the given branch.
    """
    branch = quote(branch, safe='')
    return get_repo_web_url(
        repo_or_base_url, f"/-/merge_requests/new?merge_request%5Bsource_branch%5D={branch}"
    )


def gitlab_ci_src_branch() -> Optional[str]:
    """
    Get the current branch name in a GitLab CI environment.
    If not running in GitLab CI, returns None.
    """
    # See: https://docs.gitlab.com/ci/variables/predefined_variables/
    if not is_running_in_gitlab_ci():
        return None
    if "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME" in os.environ:
        return os.environ["CI_MERGE_REQUEST_SOURCE_BRANCH_NAME"]
    elif "CI_COMMIT_BRANCH" in os.environ:
        return os.environ["CI_COMMIT_BRANCH"]
    return None


def get_gitlab_secrets_link(repo_or_base_url: git.Repo | str) -> Optional[str]:
    """
    Return a GitLab URL to manage secrets.
    Returns None in case of error.
    Args:
        repo_or_base_url (git.Repo | str): The git repository object or repository base URL.
    """
    return get_repo_web_url(repo_or_base_url, "/-/settings/ci_cd#js-cicd-variables-settings")


def get_gitlab_access_tokens_link(repo_or_base_url: git.Repo | str) -> Optional[str]:
    """
    Return a GitLab URL to create an access token.
    Returns None in case of error.
    """
    return get_repo_web_url(repo_or_base_url, "/-/settings/access_tokens")


def get_gitlab_file_link(
    repo_or_base_url: git.Repo | str,
    file: str,
    branch="main",
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> Optional[str]:
    """
    Return a GitLab URL to view a file on a branch, optionally anchored to line numbers.
    Returns None in case of error.
    """
    branch, file = quote(branch, safe=''), quote(file, safe='/')
    url_path = f"/-/blob/{branch}/{file}?ref_type=heads"
    if start_line is not None:
        url_path += f"#L{start_line}"
        if end_line is not None and end_line != start_line:
            url_path += f"-{end_line}"
    return get_repo_web_url(repo_or_base_url, url_path)
