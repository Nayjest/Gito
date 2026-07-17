"""Health score + /api/trends aggregation."""
from __future__ import annotations

import json
from pathlib import Path

from code_doctor_app import server, store


def _isolated_store(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / ".code-doctor"
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "RUNS_DIR", data_dir / "runs")
    monkeypatch.setattr(server, "AUDIT_LOG", data_dir / "audit.jsonl")
    monkeypatch.setattr(server, "REPOS_FILE", data_dir / "repos.json")
    monkeypatch.setattr(server, "POLICIES_FILE", data_dir / "policies.json")
    monkeypatch.setattr(server, "SUPPRESSIONS_FILE", data_dir / "suppressions.json")
    monkeypatch.setattr(store, "DB_PATH", data_dir / "code-doctor.db")
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)


def _write_run(runs_dir: Path, run_id: str, created_at: str, *, repo="/tmp/repo",
               status="completed", kind=None, stats=None, duration=42.0) -> None:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    meta = {
        "id": run_id,
        "kind": kind,
        "status": status,
        "created_at": created_at,
        "repo_path": repo,
        "duration_seconds": duration,
        "stats": stats or {},
    }
    if kind is None:
        meta.pop("kind")
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


# ── compute_health ───────────────────────────────────────────────────────────

def test_health_perfect_run_is_grade_a():
    health = server.compute_health({"risk_score": 0, "gate": "pass", "severity_counts": {}})
    assert health == {"score": 100, "grade": "A"}


def test_health_blocked_run_is_capped():
    health = server.compute_health({
        "risk_score": 10,
        "gate": "block",
        "severity_counts": {"1": 1},
    })
    assert health["score"] <= 45
    assert health["grade"] in {"D", "F"}


def test_health_review_gate_caps_below_a():
    health = server.compute_health({
        "risk_score": 5,
        "gate": "review",
        "severity_counts": {"2": 1},
    })
    assert health["score"] <= 75
    assert health["grade"] != "A"


def test_health_never_negative_and_handles_garbage():
    health = server.compute_health({
        "risk_score": 100,
        "gate": "block",
        "severity_counts": {"1": "not-a-number", "2": None},
    })
    assert 0 <= health["score"] <= 100
    # No stats at all → still a well-formed answer.
    empty = server.compute_health({})
    assert set(empty) == {"score", "grade"}


def test_summarize_report_includes_health(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_dir = server.RUNS_DIR / "r1"
    run_dir.mkdir(parents=True)
    (run_dir / "meta.json").write_text(json.dumps({"id": "r1"}), encoding="utf-8")
    (run_dir / "code-review-report.json").write_text(json.dumps({
        "summary": "ok",
        "total_issues": 0,
        "issues": {},
    }), encoding="utf-8")
    stats = server.summarize_report("r1")
    assert stats["health"]["grade"] == "A"
    assert stats["health"]["score"] == 100


# ── /api/trends aggregation ──────────────────────────────────────────────────

def test_trends_orders_points_oldest_first_and_groups_by_repo(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    runs = server.RUNS_DIR
    _write_run(runs, "a1", "2026-07-01T10:00:00Z", repo="/tmp/alpha",
               stats={"risk_score": 20, "total_issues": 4, "gate": "review",
                      "severity_counts": {"2": 1}})
    _write_run(runs, "a2", "2026-07-03T10:00:00Z", repo="/tmp/alpha",
               stats={"risk_score": 5, "total_issues": 1, "gate": "pass",
                      "severity_counts": {}})
    _write_run(runs, "b1", "2026-07-02T10:00:00Z", repo="/tmp/beta",
               stats={"risk_score": 60, "total_issues": 9, "gate": "block",
                      "severity_counts": {"1": 2}})
    data = server.trends({})
    assert [p["id"] for p in data["runs"]] == ["a1", "b1", "a2"]
    assert data["count"] == 3
    by_path = {r["repo_path"]: r for r in data["repos"]}
    assert [p["id"] for p in by_path["/tmp/alpha"]["points"]] == ["a1", "a2"]
    assert by_path["/tmp/alpha"]["latest"]["id"] == "a2"
    assert by_path["/tmp/alpha"]["name"] == "alpha"
    # alpha's latest run is newer than beta's → alpha listed first
    assert data["repos"][0]["repo_path"] == "/tmp/alpha"


def test_trends_recomputes_health_for_legacy_runs(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    # Run whose stats predate the `health` key.
    _write_run(server.RUNS_DIR, "old", "2026-06-01T00:00:00Z",
               stats={"risk_score": 0, "total_issues": 0, "gate": "pass",
                      "severity_counts": {}})
    data = server.trends({})
    assert data["runs"][0]["health"] == {"score": 100, "grade": "A"}


def test_trends_excludes_generation_failed_and_running_runs(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    runs = server.RUNS_DIR
    _write_run(runs, "ok", "2026-07-01T00:00:00Z", stats={"gate": "pass"})
    _write_run(runs, "gen", "2026-07-02T00:00:00Z", kind="tests")
    _write_run(runs, "boom", "2026-07-03T00:00:00Z", status="failed")
    _write_run(runs, "wip", "2026-07-04T00:00:00Z", status="running")
    data = server.trends({})
    assert [p["id"] for p in data["runs"]] == ["ok"]


def test_trends_repo_filter_and_limit(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    runs = server.RUNS_DIR
    for i in range(5):
        _write_run(runs, f"r{i}", f"2026-07-0{i + 1}T00:00:00Z", repo="/tmp/alpha",
                   stats={"gate": "pass"})
    _write_run(runs, "other", "2026-07-09T00:00:00Z", repo="/tmp/beta",
               stats={"gate": "pass"})
    data = server.trends({"repo": ["/tmp/alpha"], "limit": ["2"]})
    assert [p["id"] for p in data["runs"]] == ["r3", "r4"]
    assert len(data["repos"]) == 1
    # Bogus limit falls back to the default instead of erroring.
    assert server.trends({"limit": ["zap"]})["count"] == 6
