from pathlib import Path

import git
import pytest

from gito.core import file_lines, get_affected_code_block, read_file

CONTENT = "alpha\nbravo\ncharlie\n"


@pytest.fixture
def repo_with_file(tmp_path):
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Gito tests")
        config.set_value("user", "email", "gito-tests@example.invalid")
        config.set_value("core", "autocrlf", "false")

    # write_bytes avoids the newline translation write_text() does on Windows
    (tmp_path / "sample.txt").write_bytes(CONTENT.encode("utf-8"))
    repo.index.add(["sample.txt"])
    repo.index.commit("Initial commit")
    return repo


def test_read_file_from_head_and_from_working_tree(repo_with_file):
    assert read_file(repo_with_file, "sample.txt") == CONTENT
    assert read_file(repo_with_file, "sample.txt", use_local_files=True) == CONTENT


def test_read_file_falls_back_to_head_when_local_file_is_missing(repo_with_file):
    (Path(repo_with_file.working_tree_dir) / "sample.txt").unlink()

    assert read_file(repo_with_file, "sample.txt", use_local_files=True) == CONTENT


def test_file_lines_are_numbered(repo_with_file):
    assert file_lines(repo_with_file, "sample.txt") == "1: alpha\n2: bravo\n3: charlie\n"


def test_get_affected_code_block_returns_the_requested_range(repo_with_file):
    assert get_affected_code_block(repo_with_file, "sample.txt", 1, 2) == "1: alpha\n2: bravo"
    assert get_affected_code_block(repo_with_file, "sample.txt", "2", "3") == "2: bravo\n3: charlie"


def test_get_affected_code_block_without_line_numbers(repo_with_file):
    assert get_affected_code_block(repo_with_file, "sample.txt", 0, 2) is None
    assert get_affected_code_block(repo_with_file, "sample.txt", 1, None) is None


def test_get_affected_code_block_out_of_range(repo_with_file):
    assert not get_affected_code_block(repo_with_file, "sample.txt", 9000, 10000)


def test_get_affected_code_block_for_unknown_file(repo_with_file):
    assert get_affected_code_block(repo_with_file, "missing.txt", 1, 2) is None
