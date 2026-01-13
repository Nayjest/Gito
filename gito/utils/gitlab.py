"""
Utilities for working with GitLab platform.
"""
from typing import Optional

import git


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


def get_gitlab_create_mr_link(repo: git.Repo, branch: str) -> Optional[str]:
    """
    Return a GitLab URL to create a merge request for the given branch.
    """
    try:
        owner, repo_name = extract_gitlab_owner_repo(repo)
        return (
            f"https://gitlab.com/{owner}/{repo_name}"
            f"/-/merge_requests/new?merge_request%5Bsource_branch%5D={branch}"
        )
    except ValueError:
        return None


def get_gitlab_repo_url(repo: git.Repo, url_path: str = "") -> Optional[str]:
    """
    Return the GitLab repository URL.
    Returns None in case of error.
    """
    try:
        owner, repo_name = extract_gitlab_owner_repo(repo)
        return f"https://gitlab.com/{owner}/{repo_name}" + url_path if url_path else ""
    except ValueError:
        return None


def get_gitlab_secrets_link(repo: git.Repo) -> Optional[str]:
    """
    Return a GitLab URL to manage secrets.
    Returns None in case of error.
    """
    return get_gitlab_repo_url(repo, "/-/settings/ci_cd#js-cicd-variables-settings")


def get_gitlab_access_tokens_link(repo: git.Repo) -> Optional[str]:
    """
    Return a GitLab URL to create an access token.
    Returns None in case of error.
    """
    return get_gitlab_repo_url(repo, "/-/settings/access_tokens")
