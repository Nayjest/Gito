"""Interprocedural taint: helper-wrapped sinks, tainted returns, chains."""
from __future__ import annotations

from codepulse_app import taint_analysis as ta


def rules(findings):
    return [f["rule"] for f in findings]


def test_helper_wrapped_ssrf_is_caught_at_the_call_site():
    src = """
import requests
from flask import request

def fetch(url):
    return requests.get(url, timeout=5)

@app.route("/proxy")
def proxy():
    target = request.args.get("url")
    return fetch(target).text
"""
    findings = ta.analyze_source(src)
    assert "taint-ssrf" in rules(findings)
    hit = next(f for f in findings if f["rule"] == "taint-ssrf")
    assert "interprocedural" in hit["tags"]
    assert "fetch()" in hit["details"]
    # Reported at the handler's call site, not inside the helper.
    assert hit["line"] == 11


def test_source_returning_helper_taints_its_callers():
    src = """
from flask import request

def read_target():
    return request.form["path"]

@app.route("/dl")
def download():
    path = read_target()
    return open(path).read()
"""
    findings = ta.analyze_source(src)
    assert "taint-path-traversal" in rules(findings)


def test_two_level_helper_chain_resolves_via_fixpoint():
    src = """
import subprocess
from flask import request

def run_it(cmd):
    subprocess.run(cmd, shell=True)

def do_task(task):
    run_it(task)

@app.route("/task")
def task_handler():
    do_task(request.args["cmd"])
"""
    findings = ta.analyze_source(src)
    assert "taint-command-injection" in rules(findings)


def test_passthrough_helper_keeps_taint_alive():
    src = """
from flask import request

def normalize(p):
    return p.strip()

@app.route("/read")
def read_file():
    name = normalize(request.args["f"])
    return open(name).read()
"""
    findings = ta.analyze_source(src)
    assert "taint-path-traversal" in rules(findings)


def test_keyword_argument_into_dangerous_helper():
    src = """
import requests
from flask import request

def fetch(url):
    return requests.get(url)

@app.route("/go")
def go():
    return fetch(url=request.args["u"]).text
"""
    findings = ta.analyze_source(src)
    assert "taint-ssrf" in rules(findings)


def test_helper_with_constant_argument_is_not_flagged():
    src = """
import requests

def fetch(url):
    return requests.get(url)

def refresh():
    return fetch("https://api.internal/status")
"""
    assert ta.analyze_source(src) == []


def test_sanitized_argument_into_dangerous_helper_is_clean():
    src = """
import requests
from flask import request

def fetch_page(page):
    return requests.get(f"https://api.example.com/items?page={page}")

@app.route("/items")
def items():
    page = int(request.args.get("page", 1))
    return fetch_page(page).text
"""
    assert ta.analyze_source(src) == []


def test_recursive_helper_terminates():
    src = """
from flask import request

def spin(x):
    return spin(x)

@app.route("/r")
def r():
    return spin(request.args["q"])
"""
    # Must not hang or crash; recursion alone reaches no sink.
    assert ta.analyze_source(src) == []


def test_no_duplicate_findings_for_the_same_flow():
    src = """
import requests
from flask import request

def fetch(url):
    return requests.get(url)

@app.route("/p")
def p():
    return fetch(request.args["u"]).text
"""
    findings = ta.analyze_source(src)
    lines = [(f["line"], f["rule"]) for f in findings]
    assert len(lines) == len(set(lines))


# ── Cross-module (repo-wide) summaries ───────────────────────────────────────

def test_cross_module_path_traversal_via_helper_module():
    # The thin-route -> manager-module shape that a within-file pass misses.
    obsidian = """
from pathlib import Path

class Store:
    def __init__(self, vault):
        self.vault_path = Path(vault)

    def read_note(self, note_path):
        full_path = self.vault_path / note_path
        with open(full_path) as f:
            return f.read()
"""
    server = """
from flask import request

@app.route("/notes/<path:note_path>")
def read_note(note_path):
    store = get_store()
    return store.read_note(note_path)
"""
    summaries = ta.build_repo_summaries({"store.py": obsidian, "server.py": server})
    assert summaries["read_note"].dangerous_params  # helper is dangerous at arg 0
    findings = ta.analyze_source(server, "server.py", repo_summaries=summaries)
    assert "taint-path-traversal" in rules(findings)
    assert any("interprocedural" in f["tags"] for f in findings)


def test_route_handler_names_do_not_shadow_helpers():
    # Route handler and helper share a name; the handler must not be summarized
    # (it is an entry point), so the helper's dangerous summary survives.
    helper = """
def process(path):
    open(path).read()
"""
    server = """
from flask import request

@app.route("/x")
def process():
    return "ok"

@app.route("/y/<path:p>")
def handler(p):
    return process(p)
"""
    summaries = ta.build_repo_summaries({"helper.py": helper, "server.py": server})
    assert 0 in summaries["process"].dangerous_params
    findings = ta.analyze_source(server, "server.py", repo_summaries=summaries)
    assert "taint-path-traversal" in rules(findings)


def test_pathlib_join_propagates_taint():
    src = """
from pathlib import Path
from flask import request

BASE = Path("/data")

@app.route("/f/<path:name>")
def f(name):
    return open(BASE / name).read()
"""
    findings = ta.analyze_source(src)
    assert "taint-path-traversal" in rules(findings)


def test_ambiguous_helper_name_is_not_assumed_dangerous():
    # Two different helpers named `load`: one dangerous, one not → dropped, so
    # an unrelated call site is not flagged.
    mod_a = """
def load(path):
    return open(path).read()
"""
    mod_b = """
def load(cfg):
    return cfg.upper()
"""
    caller = """
from flask import request

@app.route("/c/<path:p>")
def c(p):
    return load(p)
"""
    summaries = ta.build_repo_summaries({"a.py": mod_a, "b.py": mod_b, "c.py": caller})
    findings = ta.analyze_source(caller, "c.py", repo_summaries=summaries)
    assert "taint-path-traversal" not in rules(findings)
