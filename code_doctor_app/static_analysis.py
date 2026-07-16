"""Deterministic static analysis for Code Doctor.

Scans the added lines of a git diff with a high-precision rule pack so every
review run gets instant, reproducible findings alongside the LLM review —
and still produces evidence when the LLM run fails. Findings use the same
issue schema as Gito reports and are merged into the run report.
"""
from __future__ import annotations

import math
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Callable, Iterator

STATIC_ISSUE_ID_BASE = 10000
MAX_FINDINGS_PER_RULE_PER_FILE = 5
MAX_TOTAL_FINDINGS = 200
MAX_LINE_LENGTH = 2000  # skip minified / generated lines
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

EXCLUDED_FILES = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "Pipfile.lock",
    "*.min.js",
    "*.min.css",
    "*.map",
    "dist/*",
    "build/*",
    "coverage/*",
    "vendor/*",
    "node_modules/*",
)

PLACEHOLDER_MARKERS = (
    "example",
    "placeholder",
    "changeme",
    "change-me",
    "change_me",
    "your-",
    "your_",
    "dummy",
    "sample",
    "replace",
    "redacted",
    "fake",
    "todo",
    "xxx",
    "<",
    "...",
)

PY_FILES = ("*.py",)
JS_FILES = ("*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs", "*.cjs")


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    total = len(value)
    return -sum((n / total) * math.log2(n / total) for n in counts.values())


def looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return value[:2] + "****"
    return value[:4] + "…" + "*" * 8


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    details: str
    severity: int
    confidence: int
    tags: tuple[str, ...]
    pattern: re.Pattern
    file_patterns: tuple[str, ...] = ("*",)
    is_secret: bool = False
    # Optional extra check on the regex match; return False to suppress.
    validator: Callable[[re.Match, str], bool] | None = None


def _entropy_secret(min_entropy: float, group: int = 1) -> Callable[[re.Match, str], bool]:
    def check(match: re.Match, _line: str) -> bool:
        value = match.group(group)
        if looks_like_placeholder(value):
            return False
        return shannon_entropy(value) >= min_entropy

    return check


def _not_placeholder(group: int = 0) -> Callable[[re.Match, str], bool]:
    def check(match: re.Match, _line: str) -> bool:
        return not looks_like_placeholder(match.group(group))

    return check


