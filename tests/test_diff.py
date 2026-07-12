from pathlib import Path

import git
import pytest

from gito.core import MergeBaseError, get_diff


def commit_file(repo, path, content, message):
    (Path(repo.working_tree_dir) / path).write_text(content, encoding="utf-8")
    repo.index.add([path])
    repo.index.commit(message)


@pytest.fixture
def diverged_repo(tmp_path):
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Gito tests")
        config.set_value("user", "email", "gito-tests@example.invalid")

    commit_file(repo, "shared.txt", "initial\n", "Initial commit")
    repo.git.branch("-M", "main")
    repo.git.checkout("-b", "feature")
    commit_file(repo, "feature_only.txt", "feature\n", "Feature change")
    repo.git.checkout("main")
    commit_file(repo, "base_only.txt", "base\n", "Base change")
    return repo


def test_merge_base_diff_includes_only_feature_changes(diverged_repo):
    diff = get_diff(diverged_repo, what="feature", against="main")

    assert [patched_file.path for patched_file in diff] == ["feature_only.txt"]


def test_direct_diff_remains_an_explicit_opt_in(diverged_repo):
    diff = get_diff(diverged_repo, what="feature", against="main", use_merge_base=False)

    assert {patched_file.path for patched_file in diff} == {"feature_only.txt", "base_only.txt"}


def test_merge_base_diff_includes_staged_and_unstaged_worktree_changes(diverged_repo):
    diverged_repo.git.checkout("feature")
    staged_path = Path(diverged_repo.working_tree_dir) / "staged.txt"
    staged_path.write_text("staged\n", encoding="utf-8")
    diverged_repo.index.add(["staged.txt"])
    (Path(diverged_repo.working_tree_dir) / "feature_only.txt").write_text(
        "feature, modified\n", encoding="utf-8"
    )

    diff = get_diff(diverged_repo, against="main")

    assert {patched_file.path for patched_file in diff} == {"feature_only.txt", "staged.txt"}


def test_merged_feature_ref_uses_its_pre_merge_base(diverged_repo):
    diverged_repo.git.merge("feature", "--no-ff", "-m", "Merge feature")

    diff = get_diff(diverged_repo, what="feature", against="main")

    assert [patched_file.path for patched_file in diff] == ["feature_only.txt"]


def test_merge_base_failure_does_not_fall_back_to_direct_diff(diverged_repo):
    unrelated_sha = diverged_repo.git.commit_tree(
        diverged_repo.head.commit.tree.hexsha, m="Unrelated history"
    )
    diverged_repo.git.update_ref("refs/heads/unrelated", unrelated_sha)

    with pytest.raises(MergeBaseError, match="Cannot determine a merge base"):
        get_diff(diverged_repo, what="feature", against="unrelated")
