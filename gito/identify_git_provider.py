"""
Module to identify the Git provider (GitHub, GitLab, Bitbucket, etc.) for a given repository
"""
import os
from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlparse
from pathlib import Path
from git import Repo


class GitProvider(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"
    BITBUCKET = "bitbucket"


def identify_git_provider_by_ci_env() -> GitProvider | None:
    """
    Identify the Git provider based on CI environment variables.
    """
    git_provider_ci_identifiers = {
        GitProvider.GITHUB: ["GITHUB_ACTIONS"],
        GitProvider.GITLAB: ["GITLAB_CI"],
        GitProvider.BITBUCKET: ["BITBUCKET_BUILD_NUMBER"],
    }
    for provider, env_vars in git_provider_ci_identifiers.items():
        if any(os.getenv(var) for var in env_vars):
            return provider
    return None


def identify_git_provider_from_remotes(repo_or_urls: Repo | list[str]) -> GitProvider | None:
    """
    Identify the Git provider based on git remote URLs.
    """
    known_urls = {
        GitProvider.GITHUB: ["github.com"],
        GitProvider.GITLAB: ["gitlab"],
        GitProvider.BITBUCKET: ["bitbucket"],
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


def identify_git_provider(repo: Repo) -> GitProvider | None:
    """
    Identify the Git provider using multiple strategies.
    """
    return identify_git_provider_by_ci_env() or \
        identify_git_provider_from_remotes(repo) or \
        identify_git_provider_from_files(repo)


@lru_cache()
def extract_base_url(git_url: str) -> str:
    """Extract base URL from git remote URL"""
    # Handle SSH URLs (git@domain:user/repo.git)
    if git_url.startswith("git@"):
        domain = git_url.split("@")[1].split(":")[0].lower()
        return f"https://{domain}"

    # Handle HTTPS URLs
    parsed = urlparse(git_url)
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def identify_git_provider_from_files(repo: Repo) -> GitProvider | None:
    """
    Identify the Git provider based on CI environment variables.
    """
    git_provider_specific_files = {
        GitProvider.GITHUB: [".github"],
        GitProvider.GITLAB: [".gitlab", ".gitlab-ci.yml"],
    }
    path = Path(repo.working_tree_dir)
    for provider, files in git_provider_specific_files.items():
        if any((path / file).exists() for file in files):
            return provider
    return None
