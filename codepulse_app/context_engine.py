"""Cross-file context engine.

Reviews that only see each changed file in isolation miss the class of bugs
that live *between* files: a function signature changes but a caller in
another module does not, an export is deleted while something still imports
it. This module closes that gap deterministically:

1. Build a repo-wide import graph (Python + JS/TS) from tracked files.
2. Extract symbols the diff removed or re-signed per changed file.
3. Scan the changed file's dependents for call sites of those symbols.
4. Emit ``source: "crossfile"`` findings for contract breaks, and a
   ``context pack`` (dependents, imports, symbol changes, usage sites) that
   the LLM verifier consumes so its verdicts see beyond the diff.

Symbol extraction is AST-level for Python (``semantic_py``) and tolerant
tokenizer-level for JS/TS (``semantic_js``), with the original regex pass kept
as the fallback whenever parsing fails — behavior degrades to the old
analysis, never worse. Findings stay conservative (confidence 2), except when
argument-binding simulation *proves* a call site breaks (confidence 1).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import semantic_js, semantic_py, static_analysis

CROSSFILE_ISSUE_ID_BASE = 20000
MAX_GRAPH_FILES = 4000
MAX_FILE_BYTES = 400_000
MAX_USAGES_PER_SYMBOL = 8
MAX_FINDINGS = 25

PY_EXTENSIONS = (".py",)
JS_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")
SOURCE_EXTENSIONS = PY_EXTENSIONS + JS_EXTENSIONS

PY_FROM_IMPORT_RE = re.compile(r"^\s*from\s+(\.*)([\w.]*)\s+import\s+([\w.,\s*()]+)", re.MULTILINE)
PY_IMPORT_RE = re.compile(r"^\s*import\s+([\w.,\s]+)", re.MULTILINE)
JS_IMPORT_RE = re.compile(
    r"""(?:import|export)\s+[^'"();]*?from\s+['"]([^'"]+)['"]"""
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|import\(\s*['"]([^'"]+)['"]\s*\)"""
    r"""|^\s*import\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)

PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)")
PY_CLASS_RE = re.compile(r"^\s*class\s+(\w+)")
JS_FUNC_RE = re.compile(
    r"^\s*(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)"
)
JS_ARROW_RE = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*(?::[^=]+)?=>"
)

_DUNDER_OR_PRIVATE = re.compile(r"^_")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_source(path: str) -> bool:
    return path.endswith(SOURCE_EXTENSIONS) and not static_analysis._excluded(path)


def list_source_files(repo_path: Path) -> list[str]:
    tracked = static_analysis._git_stdout(repo_path, "ls-files").splitlines()
    return [name for name in tracked if _is_source(name)][:MAX_GRAPH_FILES]


def _read_text(repo_path: Path, relative: str) -> str:
    target = repo_path / relative
    try:
        if not target.is_file() or target.stat().st_size > MAX_FILE_BYTES:
            return ""
        return target.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _resolve_python_module(module: str, level: int, importer: str, files: set[str]) -> str | None:
    if level:
        base_parts = Path(importer).parent.parts
        if level - 1 > len(base_parts):
            return None
        base_parts = base_parts[: len(base_parts) - (level - 1)]
        prefix = "/".join(base_parts)
        dotted = module.replace(".", "/")
        stem = f"{prefix}/{dotted}".strip("/") if dotted else prefix
    else:
        stem = module.replace(".", "/")
    for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
        if candidate in files:
            return candidate
    # `from pkg.mod import name` where pkg/mod.py exists but name is a symbol
    parent = "/".join(stem.split("/")[:-1])
    if parent:
        for candidate in (f"{parent}.py", f"{parent}/__init__.py"):
            if candidate in files:
                return candidate
    return None


