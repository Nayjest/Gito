import pytest

from gito.utils.git_platform.platform_types import (
    PlatformType,
    extract_base_url,
    identify_git_platform_by_ci_env,
    identify_git_platform_from_remotes,
)

CI_ENV_VARS = ["GITHUB_ACTIONS", "GITLAB_CI", "BITBUCKET_BUILD_NUMBER"]


@pytest.mark.parametrize(
    ("git_url", "expected"),
    [
        ("git@github.com:owner/repo.git", "https://github.com"),
        ("https://gitlab.com/owner/repo.git", "https://gitlab.com"),
        ("ssh://git@example.com/owner/repo", "https://example.com"),
        ("https://GitHub.com/owner/repo", "https://github.com"),
    ],
)
def test_extract_base_url(git_url, expected):
    assert extract_base_url(git_url) == expected


@pytest.mark.parametrize(
    ("urls", "expected"),
    [
        (["https://github.com/o/r"], PlatformType.GITHUB),
        (["https://gitlab.com/o/r"], PlatformType.GITLAB),
        (["https://bitbucket.org/o/r"], PlatformType.BITBUCKET),
        (["https://codeberg.org/o/r"], PlatformType.GITEA),
        (["https://example.com/o/r"], None),
        ([], None),
    ],
)
def test_identify_git_platform_from_remotes(urls, expected):
    assert identify_git_platform_from_remotes(urls) == expected


def test_identify_git_platform_by_ci_env(monkeypatch):
    for var in CI_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    assert identify_git_platform_by_ci_env() is None
    monkeypatch.setenv("GITLAB_CI", "true")
    assert identify_git_platform_by_ci_env() == PlatformType.GITLAB
