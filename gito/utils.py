import re
import sys
import os
from pathlib import Path
import importlib.metadata
from typing import Optional

import typer
import git
from git import Repo


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
    Returns a syntax highlighting hint based on the file's extension or name.

    This can be used to annotate code blocks for rendering with syntax highlighting,
    e.g., using Markdown-style code blocks: ```<syntax_hint>\n<code>\n```.

    Args:
      file_path (str | Path): Path to the file.

    Returns:
      str: A syntax identifier suitable for code highlighting (e.g., 'python', 'json').
    """
    p = Path(file_path)
    ext = p.suffix.lower()
    if not ext:
        name = p.name.lower()
        if name == "dockerfile":
            return "dockerfile"
        return ""
    return _EXT_TO_HINT.get(ext, ext.lstrip("."))


def is_running_in_github_action():
    return os.getenv("GITHUB_ACTIONS") == "true"


def no_subcommand(app: typer.Typer) -> bool:
    """
    Checks if the current script is being invoked as a command in a target Typer application.
    """
    return not (
        (first_arg := next((a for a in sys.argv[1:] if not a.startswith('-')), None))
        and first_arg in (
            cmd.name or cmd.callback.__name__.replace('_', '-')
            for cmd in app.registered_commands
        )
        or '--help' in sys.argv
    )


def parse_refs_pair(refs: str) -> tuple[str | None, str | None]:
    SEPARATOR = '..'
    if not refs:
        return None, None
    if SEPARATOR not in refs:
        return refs, None
    what, against = refs.split(SEPARATOR, 1)
    return what or None, against or None


def max_line_len(text: str) -> int:
    return max((len(line) for line in text.splitlines()), default=0)


def block_wrap_lr(
    text: str,
    left: str = "",
    right: str = "",
    max_rwrap: int = 60,
    min_wrap: int = 0,
) -> str:
    ml = max(max_line_len(text), min_wrap)
    lines = text.splitlines()
    wrapped_lines = []
    for line in lines:
        ln = left+line
        if ml <= max_rwrap:
            ln += ' ' * (ml - len(line)) + right
        wrapped_lines.append(ln)
    return "\n".join(wrapped_lines)


def extract_gh_owner_repo(repo: git.Repo) -> tuple[str, str]:
    """
    Extracts the GitHub owner and repository name.

    Returns:
        tuple[str, str]: A tuple containing the owner and repository name.
    """
    remote_url = repo.remotes.origin.url
    if remote_url.startswith('git@github.com:'):
        # SSH format: git@github.com:owner/repo.git
        repo_path = remote_url.split(':')[1].replace('.git', '')
    elif remote_url.startswith('https://github.com/'):
        # HTTPS format: https://github.com/owner/repo.git
        repo_path = remote_url.replace('https://github.com/', '').replace('.git', '')
    else:
        raise ValueError("Unsupported remote URL format")
    owner, repo_name = repo_path.split('/')
    return owner, repo_name


def detect_github_env() -> dict:
    """
    Try to detect GitHub repository/PR info from environment variables (for GitHub Actions).
    Returns a dict with github_repo, github_pr_sha, github_pr_number, github_ref, etc.
    """
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    pr_sha = os.environ.get("GITHUB_SHA", "")
    pr_number = os.environ.get("GITHUB_REF", "")
    branch = ""
    ref = os.environ.get("GITHUB_REF", "")
    # Try to resolve PR head SHA if available.
    # On PRs, GITHUB_HEAD_REF/BASE_REF contain branch names.
    if "GITHUB_HEAD_REF" in os.environ:
        branch = os.environ["GITHUB_HEAD_REF"]
    elif ref.startswith("refs/heads/"):
        branch = ref[len("refs/heads/"):]
    elif ref.startswith("refs/pull/"):
        # for pull_request events
        branch = ref

    d = {
        "github_repo": repo,
        "github_pr_sha": pr_sha,
        "github_pr_number": pr_number,
        "github_branch": branch,
        "github_ref": ref,
    }
    # Fallback for local usage: try to get from git
    if not repo:
        git_repo = None
        try:
            git_repo = Repo(".", search_parent_directories=True)
            origin = git_repo.remotes.origin.url
            # e.g. git@github.com:Nayjest/ai-code-review.git -> Nayjest/ai-code-review
            match = re.search(r"[:/]([\w\-]+)/([\w\-\.]+?)(\.git)?$", origin)
            if match:
                d["github_repo"] = f"{match.group(1)}/{match.group(2)}"
            d["github_pr_sha"] = git_repo.head.commit.hexsha
            d["github_branch"] = (
                git_repo.active_branch.name if hasattr(git_repo, "active_branch") else ""
            )
        except Exception:
            pass
        finally:
            if git_repo:
                try:
                    git_repo.close()
                except Exception:
                    pass
    # If branch is not a commit SHA, prefer branch for links
    if d["github_branch"]:
        d["github_pr_sha_or_branch"] = d["github_branch"]
    elif d["github_pr_sha"]:
        d["github_pr_sha_or_branch"] = d["github_pr_sha"]
    else:
        d["github_pr_sha_or_branch"] = "main"
    return d


def make_streaming_function(handler: Optional[callable] = None) -> callable:
    def stream(text):
        if handler:
            text = handler(text)
        print(text, end='', flush=True)
    return stream


def version() -> str:
    return importlib.metadata.version("gito.bot")


def remove_html_comments(text):
    """
    Removes all HTML comments (<!-- ... -->) from the input text.
    """
    return re.sub(r'<!--.*?-->\s*', '', text, flags=re.DOTALL)
