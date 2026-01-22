"""
Utility functions for Git operations.
"""
import git
import typer
from microcore import ui


def get_cwd_repo_or_fail() -> git.Repo:
    """
    Get Git repository from current working directory.

    Exits with code 2 (usage error) if not inside a Git repository.
    Returns:
        git.Repo: The Git repository object.
    Raises:
        typer.Exit: If the current folder is not a Git repository.
    """
    try:
        repo = git.Repo(".", search_parent_directories=False)
        return repo
    except git.InvalidGitRepositoryError:
        ui.error(
            "Current folder is not a Git repository.\n"
            "Navigate to your repository root and run again."
        )
        raise typer.Exit(2)
