"""
Module to identify the Git platform/provider
(GitHub, GitLab, Bitbucket, etc.) for a given repository.
"""
import os
from enum import StrEnum
from typing import Optional
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from git import Repo


class PlatformType(StrEnum):
    """Enumeration of supported Git provider/platform types."""
    GITHUB = "GitHub"
    GITLAB = "GitLab"
    BITBUCKET = "Bitbucket"
    GITEA = "Gitea"
    AZURE_DEVOPS = "Azure DevOps"


def identify_git_platform_by_ci_env() -> Optional[PlatformType]:
    """
    Identify the Git provider/platform type based on CI environment variables.
    """
    git_provider_ci_identifiers = {
        PlatformType.GITHUB: ["GITHUB_ACTIONS"],
        PlatformType.GITLAB: ["GITLAB_CI"],
        PlatformType.BITBUCKET: ["BITBUCKET_BUILD_NUMBER"],
    }
    for provider, env_vars in git_provider_ci_identifiers.items():
        if any(os.getenv(var) for var in env_vars):
            return provider
    return None


def identify_git_platform_from_remotes(repo_or_urls: Repo | list[str]) -> Optional[PlatformType]:
    """
    Identify the Git provider/platform type based on git remote URLs.
    """
    known_urls = {
        PlatformType.GITHUB: ["github.com"],
        PlatformType.GITLAB: ["gitlab"],
        PlatformType.BITBUCKET: ["bitbucket"],
        PlatformType.GITEA: ["gitea", "forgejo", "codeberg.org"],  # not tested yet
        PlatformType.AZURE_DEVOPS: ["dev.azure.com", "visualstudio.com"],  # not tested yet
    }
    if isinstance(repo_or_urls, Repo):
        try:
            remote_urls: list[str] = repo_or_urls.remotes.origin.urls or []
        except AttributeError:
            remote_urls = []
        remotes = [extract_base_url(i) for i in remote_urls]
    else:
        remotes = repo_or_urls
    for provider, url_parts in known_urls.items():
        if any(any(part in url for part in url_parts) for url in remotes):
            return provider
    return None


def identify_git_platform(repo: Repo) -> Optional[PlatformType]:
    """
    Identify the Git provider/platform type using multiple strategies.
    """
    return identify_git_platform_by_ci_env() or \
        identify_git_platform_from_remotes(repo) or \
        identify_git_platform_from_files(repo)


@lru_cache()
def extract_base_url(git_url: str) -> str:
    """Extract base URL from git remote URL"""
    # Handle SSH URLs (git@domain:user/repo.git)
    if git_url.startswith("git@"):
        domain = git_url.split("@")[1].split(":")[0].lower()
        return f"https://{domain}"

    parsed = urlparse(git_url)

    # Handle ssh://git@domain/... URLs
    # @todo: verify this is actual case and works as expected (not tested)
    if parsed.scheme == "ssh" and parsed.hostname:
        return f"https://{parsed.hostname}".lower()

    # Handle HTTPS URLs
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def identify_git_platform_from_files(repo: Repo) -> Optional[PlatformType]:
    """
    Identify the Git provider/platform type based on provider-specific files in the repository.
    """
    if repo.working_tree_dir is None:
        return None
    git_platform_specific_files = {
        PlatformType.GITHUB: [".github"],
        PlatformType.GITLAB: [".gitlab", ".gitlab-ci.yml"],
        PlatformType.GITEA: [".gitea"],  # not tested yet
        PlatformType.AZURE_DEVOPS: ["azure-pipelines.yml", ".azure-pipelines"],  # not tested yet
    }
    path = Path(repo.working_tree_dir)
    for provider, files in git_platform_specific_files.items():
        if any((path / file).exists() for file in files):
            return provider
    return None