RULES: tuple[Rule, ...] = (
    # ── Secrets ──────────────────────────────────────────────────────────
    Rule(
        id="private-key-material",
        title="Private key material committed to the repository.",
        details=(
            "A PEM private key block was added. Keys in git history stay exposed even "
            "after removal; rotate the key and load it from a secret manager instead."
        ),
        severity=1,
        confidence=1,
        tags=("security", "secret-handling"),
        pattern=re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        is_secret=True,
    ),
    Rule(
        id="aws-access-key",
        title="AWS access key ID committed to the repository.",
        details=(
            "This matches the AWS access key ID format (AKIA…). Treat it as compromised: "
            "revoke it in IAM and switch to environment-injected credentials."
        ),
        severity=1,
        confidence=1,
        tags=("security", "secret-handling"),
        pattern=re.compile(r"\b(AKIA[0-9A-Z]{16})\b"),
        is_secret=True,
        validator=_not_placeholder(1),
    ),
    Rule(
        id="github-token",
        title="GitHub token committed to the repository.",
        details=(
            "This matches GitHub's token format (ghp_/gho_/ghu_/ghs_/ghr_). Revoke it and "
            "use a scoped secret from the CI/CD secret store."
        ),
        severity=1,
        confidence=1,
        tags=("security", "secret-handling"),
        pattern=re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,})\b"),
        is_secret=True,
    ),
    Rule(
        id="slack-token",
        title="Slack token committed to the repository.",
        details="This matches Slack's token format (xox…). Revoke it and move it to a secret store.",
        severity=1,
        confidence=1,
        tags=("security", "secret-handling"),
        pattern=re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"),
        is_secret=True,
    ),
    Rule(
        id="stripe-secret-key",
        title="Stripe live secret key committed to the repository.",
        details="Live sk_/rk_ keys allow charges and refunds. Revoke immediately and rotate.",
        severity=1,
        confidence=1,
        tags=("security", "secret-handling"),
        pattern=re.compile(r"\b((?:sk|rk)_live_[0-9a-zA-Z]{8,})\b"),
        is_secret=True,
    ),
    Rule(
        id="stripe-publishable-live-key",
        title="Production-looking Stripe publishable key committed.",
        details=(
            "pk_live_ keys are publishable but still identify the production account and "
            "do not belong in the repo or templates; use an obvious placeholder."
        ),
        severity=2,
        confidence=2,
        tags=("security", "secret-handling"),
        pattern=re.compile(r"\b(pk_live_[0-9a-zA-Z]{8,})\b"),
        is_secret=True,
        validator=_not_placeholder(1),
    ),
    Rule(
        id="hardcoded-credential",
        title="Hardcoded credential-looking value assigned in code.",
        details=(
            "A key/secret/token/password variable is assigned a high-entropy literal. "
            "Load it from the environment or a secret manager and keep literals out of git."
        ),
        severity=2,
        confidence=2,
        tags=("security", "secret-handling"),
        pattern=re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|secret|token|passwd|password|auth[_-]?key)\b"
            r"\s*[:=]\s*[\"']([^\"']{12,})[\"']"
        ),
        is_secret=True,
        validator=_entropy_secret(3.0),
    ),
    Rule(
        id="env-credential",
        title="Credential-looking value added to an environment file.",
        details=(
            "Environment files are frequently committed and shared; this value looks like "
            "a real credential. Replace it with an obvious placeholder."
        ),
        severity=2,
        confidence=2,
        tags=("security", "secret-handling"),
        pattern=re.compile(
            r"^[A-Z0-9_]*(?:KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*\s*=\s*(\S{8,})\s*$"
        ),
        file_patterns=("*.env*",),
        is_secret=True,
        validator=_entropy_secret(3.0),
    ),
    # ── Correctness / danger ─────────────────────────────────────────────
    Rule(
        id="merge-conflict-marker",
        title="Unresolved merge conflict marker committed.",
        details="Conflict markers break parsing/compilation. Resolve the conflict and recommit.",
        severity=1,
        confidence=1,
        tags=("bug",),
        pattern=re.compile(r"^(?:<{7}|>{7}) "),
    ),
    Rule(
        id="python-eval-exec",
        title="Use of eval/exec on dynamic input.",
        details=(
            "eval/exec runs arbitrary code and is a common injection vector. Prefer "
            "ast.literal_eval, a dispatch dict, or explicit parsing."
        ),
        severity=2,
        confidence=2,
        tags=("security",),
        pattern=re.compile(r"(?<![\w.])(?:eval|exec)\s*\("),
        file_patterns=PY_FILES,
    ),
    Rule(
        id="pickle-load",
        title="Unpickling data can execute arbitrary code.",
        details=(
            "pickle.load(s) on untrusted bytes is remote code execution. Use JSON or a "
            "schema-validated format for anything that crosses a trust boundary."
        ),
        severity=2,
        confidence=1,
        tags=("security",),
        pattern=re.compile(r"\bpickle\.loads?\s*\("),
        file_patterns=PY_FILES,
    ),
    Rule(
        id="yaml-unsafe-load",
        title="yaml.load without an explicit safe Loader.",
        details="yaml.load can instantiate arbitrary objects. Use yaml.safe_load or Loader=SafeLoader.",
        severity=2,
        confidence=1,
        tags=("security",),
        pattern=re.compile(r"\byaml\.load\s*\((?![^)]*Loader)"),
        file_patterns=PY_FILES,
    ),
    Rule(
        id="subprocess-shell-true",
        title="subprocess call with shell=True.",
        details=(
            "shell=True routes the command through the shell, enabling injection when any "
            "part of the string is dynamic. Pass an argument list instead."
        ),
        severity=2,
        confidence=1,
        tags=("security",),
        pattern=re.compile(r"\bshell\s*=\s*True\b"),
        file_patterns=PY_FILES,
    ),
    Rule(
        id="bare-except",
        title="Bare except swallows every error, including exit signals.",
        details=(
            "except: catches SystemExit/KeyboardInterrupt and hides real failures. Catch "
            "the specific exception, or at minimum `except Exception`."
        ),
        severity=3,
        confidence=1,
        tags=("bug", "maintainability"),
        pattern=re.compile(r"^\s*except\s*:\s*(?:#.*)?$"),
        file_patterns=PY_FILES,
    ),
    Rule(
        id="mutable-default-arg",
        title="Mutable default argument is shared across calls.",
        details=(
            "Default [], {} or set() is created once and mutated by every call. Default to "
            "None and create the object inside the function."
        ),
        severity=3,
        confidence=2,
        tags=("bug",),
        pattern=re.compile(r"\bdef\s+\w+\s*\([^)]*=\s*(?:\[\]|\{\}|set\(\))"),
        file_patterns=PY_FILES,
    ),
    Rule(
        id="python-debug-leftover",
        title="Debugger breakpoint left in the code.",
        details="breakpoint()/pdb.set_trace() will hang the process in production. Remove before merging.",
        severity=2,
        confidence=1,
        tags=("bug", "maintainability"),
        pattern=re.compile(r"\b(?:breakpoint\s*\(\s*\)|pdb\.set_trace\s*\(\s*\))"),
        file_patterns=PY_FILES,
    ),
    Rule(
        id="js-debugger-statement",
        title="debugger statement left in the code.",
        details="A debugger statement pauses execution when devtools are open. Remove before merging.",
        severity=2,
        confidence=1,
        tags=("bug", "maintainability"),
        pattern=re.compile(r"^\s*debugger\s*;?\s*$"),
        file_patterns=JS_FILES,
    ),
    Rule(
        id="js-eval",
        title="Use of eval on dynamic input.",
        details="eval executes arbitrary strings as code. Use JSON.parse or explicit logic instead.",
        severity=2,
        confidence=2,
        tags=("security",),
        pattern=re.compile(r"(?<![\w.])eval\s*\("),
        file_patterns=JS_FILES,
    ),
    Rule(
        id="js-xss-sink",
        title="Raw HTML sink can introduce XSS.",
        details=(
            "innerHTML/document.write/dangerouslySetInnerHTML render unescaped markup. "
            "Use textContent or a sanitizer for anything derived from user input."
        ),
        severity=2,
        confidence=2,
        tags=("security",),
        pattern=re.compile(r"(?:\.innerHTML\s*=|document\.write\s*\(|dangerouslySetInnerHTML)"),
        file_patterns=JS_FILES,
    ),
    Rule(
        id="js-console-debug",
        title="console.log/debug left in the change.",
        details="Leftover console output leaks internals and adds noise. Use the project logger or remove it.",
        severity=4,
        confidence=2,
        tags=("maintainability",),
        pattern=re.compile(r"\bconsole\.(?:log|debug)\s*\("),
        file_patterns=JS_FILES,
    ),
    Rule(
        id="fixme-hack-comment",
        title="FIXME/HACK marker added.",
        details="A known problem is being merged. Link a ticket or fix it before it hardens into the codebase.",
        severity=5,
        confidence=2,
        tags=("maintainability",),
        pattern=re.compile(r"\b(?:FIXME|HACK)\b[:\s]"),
    ),
)

_HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def iter_added_lines(diff_text: str) -> Iterator[tuple[str, int, str]]:
    """Yield (file_path, new_line_number, line_text) for every added line."""
    current_file: str | None = None
    new_line = 0
    old_remaining = 0
    new_remaining = 0
    for raw in diff_text.splitlines():
        if old_remaining <= 0 and new_remaining <= 0:
            if raw.startswith("+++ "):
                target = raw[4:].split("\t")[0].strip()
                current_file = None if target == "/dev/null" else target.removeprefix("b/")
                continue
            match = _HUNK_RE.match(raw)
            if match and current_file:
                old_remaining = int(match.group(1) or "1")
                new_line = int(match.group(2))
                new_remaining = int(match.group(3) or "1")
            continue
        if raw.startswith("+"):
            yield current_file, new_line, raw[1:]
            new_line += 1
            new_remaining -= 1
        elif raw.startswith("-"):
            old_remaining -= 1
        elif raw.startswith("\\"):
            continue  # "\ No newline at end of file"
        else:
            new_line += 1
            old_remaining -= 1
            new_remaining -= 1


def _normalize_patterns(filters: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not filters:
        return ()
    if isinstance(filters, str):
        return tuple(item.strip() for item in filters.split(",") if item.strip())
    return tuple(str(item).strip() for item in filters if str(item).strip())


def _file_matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def _excluded(path: str) -> bool:
    return _file_matches(path, EXCLUDED_FILES)


def _included_by_filters(path: str, filters: str | list[str] | tuple[str, ...] | None) -> bool:
    patterns = _normalize_patterns(filters)
    return not patterns or _file_matches(path, patterns)


def _finding(rule: Rule, file: str, line_no: int, line: str, match: re.Match) -> dict:
    shown = line.strip()
    if rule.is_secret and match.groups():
        shown = shown.replace(match.group(1), mask_secret(match.group(1)))
    return {
        "title": rule.title,
        "details": rule.details,
        "severity": rule.severity,
        "confidence": rule.confidence,
        "tags": [*rule.tags, "static-analysis"],
        "source": "static",
        "rule": rule.id,
        "affected_lines": [
            {
                "file": file,
                "start_line": line_no,
                "end_line": line_no,
                "affected_code": f"{line_no}: {shown}",
            }
        ],
    }


def analyze_diff(
    diff_text: str,
    filters: str | list[str] | tuple[str, ...] | None = None,
) -> dict[str, list[dict]]:
    """Run the rule pack over the added lines of a unified diff."""
    issues: dict[str, list[dict]] = {}
    rule_hits: Counter = Counter()
    total = 0
    for file, line_no, line in iter_added_lines(diff_text):
        if total >= MAX_TOTAL_FINDINGS:
            break
        if (
            not file
            or _excluded(file)
            or not _included_by_filters(file, filters)
            or len(line) > MAX_LINE_LENGTH
        ):
            continue
        secret_flagged = False  # only the most specific secret rule fires per line
        for rule in RULES:
            if rule.is_secret and secret_flagged:
                continue
            if not _file_matches(file, rule.file_patterns):
                continue
            if rule_hits[(file, rule.id)] >= MAX_FINDINGS_PER_RULE_PER_FILE:
                continue
            match = rule.pattern.search(line)
            if not match:
                continue
            if rule.validator and not rule.validator(match, line):
                continue
            issues.setdefault(file, []).append(_finding(rule, file, line_no, line, match))
            rule_hits[(file, rule.id)] += 1
            total += 1
            if rule.is_secret:
                secret_flagged = True
    return issues


def _git_stdout(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def git_show_blob(repo_path: Path, ref: str, path: str) -> str:
    """File content at ``ref`` (empty string when the blob does not exist),
    matching the empty-on-failure convention of the other git helpers."""
    if not ref or not path:
        return ""
    return _git_stdout(repo_path, "show", f"{ref}:{path}")


def diff_base_and_target(
    repo_path: Path,
    mode: str = "working",
    refs: str = "",
    what: str = "",
    against: str = "",
    use_merge_base: bool = True,
) -> tuple[str, str]:
    """The (base_ref, target_ref) pair the review diff compares.

    ``target_ref`` is empty when the diff targets the working tree.
    """
    args = _diff_args(
        repo_path,
        mode=mode,
        refs=refs,
        what=what,
        against=against,
        use_merge_base=use_merge_base,
        name_only=False,
    )
    refs_part = args[1:]  # everything after "diff"
    base = refs_part[0] if refs_part else "HEAD"
    target = refs_part[1] if len(refs_part) > 1 else ""
    return base, target


def _active_ref(repo_path: Path) -> str:
    return _git_stdout(repo_path, "branch", "--show-current") or "HEAD"


def _parse_refs_pair(refs: str) -> tuple[str, str]:
    if ".." not in refs:
        return refs, ""
    what, against = refs.split("..", 1)
    return what, against


def _merge_base(repo_path: Path, what: str, against: str) -> str:
    if not against:
        return ""
    current = what or _active_ref(repo_path)
    return _git_stdout(repo_path, "merge-base", current, against)


def _diff_args(
    repo_path: Path,
    mode: str,
    refs: str,
    what: str,
    against: str,
    use_merge_base: bool,
    name_only: bool,
) -> list[str]:
    args = ["diff"]
    if name_only:
        args.append("--name-only")

    if mode == "all":
        return [*args, EMPTY_TREE_SHA]

    if refs:
        ref_what, ref_against = _parse_refs_pair(refs)
        if ref_against:
            base = (
                _merge_base(repo_path, ref_what, ref_against)
                if use_merge_base
                else ref_against
            )
            if ref_what:
                return [*args, base or ref_against, ref_what]
            return [*args, base or ref_against]
        return [*args, refs]

    if what:
        if against:
            base = _merge_base(repo_path, what, against) if use_merge_base else against
            return [*args, base or against, what]
        return [*args, f"{what}~1", what]

    base = against or "HEAD"
    if use_merge_base and against:
        base = _merge_base(repo_path, "", against) or against
    return [*args, base]


def collect_diff(
    repo_path: Path,
    mode: str = "working",
    refs: str = "",
    what: str = "",
    against: str = "",
    use_merge_base: bool = True,
    filters: str | list[str] | tuple[str, ...] | None = None,
    name_only: bool = False,
) -> str:
    """Produce the same shape of diff the review targets, as unified diff text."""
    args = _diff_args(
        repo_path,
        mode=mode,
        refs=refs,
        what=what,
        against=against,
        use_merge_base=use_merge_base,
        name_only=name_only,
    )
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return ""
    if not name_only or not filters:
        return result.stdout
    kept = [
        line
        for line in result.stdout.splitlines()
        if line and not _excluded(line) and _included_by_filters(line, filters)
    ]
    return "\n".join(kept) + ("\n" if kept else "")


def analyze_repo_changes(
    repo_path: Path,
    mode: str = "working",
    refs: str = "",
    what: str = "",
    against: str = "",
    use_merge_base: bool = True,
    filters: str | list[str] | tuple[str, ...] | None = None,
) -> dict[str, list[dict]]:
    diff_text = collect_diff(
        repo_path,
        mode=mode,
        refs=refs,
        what=what,
        against=against,
        use_merge_base=use_merge_base,
    )
    if not diff_text:
        return {}
    return analyze_diff(diff_text, filters=filters)


def collect_changed_files(
    repo_path: Path,
    mode: str = "working",
    refs: str = "",
    what: str = "",
    against: str = "",
    use_merge_base: bool = True,
    filters: str | list[str] | tuple[str, ...] | None = None,
) -> list[str]:
    names = collect_diff(
        repo_path,
        mode=mode,
        refs=refs,
        what=what,
        against=against,
        use_merge_base=use_merge_base,
        filters=filters,
        name_only=True,
    )
    return [name for name in names.splitlines() if name]


def empty_report() -> dict:
    return {
        "issues": {},
        "summary": "",
        "number_of_processed_files": 0,
        "total_issues": 0,
        "processing_warnings": [],
    }


def merge_into_report(report: dict, static_issues: dict[str, list[dict]]) -> int:
    """Merge static findings into a Gito report, skipping lines the LLM already flagged.

    Returns the number of findings added.
    """
    issues = report.setdefault("issues", {})
    next_id = STATIC_ISSUE_ID_BASE
    added = 0
    for file, findings in static_issues.items():
        existing = issues.get(file) or []
        covered: set[int] = set()
        for issue in existing:
            for block in issue.get("affected_lines") or []:
                start, end = block.get("start_line"), block.get("end_line")
                if isinstance(start, int) and isinstance(end, int) and end >= start:
                    covered.update(range(start, end + 1))
        kept = []
        for finding in findings:
            line = finding["affected_lines"][0]["start_line"]
            if line in covered:
                continue
            kept.append({**finding, "id": next_id, "file": file})
            next_id += 1
            added += 1
        if kept:
            issues[file] = existing + kept
    if added:
        report["total_issues"] = report.get("total_issues", 0) + added
    return added
