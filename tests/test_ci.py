"""CI batch mode (release plan Item 4, CLI entry path)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from codepulse_app import ci, server, store


def _isolated_store(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "code-doctor"
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "RUNS_DIR", data_dir / "runs")
    monkeypatch.setattr(server, "AUDIT_LOG", data_dir / "audit.jsonl")
    monkeypatch.setattr(server, "REPOS_FILE", data_dir / "repos.json")
    monkeypatch.setattr(server, "POLICIES_FILE", data_dir / "policies.json")
    monkeypatch.setattr(server, "SUPPRESSIONS_FILE", data_dir / "suppressions.json")
    monkeypatch.setattr(store, "DB_PATH", data_dir / "code-doctor.db")
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _scratch_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ci@test")
    _git(repo, "config", "user.name", "CI Test")
    (repo / "app.py").write_text("def handler(evt):\n    return evt\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    # Uncommitted change so mode=working has a diff to review.
    (repo / "app.py").write_text(
        "def handler(evt):\n    return query(evt['id'])\n", encoding="utf-8"
    )
    return repo


BLOCKING_REPORT = {
    "issues": {
        "app.py": [
            {
                "id": 1,
                "file": "app.py",
                "title": "SQL injection in query builder.",
                "severity": 1,
                "confidence": 1,
                "tags": ["security"],
                "affected_lines": [{"start_line": 2, "end_line": 2}],
            }
        ]
    },
    "total_issues": 1,
    "number_of_processed_files": 1,
}


def _fake_review_command(monkeypatch, tmp_path: Path, report: dict | None, exit_code: int = 0):
    """Replace the gito subprocess with one that writes a canned report."""
    source = tmp_path / "canned-report.json"
    if report is not None:
        source.write_text(json.dumps(report), encoding="utf-8")

    def fake_command(payload, out_dir, options=None):
        if report is None:
            return [sys.executable, "-c", f"import sys; sys.exit({exit_code})"]
        script = (
            "import shutil, sys; "
            f"shutil.copy({str(source)!r}, {str(Path(out_dir) / 'code-review-report.json')!r}); "
            f"sys.exit({exit_code})"
        )
        return [sys.executable, "-c", script]

    monkeypatch.setattr(server, "build_review_command", fake_command)


BASE_ARGS = ["--mode", "working", "--no-verify", "--no-cross-file", "--no-static"]


def test_gate_breached_ordering():
    assert ci.gate_breached("block", "block")
    assert ci.gate_breached("block", "review")
    assert ci.gate_breached("review", "review")
    assert not ci.gate_breached("review", "block")
    assert not ci.gate_breached("pass", "review")
    assert not ci.gate_breached("block", "none")


def test_ci_run_prints_summary_and_gates(monkeypatch, tmp_path, capsys):
    _isolated_store(monkeypatch, tmp_path)
    repo = _scratch_repo(tmp_path)
    _fake_review_command(monkeypatch, tmp_path, BLOCKING_REPORT)
    result_json = tmp_path / "result.json"

    code = ci.main(
        ["--repo", str(repo), *BASE_ARGS, "--fail-on", "block", "--json", str(result_json)]
    )

    assert code == 1  # severity-1 security finding blocks the gate
    out = capsys.readouterr()
    assert "CodePulse review" in out.out
    assert "SQL injection in query builder." in out.out
    assert "meets --fail-on block" in out.err
    result = json.loads(result_json.read_text(encoding="utf-8"))
    assert result["gate"] == "block"
    assert result["status"] == "completed"
    assert result["run_id"]


def test_ci_fail_on_none_always_exits_zero(monkeypatch, tmp_path, capsys):
    _isolated_store(monkeypatch, tmp_path)
    repo = _scratch_repo(tmp_path)
    _fake_review_command(monkeypatch, tmp_path, BLOCKING_REPORT)

    assert ci.main(["--repo", str(repo), *BASE_ARGS, "--fail-on", "none"]) == 0
    assert "CodePulse review" in capsys.readouterr().out


def test_ci_failed_review_exits_two(monkeypatch, tmp_path, capsys):
    _isolated_store(monkeypatch, tmp_path)
    repo = _scratch_repo(tmp_path)
    _fake_review_command(monkeypatch, tmp_path, report=None, exit_code=3)

    assert ci.main(["--repo", str(repo), *BASE_ARGS]) == 2
    assert "failed" in capsys.readouterr().err


def test_ci_invalid_repo_exits_two(monkeypatch, tmp_path, capsys):
    _isolated_store(monkeypatch, tmp_path)
    assert ci.main(["--repo", str(tmp_path / "nope"), *BASE_ARGS]) == 2
    assert "codepulse:" in capsys.readouterr().err


def test_ci_publish_uses_server_publisher(monkeypatch, tmp_path, capsys):
    _isolated_store(monkeypatch, tmp_path)
    repo = _scratch_repo(tmp_path)
    _fake_review_command(monkeypatch, tmp_path, BLOCKING_REPORT)
    calls: list[tuple[str, dict]] = []

    def fake_publish(run_id, payload):
        calls.append((run_id, payload))
        return {"target": "acme/app#42", "platform": "github", "posted": {"mode": "review"}}

    monkeypatch.setattr(server, "publish_run", fake_publish)

    code = ci.main(
        [
            "--repo", str(repo), *BASE_ARGS,
            "--fail-on", "none",
            "--publish-pr", "42",
            "--platform", "github",
            "--slug", "acme/app",
        ]
    )

    assert code == 0
    assert calls and calls[0][1] == {
        "pr": 42, "dryRun": False, "platform": "github", "repo": "acme/app"
    }
    assert "published to acme/app#42" in capsys.readouterr().err
