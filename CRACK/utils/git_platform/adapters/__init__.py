import git
from ..platform_types import PlatformType
from .base import BaseGitPlatform
from .github import GitHubPlatform
from .gitlab import GitLabPlatform


IMPLEMENTATIONS = {
    PlatformType.GITHUB: GitHubPlatform,
    PlatformType.GITLAB: GitLabPlatform,
}


def get_platform_adapter(
    platform_type: PlatformType,
    repo_or_base_url: git.Repo | str,
) -> BaseGitPlatform:
    """
    Factory function to get the appropriate Git platform adapter.
    Args:
        platform_type (PlatformType): The type of Git platform.
        repo_or_base_url (git.Repo | str): The git repository or its base URL.
    Returns:
        BaseGitPlatform: An instance of the appropriate Git platform adapter.
    Raises:
        ValueError: If the platform type is unsupported or if the input type is invalid.
    """
    if isinstance(repo_or_base_url, git.Repo):
        repo_base_url = None
        repo = repo_or_base_url
    elif isinstance(repo_or_base_url, str):
        repo_base_url = repo_or_base_url
        repo = None
    else:
        raise ValueError("repo_or_base_url must be a git.Repo or str")

    adapter_class = IMPLEMENTATIONS.get(platform_type)
    if not adapter_class:
        raise ValueError(f"Unsupported platform type: {platform_type}")
    return adapter_class(repo=repo, repo_base_url=repo_base_url)


__all__ = [
    "BaseGitPlatform",
    "GitHubPlatform",
    "GitLabPlatform",
]
