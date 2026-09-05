import git
import pytest

from gito.core import get_base_branch


@pytest.fixture(autouse=True)
def without_github_actions_env(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_BASE_REF", raising=False)


def make_clone(tmp_path, default_branch: str) -> git.Repo:
    origin_path = tmp_path / "origin"
    origin = git.Repo.init(origin_path)
    with origin.config_writer() as config:
        config.set_value("user", "name", "Gito tests")
        config.set_value("user", "email", "gito-tests@example.invalid")
    (origin_path / "readme.txt").write_text("hello\n", encoding="utf-8")
    origin.index.add(["readme.txt"])
    origin.index.commit("Initial commit")
    origin.git.branch("-M", default_branch)

    return git.Repo.clone_from(str(origin_path), str(tmp_path / "clone"))


def test_base_branch_is_resolved_from_the_remote_head(tmp_path):
    assert get_base_branch(make_clone(tmp_path, "main")) == "origin/main"


def test_base_branch_falls_back_to_well_known_names(tmp_path):
    repo = make_clone(tmp_path, "master")
    # Emulate the CI checkouts where refs/remotes/origin/HEAD is not created.
    repo.git.remote("set-head", "origin", "--delete")

    assert get_base_branch(repo) == "origin/master"


def test_missing_default_branch_is_an_error(tmp_path):
    repo = make_clone(tmp_path, "trunk")
    repo.git.remote("set-head", "origin", "--delete")

    with pytest.raises(ValueError, match="No default branch found"):
        get_base_branch(repo)


def test_github_base_ref_wins_when_running_in_actions(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_BASE_REF", "release/1.x")

    assert get_base_branch(make_clone(tmp_path, "main")) == "origin/release/1.x"
