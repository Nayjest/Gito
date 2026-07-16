"""AST-level Python symbol analysis for the cross-file engine.

Replaces regex matching of ``def`` lines with real parsing (stdlib ``ast``,
zero dependencies), so the context engine can:

- see every top-level function/class/method in a file, not just the ones whose
  definition line happens to appear in the diff hunks;
- diff signatures structurally instead of textually;
- simulate Python's argument binding at each call site and prove whether a
  changed signature actually breaks the call (``signature_break``).

Callers must tolerate ``SyntaxError`` from any function here (partial edits,
py2 files) and fall back to the textual path.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import NamedTuple

# Parameter kinds mirror Python's binding rules.
POSONLY = "posonly"
POSITIONAL = "positional"
VARARG = "vararg"
KWONLY = "kwonly"
KWARG = "kwarg"


class Param(NamedTuple):
    name: str
    has_default: bool
    kind: str
    annotation: str = ""
    default: str = ""


class CallSite(NamedTuple):
    lineno: int
    n_pos_args: int
    kw_names: tuple[str, ...]
    has_star_args: bool
    has_star_kwargs: bool


@dataclass
class SymbolInfo:
    kind: str  # function | class | method
    params: list[Param] = field(default_factory=list)
    decorators: list[str] = field(default_factory=list)
    lineno: int = 0
    is_exported: bool = True


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - cosmetic only
        return ""


def _params_from_arguments(args: ast.arguments) -> list[Param]:
    params: list[Param] = []
    positional = [*args.posonlyargs, *args.args]
    defaults: list[ast.expr | None] = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    for arg, default in zip(positional, defaults):
        kind = POSONLY if arg in args.posonlyargs else POSITIONAL
        params.append(
            Param(arg.arg, default is not None, kind, _unparse(arg.annotation), _unparse(default))
        )
    if args.vararg:
        params.append(Param(args.vararg.arg, False, VARARG, _unparse(args.vararg.annotation)))
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        params.append(
            Param(arg.arg, default is not None, KWONLY, _unparse(arg.annotation), _unparse(default))
        )
    if args.kwarg:
        params.append(Param(args.kwarg.arg, False, KWARG, _unparse(args.kwarg.annotation)))
    return params


def signature_string(info: SymbolInfo) -> str:
    """Normalized parameter list, compatible with the old whitespace-stripped
    textual form for simple signatures (``"account,amount=1"``)."""
    parts: list[str] = []
    posonly_pending = False
    seen_star = False
    for param in info.params:
        text = param.name
        if param.annotation:
            text += f":{param.annotation}"
        if param.has_default:
            text += f"={param.default}"
        if param.kind == POSONLY:
            posonly_pending = True
        elif posonly_pending:
            parts.append("/")
            posonly_pending = False
        if param.kind == VARARG:
            text = f"*{text}"
            seen_star = True
        elif param.kind == KWONLY and not seen_star:
            parts.append("*")
            seen_star = True
        elif param.kind == KWARG:
            text = f"**{text}"
        parts.append(text)
    if posonly_pending:
        parts.append("/")
    return "".join(",".join(parts).split())


def _dunder_all(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
            continue
        value = node.value
        if isinstance(value, (ast.List, ast.Tuple)):
            for element in value.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    names.add(element.value)
    return names


def module_symbols(source: str) -> dict[str, SymbolInfo]:
    """Top-level functions/classes plus class methods, keyed by bare name.

    Methods register under their bare name only when it does not shadow a
    top-level symbol — mirroring how dependents reference them (``obj.meth()``
    call sites match on the attribute name).
    """
    tree = ast.parse(source)
    exported_all = _dunder_all(tree)
    symbols: dict[str, SymbolInfo] = {}

    def is_exported(name: str) -> bool:
        return not name.startswith("_") or name in exported_all

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols[node.name] = SymbolInfo(
                kind="function",
                params=_params_from_arguments(node.args),
                decorators=[_unparse(dec) for dec in node.decorator_list],
                lineno=node.lineno,
                is_exported=is_exported(node.name),
            )
        elif isinstance(node, ast.ClassDef):
            symbols[node.name] = SymbolInfo(
                kind="class",
                decorators=[_unparse(dec) for dec in node.decorator_list],
                lineno=node.lineno,
                is_exported=is_exported(node.name),
            )
            for member in node.body:
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                params = _params_from_arguments(member.args)
                if params and params[0].name in {"self", "cls"} and not any(
                    "staticmethod" in dec for dec in (_unparse(d) for d in member.decorator_list)
                ):
                    params = params[1:]  # callers never pass self/cls
                symbols.setdefault(
                    member.name,
                    SymbolInfo(
                        kind="method",
                        params=params,
                        decorators=[_unparse(dec) for dec in member.decorator_list],
                        lineno=member.lineno,
                        is_exported=is_exported(member.name) and is_exported(node.name),
                    ),
                )
    return symbols


def call_sites(source: str, symbol: str) -> list[CallSite]:
    """Every call of ``symbol`` (as a bare name or attribute) in ``source``."""
    tree = ast.parse(source)
    sites: list[CallSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = ""
        if isinstance(callee, ast.Name):
            name = callee.id
        elif isinstance(callee, ast.Attribute):
            name = callee.attr
        if name != symbol:
            continue
        sites.append(
            CallSite(
                lineno=node.lineno,
                n_pos_args=sum(1 for arg in node.args if not isinstance(arg, ast.Starred)),
                kw_names=tuple(kw.arg for kw in node.keywords if kw.arg),
                has_star_args=any(isinstance(arg, ast.Starred) for arg in node.args),
                has_star_kwargs=any(kw.arg is None for kw in node.keywords),
            )
        )
    return sites


def bind_error(info: SymbolInfo, call: CallSite) -> str | None:
    """Simulate Python's argument binding; return a human reason on failure."""
    if call.has_star_args or call.has_star_kwargs:
        return None  # unpacking makes the call unverifiable — assume it binds
    positional = [p for p in info.params if p.kind in (POSONLY, POSITIONAL)]
    has_vararg = any(p.kind == VARARG for p in info.params)
    has_kwarg = any(p.kind == KWARG for p in info.params)

    if call.n_pos_args > len(positional) and not has_vararg:
        return (
            f"call passes {call.n_pos_args} positional argument(s) but the new "
            f"signature accepts only {len(positional)}"
        )
    filled = {p.name for p in positional[: call.n_pos_args]}

    by_name = {p.name: p for p in info.params if p.kind in (POSITIONAL, KWONLY)}
    for kw in call.kw_names:
        param = by_name.get(kw)
        if param is None:
            if any(p.name == kw and p.kind == POSONLY for p in info.params):
                return f"'{kw}' is positional-only in the new signature"
            if not has_kwarg:
                return f"keyword '{kw}' removed"
            continue
        if kw in filled:
            return f"argument '{kw}' passed both positionally and by keyword"
        filled.add(kw)

    for param in info.params:
        if param.kind in (VARARG, KWARG) or param.has_default:
            continue
        if param.name not in filled:
            return f"new required parameter '{param.name}' not passed"
    return None


def signature_break(
    before: SymbolInfo | None, after: SymbolInfo, call: CallSite
) -> str | None:
    """Reason the call breaks against ``after``, or None if it still binds.

    A call that was already broken against ``before`` is not this change's
    doing and reports None.
    """
    if call.has_star_args or call.has_star_kwargs:
        return None
    if before is not None and bind_error(before, call):
        return None
    return bind_error(after, call)
