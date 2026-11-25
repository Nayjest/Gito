"""Discovery of project guidance that applies to a reviewed file."""

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
import re


DEFAULT_INSTRUCTION_PATTERNS = [
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CODE_GUIDELINES.md",
    "CODE_STYLE.md",
    "CONTRIBUTING.md",
    "DEVELOPMENT.md",
    "SECURITY.md",
    ".github/copilot-instructions.md",
    ".github/instructions/**/*.md",
    ".github/instructions/**/*.instructions.md",
]

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class ProjectInstruction:
    path: str
    content: str
    applies_to: tuple[str, ...] = ()
    priority: int = 0


def _paths_for_pattern(root: Path, pattern: str) -> list[Path]:
    if any(char in pattern for char in "*?["):
        return sorted(root.glob(pattern))
    path = root / pattern
    return [path] if path.exists() else []


def _parse_instruction(text: str) -> tuple[tuple[str, ...], str]:
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        return (), text

    metadata, content = match.groups()
    for line in metadata.splitlines():
        key, separator, value = line.partition(":")
        if key.strip() != "applyTo" or not separator:
            continue
        patterns = tuple(item.strip().strip('"\'') for item in value.split(",") if item.strip())
        return patterns, content
    return (), content


def _matches_path(changed_file: str, patterns: tuple[str, ...]) -> bool:
    if not patterns:
        return True
    normalized = changed_file.replace("\\", "/").lstrip("./")
    for pattern in patterns:
        normalized_pattern = pattern.lstrip("./")
        if fnmatch(normalized, normalized_pattern):
            return True
        # GitHub's **/ convention also includes files at the repository root.
        if normalized_pattern.startswith("**/") and fnmatch(
            normalized, normalized_pattern[3:]
        ):
            return True
    return False


def _truncate_tokens(content: str, remaining_tokens: int) -> str:
    if remaining_tokens <= 0:
        return ""
    words = content.split()
    if len(words) <= remaining_tokens:
        return content
    return " ".join(words[:remaining_tokens])


def discover_project_instructions(
    repo_root: str | Path,
    changed_file: str,
    patterns: list[str] | None = None,
    max_tokens: int | None = None,
) -> list[ProjectInstruction]:
    """Return guidance files applicable to ``changed_file``.

    Files named ``AGENTS.md`` are discovered from the repository root down to the changed
    file's directory. Other files are discovered from the configured glob patterns. Missing
    optional files are ignored. ``max_tokens`` is a conservative whitespace-token budget.
    """
    root = Path(repo_root).resolve()
    changed_path = Path(changed_file)
    changed_relative = changed_path.as_posix().lstrip("./")
    configured_patterns = patterns or DEFAULT_INSTRUCTION_PATTERNS

    candidates: list[Path] = []
    for parent in [root, *((root / changed_path).parents)]:
        if parent == root or root in parent.parents:
            candidate = parent / "AGENTS.md"
            if candidate.exists():
                candidates.append(candidate)
    for pattern in configured_patterns:
        candidates.extend(_paths_for_pattern(root, pattern))

    unique_candidates = []
    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        unique_candidates.append(candidate)

    # Parent AGENTS files are ordered broadly to narrowly; configured documents follow.
    unique_candidates.sort(
        key=lambda path: (
            0 if path.name == "AGENTS.md" and path.parent != root / ".github" else 1,
            len(path.relative_to(root).parts),
            path.relative_to(root).as_posix(),
        )
    )

    result: list[ProjectInstruction] = []
    remaining = max_tokens
    for candidate in unique_candidates:
        relative = candidate.relative_to(root).as_posix()
        applies_to, content = _parse_instruction(candidate.read_text(encoding="utf-8-sig"))
        if not _matches_path(changed_relative, applies_to):
            continue
        if remaining is not None:
            content = _truncate_tokens(content, remaining)
            remaining -= len(content.split())
            if not content:
                continue
        result.append(
            ProjectInstruction(
                path=relative,
                content=content,
                applies_to=applies_to,
                priority=len(candidate.relative_to(root).parts),
            )
        )
    return result


def format_project_instructions(instructions: list[ProjectInstruction]) -> str:
    """Format discovered guidance for inclusion in a review prompt."""
    return "\n\n".join(
        f"--- PROJECT INSTRUCTIONS: {instruction.path} ---\n{instruction.content}"
        for instruction in instructions
    )
