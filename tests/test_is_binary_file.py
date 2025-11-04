from gito.constants import TEXT_ICON_RELATIVE_PATH
from tests.utils import gito_repo
from gito.core import is_binary_file


def test_is_binary_file():
    repo = gito_repo()
    assert is_binary_file(repo, TEXT_ICON_RELATIVE_PATH)
    assert not is_binary_file(repo, "LICENSE")
