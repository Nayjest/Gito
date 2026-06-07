import pytest

from gito.utils.string import block_wrap_lr, max_line_len, parse_refs_pair


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", (None, None)),
        ("main", ("main", None)),
        ("main..feature", ("main", "feature")),
        ("..feature", (None, "feature")),
    ],
)
def test_parse_refs_pair(value, expected):
    assert parse_refs_pair(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("", 0),
        ("one", 3),
        ("a\nabcd\nab", 4),
    ],
)
def test_max_line_len(value, expected):
    assert max_line_len(value) == expected


def test_block_wrap_lr_pads_to_longest_line():
    assert block_wrap_lr("a\nbcd", right="|") == "a  |\nbcd|"


def test_block_wrap_lr_left_prefix():
    assert block_wrap_lr("ab\ncd", left="> ") == "> ab\n> cd"


def test_block_wrap_lr_skips_right_when_over_max_rwrap():
    assert block_wrap_lr("abcdef", right="|", max_rwrap=3) == "abcdef"
