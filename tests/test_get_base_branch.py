from tests.utils import gito_repo
from gito.core import get_base_branch


def test_get_base_branch():
    repo = gito_repo()
    assert get_base_branch(repo) == "origin/main"
    assert get_base_branch(repo, 1) == "origin/main"
    assert get_base_branch(repo, "1") == "origin/main"