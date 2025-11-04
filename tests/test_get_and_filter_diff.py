import pytest
from tests.utils import gito_repo
from unidiff.patch import PatchSet, PatchedFile
from gito.core import get_diff, filter_diff


def test_get_base_branch():
    repo = gito_repo()
    diff = get_diff(repo, "main", "HEAD~3")
    assert isinstance(diff, PatchSet)
    assert isinstance(diff[0], PatchedFile)
    diff1 = get_diff(repo, "INDEX", "HEAD~3")
    assert isinstance(diff1, PatchSet)
    assert isinstance(diff1[0], PatchedFile)
    diff2 = get_diff(repo, None, "HEAD~3")
    assert diff2 == diff1
    assert filter_diff(diff1, []) == diff2
    with pytest.raises(ValueError):
        assert filter_diff(diff1, None)
    assert filter_diff(diff1, "*") == [i for i in diff2]
