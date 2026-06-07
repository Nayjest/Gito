import pytest

from gito.utils.git_platform.adapters import (
    GitHubPlatform,
    GitLabPlatform,
    get_platform_adapter,
)
from gito.utils.git_platform.platform_types import PlatformType

GH_BASE = "https://github.com/o/r"
GL_BASE = "https://gitlab.com/o/r"


def test_factory_returns_github_adapter():
    adapter = get_platform_adapter(PlatformType.GITHUB, GH_BASE)
    assert isinstance(adapter, GitHubPlatform)
    assert adapter.repo_base_url == GH_BASE


def test_factory_returns_gitlab_adapter():
    adapter = get_platform_adapter(PlatformType.GITLAB, GL_BASE)
    assert isinstance(adapter, GitLabPlatform)


def test_github_adapter_urls():
    adapter = get_platform_adapter(PlatformType.GITHUB, GH_BASE)
    assert adapter.create_pr_url("br") == f"{GH_BASE}/compare/br?expand=1"
    assert adapter.secrets_management_url() == f"{GH_BASE}/settings/secrets/actions"
    assert adapter.file_url("a.py", start_line=3) == f"{GH_BASE}/blob/main/a.py#L3"


def test_gitlab_adapter_create_pr_url():
    adapter = get_platform_adapter(PlatformType.GITLAB, GL_BASE)
    assert "merge_requests/new" in adapter.create_pr_url("br")


def test_factory_unsupported_platform():
    with pytest.raises(ValueError):
        get_platform_adapter(PlatformType.BITBUCKET, GH_BASE)


def test_factory_invalid_input_type():
    with pytest.raises(ValueError):
        get_platform_adapter(PlatformType.GITHUB, 123)


def test_adapter_requires_repo_or_base_url():
    with pytest.raises(ValueError):
        GitHubPlatform(repo=None, repo_base_url=None)
