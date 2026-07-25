"""Deep Scan mode: max coverage + LLM reasoning + deterministic depth."""
from __future__ import annotations

import tomllib
from pathlib import Path

from codepulse_app import server, static_analysis as sa, taint_analysis as ta


# --- deep_scan_requested / apply_deep_scan_defaults ---------------------

def test_deep_scan_requested_coercion():
    assert server.deep_scan_requested({"deepScan": True})
    assert server.deep_scan_requested({"deepScan": "true"})
    assert server.deep_scan_requested({"deepScan": 1})
    assert not server.deep_scan_requested({"deepScan": False})
    assert not server.deep_scan_requested({"deepScan": "false"})
    assert not server.deep_scan_requested({})  # default off


def test_apply_deep_scan_defaults_maxes_levers():
    out = server.apply_deep_scan_defaults({"deepScan": True})
    assert out["scopeGate"] is False          # nothing skipped from the LLM
    assert out["verifyFindings"] is True       # skeptical second pass forced on
    for engine in ("staticAnalysis", "crossFileAnalysis", "taintAnalysis", "dependencyScan"):
        assert out[engine] is True
    assert out["filters"] == server.DEEP_FILTERS
    # Broadened beyond the JS/TS/Python default to more languages.
    assert "*.go" in out["filters"] and "*.rb" in out["filters"]


def test_apply_deep_scan_respects_explicit_filter():
    out = server.apply_deep_scan_defaults({"deepScan": True, "filters": "*.py"})
    assert out["filters"] == "*.py"  # a deliberately narrow scope is kept


def test_apply_deep_scan_noop_when_off():
    payload = {"deepScan": False, "filters": "*.py"}
    assert server.apply_deep_scan_defaults(payload) is payload  # unchanged object


def test_subprocess_env_selects_deep_profile(monkeypatch):
    monkeypatch.setenv("ZYLOO_API_KEY", "sk-zy-x")
    deep_env = server.subprocess_env({"provider": "zyloo", "deepScan": True})
    assert deep_env["GITO_EXTRA_PROJECT_CONFIG"].endswith("review_profile.deep.toml")
    normal_env = server.subprocess_env({"provider": "zyloo"})
    assert normal_env["GITO_EXTRA_PROJECT_CONFIG"].endswith("review_profile.toml")


def test_deep_profile_is_valid_and_bigger_budget():
    conf = tomllib.loads(server.REVIEW_PROFILE_DEEP.read_text(encoding="utf-8"))
    assert conf["max_code_tokens"] >= 96000       # whole large files fit
    assert conf["retries"] >= 5
    assert "DEEP SCAN" in conf["prompt_vars"]["self_id"]


# --- deterministic depth: interprocedural taint -------------------------

def _chain(n: int) -> str:
    lines = ["import os", "@app.route('/x')", "def handler(request):",
             "    v = request.args.get('v')", "    f1(v)"]
    for i in range(1, n):
        lines += [f"def f{i}(a):", f"    f{i + 1}(a)"]
    lines += [f"def f{n}(a):", "    os.system(a)"]
    return "\n".join(lines)


def test_short_helper_chain_resolves_in_standard_mode():
    # The fixpoint fix lets multi-hop forwarder chains resolve at standard depth.
    assert "taint-command-injection" in [f["rule"] for f in ta.analyze_source(_chain(4))]


def test_deep_reaches_longer_helper_chains_than_standard():
    src = _chain(6)  # beyond the standard fixpoint depth, within deep's
    std = [f["rule"] for f in ta.analyze_source(src, deep=False)]
    deep = [f["rule"] for f in ta.analyze_source(src, deep=True)]
    assert "taint-command-injection" not in std
    assert "taint-command-injection" in deep


def test_ambiguous_same_name_helper_is_never_flagged():
    # Two different functions named 'handle' disagree -> the target is ambiguous
    # and a tainted call to it must NOT be flagged (false-positive guard).
    src = (
        "import os\n"
        "@app.route('/x')\n"
        "def r(request):\n"
        "    v = request.args.get('v')\n"
        "    handle(v)\n"
        "def handle(a):\n"
        "    os.system(a)\n"
        "def handle(a):\n"
        "    return len(a)\n"
    )
    assert ta.analyze_source(src) == []


# --- new taint sinks ----------------------------------------------------

def _rules(src: str) -> list[str]:
    return [f["rule"] for f in ta.analyze_source(src)]


def test_new_deserialization_sinks():
    assert "taint-deserialization" in _rules(
        "import dill\ndef h(request):\n    dill.loads(request.data)\n"
    )
    assert "taint-deserialization" in _rules(
        "import jsonpickle\ndef h(request):\n    jsonpickle.decode(request.data)\n"
    )


def test_new_process_exec_sinks():
    assert "taint-command-injection" in _rules(
        "import os\ndef h(request):\n    os.execv(request.args.get('p'), [])\n"
    )
    assert "taint-command-injection" in _rules(
        "import pty\ndef h(request):\n    pty.spawn(request.args.get('c'))\n"
    )


def test_new_sinks_quiet_on_constants():
    assert ta.analyze_source(
        "import os\ndef h():\n    os.execv('/bin/ls', [])\n    os.rmdir('/tmp/x')\n"
    ) == []


# --- new static rules ---------------------------------------------------

def _static(src: str, name: str = "f.py") -> set[str]:
    diff = f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n@@ -0,0 +1,50 @@\n"
    diff += "".join(f"+{line}\n" for line in src.splitlines())
    out = sa.analyze_diff(diff)
    return {i["rule"] if "rule" in i else i.get("id", "") for items in out.values() for i in items}


def test_yaml_unsafe_load_only_without_safe_loader():
    assert "yaml-unsafe-load" in _static("data = yaml.load(raw)")
    assert "yaml-unsafe-load" not in _static("data = yaml.load(raw, Loader=yaml.SafeLoader)")


def test_new_static_python_rules_fire():
    assert "archive-extractall" in _static("tar.extractall(dest)")
    assert "pickle-load-untyped" in _static("obj = pickle.loads(blob)")
    assert "django-debug-true" in _static("DEBUG = True")
    assert "django-allowed-hosts-wildcard" in _static("ALLOWED_HOSTS = ['*']")
    assert "jwt-none-algorithm" in _static("jwt.decode(t, algorithms=['none'])")
    assert "debug-breakpoint-left" in _static("    breakpoint()")


def test_new_static_js_rules_fire():
    assert "react-dangerous-html" in _static("<div dangerouslySetInnerHTML={{__html: x}} />", "c.jsx")
    assert "js-document-write" in _static("document.write(userInput)", "c.js")


# --- deep caps ----------------------------------------------------------

def test_deep_raises_per_rule_cap(tmp_path):
    # 12 lines each tripping the same rule: standard caps at 5/rule, deep keeps all.
    lines = [f"tar{i}.extractall(d{i})" for i in range(12)]
    body = "".join(f"+{ln}\n" for ln in lines)
    diff = ("diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
            f"@@ -0,0 +1,{len(lines)} @@\n" + body)
    std = sa.analyze_diff(diff)["f.py"]
    deep = sa.analyze_diff(
        diff,
        max_total=sa.DEEP_MAX_TOTAL_FINDINGS,
        max_per_rule=sa.DEEP_MAX_FINDINGS_PER_RULE_PER_FILE,
    )["f.py"]
    assert len(std) == sa.MAX_FINDINGS_PER_RULE_PER_FILE      # 5
    assert len(deep) == 12                                    # nothing dropped
