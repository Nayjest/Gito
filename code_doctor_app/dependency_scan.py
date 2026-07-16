"""Offline supply-chain / dependency checks for manifests.

No network or CVE feed is required — these are deterministic, offline
heuristics that flag the highest-signal supply-chain smells:

- **Typosquatting:** a dependency name one edit away from a very popular
  package (e.g. ``python-dateutils`` vs ``python-dateutil``), the classic
  malicious-package vector.
- **Unpinned versions:** no exact pin, so ``pip install`` / ``npm install``
  can silently pull a new (possibly compromised) release — a reproducibility
  and supply-chain risk.
- **Direct URL / VCS installs:** a dependency fetched from a git or http URL
  bypasses the index and its checks entirely.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

DEP_ISSUE_ID_BASE = 40000

# Popular packages used as the typosquat reference set. A candidate name that
# is edit-distance 1 from one of these (but not equal) is suspicious.
POPULAR_PYPI = frozenset({
    "requests", "urllib3", "numpy", "pandas", "flask", "django", "fastapi",
    "pydantic", "sqlalchemy", "boto3", "click", "jinja2", "pyyaml", "pillow",
    "python-dateutil", "setuptools", "wheel", "pytest", "scipy", "matplotlib",
    "cryptography", "certifi", "aiohttp", "beautifulsoup4", "lxml", "redis",
    "celery", "tqdm", "openai", "anthropic", "torch", "tensorflow", "scikit-learn",
})
POPULAR_NPM = frozenset({
    "react", "react-dom", "lodash", "axios", "express", "chalk", "commander",
    "webpack", "vite", "typescript", "eslint", "prettier", "jest", "vitest",
    "next", "vue", "svelte", "rxjs", "moment", "dayjs", "uuid", "dotenv",
    "cors", "body-parser", "mongoose", "sequelize", "socket.io", "ws",
})


def _edit_distance_one(a: str, b: str) -> bool:
    """True when a and b differ by a single insertion, deletion, substitution,
    or adjacent transposition — the common typosquat mutations."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        diffs = [i for i in range(la) if a[i] != b[i]]
        if len(diffs) == 1:  # one substitution
            return True
        if len(diffs) == 2 and diffs[1] == diffs[0] + 1:  # adjacent transposition
            i = diffs[0]
            return a[i] == b[i + 1] and a[i + 1] == b[i]
        return False
    # one insertion/deletion: walk the shorter against the longer
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    i = j = 0
    skipped = False
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
        elif skipped:
            return False
        else:
            skipped = True
            j += 1
    return True


def _typosquat_of(name: str, popular: frozenset[str]) -> str | None:
    lowered = name.lower()
    if lowered in popular:
        return None
    for target in popular:
        if _edit_distance_one(lowered, target):
            return target
    return None


def _finding(rule: str, title: str, details: str, severity: int,
             file: str, line_no: int, text: str) -> dict[str, Any]:
    return {
        "title": title,
        "details": details,
        "severity": severity,
        "confidence": 2,
        "tags": ["security", "supply-chain", "static-analysis"],
        "source": "dependency",
        "rule": rule,
        "affected_lines": [{
            "file": file, "start_line": line_no, "end_line": line_no,
            "affected_code": f"{line_no}: {text.strip()}",
        }],
    }


_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(.*)$")