def _resolve_js_specifier(spec: str, importer: str, files: set[str]) -> str | None:
    if not spec.startswith("."):
        return None  # bare specifier → external package
    base = (Path(importer).parent / spec).as_posix()
    parts: list[str] = []
    for part in base.split("/"):
        if part == "..":
            if not parts:
                return None
            parts.pop()
        elif part not in {".", ""}:
            parts.append(part)
    stem = "/".join(parts)
    if stem in files:
        return stem
    for ext in JS_EXTENSIONS:
        if f"{stem}{ext}" in files:
            return f"{stem}{ext}"
    for ext in JS_EXTENSIONS:
        if f"{stem}/index{ext}" in files:
            return f"{stem}/index{ext}"
    return None


def file_imports(repo_path: Path, relative: str, files: set[str]) -> set[str]:
    """Repo files that ``relative`` imports."""
    text = _read_text(repo_path, relative)
    if not text:
        return set()
    imports: set[str] = set()
    if relative.endswith(PY_EXTENSIONS):
        for match in PY_FROM_IMPORT_RE.finditer(text):
            resolved = _resolve_python_module(
                match.group(2), len(match.group(1)), relative, files
            )
            if resolved and resolved != relative:
                imports.add(resolved)
        for match in PY_IMPORT_RE.finditer(text):
            for module in match.group(1).split(","):
                resolved = _resolve_python_module(module.strip().split(" as ")[0], 0, relative, files)
                if resolved and resolved != relative:
                    imports.add(resolved)
    else:
        for match in JS_IMPORT_RE.finditer(text):
            spec = next((group for group in match.groups() if group), "")
            resolved = _resolve_js_specifier(spec, relative, files)
            if resolved and resolved != relative:
                imports.add(resolved)
    return imports


def build_import_graph(repo_path: Path, files: list[str]) -> dict[str, set[str]]:
    """file → set of repo files it imports."""
    file_set = set(files)
    return {name: file_imports(repo_path, name, file_set) for name in files}


def dependents_of(graph: dict[str, set[str]], target: str) -> list[str]:
    return sorted(name for name, imports in graph.items() if target in imports)


def _defs_in_lines(lines: list[str], is_python: bool) -> dict[str, str]:
    """Symbol name → normalized parameter list for definition lines."""
    defs: dict[str, str] = {}
    patterns = (PY_DEF_RE, PY_CLASS_RE) if is_python else (JS_FUNC_RE, JS_ARROW_RE)
    for line in lines:
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                name = match.group(1)
                params = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
                defs[name] = re.sub(r"\s+", "", params or "")
                break
    return defs


def _diff_symbol_changes_textual(diff_text: str) -> dict[str, dict[str, Any]]:
    """Regex fallback: symbol changes read from the diff hunks alone."""
    removed_lines: dict[str, list[str]] = {}
    added_lines: dict[str, list[str]] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            current = None if name == "/dev/null" else name.removeprefix("b/")
            continue
        if line.startswith(("--- ", "diff --git", "index ", "@@")) or current is None:
            continue
        if line.startswith("-") and not line.startswith("---"):
            removed_lines.setdefault(current, []).append(line[1:])
        elif line.startswith("+") and not line.startswith("+++"):
            added_lines.setdefault(current, []).append(line[1:])

    changes: dict[str, dict[str, Any]] = {}
    for file in sorted(set(removed_lines) | set(added_lines)):
        if not _is_source(file):
            continue
        is_python = file.endswith(PY_EXTENSIONS)
        removed_defs = _defs_in_lines(removed_lines.get(file, []), is_python)
        added_defs = _defs_in_lines(added_lines.get(file, []), is_python)
        removed = sorted(
            name for name in removed_defs
            if name not in added_defs and not _DUNDER_OR_PRIVATE.match(name)
        )
        signature_changed = [
            {"name": name, "before": removed_defs[name], "after": added_defs[name]}
            for name in sorted(removed_defs)
            if name in added_defs
            and removed_defs[name] != added_defs[name]
            and not _DUNDER_OR_PRIVATE.match(name)
        ]
        added = sorted(name for name in added_defs if name not in removed_defs)
        if removed or signature_changed or added:
            changes[file] = {
                "added": added,
                "removed": removed,
                "signature_changed": signature_changed or [],
            }
    return changes


