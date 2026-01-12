"""
Utilities for processing HTML.
"""
import re


def remove_html_comments(text):
    """
    Removes all HTML comments (<!-- ... -->) from the input text.
    """
    return re.sub(r'<!--.*?-->\s*', '', text, flags=re.DOTALL)