def scan_requirements(text: str, file: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("-"):  # -e, -r, --hash, options
            continue
        if re.search(r"(?:git\+|https?://|@\s*https?://|@\s*git)", stripped):
            name = _REQ_LINE.match(stripped)
            findings.append(_finding(
                "dep-url-install",
                "Dependency installed from a URL/VCS.",
                "This dependency is fetched directly from a URL or git repo, "
                "bypassing the package index and its integrity checks. Pin to a "
                "released, hashed version from the index.",
                2, file, i, raw,
            ))
            continue
        m = _REQ_LINE.match(stripped)
        if not m:
            continue
        name, rest = m.group(1), m.group(2)
        target = _typosquat_of(name, POPULAR_PYPI)
        if target:
            findings.append(_finding(
                "dep-typosquat",
                f"Possible typosquat of '{target}'.",
                f"'{name}' is one character from the popular package '{target}'. "
                "Confirm this is the intended package — typosquatted packages are "
                "a common malware vector.",
                1, file, i, raw,
            ))
        if "==" not in rest and not rest.strip().startswith("=="):
            findings.append(_finding(
                "dep-unpinned",
                f"Dependency '{name}' is not pinned.",
                "Without an exact == pin, installs can pull a newer (possibly "
                "compromised) release and builds aren't reproducible. Pin the "
                "version and consider hashes.",
                3, file, i, raw,
            ))
    return findings


def scan_package_json(text: str, file: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    lines = text.splitlines()

    def line_of(name: str) -> tuple[int, str]:
        for i, raw in enumerate(lines, start=1):
            if re.search(rf'"{re.escape(name)}"\s*:', raw):
                return i, raw
        return 1, ""

    findings: list[dict[str, Any]] = []
    for section in ("dependencies", "devDependencies", "optionalDependencies"):
        deps = data.get(section)
        if not isinstance(deps, dict):
            continue
        for name, spec in deps.items():
            i, raw = line_of(name)
            target = _typosquat_of(name, POPULAR_NPM)
            if target:
                findings.append(_finding(
                    "dep-typosquat",
                    f"Possible typosquat of '{target}'.",
                    f"'{name}' is one character from the popular package "
                    f"'{target}'. Confirm this is intended.",
                    1, file, i, raw,
                ))
            spec_s = str(spec).strip()
            if re.search(r"(?:git(?:\+|://)|https?://|github:)", spec_s):
                findings.append(_finding(
                    "dep-url-install",
                    f"Dependency '{name}' installed from a URL/VCS.",
                    "Fetching a dependency from a URL or git repo bypasses the "
                    "registry and its checks. Use a published, version-pinned "
                    "release.",
                    2, file, i, raw,
                ))
            elif spec_s in {"*", "latest", "x", ""} or spec_s.startswith((">", "<")):
                # ^ and ~ are the npm norm; flagging every one is noise. Only
                # truly-floating specs (wildcard / open range) are called out.
                findings.append(_finding(
                    "dep-unpinned",
                    f"Dependency '{name}' uses an unbounded version ({spec_s or '*'}).",
                    "A wildcard or open-ended range lets installs pull an "
                    "arbitrary future (possibly compromised) release. Constrain "
                    "it to a specific version or a bounded range.",
                    3, file, i, raw,
                ))
    return findings


MANIFEST_SCANNERS = {
    "requirements.txt": scan_requirements,
    "package.json": scan_package_json,
}


def analyze_repo_changes(
    repo_path: Path,
    mode: str = "working",
    refs: str = "",
    what: str = "",
    against: str = "",
    use_merge_base: bool = True,
    filters: Any = None,
) -> dict[str, list[dict[str, Any]]]:
    """Scan changed manifest files in the review scope."""
    from . import static_analysis

    diff_text = static_analysis.collect_diff(
        repo_path, mode=mode, refs=refs, what=what,
        against=against, use_merge_base=use_merge_base,
    )
    if not diff_text:
        return {}
    changed = {
        file for file, _ln, _t in static_analysis.iter_added_lines(diff_text) if file
    }
    _base, target_ref = static_analysis.diff_base_and_target(
        repo_path, mode=mode, refs=refs, what=what,
        against=against, use_merge_base=use_merge_base,
    )
    issues: dict[str, list[dict[str, Any]]] = {}
    for file in changed:
        scanner = MANIFEST_SCANNERS.get(Path(file).name)
        if scanner is None:
            continue
        text = static_analysis.git_show_blob(repo_path, target_ref, file) if target_ref else ""
        if not text:
            try:
                text = (repo_path / file).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
        found = scanner(text, file)
        if found:
            issues[file] = found
    return issues


def merge_into_report(report: dict[str, Any], dep_issues: dict[str, list[dict]]) -> int:
    issues = report.setdefault("issues", {})
    next_id = DEP_ISSUE_ID_BASE
    added = 0
    for file, findings in dep_issues.items():
        existing = issues.get(file) or []
        for finding in findings:
            existing.append({**finding, "id": next_id, "file": file})
            next_id += 1
            added += 1
        issues[file] = existing
    if added:
        report["total_issues"] = report.get("total_issues", 0) + added
    return added
