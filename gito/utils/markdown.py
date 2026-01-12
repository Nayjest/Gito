"""
Utilities for generating Markdown.
"""
from pathlib import Path


_EXT_TO_HINT: dict[str, str] = {
    # scripting & languages
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".dart": "dart",
    ".php": "php",
    ".pl": "perl",
    ".pm": "perl",
    ".lua": "lua",
    # web & markup
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".json": "json",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".csv": "csv",
    ".md": "markdown",
    ".rst": "rest",
    # shell & config
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".fish": "bash",
    ".ps1": "powershell",
    ".dockerfile": "dockerfile",
    # build & CI
    ".makefile": "makefile",
    ".mk": "makefile",
    "CMakeLists.txt": "cmake",
    "Dockerfile": "dockerfile",
    ".gradle": "groovy",
    ".travis.yml": "yaml",
    # data & queries
    ".sql": "sql",
    ".graphql": "graphql",
    ".proto": "protobuf",
    ".yara": "yara",
}


def syntax_hint(file_path: str | Path) -> str:
    """
    Return a syntax highlighting hint for markdown code snippet
    based on the file's extension or name.

    This can be used to annotate code blocks for rendering with syntax highlighting,
    e.g., using Markdown-style code blocks: ```<syntax_hint>\n<code>\n```.

    Args:
      file_path (str | Path): Path to the file.

    Returns:
      str: A syntax identifier suitable for code highlighting (e.g., 'python', 'json').
    """
    p = Path(file_path)
    name = p.name
    # Check full filename first (e.g., Dockerfile, CMakeLists.txt)
    if name in _EXT_TO_HINT:
        return _EXT_TO_HINT[name]
    ext = p.suffix.lower()
    if not ext:
        return ""
    return _EXT_TO_HINT.get(ext, ext.lstrip("."))