def _changed_files_from_diff(diff_text: str) -> list[str]:
    files: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            name = line[4:].split("\t")[0].strip()
            if name != "/dev/null":
                name = name.removeprefix("b/")
                if name not in files:
                    files.append(name)
    return files


def _before_and_after_sources(
    repo_path: Path, file: str, base_ref: str, target_ref: str
) -> tuple[str, str]:
    before = static_analysis.git_show_blob(repo_path, base_ref, file)
    if target_ref:
        after = static_analysis.git_show_blob(repo_path, target_ref, file)
    else:
        after = _read_text(repo_path, file)
    return before, after


def _entry_from_symbol_maps(
    before: dict[str, str], after: dict[str, str]
) -> dict[str, Any] | None:
    removed = sorted(
        name for name in before
        if name not in after and not _DUNDER_OR_PRIVATE.match(name)
    )
    signature_changed = [
        {"name": name, "before": before[name], "after": after[name]}
        for name in sorted(before)
        if name in after and before[name] != after[name] and not _DUNDER_OR_PRIVATE.match(name)
    ]
    added = sorted(name for name in after if name not in before)
    if not (removed or signature_changed or added):
        return None
    return {"added": added, "removed": removed, "signature_changed": signature_changed}


def python_symbol_infos(
    repo_path: Path, file: str, base_ref: str, target_ref: str = ""
) -> tuple[dict[str, semantic_py.SymbolInfo] | None, dict[str, semantic_py.SymbolInfo] | None]:
    """(before, after) SymbolInfo maps for a changed Python file; None on parse failure."""
    before_src, after_src = _before_and_after_sources(repo_path, file, base_ref, target_ref)
    try:
        before = semantic_py.module_symbols(before_src)
    except (SyntaxError, ValueError):
        before = None
    try:
        after = semantic_py.module_symbols(after_src)
    except (SyntaxError, ValueError):
        after = None
    return before, after


def _semantic_file_entry(
    repo_path: Path, file: str, base_ref: str, target_ref: str
) -> dict[str, Any] | None:
    """Symbol changes for one file by parsing full before/after content.

    Raises on parse failure so the caller can fall back to the textual pass.
    """
    if file.endswith(PY_EXTENSIONS):
        before_syms, after_syms = python_symbol_infos(repo_path, file, base_ref, target_ref)
        if before_syms is None or after_syms is None:
            raise ValueError(f"unparseable Python in {file}")
        before = {name: semantic_py.signature_string(info) for name, info in before_syms.items()}
        after = {name: semantic_py.signature_string(info) for name, info in after_syms.items()}
    else:
        before_src, after_src = _before_and_after_sources(repo_path, file, base_ref, target_ref)
        before = semantic_js.module_symbols(before_src)
        after = semantic_js.module_symbols(after_src)
    return _entry_from_symbol_maps(before, after)


def diff_symbol_changes(
    diff_text: str,
    repo_path: Path | None = None,
    base_ref: str = "HEAD",
    target_ref: str = "",
) -> dict[str, dict[str, Any]]:
    """Per changed file: symbols the diff added, removed, or re-signed.

    With a ``repo_path``, before/after file contents are parsed so symbols
    whose definition lines never appear in the diff hunks are still seen;
    without one (or when parsing fails) the textual diff pass is used.
    """
    textual = _diff_symbol_changes_textual(diff_text)
    if repo_path is None:
        return textual
    changes: dict[str, dict[str, Any]] = {}
    for file in _changed_files_from_diff(diff_text):
        if not _is_source(file):
            continue
        try:
            entry = _semantic_file_entry(repo_path, file, base_ref, target_ref)
        except Exception:  # noqa: BLE001 - degrade to the textual analysis, never worse
            entry = textual.get(file)
        if entry:
            changes[file] = entry
    return changes


