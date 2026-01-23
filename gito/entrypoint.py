"""Entry point for the Gito command-line interface (CLI)."""

# Defer Git installation check to commands that actually need Git.
# Calling `ensure_git_installed()` at import time caused CLI invocations
# (for example `python -m gito version`) to exit in environments without
# Git available. Individual commands should call `ensure_git_installed()`
# when they require Git functionality.
# flake8: noqa: E402, F401

def main():
	"""Lazily import and run the CLI main to avoid heavy imports at module import time."""
	from .cli import main as _main
	return _main()

__all__ = ["main"]
