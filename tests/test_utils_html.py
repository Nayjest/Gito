import pytest

from gito.utils.html import remove_html_comments


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a<!-- c -->b", "ab"),
        ("no comments here", "no comments here"),
        ("x<!--\nmulti\nline\n-->y", "xy"),
        ("<!-- only -->", ""),
    ],
)
def test_remove_html_comments(value, expected):
    assert remove_html_comments(value) == expected
