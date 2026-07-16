"""Heuristic JS/TS symbol extraction for the cross-file engine.

Deliberately *not* a full parser (R7: no new hard dependencies). The default
path is a tolerant line tokenizer that improves on the old one-line regexes:

- tracks brace depth, so exported class methods are attributed to the class
  instead of being mistaken for top-level functions;
- follows ``export { a as b }`` aliases;
- captures parameter lists that span multiple lines.

If ``tree_sitter_languages`` happens to be installed, real grammars are used
instead; every tree-sitter failure silently falls back to the heuristic so a
broken grammar can never fail a review. The active mode is surfaced in
``/api/health`` as ``engines.semanticJs``.
"""

from __future__ import annotations

import re

try:  # optional dependency (R7); feature-detected once at import
    import tree_sitter_languages  # type: ignore

    SEMANTIC_JS_MODE = "tree-sitter"
except ImportError:  # pragma: no cover - depends on the environment
    tree_sitter_languages = None
    SEMANTIC_JS_MODE = "heuristic"

FUNC_RE = re.compile(
    r"^\s*(?P<export>export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*(?P<name>\w+)\s*\("
)
ARROW_RE = re.compile(
    r"^\s*(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*(?:async\s*)?\("
)
FUNC_EXPR_RE = re.compile(
    r"^\s*(?P<export>export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*(?:async\s+)?function\s*\*?\s*\w*\s*\("
)
CLASS_RE = re.compile(r"^\s*(?:export\s+(?:default\s+)?)?(?:abstract\s+)?class\s+(?P<name>\w+)")
METHOD_RE = re.compile(
    r"^\s*(?:public\s+|private\s+|protected\s+|static\s+|readonly\s+|override\s+|async\s+|get\s+|set\s+)*"
    r"(?P<name>\w+)\s*\("
)
EXPORT_CLAUSE_RE = re.compile(r"^\s*export\s*\{(?P<names>[^}]*)\}")
METHOD_KEYWORD_BLOCKLIST = {
    "if", "for", "while", "switch", "catch", "return", "function", "constructor", "super", "new", "typeof", "await",
}


def _capture_params(lines: list[str], index: int, column: int) -> str:
    """Text between the opening paren at (index, column) and its match."""
    depth = 0
    collected: list[str] = []
    for row in range(index, min(index + 6, len(lines))):
        text = lines[row][column if row == index else 0:]
        for pos, char in enumerate(text):
            if char == "(":
                depth += 1
                if depth == 1:
                    continue
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return "".join(collected)
            if depth >= 1:
                collected.append(char)
        collected.append(" ")
    return "".join(collected)


def _normalize(params: str) -> str:
    return "".join(params.split())


def _heuristic_symbols(source: str) -> dict[str, str]:
    lines = source.splitlines()
    symbols: dict[str, str] = {}
    aliases: list[tuple[str, str]] = []
    depth = 0
    class_stack: list[int] = []  # depth *inside* each open class body

    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("//", "*", "/*")):
            depth += line.count("{") - line.count("}")
            continue

        clause = EXPORT_CLAUSE_RE.match(line)
        if clause:
            for piece in clause.group("names").split(","):
                piece = piece.strip()
                if not piece:
                    continue
                if " as " in piece:
                    original, alias = (part.strip() for part in piece.split(" as ", 1))
                    aliases.append((alias, original))
                else:
                    aliases.append((piece, piece))

        in_class = bool(class_stack) and depth == class_stack[-1]
        matched = None
        if depth == 0:
            matched = FUNC_RE.match(line) or FUNC_EXPR_RE.match(line) or ARROW_RE.match(line)
            if matched:
                paren = line.index("(", matched.start("name"))
                symbols[matched.group("name")] = _normalize(_capture_params(lines, index, paren))
            elif CLASS_RE.match(line):
                pass  # class body tracked via depth below
        elif in_class:
            method = METHOD_RE.match(line)
            if (
                method
                and method.group("name") not in METHOD_KEYWORD_BLOCKLIST
                and "{" in line[method.end() - 1:]
            ):
                paren = line.index("(", method.start("name"))
                symbols.setdefault(
                    method.group("name"), _normalize(_capture_params(lines, index, paren))
                )

        opened = line.count("{") - line.count("}")
        if depth == 0 and CLASS_RE.match(line) and opened > 0:
            class_stack.append(depth + 1)
        depth += opened
        while class_stack and depth < class_stack[-1]:
            class_stack.pop()

    for alias, original in aliases:
        if alias not in symbols:
            symbols[alias] = symbols.get(original, "")
    return symbols


def _tree_sitter_symbols(source: str) -> dict[str, str]:  # pragma: no cover - optional path
    parser = tree_sitter_languages.get_parser("javascript")
    tree = parser.parse(source.encode("utf-8"))
    raw = source.encode("utf-8")
    symbols: dict[str, str] = {}

    def text(node) -> str:
        return raw[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")

    def visit(node) -> None:
        if node.type in {"function_declaration", "generator_function_declaration"}:
            name = node.child_by_field_name("name")
            params = node.child_by_field_name("parameters")
            if name is not None:
                symbols[text(name)] = _normalize(text(params).strip("()") if params else "")
        elif node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            value = node.child_by_field_name("value")
            if name is not None and value is not None and value.type in {"arrow_function", "function"}:
                params = value.child_by_field_name("parameters")
                symbols[text(name)] = _normalize(text(params).strip("()") if params else "")
        elif node.type == "method_definition":
            name = node.child_by_field_name("name")
            params = node.child_by_field_name("parameters")
            if name is not None and text(name) != "constructor":
                symbols.setdefault(text(name), _normalize(text(params).strip("()") if params else ""))
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    # Alias exports are cheaper via the heuristic scanner even in this mode.
    for alias, original in (
        (a, o) for line in source.splitlines()
        for match in [EXPORT_CLAUSE_RE.match(line)] if match
        for piece in match.group("names").split(",") if piece.strip()
        for a, o in [
            tuple(part.strip() for part in piece.split(" as ", 1))
            if " as " in piece else (piece.strip(), piece.strip())
        ]
    ):
        if alias not in symbols:
            symbols[alias] = symbols.get(original, "")
    return symbols


def module_symbols(source: str) -> dict[str, str]:
    """Symbol name → normalized parameter string for a JS/TS module."""
    if SEMANTIC_JS_MODE == "tree-sitter":
        try:
            return _tree_sitter_symbols(source)
        except Exception:  # noqa: BLE001 - grammar problems must never fail a review
            pass
    return _heuristic_symbols(source)
