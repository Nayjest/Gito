"""Heuristic JS/TS symbol extraction (release plan Item 1)."""
from __future__ import annotations

from codepulse_app import semantic_js


def test_top_level_functions_arrows_and_exports():
    source = (
        "export function invite(req, res) {\n  return res\n}\n"
        "export const pay = async (amount, currency) => {\n  return amount\n}\n"
        "const fmt = function (value) {\n  return String(value)\n}\n"
        "function internal(x) {\n  return x\n}\n"
    )
    symbols = semantic_js.module_symbols(source)

    assert symbols["invite"] == "req,res"
    assert symbols["pay"] == "amount,currency"
    assert symbols["fmt"] == "value"
    assert symbols["internal"] == "x"


def test_class_methods_are_not_mistaken_for_top_level_functions():
    source = (
        "export class Billing {\n"
        "  refund(accountId, reason) {\n"
        "    return accountId\n"
        "  }\n"
        "  async charge(amount) {\n"
        "    if (amount) {\n"
        "      return amount\n"
        "    }\n"
        "  }\n"
        "}\n"
        "export function outside(a) {\n  return a\n}\n"
    )
    symbols = semantic_js.module_symbols(source)

    assert symbols["refund"] == "accountId,reason"
    assert symbols["charge"] == "amount"
    assert symbols["outside"] == "a"
    # Control-flow keywords inside method bodies never register as symbols.
    assert "if" not in symbols
    assert "return" not in symbols


def test_export_alias_clause_is_followed():
    source = (
        "function original(a, b) {\n  return a\n}\n"
        "export { original as renamed, original }\n"
    )
    symbols = semantic_js.module_symbols(source)

    assert symbols["renamed"] == "a,b"
    assert symbols["original"] == "a,b"


def test_multiline_parameter_lists_are_captured():
    source = (
        "export function configure(\n"
        "  host,\n"
        "  port,\n"
        "  options\n"
        ") {\n"
        "  return host\n"
        "}\n"
    )
    symbols = semantic_js.module_symbols(source)

    assert symbols["configure"] == "host,port,options"


def test_mode_constant_is_surfaced():
    assert semantic_js.SEMANTIC_JS_MODE in {"heuristic", "tree-sitter"}
