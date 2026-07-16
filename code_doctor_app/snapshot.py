"""Local (non-git) project analysis via managed git snapshots.

Code Doctor's whole analysis stack (gito, static analysis, cross-file) is
diff-based and assumes a git work tree. To review a plain local folder that
isn't under git, we materialize a throwaway git repository: the folder's
files are copied into a Code Doctor-managed snapshot under the data dir,
`git init` + one baseline commit, and the review runs against the git
empty-tree diff (every file reads as "added", i.e. a whole-codebase review).

The user's own folder is never modified and never gets a `.git`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

# Git's canonical empty-tree object. Diffing HEAD against it yields every
# tracked line as an addition, which is exactly a whole-codebase review and
# needs no remote or base branch (unlike `gito review --all`).
EMPTY_TREE_HASH = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# The refs range a snapshot review targets: empty tree ..= HEAD.
SNAPSHOT_REFS = f"HEAD..{EMPTY_TREE_HASH}"

# Directories never worth copying into a snapshot: version control, virtual
# envs, dependency trees, build output, tooling caches.
SNAPSHOT_IGNORE_DIRS = frozenset(
    {
        ".git", ".hg", ".svn",
        ".venv", "venv", "env", "__pycache__",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
        "node_modules", "bower_components",
        "dist", "build", "out", ".next", ".nuxt", ".svelte-kit",
        ".idea", ".vscode", ".gradle", ".terraform", "target",
        ".code-doctor",  # never snapshot our own data dir
    }
)

# Guardrails so a mistaken path (e.g. a home directory) can't trigger a
# runaway copy.
MAX_SNAPSHOT_FILES = 20_000
MAX_SNAPSHOT_BYTES = 300 * 1024 * 1024  # 300 MB


class SnapshotTooLargeError(ValueError):
    """The source folder exceeds the snapshot size guardrails."""


def is_git_work_tree(path: Path) -> bool:
    """True when ``path`` is inside a git work tree."""
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def snapshot_id(source: Path) -> str:
    """Stable per-source id, so re-reviews reuse (and refresh) one snapshot."""
    return uuid.uuid5(uuid.NAMESPACE_URL, "code-doctor-snapshot:" + str(source)).hex[:12]


def _ignored(_dirpath: str, names: list[str]) -> set[str]:
    """``shutil.copytree`` ignore callback: drop the excluded directories."""
    return {name for name in names if name in SNAPSHOT_IGNORE_DIRS}


def measure(source: Path) -> tuple[int, int]:
    """Count files and bytes that would be snapshotted (ignored dirs skipped).

    Stops early once a guardrail is exceeded — the exact totals past the
    limit don't matter, only that it is over.
    """
    files = 0
    total = 0
    for root, dirs, filenames in os.walk(source):
        dirs[:] = [d for d in dirs if d not in SNAPSHOT_IGNORE_DIRS]
        for name in filenames:
            candidate = Path(root) / name
            if candidate.is_symlink():
                continue
            files += 1
            try:
                total += candidate.stat().st_size
            except OSError:
                pass
            if files > MAX_SNAPSHOT_FILES or total > MAX_SNAPSHOT_BYTES:
                return files, total
    return files, total


def list_files(source: Path, limit: int = MAX_SNAPSHOT_FILES) -> list[str]:
    """Repo-relative paths that would be snapshotted (ignored dirs skipped).

    Used to preview scope without paying for a full snapshot copy.
    """
    source = source.resolve()
    out: list[str] = []
    for root, dirs, filenames in os.walk(source):
        dirs[:] = [d for d in dirs if d not in SNAPSHOT_IGNORE_DIRS]
        for name in sorted(filenames):
            candidate = Path(root) / name
            if candidate.is_symlink():
                continue
            out.append(str(candidate.relative_to(source)))
            if len(out) >= limit:
                return out
    return out


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def build_snapshot(source: Path, snapshots_dir: Path) -> Path:
    """Copy ``source`` into a managed snapshot and commit it as a git baseline.

    Returns the snapshot's work-tree path — a real git repo the existing
    review pipeline can operate on unchanged. An existing snapshot for the
    same source is refreshed in place so re-reviews see current content.
    """
    source = source.resolve()
    if not source.is_dir():
        raise ValueError("Repository path does not exist or is not a directory.")

    files, total = measure(source)
    if files == 0:
        raise ValueError("The folder has no files to analyze.")
    if files > MAX_SNAPSHOT_FILES or total > MAX_SNAPSHOT_BYTES:
        raise SnapshotTooLargeError(
            f"Folder is too large to snapshot (over {MAX_SNAPSHOT_FILES} files or "
            f"{MAX_SNAPSHOT_BYTES // (1024 * 1024)} MB). Point Code Doctor at a git "
            "repository or a smaller folder."
        )

    root = snapshots_dir / snapshot_id(source)
    tree = root / "tree"
    if tree.exists():
        shutil.rmtree(tree)
    root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, tree, ignore=_ignored, symlinks=True)

    _git(tree, "init", "-q", "-b", "main")
    _git(tree, "add", "-A")
    # Identity is set per-invocation so the baseline commit never depends on
    # (or writes to) the machine's global git config.
    _git(
        tree,
        "-c", "user.email=snapshot@code-doctor.local",
        "-c", "user.name=Code Doctor",
        "commit", "-q", "--allow-empty", "-m", "Code Doctor snapshot baseline",
    )
    (root / "source.txt").write_text(str(source) + "\n", encoding="utf-8")
    return tree
