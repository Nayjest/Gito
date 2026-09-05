import pytest
from unidiff import PatchSet

from gito.core import filter_diff

DIFF = """diff --git a/gito/core.py b/gito/core.py
index 1111111..2222222 100644
--- a/gito/core.py
+++ b/gito/core.py
@@ -1 +1 @@
-old
+new
diff --git a/tests/test_core.py b/tests/test_core.py
index 3333333..4444444 100644
--- a/tests/test_core.py
+++ b/tests/test_core.py
@@ -1 +1 @@
-old
+new
diff --git a/README.md b/README.md
index 5555555..6666666 100644
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-old
+new
"""


def paths(patch_set):
    return [patched_file.path for patched_file in patch_set]


@pytest.fixture
def patch_set() -> PatchSet:
    return PatchSet.from_string(DIFF)


def test_empty_filters_return_the_diff_unchanged(patch_set):
    assert filter_diff(patch_set, []) is patch_set
    assert filter_diff(patch_set, "") is patch_set
    assert filter_diff(patch_set, " , ") is patch_set


def test_filters_may_be_a_comma_separated_string(patch_set):
    assert paths(filter_diff(patch_set, "gito/*, README.md")) == ["gito/core.py", "README.md"]


def test_filters_may_be_a_list(patch_set):
    assert paths(filter_diff(patch_set, ["tests/*"])) == ["tests/test_core.py"]


def test_exclude_inverts_the_matching(patch_set):
    assert paths(filter_diff(patch_set, "tests/*", exclude=True)) == [
        "gito/core.py",
        "README.md",
    ]


def test_non_matching_filters_produce_an_empty_result(patch_set):
    assert filter_diff(patch_set, "*.rs") == []


def test_unsupported_filter_type_is_rejected(patch_set):
    with pytest.raises(ValueError, match="filter_diff"):
        filter_diff(patch_set, None)
