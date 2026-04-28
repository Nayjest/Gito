import pytest

from gito.utils.string import parse_refs_pair


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
