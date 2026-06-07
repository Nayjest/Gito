from gito.utils.git_platform.github import (
    get_gh_create_pr_link,
    get_gh_file_link,
    get_gh_secrets_link,
    gh_ci_src_branch,
)
from gito.utils.git_platform.gitlab import (
    get_gitlab_create_mr_link,
    get_gitlab_file_link,
    gitlab_ci_src_branch,
)

BASE = "https://github.com/o/r"
GL_BASE = "https://gitlab.com/o/r"


def test_gh_create_pr_link():
    assert get_gh_create_pr_link(BASE, "feat/x") == f"{BASE}/compare/feat%2Fx?expand=1"


def test_gh_secrets_link():
    assert get_gh_secrets_link(BASE) == f"{BASE}/settings/secrets/actions"


def test_gh_file_link_with_line_range():
    assert get_gh_file_link(BASE, "src/a.py", branch="main", start_line=5, end_line=10) == (
        f"{BASE}/blob/main/src/a.py#L5-L10"
    )


def test_gh_file_link_single_line():
    assert get_gh_file_link(BASE, "a.py", start_line=5, end_line=5) == f"{BASE}/blob/main/a.py#L5"


def test_gh_file_link_no_lines():
    assert get_gh_file_link(BASE, "a.py") == f"{BASE}/blob/main/a.py"


def test_gitlab_create_mr_link():
    url = get_gitlab_create_mr_link(GL_BASE, "feat/x")
    assert url == f"{GL_BASE}/-/merge_requests/new?merge_request%5Bsource_branch%5D=feat%2Fx"


def test_gitlab_file_link_with_line_range():
    assert get_gitlab_file_link(GL_BASE, "a.py", start_line=5, end_line=10) == (
        f"{GL_BASE}/-/blob/main/a.py?ref_type=heads#L5-10"
    )


def test_gh_ci_src_branch_head_ref(monkeypatch):
    monkeypatch.setenv("GITHUB_HEAD_REF", "feature")
    assert gh_ci_src_branch() == "feature"


def test_gh_ci_src_branch_from_ref(monkeypatch):
    monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    assert gh_ci_src_branch() == "main"


def test_gh_ci_src_branch_none(monkeypatch):
    monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)
    monkeypatch.delenv("GITHUB_REF", raising=False)
    assert gh_ci_src_branch() is None


def test_gitlab_ci_src_branch_not_in_ci(monkeypatch):
    monkeypatch.delenv("GITLAB_CI", raising=False)
    assert gitlab_ci_src_branch() is None
