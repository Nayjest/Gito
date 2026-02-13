import sys
import textwrap
from typing import Optional, Callable

import typer
import microcore as mc

from .git_platform.github import is_running_in_github_action
from .git_platform.gitlab import is_running_in_gitlab_ci
from .package_metadata import version


def is_running_in_ci() -> bool:
    """
    Check if the current environment is a Continuous Integration (CI) environment.
    Returns:
        True if running in GitHub Actions or GitLab CI, False otherwise.
    """
    return is_running_in_github_action() or is_running_in_gitlab_ci()


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


def logo(indent=2) -> str:
    """Generate Gito ASCII art logo."""
    r = mc.ui.reset

    # Character classifications
    CHAR_TYPES = {
        'shadow': set('╔═╗║╚╝╠╣╦╩╬'),
        'letter': set('█▓▒░■'),
        'version': set('⟦⟧v0123456789.'),
        'accent': set('⌬⧉▣⟁⟨⟩∘∙→⇡{⊕}'),
    }

    # Yellow → orange → red-orange → teal → cyan → blue
    COLORS = [
        (255, 220, 0),
        (255, 180, 30),
        (255, 120, 50),
        (80, 200, 180),
        (0, 230, 255),
        (30, 160, 255),
    ]

    def lerp(a, b, t):
        """Linear interpolation."""
        return int(a + (b - a) * t)

    def get_gradient_color(t, colors=COLORS):
        """Get interpolated color at position t (0-1)."""
        segment = t * (len(colors) - 1)
        idx = min(int(segment), len(colors) - 2)
        local_t = segment - idx
        c1, c2 = colors[idx], colors[idx + 1]
        return [lerp(c1[i], c2[i], local_t) for i in range(3)]

    def apply_gradient(text, row, total_rows, dim_decorations=True):
        """Apply gradient with character-based styling."""
        chars = list(text)
        non_space = [(i, c) for i, c in enumerate(chars) if c.strip()]
        if not non_space:
            return text

        start_pos, end_pos = non_space[0][0], non_space[-1][0]
        span = max(end_pos - start_pos, 1)
        row_t = row / max(total_rows - 1, 1)
        shadow_mult = 0.7 - row_t * 0.35

        result = []
        for i, char in enumerate(chars):
            if not char.strip():
                result.append(char)
                continue

            t = (i - start_pos) / span
            rgb = get_gradient_color(t)

            if char in CHAR_TYPES['shadow']:
                rgb = [int(c * shadow_mult) for c in rgb]
            elif (
                dim_decorations
                and char not in CHAR_TYPES['letter']
                and char not in CHAR_TYPES['version']
            ):
                if char in CHAR_TYPES['accent']:
                    rgb = [230, lerp(63, 108, row_t), lerp(45, 81, row_t)]
                else:
                    rgb = [lerp(70, 35, row_t), lerp(150, 80, row_t), lerp(90, 45, row_t)]

            result.append(f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{char}")

        return ''.join(result)

    lines = [
        "⇡⇡ ∘⌬ ╭──┬──▓████▓╗──◀▓██▓╗ ████████╗→─┬─▓████▓╗──/┬─╮ ⎔ /  +",
        "∙   ˊˊ│╭─■▓█╔═════╝─○──█▓╔╝ █╔═█▓╔═█║─■▓█╔═════██╗/◉╮╰-◉/ ⧉ ∙",
        "◀--┬=⟨⟨⟨⟨▓██║ ˊ <▓██▓╗ █▓║─-╚╝ █▓║ ╚╝<▓█╔╝ ▓▓╗  █▓⟩⟩⟩⟩─┬▣ --▶",
        " ∙┬┘ -─┼◉╚▓█║∘{⊕}∘█▓╔╝ █▓║─//╮ █▓║──○─╚▓█╗ ╚═╝ █▓╔╝◉┤╭─┼▣ ∙",
        "◉-╯∙∙⟁ ╰◉-╚═▓█████▓╔╝ ▓██▓╗  ╰─█▓║─→─╮∘╚═▓████▓╔═╝──╯└-∙",
        "╭──⊕─⊕──{⊕}─╚══════╝──╚═══╝─┴──╚═╝∘∘ ╰⟁ +╚═════╝ ∘∘⌬ "+f"⟦v{version()}⟧",
    ]

    total = len(lines)
    gradient_lines = [apply_gradient(line, i, total) for i, line in enumerate(lines)]
    tagline = apply_gradient("AI Code Reviewer", total // 2, total, dim_decorations=False)

    logo_text = '\n'.join(gradient_lines + [' ' * 22 + tagline + r])
    return textwrap.indent(logo_text, ' ' * indent)
