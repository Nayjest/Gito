"""Taint / dataflow analysis for Python — intra- and interprocedural.

Regex rules catch a *pattern on one line*; they cannot tell whether the
argument to ``open()`` came from a request or a constant. This module tracks
untrusted input from **sources** (HTTP request data, route-handler
parameters, ``input()``) to dangerous **sinks** (file open, outbound HTTP,
subprocess, eval/exec, SQL execute, template render) through assignments,
f-strings, concatenation, ``.format()``, and common wrapper calls.

On top of the per-function pass, a module-level **summary fixpoint** makes
the analysis interprocedural within a file: every function is summarized as
(a) which of its parameters reach a sink in its body, (b) whether its return
value is itself untrusted (it reads from a source), and (c) which parameters
flow through to its return value. Call sites then apply those summaries, so
production patterns like a route handler calling ``fetch(url)`` where the
helper does ``requests.get(url)`` — or ``data = read_payload()`` feeding a
sink — are detected even though source and sink live in different functions.

Summaries are built across every file in the repository (keyed by bare
function/method name, the same key call sites resolve through), so a flow
whose sink lives in a different module than the untrusted source — the
"thin route handler → manager/helper module" shape — is caught too.

It is deliberately conservative: it reports a finding only when a tainted
value provably reaches a sink argument, route handlers are never treated as
callable helper targets, and an ambiguous helper name is never assumed
dangerous, so false positives stay rare.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from . import static_analysis

TAINT_ISSUE_ID_BASE = 30000
PY_FILES = ("*.py",)

# Attribute roots that are always untrusted in a web app.
REQUEST_ROOTS = frozenset({"request"})
# Calls whose return value is untrusted regardless of arguments.
SOURCE_CALLS = frozenset({"input"})


@dataclass(frozen=True)
class SinkSpec:
    """A dangerous call and what a tainted argument to it means."""

    title: str
    detail: str
    tags: tuple[str, ...]
    severity: int
    # Which positional argument indexes are dangerous (None = any argument).
    arg_indexes: tuple[int, ...] | None = None
    # For SQL: only dangerous when the argument is a *built* string
    # (f-string / concat / %/.format), not a plain tainted variable.
    require_built_string: bool = False
    # For method sinks (path.read_text()): the danger is a tainted *receiver*.
    check_receiver: bool = False


# Dotted call name (last one or two attributes) -> what a tainted arg means.
SINKS: dict[str, SinkSpec] = {
    "open": SinkSpec(
        "Untrusted input used as a file path.",
        "A request-derived value reaches open(); an attacker can read or write "
        "arbitrary files via path traversal (../). Resolve against a fixed root "
        "and reject paths that escape it.",
        ("security", "path-traversal"), 1, arg_indexes=(0,),
    ),
    "send_file": SinkSpec(
        "Untrusted input passed to send_file (path traversal).",
        "send_file() with a request-derived path lets an attacker download "
        "arbitrary files. Serve from a fixed directory with send_from_directory "
        "or validate the resolved path stays inside the intended root.",
        ("security", "path-traversal"), 1, arg_indexes=(0,),
    ),
    "os.system": SinkSpec(
        "Untrusted input reaches a shell command.",
        "os.system() runs its string through the shell; request-derived input "
        "here is command injection. Use subprocess with an argument list and no "
        "shell.",
        ("security", "command-injection"), 1,
    ),
    "os.popen": SinkSpec(
        "Untrusted input reaches a shell command.",
        "os.popen() runs a shell; request-derived input is command injection. "
        "Use subprocess with an argument list.",
        ("security", "command-injection"), 1,
    ),
    "subprocess.run": SinkSpec(
        "Untrusted input reaches a subprocess call.",
        "A request-derived value reaches subprocess; with shell=True this is "
        "command injection, and even as an argument it can be abused. Validate "
        "against an allowlist and never pass user input to shell=True.",
        ("security", "command-injection"), 1, arg_indexes=(0,),
    ),
    "subprocess.call": SinkSpec(
        "Untrusted input reaches a subprocess call.",
        "A request-derived value reaches subprocess. Validate against an "
        "allowlist and avoid shell=True.",
        ("security", "command-injection"), 1, arg_indexes=(0,),
    ),
    "subprocess.Popen": SinkSpec(
        "Untrusted input reaches a subprocess call.",
        "A request-derived value reaches subprocess.Popen. Validate against an "
        "allowlist and avoid shell=True.",
        ("security", "command-injection"), 1, arg_indexes=(0,),
    ),
    "subprocess.check_output": SinkSpec(
        "Untrusted input reaches a subprocess call.",
        "A request-derived value reaches subprocess. Validate against an "
        "allowlist and avoid shell=True.",
        ("security", "command-injection"), 1, arg_indexes=(0,),
    ),
    "eval": SinkSpec(
        "Untrusted input passed to eval (code injection).",
        "eval() on request-derived input executes attacker code. Parse the value "
        "explicitly (ast.literal_eval for data) instead.",
        ("security", "code-injection"), 1, arg_indexes=(0,),
    ),
    "exec": SinkSpec(
        "Untrusted input passed to exec (code injection).",
        "exec() on request-derived input executes attacker code. Do not execute "
        "user-supplied code; if you must, isolate it in a real sandbox.",
        ("security", "code-injection"), 1, arg_indexes=(0,),
    ),
    "pickle.loads": SinkSpec(
        "Untrusted input deserialized with pickle (RCE).",
        "pickle.loads() on request-derived bytes is remote code execution. Use "
        "JSON or a schema-validated format across trust boundaries.",
        ("security", "deserialization"), 1, arg_indexes=(0,),
    ),
    "requests.get": SinkSpec(
        "Untrusted input used as a request URL (SSRF).",
        "A request-derived URL reaches an outbound HTTP call; an attacker can "
        "reach internal services (cloud metadata, localhost). Validate the host "
        "against an allowlist and block private/link-local ranges.",
        ("security", "ssrf"), 1, arg_indexes=(0,),
    ),
    "requests.post": SinkSpec(
        "Untrusted input used as a request URL (SSRF).",
        "A request-derived URL reaches an outbound HTTP call. Validate the host "
        "against an allowlist and block internal ranges.",
        ("security", "ssrf"), 1, arg_indexes=(0,),
    ),
    "requests.put": SinkSpec(
        "Untrusted input used as a request URL (SSRF).",
        "A request-derived URL reaches an outbound HTTP call. Validate the host.",
        ("security", "ssrf"), 1, arg_indexes=(0,),
    ),
    "requests.delete": SinkSpec(
        "Untrusted input used as a request URL (SSRF).",
        "A request-derived URL reaches an outbound HTTP call. Validate the host.",
        ("security", "ssrf"), 1, arg_indexes=(0,),
    ),
    "requests.head": SinkSpec(
        "Untrusted input used as a request URL (SSRF).",
        "A request-derived URL reaches an outbound HTTP call. Validate the host.",
        ("security", "ssrf"), 1, arg_indexes=(0,),
    ),
    "requests.request": SinkSpec(
        "Untrusted input used as a request URL (SSRF).",
        "A request-derived URL reaches an outbound HTTP call. Validate the host.",
        ("security", "ssrf"), 1, arg_indexes=(1,),
    ),
    "urlopen": SinkSpec(
        "Untrusted input used as a request URL (SSRF).",
        "A request-derived URL reaches urlopen(); an attacker can reach internal "
        "services or read local files via file://. Validate the scheme and host.",
        ("security", "ssrf"), 1, arg_indexes=(0,),
    ),
    "render_template_string": SinkSpec(
        "Untrusted input rendered as a template (SSTI).",
        "render_template_string() on request-derived input is server-side "
        "template injection, which leads to RCE in Jinja2. Render a fixed "
        "template and pass user data as context variables.",
        ("security", "template-injection"), 1, arg_indexes=(0,),
    ),
    "Path.read_text": SinkSpec(
        "Untrusted input used as a file path.",
        "A request-derived path is read via pathlib; an attacker can read "
        "arbitrary files via traversal. Resolve against a fixed root and reject "
        "paths that escape it.",
        ("security", "path-traversal"), 1, arg_indexes=(), check_receiver=True,
    ),
    "read_text": SinkSpec(
        "Untrusted input used as a file path.",
        "A request-derived path is read via pathlib; validate it stays inside "
        "the intended directory.",
        ("security", "path-traversal"), 1, arg_indexes=(), check_receiver=True,
    ),
    "write_text": SinkSpec(
        "Untrusted input used as a file path (arbitrary write).",
        "A request-derived path is written via pathlib; an attacker can write "
        "outside the intended directory. Resolve against a fixed root first.",
        ("security", "path-traversal"), 1, arg_indexes=(), check_receiver=True,
    ),
    "read_bytes": SinkSpec(
        "Untrusted input used as a file path.",
        "A request-derived path is read via pathlib; validate it stays inside "
        "the intended directory.",
        ("security", "path-traversal"), 1, arg_indexes=(), check_receiver=True,
    ),
    "write_bytes": SinkSpec(
        "Untrusted input used as a file path (arbitrary write).",
        "A request-derived path is written via pathlib; resolve against a fixed "
        "root first.",
        ("security", "path-traversal"), 1, arg_indexes=(), check_receiver=True,
    ),
    "redirect": SinkSpec(
        "Untrusted input used in a redirect (open redirect).",
        "redirect() to a request-derived target lets an attacker send users to "
        "an external phishing site. Redirect only to a fixed allowlist of paths.",
        ("security", "open-redirect"), 2, arg_indexes=(0,),
    ),
    "httpx.get": SinkSpec(
        "Untrusted input used as a request URL (SSRF).",
        "A request-derived URL reaches an outbound HTTP call. Validate the host "
        "against an allowlist and block internal ranges.",
        ("security", "ssrf"), 1, arg_indexes=(0,),
    ),
    "httpx.post": SinkSpec(
        "Untrusted input used as a request URL (SSRF).",
        "A request-derived URL reaches an outbound HTTP call. Validate the host.",
        ("security", "ssrf"), 1, arg_indexes=(0,),
    ),
    "cursor.execute": SinkSpec(
        "SQL query built from untrusted input (SQL injection).",
        "A request-derived value is concatenated/formatted into SQL. Use "
        "parameterized queries (execute(sql, params)) instead of building the "
        "string.",
        ("security", "sql-injection"), 1, arg_indexes=(0,), require_built_string=True,
    ),
    "execute": SinkSpec(
        "SQL query built from untrusted input (SQL injection).",
        "A request-derived value is concatenated/formatted into a SQL string. Use "
        "parameterized queries instead.",
        ("security", "sql-injection"), 1, arg_indexes=(0,), require_built_string=True,
    ),
}

# Calls that pass their tainted argument through (taint propagates to result).
PASSTHROUGH_CALLS = frozenset({
    "str", "bytes", "Path", "PurePath", "open",  # open also a sink; handled first
    "strip", "lstrip", "rstrip", "lower", "upper", "format", "join",
    "resolve", "expanduser", "encode", "decode", "read", "get", "getlist",
    "os.path.join", "os.path.abspath", "os.path.normpath", "posixpath.join",
})

# Calls that neutralize taint (result is trusted).
SANITIZER_CALLS = frozenset({"int", "float", "bool", "len", "hash", "id"})


def _call_name(node: ast.Call) -> str:
    """Best-effort dotted name for a call target, last two components."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        cur: ast.expr | None = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
        parts.reverse()
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return parts[-1] if parts else ""
    return ""


