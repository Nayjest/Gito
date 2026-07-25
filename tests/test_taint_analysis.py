"""AST taint / dataflow analysis (Direction 1)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from codepulse_app import snapshot, taint_analysis as ta


def rules(src: str) -> list[str]:
    return [f["rule"] for f in ta.analyze_source(src)]


# --- sources reach sinks ------------------------------------------------

def test_ssrf_request_url_to_requests_get():
    src = (
        "import requests\n"
        "def h(request):\n"
        "    url = request.args.get('url')\n"
        "    return requests.get(url)\n"
    )
    assert "taint-ssrf" in rules(src)


def test_path_traversal_route_param_to_send_file():
    src = (
        "@app.route('/dl/<path:fp>')\n"
        "def dl(fp):\n"
        "    return send_file(fp)\n"
    )
    assert "taint-path-traversal" in rules(src)


def test_command_injection_through_fstring():
    src = (
        "import os\n"
        "def h(request):\n"
        "    name = request.args.get('name')\n"
        "    os.system(f'echo {name}')\n"
    )
    assert "taint-command-injection" in rules(src)


def test_sql_injection_only_on_built_string():
    concat = (
        "def h(request, cur):\n"
        "    n = request.args.get('n')\n"
        "    cur.execute('select * from t where n=' + n)\n"
    )
    assert "taint-sql-injection" in rules(concat)
    param = (
        "def h(request, cur):\n"
        "    n = request.args.get('n')\n"
        "    cur.execute('select * from t where n=?', (n,))\n"
    )
    assert "taint-sql-injection" not in rules(param)


def test_code_injection_eval_and_pickle():
    assert "taint-code-injection" in rules(
        "def h(request):\n    eval(request.args.get('x'))\n"
    )
    assert "taint-deserialization" in rules(
        "import pickle\ndef h(request):\n    pickle.loads(request.data)\n"
    )


def test_pathlib_read_text_on_tainted_receiver():
    src = (
        "from pathlib import Path\n"
        "def h(request):\n"
        "    p = Path(request.args.get('f'))\n"
        "    return p.read_text()\n"
    )
    assert "taint-path-traversal" in rules(src)


def test_open_redirect():
    src = (
        "def h(request):\n"
        "    target = request.args.get('next')\n"
        "    return redirect(target)\n"
    )
    assert "taint-open-redirect" in rules(src)


# --- must NOT flag (precision) -----------------------------------------

def test_constant_path_is_not_flagged():
    assert rules("def h():\n    open('/etc/config.ini')\n") == []


def test_non_route_parameter_is_not_a_source():
    # A plain function param is not untrusted; only route handlers taint params.
    assert rules("def helper(fp):\n    open(fp)\n") == []


def test_sanitized_value_is_not_flagged():
    src = (
        "import requests\n"
        "def h(request):\n"
        "    n = int(request.args.get('n'))\n"
        "    requests.get(n)\n"
    )
    assert rules(src) == []


def test_reassignment_clears_taint():
    src = (
        "def h(request):\n"
        "    x = request.args.get('x')\n"
        "    x = 'safe-constant'\n"
        "    open(x)\n"
    )
    assert rules(src) == []


def test_syntax_error_returns_empty():
    assert ta.analyze_source("def (: this is not python") == []


# --- repo integration ---------------------------------------------------

def test_analyze_repo_changes_limits_to_added_lines(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "svc.py").write_text(
        "import requests\n"
        "def h(request):\n"
        "    u = request.args.get('u')\n"
        "    requests.get(u)\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        check=True,
    )
    # Whole-tree (snapshot-style) review sees every line as added.
    issues = ta.analyze_repo_changes(
        repo, mode="refs", refs=snapshot.SNAPSHOT_REFS, use_merge_base=False
    )
    assert "svc.py" in issues
    assert issues["svc.py"][0]["source"] == "taint"
    assert issues["svc.py"][0]["affected_lines"][0]["start_line"] == 4


def test_merge_into_report_assigns_taint_id_range_and_skips_covered():
    report = {"issues": {"svc.py": [
        {"id": 1, "title": "LLM finding", "affected_lines": [{"start_line": 4, "end_line": 4}]}
    ]}, "total_issues": 1}
    taint = {"svc.py": [
        {"title": "SSRF", "affected_lines": [{"start_line": 4, "end_line": 4}], "tags": []},
        {"title": "traversal", "affected_lines": [{"start_line": 9, "end_line": 9}], "tags": []},
    ]}
    added = ta.merge_into_report(report, taint)
    assert added == 1  # line 4 already covered by the LLM finding (corroborated, not added)
    new = [i for i in report["issues"]["svc.py"] if i.get("id", 0) >= ta.TAINT_ISSUE_ID_BASE]
    assert len(new) == 1 and new[0]["affected_lines"][0]["start_line"] == 9
    # The overlapping LLM finding is corroborated by the taint rule rather than
    # the taint finding being silently dropped.
    llm = report["issues"]["svc.py"][0]
    assert llm["id"] == 1
    assert (llm.get("corroborated_by") or [])  # non-empty corroboration list
