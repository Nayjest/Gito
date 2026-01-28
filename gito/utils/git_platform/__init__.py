"""
Utilities for interacting with various Git hosting platforms.
"""
import git

from .platform_types import PlatformType, identify_git_platform
from .adapters import get_platform_adapter, BaseGitPlatform


def platform(repo: git.Repo) -> BaseGitPlatform:
    """
    Get the appropriate Git platform adapter for the given repository.
    Args:
        repo (git.Repo): The Git repository instance.
    Returns:
        BaseGitPlatform: An instance of the platform adapter.
    Raises:
        ValueError: If the platform type is unsupported or if the input type is invalid.
    """
    platform_type: PlatformType = identify_git_platform(repo)
    return get_platform_adapter(platform_type, repo=repo)
