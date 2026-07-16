from __future__ import annotations

import subprocess
from pathlib import Path

from code_doctor_app import context_engine


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _make_repo(path: Path, files: dict[str, str]) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _git(path, "config", "user.email", "code-doctor@example.local")
    _git(path, "config", "user.name", "Code Doctor")
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-m", "initial")
    return path


def test_import_graph_resolves_python_and_ts(tmp_path):
    repo = _make_repo(
        tmp_path / "repo",
        {
            "pkg/__init__.py": "",
            "pkg/billing.py": "def charge(account, amount):\n    return amount\n",
            "pkg/api.py": "from pkg.billing import charge\n\ndef handler():\n    return charge('a', 1)\n",
            "web/lib/util.ts": "export function fmt(x: number) { return String(x) }\n",
            "web/routes/pay.ts": "import { fmt } from '../lib/util'\nexport const pay = () => fmt(1)\n",
        },
    )

    files = context_engine.list_source_files(repo)
    graph = context_engine.build_import_graph(repo, files)

    assert "pkg/billing.py" in graph["pkg/api.py"]
    assert "web/lib/util.ts" in graph["web/routes/pay.ts"]
    assert context_engine.dependents_of(graph, "pkg/billing.py") == ["pkg/api.py"]


def test_diff_symbol_changes_detects_removed_and_resigned():
    diff = (
        "diff --git a/pkg/billing.py b/pkg/billing.py\n"
        "--- a/pkg/billing.py\n"
        "+++ b/pkg/billing.py\n"
        "@@ -1,5 +1,4 @@\n"
        "-def charge(account, amount):\n"
        "+def charge(account, amount, currency):\n"
        "     return amount\n"
        "-def legacy_charge(account):\n"
        "-    return account\n"
    )

    changes = context_engine.diff_symbol_changes(diff)

    entry = changes["pkg/billing.py"]
    assert entry["removed"] == ["legacy_charge"]
    assert entry["signature_changed"] == [
        {"name": "charge", "before": "account,amount", "after": "account,amount,currency"}
    ]


def test_analyze_cross_file_flags_broken_callers(tmp_path):
    repo = _make_repo(
        tmp_path / "repo",
        {
            "pkg/__init__.py": "",
            "pkg/billing.py": (
                "def charge(account, amount):\n    return amount\n\n"
                "def legacy_charge(account):\n    return account\n"
            ),
            "pkg/api.py": (
                "from pkg.billing import charge, legacy_charge\n\n"
                "def handler():\n"
                "    legacy_charge('a')\n"
                "    return charge('a', 1)\n"
            ),
        },
    )
    # Change the signature of charge and delete legacy_charge without touching api.py.
    (repo / "pkg/billing.py").write_text(
        "def charge(account, amount, currency):\n    return amount\n", encoding="utf-8"
    )

    result = context_engine.analyze_cross_file(repo, mode="working", against="HEAD", use_merge_base=False)

    findings = result["findings"]["pkg/billing.py"]
    titles = " ".join(finding["title"] for finding in findings)
    assert "legacy_charge" in titles
    assert "charge" in titles
    assert all(finding["source"] == "crossfile" for finding in findings)
    assert all("cross-file" in finding["tags"] for finding in findings)
    # Usage evidence points into the dependent file.
    assert any(
        block["file"] == "pkg/api.py"
        for finding in findings
        for block in finding["affected_lines"]
    )
    pack = result["pack"]
    assert pack["files"]["pkg/billing.py"]["dependents"] == ["pkg/api.py"]


def test_analyze_cross_file_quiet_when_no_contract_breaks(tmp_path):
    repo = _make_repo(
        tmp_path / "repo",
        {"app.py": "def run():\n    return 1\n"},
    )
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")

    result = context_engine.analyze_cross_file(repo, mode="working", against="HEAD", use_merge_base=False)

    assert result["findings"] == {}
    assert "app.py" in result["pack"]["files"]


def test_merge_into_report_assigns_crossfile_ids():
    report = {"issues": {"pkg/billing.py": [{"id": 1, "title": "existing"}]}, "total_issues": 1}
    added = context_engine.merge_into_report(
        report,
        {
            "pkg/billing.py": [
                {"title": "Removed symbol", "severity": 2, "tags": ["cross-file"], "source": "crossfile", "affected_lines": []}
            ]
        },
    )

    assert added == 1
    issue = report["issues"]["pkg/billing.py"][1]
    assert issue["id"] == context_engine.CROSSFILE_ISSUE_ID_BASE
    assert report["total_issues"] == 2
