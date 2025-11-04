from pathlib import Path
from git import Repo


ROOT = Path(__file__).parent.parent

def gito_repo() -> Repo:
    """
    Get the Git repository object for the Gito root directory.

    Returns:
        Repo: The Git repository object.
    """
    return Repo(ROOT)