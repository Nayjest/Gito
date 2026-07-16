"""Legacy-data upgrade check (release plan §4c): a frozen v4.3 pre-SQLite
``.code-doctor/`` directory must migrate cleanly and render, and a second
boot must migrate nothing (idempotency). Regenerate the fixture only
deliberately — its stability is the point."""
from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from code_doctor_app import server, store

FIXTURE = Path(__file__).parent / "fixtures" / "data-v4.3.tar.gz"
LEGACY_RUN_ID = "20260101-090000-4f3a2b1c"


@pytest.fixture
def legacy_data_dir(monkeypatch, tmp_path: Path) -> Path:
    with tarfile.open(FIXTURE) as tar:
        tar.extractall(tmp_path, filter="data")
    data_dir = tmp_path / "code-doctor"
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "RUNS_DIR", data_dir / "runs")
    monkeypatch.setattr(server, "AUDIT_LOG", data_dir / "audit.jsonl")
    monkeypatch.setattr(server, "REPOS_FILE", data_dir / "repos.json")
    monkeypatch.setattr(server, "POLICIES_FILE", data_dir / "policies.json")
    monkeypatch.setattr(server, "SUPPRESSIONS_FILE", data_dir / "suppressions.json")
    monkeypatch.setattr(store, "DB_PATH", data_dir / "code-doctor.db")
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    return data_dir


def _migrate() -> bool:
    return store.migrate_legacy(server.AUDIT_LOG, server.SUPPRESSIONS_FILE, server.REPOS_FILE)


def test_v43_data_migrates_and_renders(legacy_data_dir):
    assert _migrate() is True

    events = store.read_audit(50)
    assert len(events) == 4
    assert events[-1]["event"] == "review_queued"  # newest first

    suppressions = store.get_suppressions()
    assert set(suppressions) == {"deadbeefcafe0123"}
    assert suppressions["deadbeefcafe0123"]["action"] == "dismiss"

    repos = store.list_repos()
    assert [repo["id"] for repo in repos] == ["3f9c2e7a1b5d"]
    assert repos[0]["name"] == "acme-payments-api"

    # The old run must render through the current detail endpoint logic.
    detail = server.get_review(LEGACY_RUN_ID)
    assert detail["meta"]["status"] == "completed"
    assert detail["meta"]["stats"]["gate"] == "block"
    assert detail["report"]["total_issues"] == 1
    # Newer-artifact keys are absent, never errors (rule R6).
    assert detail.get("context_pack") in (None, {})

    # Old stats recompute identically under current scoring for legacy shapes.
    stats = server.summarize_report(LEGACY_RUN_ID)
    assert stats["gate"] == "block"
    assert stats["cross_file_issues"] == 0

    reviews = server.list_reviews()
    assert any(item["id"] == LEGACY_RUN_ID for item in reviews)


def test_v43_migration_is_idempotent(legacy_data_dir):
    assert _migrate() is True
    before = (store.audit_count(), len(store.get_suppressions()), len(store.list_repos()))

    assert _migrate() is False  # second boot: nothing to do
    assert (store.audit_count(), len(store.get_suppressions()), len(store.list_repos())) == before

    # New events append after the migrated history, they don't collide with it.
    server.audit_event("post_migration_probe", run_id=LEGACY_RUN_ID)
    events = store.read_audit(1)
    assert events[0]["event"] == "post_migration_probe"
    assert store.audit_count() == before[0] + 1