def _attr_root(node: ast.expr) -> str | None:
    """Root Name of an attribute chain, e.g. request.args.get -> 'request'."""
    cur = node
    while isinstance(cur, ast.Attribute):
        cur = cur.value
    if isinstance(cur, ast.Call):
        cur = cur.func
        while isinstance(cur, ast.Attribute):
            cur = cur.value
    return cur.id if isinstance(cur, ast.Name) else None


@dataclass
class _FuncSummary:
    """Interprocedural facts about one module-level function."""

    param_names: list[str]
    # param index -> finding template of the sink that param reaches.
    dangerous_params: dict[int, dict[str, Any]]
    # The return value is untrusted regardless of arguments (reads a source).
    returns_taint: bool = False
    # Param indexes whose taint flows through to the return value.
    param_flows_to_return: frozenset[int] = frozenset()

    def shape(self) -> tuple:
        """Comparable fingerprint for the fixpoint loop."""
        return (
            frozenset(self.dangerous_params),
            self.returns_taint,
            self.param_flows_to_return,
        )


class _FunctionTaint(ast.NodeVisitor):
    """Forward taint pass over one function body."""

    def __init__(
        self,
        tainted_params: set[str],
        summaries: dict[str, _FuncSummary] | None = None,
    ) -> None:
        self.tainted: set[str] = set(tainted_params)
        self.findings: list[dict[str, Any]] = []
        self.summaries = summaries or {}
        self.return_tainted = False

    # --- taint queries -------------------------------------------------
    def is_source(self, node: ast.expr) -> bool:
        """A directly-untrusted expression (request.*, input(), argv)."""
        if isinstance(node, ast.Attribute):
            return _attr_root(node) in REQUEST_ROOTS
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in SOURCE_CALLS or name.split(".")[-1] in SOURCE_CALLS:
                return True
            # request.get_json(), request.args.get(), form.get() on a tainted obj
            if isinstance(node.func, ast.Attribute):
                if _attr_root(node.func) in REQUEST_ROOTS:
                    return True
        if isinstance(node, ast.Subscript):
            return self.is_tainted(node.value)
        return False

    def is_tainted(self, node: ast.expr | None) -> bool:
        if node is None:
            return False
        if self.is_source(node):
            return True
        if isinstance(node, ast.Name):
            return node.id in self.tainted
        if isinstance(node, ast.Attribute):
            return _attr_root(node) in REQUEST_ROOTS or (
                isinstance(node.value, ast.Name) and node.value.id in self.tainted
            )
        if isinstance(node, ast.Subscript):
            return self.is_tainted(node.value)
        # Add/Mod: string concat and %-format. Div: pathlib join
        # (``base / user_path``) — the dominant way an untrusted segment
        # reaches a file sink.
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod, ast.Div)):
            return self.is_tainted(node.left) or self.is_tainted(node.right)
        if isinstance(node, ast.JoinedStr):  # f-string
            return any(
                isinstance(v, ast.FormattedValue) and self.is_tainted(v.value)
                for v in node.values
            )
        if isinstance(node, ast.Call):
            name = _call_name(node)
            short = name.split(".")[-1]
            if name in SANITIZER_CALLS or short in SANITIZER_CALLS:
                return False
            if name in PASSTHROUGH_CALLS or short in PASSTHROUGH_CALLS:
                # ".get(...)" etc. on a tainted receiver, or wrapper(tainted)
                if isinstance(node.func, ast.Attribute) and self.is_tainted(node.func.value):
                    return True
                return any(self.is_tainted(a) for a in node.args)
            # Interprocedural: a local function that reads a source itself, or
            # passes a tainted argument through to its return value.
            summary = self.summaries.get(short)
            if summary is not None:
                if summary.returns_taint:
                    return True
                for index in summary.param_flows_to_return:
                    if index < len(node.args) and self.is_tainted(node.args[index]):
                        return True
        if isinstance(node, ast.IfExp):
            return self.is_tainted(node.body) or self.is_tainted(node.orelse)
        return False

    @staticmethod
    def _is_built_string(node: ast.expr | None) -> bool:
        """f-string, +concat, %-format, or .format() — a constructed string."""
        if isinstance(node, ast.JoinedStr):
            return True
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.attr == "format"
        return False

    # --- visiting ------------------------------------------------------
    def visit_Assign(self, node: ast.Assign) -> None:
        self.generic_visit(node)
        if self.is_tainted(node.value):
            for target in node.targets:
                self._bind(target)
        else:
            for target in node.targets:
                self._unbind(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.generic_visit(node)
        if node.value is not None and self.is_tainted(node.value):
            self._bind(node.target)

    def _bind(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.tainted.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind(elt)

    def _unbind(self, target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            self.tainted.discard(target.id)

    def visit_Return(self, node: ast.Return) -> None:
        self.generic_visit(node)
        if node.value is not None and self.is_tainted(node.value):
            self.return_tainted = True

    def visit_Call(self, node: ast.Call) -> None:
        self.generic_visit(node)
        name = _call_name(node)
        spec = SINKS.get(name) or SINKS.get(name.split(".")[-1])
        if spec is None:
            self._check_wrapper_sink(node, name.split(".")[-1])
            return
        if spec.check_receiver:
            if isinstance(node.func, ast.Attribute) and self.is_tainted(node.func.value):
                self.findings.append(self._finding(spec, node))
            return
        indexes = spec.arg_indexes
        args = node.args
        checked = (
            [args[i] for i in indexes if i < len(args)]
            if indexes is not None
            else list(args)
        )
        for arg in checked:
            if spec.require_built_string and not self._is_built_string(arg):
                continue
            if self.is_tainted(arg):
                self.findings.append(self._finding(spec, node))
                break

    def _check_wrapper_sink(self, node: ast.Call, short: str) -> None:
        """A tainted argument to a local helper whose body reaches a sink."""
        summary = self.summaries.get(short)
        if summary is None or not summary.dangerous_params:
            return
        tainted_index = None
        for index in summary.dangerous_params:
            if index < len(node.args) and self.is_tainted(node.args[index]):
                tainted_index = index
                break
        if tainted_index is None:
            for kw in node.keywords:
                if kw.arg in summary.param_names:
                    index = summary.param_names.index(kw.arg)
                    if index in summary.dangerous_params and self.is_tainted(kw.value):
                        tainted_index = index
                        break
        if tainted_index is None:
            return
        template = summary.dangerous_params[tainted_index]
        self.findings.append({
            **template,
            "details": (
                f"{template['details']} (Interprocedural flow: the untrusted "
                f"value is passed to {short}(), which forwards it to the "
                f"dangerous call at line {template['line']}.)"
            ),
            "tags": [*template["tags"], "interprocedural"],
            "line": node.lineno,
        })

    def _finding(self, spec: SinkSpec, node: ast.Call) -> dict[str, Any]:
        return {
            "title": spec.title,
            "details": spec.detail,
            "severity": spec.severity,
            "confidence": 2,
            "tags": [*spec.tags, "taint", "dataflow"],
            "source": "taint",
            "rule": f"taint-{spec.tags[-1]}",
            "line": node.lineno,
        }


_ROUTE_DECORATORS = frozenset({"route", "get", "post", "put", "delete", "patch"})


def _has_route_decorator(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = ""
        if isinstance(target, ast.Attribute):
            name = target.attr
        elif isinstance(target, ast.Name):
            name = target.id
        if name in _ROUTE_DECORATORS:
            return True
    return False


def _route_handler_params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Path parameters of a Flask/FastAPI-style route handler are untrusted."""
    if not _has_route_decorator(func):
        return set()
    args = func.args
    names = [a.arg for a in (args.posonlyargs + args.args + args.kwonlyargs)]
    return {n for n in names if n not in {"self", "cls"}}


def _positional_params(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    args = func.args
    return [a.arg for a in (args.posonlyargs + args.args)]


def _summarize_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    summaries: dict[str, _FuncSummary],
) -> _FuncSummary:
    """One summary iteration for a function, using the current summaries.

    Parameter indexes in the returned summary are **call-site relative**: the
    implicit ``self``/``cls`` receiver of a method is dropped, so a caller
    like ``obj.method(x)`` maps ``x`` to index 0 regardless of ``self``.
    """
    params = _positional_params(func)
    offset = 1 if params and params[0] in {"self", "cls"} else 0

    def run(tainted: set[str]) -> _FunctionTaint:
        walker = _FunctionTaint(tainted, summaries)
        for stmt in func.body:
            walker.visit(stmt)
        return walker

    base = run(set())
    dangerous: dict[int, dict[str, Any]] = {}
    flows_to_return: set[int] = set()
    for index, name in enumerate(params):
        if name in {"self", "cls"}:
            continue
        call_index = index - offset
        walker = run({name})
        # Findings the *base* run also produces come from sources in the body,
        # not from this parameter — only param-caused findings make it dangerous.
        param_findings = [f for f in walker.findings if f not in base.findings]
        if param_findings:
            dangerous[call_index] = param_findings[0]
        if walker.return_tainted and not base.return_tainted:
            flows_to_return.add(call_index)
    return _FuncSummary(
        param_names=params[offset:],
        dangerous_params=dangerous,
        returns_taint=base.return_tainted,
        param_flows_to_return=frozenset(flows_to_return),
    )


_MAX_FIXPOINT_ROUNDS = 4
MAX_REPO_SUMMARY_FILES = 4000


def _collect_functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _summary_targets(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Functions usable as call targets — route handlers are entry points, not
    helpers, and are excluded so they never collide with same-named helpers."""
    return [f for f in _collect_functions(tree) if not _has_route_decorator(f)]


def _summaries_fixpoint(
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef],
    seed: dict[str, _FuncSummary] | None = None,
) -> dict[str, _FuncSummary]:
    """Fixpoint over function summaries so helper chains (a→b→sink) resolve.

    When two functions share a name but summarize to different shapes, the
    name is dropped from the lookup table — an ambiguous cross-file target is
    never assumed dangerous, keeping false positives rare.
    """
    summaries: dict[str, _FuncSummary] = dict(seed or {})
    computed: dict[str, _FuncSummary] = {}
    ambiguous: set[str] = set()
    for _ in range(_MAX_FIXPOINT_ROUNDS):
        changed = False
        for func in functions:
            summary = _summarize_function(func, summaries)
            name = func.name
            previous = computed.get(name)
            if name in ambiguous:
                continue
            if previous is not None and previous.shape() != summary.shape():
                # Same name, conflicting behavior across definitions → drop it.
                ambiguous.add(name)
                computed.pop(name, None)
                summaries.pop(name, None)
                changed = True
                continue
            if previous is None or previous.shape() != summary.shape():
                changed = True
            computed[name] = summary
            summaries[name] = summary
        if not changed:
            break
    return summaries


def build_repo_summaries(sources: dict[str, str]) -> dict[str, _FuncSummary]:
    """Cross-module summary registry: every function/method in the repo.

    Keyed by bare function/method name (the same key call sites resolve
    through), so a route handler in ``server.py`` that calls
    ``store.read_note(path)`` picks up the summary of ``read_note`` defined in
    ``obsidian.py``. This is what turns a within-file analysis into a
    cross-file one for the common "thin route → manager/helper module" shape.
    """
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for filename, source in list(sources.items())[:MAX_REPO_SUMMARY_FILES]:
        try:
            tree = ast.parse(source, filename=filename)
        except (SyntaxError, ValueError):
            continue
        functions.extend(_summary_targets(tree))
    return _summaries_fixpoint(functions)


def analyze_source(
    source: str,
    filename: str = "<unknown>",
    repo_summaries: dict[str, _FuncSummary] | None = None,
) -> list[dict[str, Any]]:
    """Return taint findings (with 1-based ``line``) for one Python module.

    ``repo_summaries`` (from :func:`build_repo_summaries`) enables cross-module
    detection; without it the analysis is file-local but still interprocedural.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except (SyntaxError, ValueError):
        return []
    summaries = _summaries_fixpoint(_summary_targets(tree), seed=repo_summaries)
    findings: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            walker = _FunctionTaint(_route_handler_params(node), summaries)
            for stmt in node.body:
                walker.visit(stmt)
            findings.extend(walker.findings)
    # One finding per (line, rule): helper chains can rediscover the same flow.
    seen: set[tuple[int, str]] = set()
    unique = []
    for finding in sorted(findings, key=lambda f: f["line"]):
        key = (finding["line"], finding["rule"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique


def _to_issue(finding: dict[str, Any], file: str, source_line: str) -> dict[str, Any]:
    line = finding["line"]
    return {
        "title": finding["title"],
        "details": finding["details"],
        "severity": finding["severity"],
        "confidence": finding["confidence"],
        "tags": [*finding["tags"], "static-analysis"],
        "source": "taint",
        "rule": finding["rule"],
        "affected_lines": [
            {
                "file": file,
                "start_line": line,
                "end_line": line,
                "affected_code": f"{line}: {source_line.strip()}",
            }
        ],
    }


def analyze_repo_changes(
    repo_path: Path,
    mode: str = "working",
    refs: str = "",
    what: str = "",
    against: str = "",
    use_merge_base: bool = True,
    filters: str | list[str] | tuple[str, ...] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Run the taint pass over the changed Python files of a review scope.

    Findings are limited to lines the review actually touched (added lines of
    the diff), so a normal diff review never re-flags untouched code; a
    whole-tree snapshot review sees every line as added and so covers it all.
    """
    diff_text = static_analysis.collect_diff(
        repo_path, mode=mode, refs=refs, what=what,
        against=against, use_merge_base=use_merge_base,
    )
    if not diff_text:
        return {}
    added: dict[str, set[int]] = {}
    for file, line_no, _text in static_analysis.iter_added_lines(diff_text):
        if file and fnmatch(file, "*.py"):
            added.setdefault(file, set()).add(line_no)

    base_ref, target_ref = static_analysis.diff_base_and_target(
        repo_path, mode=mode, refs=refs, what=what,
        against=against, use_merge_base=use_merge_base,
    )

    # Cross-module summaries: read every tracked .py file so a flow whose sink
    # lives in a different module than the changed source is still resolved.
    repo_summaries = _repo_summaries(repo_path)

    changed_sources: dict[str, str] = {}
    for file in added:
        source = static_analysis.git_show_blob(repo_path, target_ref, file) if target_ref else ""
        if not source:
            fs_path = repo_path / file
            try:
                source = fs_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
        changed_sources[file] = source

    issues: dict[str, list[dict[str, Any]]] = {}
    for file, added_lines in added.items():
        source = changed_sources.get(file)
        if not source:
            continue
        lines = source.splitlines()
        for finding in analyze_source(source, file, repo_summaries=repo_summaries):
            if finding["line"] not in added_lines:
                continue
            text = lines[finding["line"] - 1] if finding["line"] <= len(lines) else ""
            issues.setdefault(file, []).append(_to_issue(finding, file, text))
    return issues


def _repo_summaries(repo_path: Path) -> dict[str, _FuncSummary]:
    """Build cross-module function summaries from every tracked .py file."""
    sources: dict[str, str] = {}
    try:
        listing = static_analysis._git_stdout(repo_path, "ls-files", "*.py")
    except Exception:
        listing = ""
    files = [line for line in listing.splitlines() if line] if listing else []
    if not files:
        files = [str(p.relative_to(repo_path)) for p in repo_path.rglob("*.py")]
    for rel in files[:MAX_REPO_SUMMARY_FILES]:
        try:
            sources[rel] = (repo_path / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return build_repo_summaries(sources)


def merge_into_report(report: dict[str, Any], taint_issues: dict[str, list[dict]]) -> int:
    """Merge taint findings into a report, skipping lines already flagged."""
    issues = report.setdefault("issues", {})
    next_id = TAINT_ISSUE_ID_BASE
    added = 0
    for file, findings in taint_issues.items():
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
