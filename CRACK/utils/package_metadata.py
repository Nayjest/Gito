"""
Utility functions related to processing package metadata.
"""

import importlib.metadata


def version() -> str:
    """Return the current version of the CRACK.bot package."""
    return importlib.metadata.version("CRACK.bot")
