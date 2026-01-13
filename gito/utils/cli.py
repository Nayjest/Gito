import sys
from typing import Optional, Callable

import typer


def make_streaming_function(handler: Optional[Callable] = None) -> Callable:
    """
    Create a streaming function that processes text chunks using an optional handler.
    Used as callback for streaming LLM responses.
    Args:
        handler (Callable, optional): A function to process each text chunk before printing.
            If None, the text chunk is printed as is.
    Returns:
        Callable: A function that takes a text chunk and processes it.
    """
    def stream(text):
        if handler:
            text = handler(text)
        print(text, end='', flush=True)
    return stream


def no_subcommand(app: typer.Typer) -> bool:
    """
    Check if no subcommand was provided to the target Typer application.
    """
    return not (
        (first_arg := next((a for a in sys.argv[1:] if not a.startswith('-')), ""))
        and first_arg in (
            cmd.name or (cmd.callback.__name__.replace('_', '-') if cmd.callback else "")
            for cmd in app.registered_commands
        )
        or '--help' in sys.argv
    )
