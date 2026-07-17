"""Dependency scanning + SARIF export (Direction 3)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from code_doctor_app import dependency_scan as dep, sarif, snapshot


def rules(findings):
    return [f["rule"] for f in findings]


# --- dependency scan ----------------------------------------------------

def test_requirements_flags_unpinned_typosquat_and_url():
    text = (
        "requests==2.31.0\n"      # fine
        "flask\n"                  # unpinned
        "requets==1.0\n"           # typosquat of requests (deletion)
        "boto4 @ git+https://x/y.git\n"  # url install
    )
    findings = dep.scan_requirements(text, "requirements.txt")
    by_line = {f["affected_lines"][0]["start_line"]: f["rule"] for f in findings}
    assert 2 in by_line and by_line[2] == "dep-unpinned"
    assert "dep-typosquat" in rules(findings)
    assert "dep-url-install" in rules(findings)
    # The correctly-pinned popular package is not flagged.
    assert 1 not in by_line


def test_typosquat_detects_transposition():
    text = "lodahs==1.0.0\n"  # transposition of lodash — but that's npm; use pypi
    # pandsa is a transposition of pandas
    findings = dep.scan_requirements("pandsa==2.0\n", "requirements.txt")
    assert "dep-typosquat" in rules(findings)


def test_package_json_flags_wildcard_and_url_not_caret():
    text = json.dumps({
        "dependencies": {"react": "^18.0.0", "axios": "*", "left-pad": ">=1.0.0"},
        "devDependencies": {"evil": "git+https://e/x"},
    })
    findings = dep.scan_package_json(text, "package.json")
    unpinned = [f for f in findings if f["rule"] == "dep-unpinned"]
    names = " ".join(f["title"] for f in unpinned)
    # Wildcard and open range flagged; the conventional caret range is not.
    assert "axios" in names and "left-pad" in names
    assert "react" not in names
    assert "dep-url-install" in rules(findings)


def test_exact_pins_and_known_packages_are_quiet():
    assert dep.scan_requirements("requests==2.31.0\nnumpy==1.26.0\n", "requirements.txt") == []
    assert dep.scan_package_json(
        json.dumps({"dependencies": {"react": "18.2.0"}}), "package.json"
    ) == []


def test_analyze_repo_changes_scans_manifests(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    (repo / "requirements.txt").write_text("flask\nrequets==1.0\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "i"], check=True,
    )
    issues = dep.analyze_repo_changes(
        repo, mode="refs", refs=snapshot.SNAPSHOT_REFS, use_merge_base=False
    )
    assert "requirements.txt" in issues
    assert {"dep-unpinned", "dep-typosquat"} <= set(rules(issues["requirements.txt"]))


def test_merge_into_report_uses_dep_id_range():
    report = {"issues": {}, "total_issues": 0}
    added = dep.merge_into_report(report, {"requirements.txt": [
        {"title": "x", "affected_lines": [{"start_line": 1, "end_line": 1}], "tags": []}
    ]})
    assert added == 1
    got = report["issues"]["requirements.txt"][0]
    assert got["id"] >= dep.DEP_ISSUE_ID_BASE


# --- SARIF --------------------------------------------------------------

def test_sarif_document_shape_and_levels():
    issues = [
        {"file": "a.py", "severity": 1, "confidence": 2, "title": "SSRF",
         "rule": "taint-ssrf", "tags": ["security"],
         "affected_lines": [{"start_line": 10, "end_line": 12}]},
        {"file": "b.py", "severity": 4, "confidence": 3, "title": "Nit",
         "rule": "style", "tags": [], "affected_lines": [{"start_line": 3}]},
    ]
    doc = sarif.to_sarif(issues, "5.0.0")
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "CodePulse"
    assert run["tool"]["driver"]["version"] == "5.0.0"
    assert len(run["results"]) == 2
    r0 = run["results"][0]
    assert r0["ruleId"] == "taint-ssrf"
    assert r0["level"] == "error"
    loc = r0["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "a.py"
    assert loc["region"] == {"startLine": 10, "endLine": 12}
    assert run["results"][1]["level"] == "note"
    # Rules are de-duplicated into the driver.
    assert {r["id"] for r in run["tool"]["driver"]["rules"]} == {"taint-ssrf", "style"}


def test_sarif_dumps_is_valid_json():
    payload = sarif.dumps([], "5.0.0")
    doc = json.loads(payload)
    assert doc["runs"][0]["results"] == []
