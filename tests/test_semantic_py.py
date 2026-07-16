"""AST symbol extraction and argument-binding simulation (release plan Item 1)."""
from __future__ import annotations

import pytest

from code_doctor_app import semantic_py
from code_doctor_app.semantic_py import CallSite


def _sym(source: str, name: str) -> semantic_py.SymbolInfo:
    return semantic_py.module_symbols(source)[name]


def _call(n_pos: int = 0, kw: tuple[str, ...] = (), star: bool = False, star_kw: bool = False) -> CallSite:
    return CallSite(lineno=1, n_pos_args=n_pos, kw_names=kw, has_star_args=star, has_star_kwargs=star_kw)


# ── module_symbols ───────────────────────────────────────────────────────────


def test_module_symbols_functions_classes_and_methods():
    source = (
        "def charge(account, amount=1):\n    pass\n\n"
        "class Billing:\n"
        "    def refund(self, account_id):\n        pass\n"
        "    @staticmethod\n"
        "    def rate(currency):\n        pass\n"
    )
    symbols = semantic_py.module_symbols(source)

    assert symbols["charge"].kind == "function"
    assert [p.name for p in symbols["charge"].params] == ["account", "amount"]
    assert symbols["charge"].params[1].has_default is True
    assert symbols["Billing"].kind == "class"
    # self is stripped from methods; staticmethod params kept whole.
    assert [p.name for p in symbols["refund"].params] == ["account_id"]
    assert symbols["refund"].kind == "method"
    assert [p.name for p in symbols["rate"].params] == ["currency"]


def test_module_symbols_decorated_defs_and_export_flags():
    source = (
        "__all__ = ['_hidden_but_exported']\n\n"
        "import functools\n\n"
        "@functools.lru_cache(maxsize=None)\n"
        "def cached(key):\n    pass\n\n"
        "def _private():\n    pass\n\n"
        "def _hidden_but_exported():\n    pass\n"
    )
    symbols = semantic_py.module_symbols(source)

    assert symbols["cached"].decorators == ["functools.lru_cache(maxsize=None)"]
    assert symbols["cached"].is_exported is True
    assert symbols["_private"].is_exported is False
    assert symbols["_hidden_but_exported"].is_exported is True


def test_module_symbols_raises_on_broken_source():
    with pytest.raises(SyntaxError):
        semantic_py.module_symbols("def broken(:\n")


def test_signature_string_matches_textual_normalization():
    info = _sym("def charge(account, amount=1):\n    pass\n", "charge")
    assert semantic_py.signature_string(info) == "account,amount=1"

    info = _sym("def f(a, /, b, *args, c, d=2, **kw):\n    pass\n", "f")
    assert semantic_py.signature_string(info) == "a,/,b,*args,c,d=2,**kw"


# ── call_sites ───────────────────────────────────────────────────────────────


def test_call_sites_matches_names_attributes_and_ignores_strings():
    source = (
        "from pkg import billing\n"
        "def handler():\n"
        "    charge('a', 1)\n"
        "    billing.charge('b', amount=2)\n"
        "    print('call charge(x) inside a string')\n"
        "    # charge('commented', 0)\n"
        "    label = 'charge'\n"
    )
    sites = semantic_py.call_sites(source, "charge")

    assert len(sites) == 2  # string/comment mentions never match
    assert sites[0].n_pos_args == 2
    assert sites[1].n_pos_args == 1
    assert sites[1].kw_names == ("amount",)


def test_call_sites_flags_star_args():
    sites = semantic_py.call_sites("charge(*args, **kwargs)\n", "charge")
    assert sites[0].has_star_args is True
    assert sites[0].has_star_kwargs is True


# ── bind_error / signature_break ─────────────────────────────────────────────


def test_binding_new_required_parameter():
    after = _sym("def charge(account, amount, currency):\n    pass\n", "charge")
    assert semantic_py.bind_error(after, _call(n_pos=2)) == "new required parameter 'currency' not passed"
    assert semantic_py.bind_error(after, _call(n_pos=3)) is None
    assert semantic_py.bind_error(after, _call(n_pos=2, kw=("currency",))) is None


def test_binding_defaults_satisfy_missing_args():
    after = _sym("def charge(account, amount, currency='usd'):\n    pass\n", "charge")
    assert semantic_py.bind_error(after, _call(n_pos=2)) is None


def test_binding_removed_keyword():
    after = _sym("def charge(account):\n    pass\n", "charge")
    assert semantic_py.bind_error(after, _call(n_pos=1, kw=("amount",))) == "keyword 'amount' removed"


def test_binding_too_many_positionals_and_varargs_absorb():
    after = _sym("def charge(account):\n    pass\n", "charge")
    assert "positional" in semantic_py.bind_error(after, _call(n_pos=3))

    with_varargs = _sym("def charge(account, *rest):\n    pass\n", "charge")
    assert semantic_py.bind_error(with_varargs, _call(n_pos=3)) is None


def test_binding_kwonly_and_kwargs():
    after = _sym("def charge(account, *, currency):\n    pass\n", "charge")
    assert semantic_py.bind_error(after, _call(n_pos=1)) == "new required parameter 'currency' not passed"
    assert semantic_py.bind_error(after, _call(n_pos=1, kw=("currency",))) is None
    # Positional overflow cannot fill a kw-only slot.
    assert semantic_py.bind_error(after, _call(n_pos=2)) is not None

    absorbing = _sym("def charge(account, **extra):\n    pass\n", "charge")
    assert semantic_py.bind_error(absorbing, _call(n_pos=1, kw=("anything",))) is None


def test_binding_positional_only_rejects_keyword():
    after = _sym("def charge(account, /):\n    pass\n", "charge")
    assert semantic_py.bind_error(after, _call(kw=("account",))) == "'account' is positional-only in the new signature"


def test_binding_duplicate_positional_and_keyword():
    after = _sym("def charge(account, amount):\n    pass\n", "charge")
    assert "both positionally and by keyword" in semantic_py.bind_error(after, _call(n_pos=2, kw=("amount",)))


def test_signature_break_skips_calls_broken_before_the_change():
    before = _sym("def charge(account, amount):\n    pass\n", "charge")
    after = _sym("def charge(account, amount, currency):\n    pass\n", "charge")

    # Call was already invalid against the old signature → not this diff's fault.
    assert semantic_py.signature_break(before, after, _call(n_pos=5)) is None
    # Valid before, broken after → reported.
    assert semantic_py.signature_break(before, after, _call(n_pos=2)) == (
        "new required parameter 'currency' not passed"
    )
    # Star-args calls are unverifiable → never reported.
    assert semantic_py.signature_break(before, after, _call(n_pos=1, star=True)) is None