def _regex_usages(
    repo_path: Path, symbol: str, files: list[str], limit: int
) -> list[dict[str, Any]]:
    pattern = re.compile(rf"\b{re.escape(symbol)}\s*\(")
    word = re.compile(rf"\b{re.escape(symbol)}\b")
    usages: list[dict[str, Any]] = []
    for file in files:
        text = _read_text(repo_path, file)
        if not text:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("import ", "from ", "//", "#", "*")):
                continue
            if pattern.search(line) or (word.search(line) and "(" not in stripped):
                usages.append({"file": file, "line": line_no, "code": stripped[:200]})
                if len(usages) >= limit:
                    return usages
    return usages


def find_usages(
    repo_path: Path,
    symbol: str,
    dependent_files: list[str],
    limit: int = MAX_USAGES_PER_SYMBOL,
    before: semantic_py.SymbolInfo | None = None,
    after: semantic_py.SymbolInfo | None = None,
) -> list[dict[str, Any]]:
    """Usage sites of ``symbol`` across dependents.

    When before/after ``SymbolInfo`` is supplied (a Python signature change),
    Python dependents are checked with real call-site binding: each usage
    gains ``break_reason`` when the call provably no longer binds, or
    ``verified_ok`` when it still does. Non-Python dependents and unparseable
    files keep the regex scan.
    """
    if after is None:
        return _regex_usages(repo_path, symbol, dependent_files, limit)
    usages: list[dict[str, Any]] = []
    regex_files: list[str] = []
    for file in dependent_files:
        if not file.endswith(PY_EXTENSIONS):
            regex_files.append(file)
            continue
        text = _read_text(repo_path, file)
        if not text:
            continue
        try:
            calls = semantic_py.call_sites(text, symbol)
        except (SyntaxError, ValueError):
            regex_files.append(file)
            continue
        lines = text.splitlines()
        for call in calls:
            usage: dict[str, Any] = {
                "file": file,
                "line": call.lineno,
                "code": (lines[call.lineno - 1].strip() if 0 < call.lineno <= len(lines) else "")[:200],
                "kind": "call",
            }
            reason = semantic_py.signature_break(before, after, call)
            if reason:
                usage["break_reason"] = reason
            else:
                usage["verified_ok"] = True
            usages.append(usage)
            if len(usages) >= limit:
                return usages
    if regex_files:
        usages.extend(_regex_usages(repo_path, symbol, regex_files, limit - len(usages)))
    return usages[:limit]


def _finding(
    file: str,
    title: str,
    details: str,
    usages: list[dict[str, Any]],
    tags: list[str],
    confidence: int = 2,
) -> dict[str, Any]:
    return {
        "title": title,
        "details": details,
        "severity": 2,
        "confidence": confidence,
        "tags": ["cross-file", *tags],
        "source": "crossfile",
        "affected_lines": [
            {
                "file": usage["file"],
                "start_line": usage["line"],
                "end_line": usage["line"],
                "affected_code": f"{usage['line']}: {usage['code']}",
            }
            for usage in usages
        ],
    }


