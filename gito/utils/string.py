"""
String utility functions.
"""


def max_line_len(text: str) -> int:
    """
    Return the length of the longest line in the given text.
    Args:
        text (str): The input text.
    Returns:
        int: The length of the longest line.
    """
    return max((len(line) for line in text.splitlines()), default=0)


def block_wrap_lr(
    text: str,
    left: str = "",
    right: str = "",
    max_rwrap: int = 60,
    min_wrap: int = 0,
) -> str:
    """
    Wrap the given text block with specified left and right strings.
    If the maximum line length of the text is less than or equal to max_rwrap,
    the right string is added to the end of each line, padded with spaces
    to align with the longest line.
    Args:
        text (str): The input text block.
        left (str): The string to prepend to each line.
        right (str): The string to append to each line if wrapping conditions are met.
        max_rwrap (int): The maximum line length to apply right wrapping.
        min_wrap (int): The minimum line length to consider for wrapping.
    Returns:
        str: The wrapped text block.
    """
    ml = max(max_line_len(text), min_wrap)
    lines = text.splitlines()
    wrapped_lines = []
    for line in lines:
        ln = left + line
        if ml <= max_rwrap:
            ln += ' ' * (ml - len(line)) + right
        wrapped_lines.append(ln)
    return "\n".join(wrapped_lines)


def parse_refs_pair(refs: str) -> tuple[str | None, str | None]:
    SEPARATOR = '..'
    if not refs:
        return None, None
    if SEPARATOR not in refs:
        return refs, None
    what, against = refs.split(SEPARATOR, 1)
    return what or None, against or None
