from __future__ import annotations

from pathlib import Path

from code_doctor_app import server


def _isolated_store(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "code-doctor"
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "RUNS_DIR", data_dir / "runs")
    monkeypatch.setattr(server, "AUDIT_LOG", data_dir / "audit.jsonl")
    monkeypatch.setattr(server, "REPOS_FILE", data_dir / "repos.json")
    monkeypatch.setattr(server, "POLICIES_FILE", data_dir / "policies.json")
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)


def test_summarize_report_blocks_on_critical_issue(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-block"
    server.atomic_write_json(
        server.report_path(run_id),
        {
            "summary": "Authorization bypass found.",
            "number_of_processed_files": 1,
            "total_issues": 1,
            "issues": {
                "services/refunds.py": [
                    {
                        "id": 1,
                        "severity": 1,
                        "confidence": 1,
                        "title": "Missing ownership check.",
                        "tags": ["security"],
                    }
                ]
            },
        },
    )

    stats = server.summarize_report(run_id)

    assert stats["gate"] == "block"
    # severity 1 (10) × confidence 1 (1.0) × security multiplier (1.25) → 13
    assert stats["risk_score"] == 13
    assert stats["highest_severity"] == 1
    assert stats["severity_counts"]["1"] == 1
    assert stats["lifecycle"]["new"] == 1
    assert stats["lifecycle"]["baselined"] is False


def test_summarize_report_escalates_sensitive_files(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-sensitive"
    server.atomic_write_json(
        server.report_path(run_id),
        {
            "summary": "Secret hygiene issue.",
            "number_of_processed_files": 1,
            "total_issues": 1,
            "issues": {
                ".env.example": [
                    {
                        "id": 1,
                        "severity": 3,
                        "confidence": 1,
                        "title": "Production-looking key committed.",
                        "tags": ["secret-handling"],
                    }
                ]
            },
        },
    )

    stats = server.summarize_report(run_id)

    assert stats["gate"] == "review"
    assert stats["sensitive_files"] == [".env.example"]
    # severity 3 (3) × confidence 1 (1.0) × security multiplier (1.25) → 4
    assert stats["risk_score"] == 4


def test_export_review_csv_quotes_findings(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-export"
    server.atomic_write_json(
        server.meta_path(run_id),
        {"id": run_id, "status": "completed", "stats": {}},
    )
    server.atomic_write_json(
        server.report_path(run_id),
        {
            "issues": {
                "web/src/invite.ts": [
                    {
                        "id": 7,
                        "severity": 2,
                        "confidence": 1,
                        "title": 'Missing await before "send"',
                        "tags": ["async-flow", "bug"],
                    }
                ]
            }
        },
    )

    body, content_type = server.export_review(run_id, "csv")

    assert content_type == "text/csv; charset=utf-8"
    assert '"7","web/src/invite.ts","2","1","Missing await before ""send""","async-flow;bug"' in body
    assert server.read_audit()[-1]["event"] == "review_exported"


def test_summarize_report_tracks_issue_lifecycle(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    repo = "/repos/payments"
    persisting_issue = {
        "id": 1,
        "severity": 2,
        "confidence": 1,
        "title": "Missing ownership check.",
        "tags": ["security"],
    }
    fixed_issue = {
        "id": 2,
        "severity": 3,
        "confidence": 1,
        "title": "Bare except hides failures.",
        "tags": ["bug"],
    }
    brand_new_issue = {
        "id": 3,
        "severity": 2,
        "confidence": 1,
        "title": "Unawaited promise in invite flow.",
        "tags": ["bug"],
    }

    server.atomic_write_json(
        server.meta_path("run-1"),
        {"id": "run-1", "repo_path": repo, "created_at": "2026-07-10T00:00:00Z"},
    )
    server.atomic_write_json(
        server.report_path("run-1"),
        {"issues": {"a.py": [persisting_issue, fixed_issue]}},
    )
    stats_one = server.summarize_report("run-1")
    server.update_meta("run-1", status="completed", stats=stats_one)

    server.atomic_write_json(
        server.meta_path("run-2"),
        {"id": "run-2", "repo_path": repo, "created_at": "2026-07-11T00:00:00Z"},
    )
    server.atomic_write_json(
        server.report_path("run-2"),
        {"issues": {"a.py": [persisting_issue], "b.ts": [brand_new_issue]}},
    )

    stats_two = server.summarize_report("run-2")

    assert stats_one["lifecycle"] == {"new": 2, "recurring": 0, "resolved": 0, "baselined": False}
    assert stats_two["lifecycle"] == {"new": 1, "recurring": 1, "resolved": 1, "baselined": True}


def test_seed_sample_data_populates_enterprise_overview(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)

    meta = server.seed_sample_data()
    overview = server.overview()

    assert meta["id"].startswith("sample-")
    assert overview["metrics"]["repos"] == 1
    assert overview["metrics"]["completed"] == 1
    assert overview["metrics"]["gateCounts"]["block"] == 1
    assert any(item["label"] == "Evidence exports" and item["ready"] for item in overview["readiness"])
