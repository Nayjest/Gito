from tests.utils import gito_repo
from gito.core import read_file, file_lines, get_affected_code_block


def test_read_file_lines():
    repo = gito_repo()
    content = open("LICENSE", "r", encoding="utf-8").read()
    assert read_file(repo, "LICENSE", False) == content
    assert read_file(repo, "LICENSE", True) == content

    repo = gito_repo()
    n_lines = file_lines(repo, "LICENSE")
    for ln in content.splitlines():
        assert ln in n_lines

    assert n_lines[:3] == "1: "

    assert get_affected_code_block(
        repo, "LICENSE", 1, 2
    ) == "1: MIT License\n2: "

    assert not get_affected_code_block(
        repo, "LICENSE", 9000, 10000
    )
