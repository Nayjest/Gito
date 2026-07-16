from __future__ import annotations

import subprocess
from pathlib import Path

from code_doctor_app import static_analysis as sa


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _make_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _git(path, "config", "user.email", "code-doctor@example.local")
    _git(path, "config", "user.name", "Code Doctor")
    return path


def make_diff(path: str, added_lines: list[str], start: int = 1) -> str:
    body = "\n".join("+" + line for line in added_lines)
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        f"@@ -0,0 +{start},{len(added_lines)} @@\n"
        f"{body}\n"
    )


def issues_for(diff: str, path: str) -> list[dict]:
    return sa.analyze_diff(diff).get(path, [])


def rule_ids(findings: list[dict]) -> set[str]:
    return {finding["rule"] for finding in findings}


def test_detects_and_masks_aws_key():
    diff = make_diff("app/config.py", ['ACCESS = "AKIAZXCVBNMASDFGHJKQ"'], start=12)
    findings = issues_for(diff, "app/config.py")

    assert rule_ids(findings) == {"aws-access-key"}
    finding = findings[0]
    assert finding["severity"] == 1
    assert finding["affected_lines"][0]["start_line"] == 12
    code = finding["affected_lines"][0]["affected_code"]
    assert "AKIAZXCVBNMASDFGHJKQ" not in code  # secret is masked in evidence
    assert "AKIA" in code


def test_skips_placeholder_env_values_but_flags_live_looking_keys():
    safe = make_diff(".env.example", ["PAYMENT_KEY=replace-with-local-test-key"])
    risky = make_diff(".env.example", ["PAYMENT_KEY=pk_live_123456789"])

    assert issues_for(safe, ".env.example") == []
    assert "stripe-publishable-live-key" in rule_ids(issues_for(risky, ".env.example"))


def test_python_danger_rules_report_correct_lines():
    diff = make_diff(
        "svc/run.py",
        [
            "import subprocess",
            "subprocess.run(cmd, shell=True)",
            "try:",
            "    pass",
            "except:",
        ],
        start=10,
    )
    findings = issues_for(diff, "svc/run.py")
    by_rule = {finding["rule"]: finding for finding in findings}

    assert by_rule["subprocess-shell-true"]["affected_lines"][0]["start_line"] == 11
    assert by_rule["bare-except"]["affected_lines"][0]["start_line"] == 14


def test_flags_flask_debug_and_bind_all_interfaces():
    diff = make_diff(
        "svc/server.py",
        ['app.run(host="0.0.0.0", port=3000, debug=True)'],
        start=100,
    )
    findings = issues_for(diff, "svc/server.py")
    by_rule = {finding["rule"]: finding for finding in findings}
    assert by_rule["flask-debug-true"]["affected_lines"][0]["start_line"] == 100
    assert by_rule["bind-all-interfaces"]["affected_lines"][0]["start_line"] == 100
    # debug bound to a variable/env flag must NOT trip the rule.
    safe = make_diff("svc/server.py", ["app.run(debug=settings.DEBUG)"])
    assert "flask-debug-true" not in rule_ids(issues_for(safe, "svc/server.py"))


def test_flags_wildcard_cors_in_python_and_js():
    py = make_diff("svc/api.py", ['CORS(app, resources={r"/*": {"origins": "*"}})'])
    assert "cors-wildcard-origin" in rule_ids(issues_for(py, "svc/api.py"))
    js = make_diff("web/server.js", ['app.use(cors({ origin: "*" }))'])
    assert "cors-wildcard-origin" in rule_ids(issues_for(js, "web/server.js"))
    # A specific allowlisted origin must not trip it.
    ok = make_diff("svc/api.py", ['CORS(app, origins="https://app.example.com")'])
    assert "cors-wildcard-origin" not in rule_ids(issues_for(ok, "svc/api.py"))


def test_python_rules_do_not_apply_to_other_languages():
    diff = make_diff("notes.md", ["subprocess.run(cmd, shell=True)", "except:"])
    assert issues_for(diff, "notes.md") == []


def test_js_rules_flag_xss_and_debug_leftovers():
    diff = make_diff(
        "web/app.ts",
        ["el.innerHTML = userInput", "debugger;", "console.log(token)"],
    )
    ids = rule_ids(issues_for(diff, "web/app.ts"))
    assert {"js-xss-sink", "js-debugger-statement", "js-console-debug"} <= ids


def test_merge_conflict_markers_flagged_anywhere():
    diff = make_diff("src/thing.py", ["<<<<<<< HEAD"])
    assert rule_ids(issues_for(diff, "src/thing.py")) == {"merge-conflict-marker"}


def test_lockfiles_are_excluded():
    diff = make_diff("package-lock.json", ['"token": "ghp_' + "a" * 40 + '"'])
    assert sa.analyze_diff(diff) == {}


def test_analyze_diff_applies_review_filters():
    diff = make_diff("src/app.py", ["subprocess.run(cmd, shell=True)"])
    diff += make_diff("web/app.ts", ["console.log(token)"])

    findings = sa.analyze_diff(diff, filters="*.py")

    assert set(findings) == {"src/app.py"}


def test_collect_changed_files_applies_filters(tmp_path):
    repo = _make_repo(tmp_path / "repo")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "web.js").write_text("console.log('ok')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
    (repo / "web.js").write_text("console.log('changed')\n", encoding="utf-8")

    files = sa.collect_changed_files(
        repo,
        mode="working",
        against="HEAD",
        use_merge_base=False,
        filters="*.py",
    )

    assert files == ["app.py"]


def test_diff_args_omit_empty_ref_for_against_only_pair(tmp_path):
    args = sa._diff_args(  # noqa: SLF001 - regression coverage for git argv shape
        tmp_path,
        mode="refs",
        refs="..main",
        what="",
        against="",
        use_merge_base=False,
        name_only=False,
    )

    assert args == ["diff", "main"]


def test_merge_into_report_dedupes_lines_llm_already_flagged():
    static_issues = sa.analyze_diff(
        make_diff("svc/run.py", ["subprocess.run(cmd, shell=True)"], start=5)
    )
    report = {
        "issues": {
            "svc/run.py": [
                {
                    "id": 1,
                    "severity": 2,
                    "title": "Shell injection risk.",
                    "tags": ["security"],
                    "affected_lines": [{"start_line": 4, "end_line": 6}],
                }
            ]
        },
        "total_issues": 1,
    }

    added = sa.merge_into_report(report, static_issues)

    assert added == 0
    assert report["total_issues"] == 1
    assert len(report["issues"]["svc/run.py"]) == 1


def test_merge_into_report_appends_new_findings_with_stable_ids():
    static_issues = sa.analyze_diff(
        make_diff("svc/run.py", ["subprocess.run(cmd, shell=True)"], start=5)
    )
    report = {"issues": {}, "total_issues": 0}

    added = sa.merge_into_report(report, static_issues)

    assert added == 1
    finding = report["issues"]["svc/run.py"][0]
    assert finding["id"] >= sa.STATIC_ISSUE_ID_BASE
    assert finding["source"] == "static"
    assert report["total_issues"] == 1
