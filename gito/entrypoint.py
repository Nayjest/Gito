"""Entry point for the Gito command-line interface (CLI)."""

from .git_installation_check import ensure_git_installed

ensure_git_installed()  # should be called before importing other modules that depend on Git

# flake8: noqa: E402, F401
from .cli import main

__all__ = ["main"]