def analyze_cross_file(
    repo_path: Path,
    mode: str = "working",
    refs: str = "",
    what: str = "",
    against: str = "",
    use_merge_base: bool = True,
    filters: str | list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Return ``{"pack": <context pack>, "findings": {file: [issues]}}``."""
    diff_text = static_analysis.collect_diff(
        repo_path,
        mode=mode,
        refs=refs,
        what=what,
        against=against,
        use_merge_base=use_merge_base,
    )
    changed_files = static_analysis.collect_changed_files(
        repo_path,
        mode=mode,
        refs=refs,
        what=what,
        against=against,
        use_merge_base=use_merge_base,
        filters=filters,
    )
    changed_source = [file for file in changed_files if _is_source(file)]
    if not changed_source:
        return {"pack": {"generated_at": _utc_now(), "files": {}, "graph_files": 0}, "findings": {}}

    source_files = list_source_files(repo_path)
    graph = build_import_graph(repo_path, source_files)
    base_ref, target_ref = static_analysis.diff_base_and_target(
        repo_path,
        mode=mode,
        refs=refs,
        what=what,
        against=against,
        use_merge_base=use_merge_base,
    )
    symbol_changes = (
        diff_symbol_changes(diff_text, repo_path, base_ref, target_ref) if diff_text else {}
    )

    pack_files: dict[str, Any] = {}
    findings: dict[str, list[dict[str, Any]]] = {}
    finding_count = 0

    for file in changed_source:
        dependents = dependents_of(graph, file)
        symbols = symbol_changes.get(file, {"added": [], "removed": [], "signature_changed": []})
        usages: dict[str, list[dict[str, Any]]] = {}

        for name in symbols["removed"]:
            found = find_usages(repo_path, name, dependents)
            if found:
                usages[name] = found
                if finding_count < MAX_FINDINGS:
                    referencing = sorted({usage["file"] for usage in found})
                    findings.setdefault(file, []).append(
                        _finding(
                            file,
                            f"Removed symbol `{name}` is still referenced elsewhere.",
                            f"`{name}` was removed from {file}, but "
                            f"{len(found)} reference(s) remain in: {', '.join(referencing)}. "
                            "Those call sites will break at import or call time.",
                            found,
                            ["breaking-change"],
                        )
                    )
                    finding_count += 1

        before_infos: dict[str, semantic_py.SymbolInfo] | None = None
        after_infos: dict[str, semantic_py.SymbolInfo] | None = None
        if symbols["signature_changed"] and file.endswith(PY_EXTENSIONS):
            try:
                before_infos, after_infos = python_symbol_infos(
                    repo_path, file, base_ref, target_ref
                )
            except Exception:  # noqa: BLE001 - binding check is best-effort
                before_infos = after_infos = None

        for change in symbols["signature_changed"]:
            name = change["name"]
            before_info = (before_infos or {}).get(name)
            after_info = (after_infos or {}).get(name)
            found = find_usages(
                repo_path, name, dependents, before=before_info, after=after_info
            )
            if found:
                usages[name] = found
                # Every call site provably binds against the new signature →
                # the contract did not break for any dependent; stay quiet.
                if all(usage.get("verified_ok") for usage in found):
                    continue
                if finding_count < MAX_FINDINGS:
                    referencing = sorted({usage["file"] for usage in found})
                    break_reasons = [
                        usage["break_reason"] for usage in found if usage.get("break_reason")
                    ]
                    details = (
                        f"`{name}` changed from `({change['before']})` to "
                        f"`({change['after']})` in {file}. "
                        f"{len(found)} call site(s) in {', '.join(referencing)} "
                        "were not updated in this diff."
                    )
                    if break_reasons:
                        details += f" Argument-binding check: {break_reasons[0]}."
                    findings.setdefault(file, []).append(
                        _finding(
                            file,
                            f"Signature of `{name}` changed; external call sites may need updates.",
                            details,
                            found,
                            ["api-contract"],
                            confidence=1 if break_reasons else 2,
                        )
                    )
                    finding_count += 1

        pack_files[file] = {
            "imports": sorted(graph.get(file, set())),
            "dependents": dependents,
            "symbols": symbols,
            "usages": usages,
        }

    return {
        "pack": {
            "generated_at": _utc_now(),
            "files": pack_files,
            "graph_files": len(source_files),
        },
        "findings": findings,
    }


def merge_into_report(report: dict, crossfile_issues: dict[str, list[dict]]) -> int:
    """Merge cross-file findings into a Gito report. Returns findings added."""
    issues = report.setdefault("issues", {})
    next_id = CROSSFILE_ISSUE_ID_BASE
    added = 0
    for file, findings in crossfile_issues.items():
        existing = issues.get(file) or []
        stamped = []
        for finding in findings:
            stamped.append({**finding, "id": next_id, "file": file})
            next_id += 1
            added += 1
        if stamped:
            issues[file] = existing + stamped
    if added:
        report["total_issues"] = report.get("total_issues", 0) + added
    return added
