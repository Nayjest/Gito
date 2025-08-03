import logging
import sys
from dataclasses import dataclass

import typer

from gito.utils import filter_kwargs, no_subcommand  # Adjust import as needed


def test_no_subcommand():
    original_argv = sys.argv.copy()
    try:
        app = typer.Typer()
        app.command(name="test-cmd")(lambda: None)

        # Test with no subcommand
        sys.argv = ["script.py"]
        assert no_subcommand(app) is True

        # Test with valid subcommand
        sys.argv = ["script.py", "test-cmd"]
        assert no_subcommand(app) is False

        # Test with --help
        sys.argv = ["script.py", "--help"]
        assert no_subcommand(app) is False

        # Test with option but no subcommand
        sys.argv = ["script.py", "--verbose"]
        assert no_subcommand(app) is True

        # Test with invalid subcommand
        sys.argv = ["script.py", "invalid-cmd"]
        assert no_subcommand(app) is True
    finally:
        sys.argv = original_argv


def test_filter_kwargs(caplog):

    @dataclass
    class Example:
        a: int
        b: str = "default"

    with caplog.at_level(logging.WARNING):
        assert filter_kwargs(
            Example,
            {"a": 1, "b": "test", "c": 3}
        ) == {"a": 1, "b": "test"}
        assert "'c'" in caplog.text

    caplog.clear()
    assert filter_kwargs(
        Example,
        {"a": 1, "b": "test", "d": 3},
        log_warnings=False
    ) == {"a": 1, "b": "test"}
    assert caplog.text == ""
