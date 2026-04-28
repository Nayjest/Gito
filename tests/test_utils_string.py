import pytest

from gito.utils.string import max_line_len, parse_refs_pair


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
