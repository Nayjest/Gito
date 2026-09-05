import logging
from pathlib import Path

import git
import pytest

from gito.constants import TEXT_ICON_RELATIVE_PATH
from gito.core import is_binary_file

PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"


@pytest.fixture
def repo_with_files(tmp_path):
    repo = git.Repo.init(tmp_path)
    with repo.config_writer() as config:
        config.set_value("user", "name", "Gito tests")
        config.set_value("user", "email", "gito-tests@example.invalid")

    (tmp_path / "text.txt").write_text("plain text\n", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(PNG_HEADER)
    repo.index.add(["text.txt", "image.png"])
    repo.index.commit("Initial commit")
    return repo


def test_committed_text_file_is_not_binary(repo_with_files):
    assert not is_binary_file(repo_with_files, "text.txt")


def test_committed_binary_file_is_binary(repo_with_files):
    assert is_binary_file(repo_with_files, "image.png")


def test_uncommitted_files_fall_back_to_the_working_tree(repo_with_files):
    root = Path(repo_with_files.working_tree_dir)
    (root / "new.txt").write_text("still text\n", encoding="utf-8")
    (root / "new.png").write_bytes(PNG_HEADER)

    assert not is_binary_file(repo_with_files, "new.txt")
    assert is_binary_file(repo_with_files, "new.png")


def test_missing_file_is_reported_as_binary(repo_with_files, caplog):
    with caplog.at_level(logging.ERROR):
        assert is_binary_file(repo_with_files, "does-not-exist.txt")
    assert "not found in the repository" in caplog.text


def test_text_icon_relative_path_points_at_a_real_file():
    icon = Path(__file__).resolve().parent.parent / TEXT_ICON_RELATIVE_PATH
    assert icon.is_file()
