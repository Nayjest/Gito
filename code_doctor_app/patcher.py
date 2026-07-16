"""Auto-fix apply + write-tests-into-repo (release plan Item 3).

This is the first module that writes into user repositories, so every write
is guarded four ways: the finding's recorded ``affected_code`` must still
match the file exactly (drifted files are refused, never guessed at), the
original is backed up under the run directory before any write, targets are
resolved strictly inside the repo root with symlinks refused, and every
action is an explicit per-fix user request — there is no bulk apply.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

LINE_PREFIX_RE = re.compile(r"^\s*\d+:\s?")


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def strip_line_prefixes(code: str) -> list[str]:
    """Drop the ``NN: `` prefixes gito puts on affected_code lines."""
    return [LINE_PREFIX_RE.sub("", line) for line in code.splitlines()]


def resolve_repo_file(repo_path: Path, relative: str) -> Path:
    """Resolve ``relative`` strictly inside the repo; refuse symlinked targets."""
    candidate = Path(str(relative).strip())
    if candidate.is_absolute() or not candidate.parts or any(
        part in ("..", "") for part in candidate.parts
    ):
        raise ValueError(f"Unsafe file path: {relative!r}")
    root = repo_path.resolve()
    target = root / candidate
    resolved = target.resolve()
    if root != resolved and root not in resolved.parents:
        raise ValueError(f"Unsafe file path: {relative!r}")
    # A symlink anywhere between the root and the target can redirect the
    # write outside the repo even when resolve() lands inside it.
    probe = target
    while probe != root:
        if probe.is_symlink():
            raise ValueError(f"Refusing symlinked path: {relative!r}")
        probe = probe.parent
    return resolved


def _first_block(issue: dict[str, Any]) -> dict[str, Any] | None:
    blocks = issue.get("affected_lines") or []
    if blocks and isinstance(blocks[0], dict):
        return blocks[0]
    return None


def plan_fix(repo_path: Path, issue: dict[str, Any]) -> dict[str, Any]:
    """Dry plan for applying a finding's proposal. Never writes.

    ``applicable`` is True only when the recorded affected lines still match
    the file content exactly — a drifted file is refused, never guessed.
    """
    file = str(issue.get("file") or "").strip()
    block = _first_block(issue)
    base = {"file": file, "applicable": False}
    if not file:
        return base | {"reason": "finding has no file"}
    if not block:
        return base | {"reason": "finding has no affected-lines block"}
    proposal = str(block.get("proposal") or "").strip("\n")
    if not proposal.strip():
        return base | {"reason": "finding has no proposed fix"}
    start = block.get("start_line")
    end = block.get("end_line") or start
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        return base | {"reason": "finding has no usable line range"}
    try:
        target = resolve_repo_file(repo_path, file)
    except ValueError as exc:
        return base | {"reason": str(exc)}
    if not target.is_file():
        return base | {"reason": "file no longer exists"}

    content = target.read_text(encoding="utf-8", errors="ignore")
    lines = content.splitlines()
    if end > len(lines):
        return base | {"reason": "file changed since review (fewer lines than the finding)"}
    current = lines[start - 1 : end]
    expected = strip_line_prefixes(str(block.get("affected_code") or ""))
    # Compare ignoring trailing whitespace; the reviewed snippet must still
    # be exactly what is in the file.
    if [line.rstrip() for line in expected] != [line.rstrip() for line in current]:
        return base | {"reason": "file changed since review"}

    return {
        "file": file,
        "start_line": start,
        "end_line": end,
        "before": current,
        "after": proposal.splitlines(),
        "applicable": True,
        "reason": "",
    }


def _backup_dir(run_dir: Path, issue_id: Any) -> Path:
    return run_dir / "backups" / str(issue_id)


def apply_fix(
    repo_path: Path, run_dir: Path, issue: dict[str, Any]
) -> dict[str, Any]:
    """Apply a planned fix: re-validate (TOCTOU guard), back up, then write
    atomically. Returns the plan plus the backup's run-relative path."""
    plan = plan_fix(repo_path, issue)
    if not plan.get("applicable"):
        raise ValueError(f"Fix is not applicable: {plan.get('reason')}")
    target = resolve_repo_file(repo_path, plan["file"])
    content = target.read_text(encoding="utf-8", errors="ignore")
    ends_with_newline = content.endswith("\n")
    lines = content.splitlines()

    backup_root = _backup_dir(run_dir, issue.get("id"))
    backup_file = backup_root / plan["file"]
    backup_file.parent.mkdir(parents=True, exist_ok=True)
    backup_file.write_text(content, encoding="utf-8")

    new_lines = lines[: plan["start_line"] - 1] + plan["after"] + lines[plan["end_line"] :]
    new_content = "\n".join(new_lines) + ("\n" if ends_with_newline else "")
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".code-doctor-fix-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(new_content)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    backup_rel = str(backup_file.relative_to(run_dir))
    return plan | {"backup": backup_rel}


def revert_fix(repo_path: Path, run_dir: Path, issue: dict[str, Any], ledger_entry: dict[str, Any]) -> str:
    """Restore the backed-up original for an applied fix."""
    backup_rel = str(ledger_entry.get("backup") or "")
    backup_file = (run_dir / backup_rel).resolve()
    if run_dir.resolve() not in backup_file.parents or not backup_file.is_file():
        raise ValueError("No backup found for this fix.")
    file = str(issue.get("file") or "")
    target = resolve_repo_file(repo_path, file)
    content = backup_file.read_text(encoding="utf-8")
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".code-doctor-revert-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return file


def load_ledger(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "fixes.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_ledger(run_dir: Path, ledger: dict[str, Any]) -> None:
    (run_dir / "fixes.json").write_text(json.dumps(ledger, indent=2), encoding="utf-8")


def write_generated_tests(
    repo_path: Path, run_dir: Path, overwrite: bool = False
) -> list[dict[str, Any]]:
    """Write this run's generated test files into the repo.

    Refuses to overwrite existing files unless ``overwrite`` is set; when
    overwriting, the original is backed up like a fix."""
    artifact_path = run_dir / "generated-tests.json"
    if not artifact_path.exists():
        raise ValueError("This run has no generated tests. Generate them first.")
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    files = artifact.get("files") or []
    if not files:
        raise ValueError("The generated-tests artifact contains no files.")

    written: list[dict[str, Any]] = []
    skipped: list[str] = []
    for item in files:
        rel = str(item.get("path") or "")
        target = resolve_repo_file(repo_path, rel)
        existed = target.exists()
        if existed and not overwrite:
            skipped.append(rel)
            continue
        if existed:
            backup = _backup_dir(run_dir, "generated-tests") / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(
                target.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(item.get("content") or ""), encoding="utf-8")
        written.append({"path": rel, "overwrote": existed})
    if not written and skipped:
        raise ValueError(
            f"All {len(skipped)} file(s) already exist. Pass overwrite to replace them."
        )
    return written
