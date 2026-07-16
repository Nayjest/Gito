"""Auto-fix apply / revert safety (release plan Item 3)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from code_doctor_app import patcher, server, store

ORIGINAL = "def add(a, b):\n    return a - b\n\ndef sub(a, b):\n    return a - b\n"


def _issue(**overrides) -> dict:
    issue = {
        "id": 7,
        "file": "calc.py",
        "affected_lines": [
            {
                "start_line": 1,
                "end_line": 2,
                "affected_code": "1: def add(a, b):\n2:     return a - b",
                "proposal": "def add(a, b):\n    return a + b",
            }
        ],
    }
    issue.update(overrides)
    return issue


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text(ORIGINAL, encoding="utf-8")
    return repo


def test_plan_fix_applicable_when_file_matches_recorded_snippet(tmp_path):
    repo = _repo(tmp_path)
    plan = patcher.plan_fix(repo, _issue())
    assert plan["applicable"] is True
    assert plan["before"] == ["def add(a, b):", "    return a - b"]
    assert plan["after"] == ["def add(a, b):", "    return a + b"]


def test_plan_fix_refuses_drifted_file(tmp_path):
    repo = _repo(tmp_path)
    (repo / "calc.py").write_text(
        "def add(a, b):\n    return a * b\n", encoding="utf-8"
    )
    plan = patcher.plan_fix(repo, _issue())
    assert plan["applicable"] is False
    assert "changed since review" in plan["reason"]


def test_plan_fix_refuses_shrunken_file_and_missing_pieces(tmp_path):
    repo = _repo(tmp_path)
    beyond_eof = _issue()
    beyond_eof["affected_lines"][0]["start_line"] = 400
    beyond_eof["affected_lines"][0]["end_line"] = 401
    assert patcher.plan_fix(repo, beyond_eof)["applicable"] is False

    no_proposal = _issue()
    no_proposal["affected_lines"][0]["proposal"] = "  "
    assert "no proposed fix" in patcher.plan_fix(repo, no_proposal)["reason"]

    assert "no file" in patcher.plan_fix(repo, _issue(file=""))["reason"]
    assert (
        "no affected-lines block"
        in patcher.plan_fix(repo, _issue(affected_lines=[]))["reason"]
    )


def test_apply_fix_writes_backup_and_new_content(tmp_path):
    repo = _repo(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result = patcher.apply_fix(repo, run_dir, _issue())
    content = (repo / "calc.py").read_text(encoding="utf-8")
    assert "return a + b" in content
    assert content.endswith("\n")
    # Untouched lines survive the splice.
    assert "def sub(a, b):" in content
    backup = run_dir / result["backup"]
    assert backup.read_text(encoding="utf-8") == ORIGINAL


def test_apply_fix_raises_on_drift_instead_of_guessing(tmp_path):
    repo = _repo(tmp_path)
    (repo / "calc.py").write_text("something else\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not applicable"):
        patcher.apply_fix(repo, tmp_path / "run", _issue())
    assert (repo / "calc.py").read_text(encoding="utf-8") == "something else\n"


def test_revert_fix_restores_original(tmp_path):
    repo = _repo(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    issue = _issue()
    entry = patcher.apply_fix(repo, run_dir, issue)
    assert (repo / "calc.py").read_text(encoding="utf-8") != ORIGINAL
    reverted = patcher.revert_fix(repo, run_dir, issue, entry)
    assert reverted == "calc.py"
    assert (repo / "calc.py").read_text(encoding="utf-8") == ORIGINAL


def test_revert_fix_requires_backup_inside_run_dir(tmp_path):
    repo = _repo(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    with pytest.raises(ValueError, match="No backup"):
        patcher.revert_fix(repo, run_dir, _issue(), {"backup": "../outside.py"})
    with pytest.raises(ValueError, match="No backup"):
        patcher.revert_fix(repo, run_dir, _issue(), {"backup": "missing.py"})


def test_resolve_repo_file_rejects_escape_attempts(tmp_path):
    repo = _repo(tmp_path)
    for bad in ("../escape.py", "/etc/passwd", "a/../../b.py", ""):
        with pytest.raises(ValueError):
            patcher.resolve_repo_file(repo, bad)


def test_resolve_repo_file_refuses_symlinked_directory(tmp_path):
    repo = _repo(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / "vendor").symlink_to(outside)
    # Escapes the repo entirely → caught as an unsafe path.
    with pytest.raises(ValueError):
        patcher.resolve_repo_file(repo, "vendor/pwned.py")
    # Stays inside the repo but goes through a symlink → refused explicitly.
    real = repo / "real"
    real.mkdir()
    (repo / "alias").symlink_to(real)
    with pytest.raises(ValueError, match="symlink"):
        patcher.resolve_repo_file(repo, "alias/pwned.py")


def test_write_generated_tests_skips_existing_unless_overwrite(tmp_path):
    repo = _repo(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "generated-tests.json").write_text(
        json.dumps(
            {
                "files": [
                    {"path": "tests/test_calc.py", "content": "def test(): pass\n"},
                    {"path": "calc.py", "content": "overwritten\n"},
                ]
            }
        ),
        encoding="utf-8",
    )
    written = patcher.write_generated_tests(repo, run_dir)
    assert [w["path"] for w in written] == ["tests/test_calc.py"]
    assert (repo / "calc.py").read_text(encoding="utf-8") == ORIGINAL

    written = patcher.write_generated_tests(repo, run_dir, overwrite=True)
    overwrote = {w["path"]: w["overwrote"] for w in written}
    assert overwrote["calc.py"] is True
    assert (repo / "calc.py").read_text(encoding="utf-8") == "overwritten\n"
    backup = run_dir / "backups" / "generated-tests" / "calc.py"
    assert backup.read_text(encoding="utf-8") == ORIGINAL


def test_write_generated_tests_raises_when_everything_already_exists(tmp_path):
    repo = _repo(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "generated-tests.json").write_text(
        json.dumps({"files": [{"path": "calc.py", "content": "x\n"}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="already exist"):
        patcher.write_generated_tests(repo, run_dir)
    with pytest.raises(ValueError, match="no generated tests"):
        patcher.write_generated_tests(repo, tmp_path)


# --- Endpoint plumbing (server.fix_plan / fix_apply / fix_revert / tests_write) ---


def _isolated_store(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "code-doctor"
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "RUNS_DIR", data_dir / "runs")
    monkeypatch.setattr(server, "AUDIT_LOG", data_dir / "audit.jsonl")
    monkeypatch.setattr(server, "REPOS_FILE", data_dir / "repos.json")
    monkeypatch.setattr(server, "POLICIES_FILE", data_dir / "policies.json")
    monkeypatch.setattr(server, "SUPPRESSIONS_FILE", data_dir / "suppressions.json")
    monkeypatch.setattr(store, "DB_PATH", data_dir / "code-doctor.db")


def _seed_run(monkeypatch, tmp_path: Path, run_id: str = "run-fix") -> Path:
    _isolated_store(monkeypatch, tmp_path)
    repo = _repo(tmp_path)
    server.atomic_write_json(
        server.meta_path(run_id),
        {"id": run_id, "status": "completed", "repo_path": str(repo)},
    )
    server.atomic_write_json(
        server.report_path(run_id),
        {"issues": {"calc.py": [_issue()]}, "total_issues": 1},
    )
    return repo


def _audit_events(run_id: str = "run-fix") -> list[str]:
    if not server.AUDIT_LOG.exists():
        return []
    return [
        json.loads(line)["event"]
        for line in server.AUDIT_LOG.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_fix_plan_endpoint_returns_plan_and_empty_ledger(monkeypatch, tmp_path):
    _seed_run(monkeypatch, tmp_path)
    plan = server.fix_plan("run-fix", {"issueId": 7})
    assert plan["applicable"] is True
    assert plan["ledger"] == {}


def test_fix_apply_and_revert_round_trip_with_ledger_and_audit(monkeypatch, tmp_path):
    repo = _seed_run(monkeypatch, tmp_path)
    result = server.fix_apply("run-fix", {"issueId": "7"})
    assert result["applied"] is True
    assert "return a + b" in (repo / "calc.py").read_text(encoding="utf-8")

    plan = server.fix_plan("run-fix", {"issueId": 7})
    assert plan["ledger"]["backup"] == result["backup"]
    # File now differs from the reviewed snippet, so a fresh apply is refused.
    assert plan["applicable"] is False

    reverted = server.fix_revert("run-fix", {"issueId": 7})
    assert reverted["reverted"] is True
    assert (repo / "calc.py").read_text(encoding="utf-8") == ORIGINAL
    with pytest.raises(ValueError, match="not currently applied"):
        server.fix_revert("run-fix", {"issueId": 7})
    assert _audit_events() == ["fix_applied", "fix_reverted"]


def test_fix_endpoints_reject_unknown_finding_and_missing_repo(monkeypatch, tmp_path):
    _seed_run(monkeypatch, tmp_path)
    with pytest.raises(FileNotFoundError):
        server.fix_plan("run-fix", {"issueId": 999})
    server.atomic_write_json(
        server.meta_path("run-fix"),
        {"id": "run-fix", "status": "completed", "repo_path": str(tmp_path / "gone")},
    )
    with pytest.raises(ValueError, match="no longer exists"):
        server.fix_plan("run-fix", {"issueId": 7})


def test_tests_write_endpoint_writes_and_audits(monkeypatch, tmp_path):
    repo = _seed_run(monkeypatch, tmp_path)
    (server.run_dir("run-fix") / "generated-tests.json").write_text(
        json.dumps({"files": [{"path": "tests/test_calc.py", "content": "pass\n"}]}),
        encoding="utf-8",
    )
    result = server.tests_write("run-fix", {})
    assert result["written"] == [{"path": "tests/test_calc.py", "overwrote": False}]
    assert (repo / "tests" / "test_calc.py").read_text(encoding="utf-8") == "pass\n"
    assert "tests_written" in _audit_events()
