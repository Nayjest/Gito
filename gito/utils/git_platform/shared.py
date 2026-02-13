from typing import Optional

from git import Repo


def get_repo_domain_and_path(repo: Repo) -> tuple[str, str]:
    """
    Extract the domain and repository path from the git remote URL.
    Examples:
        git@github.com:owner/repo.git -> ('github.com', 'owner/repo')
        https://gitlab.com/owner/repo.git -> ('gitlab.com', 'owner/repo')
    Args:
        repo (git.Repo): The git repository object.
    Returns:
        tuple[str, str]: A tuple containing the domain and repository path.
    Raises:
        ValueError: If the remote URL format is unsupported.
    """
    try:
        remote_url = repo.remotes.origin.url
    except Exception as e:
        raise ValueError("Could not get remote URL from the repository.") from e
    if remote_url.endswith(".git"):
        remote_url = remote_url[:-4]
    if remote_url.startswith('git@') and ':' in remote_url:
        domain, path = remote_url[4:].split(':', 1)
        return domain, path
    elif remote_url.startswith('https://'):
        domain, path = remote_url[8:].split('/', 1)
        return domain, path
    else:
        raise ValueError("Unsupported git remote URL format")


def get_repo_base_web_url(repo: Repo) -> Optional[str]:
    """
    Get the base web URL of the repository.
    Tested / supported platforms: GitHub, GitLab.
    Args:
        repo (Repo): The git repository object.
    Returns:
        Optional[str]: The web URL of the repository.
    """
    try:
        domain, path = get_repo_domain_and_path(repo)
    except ValueError:
        return None
    return f"https://{domain}/{path}"


def get_repo_web_url(
    repo: Repo | str,
    subpath: str
) -> Optional[str]:
    """
    Get the web URL of the repository.
    Tested / supported platforms: GitHub, GitLab.
    Args:
        repo (Repo | str): The git repository object or repository base URL.
        subpath (str): Path to append to the base URL.
    Returns:
        Optional[str]: The web URL of the repository.
    """
    if isinstance(repo, Repo):
        repo = get_repo_base_web_url(repo)
    return (repo + "/" + subpath.lstrip('/')) if repo else None


def get_repo_owner_and_name(repo: Repo) -> tuple[str, str]:
    """
    Extract the repository owner and repository name.

    Returns:
        tuple[str, str]: A tuple containing the owner and repository name.
    Raises:
        ValueError: If the remote URL format is unsupported.
    """
    domain, path = get_repo_domain_and_path(repo)
    owner, repo_name = path.split('/', 1)
    return owner, repo_name
