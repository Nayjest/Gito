from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from codepulse_app import store


@pytest.fixture(autouse=True)
def _isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "code-doctor.db")
    yield
    store.close()


def test_audit_round_trip_newest_first():
    for index in range(5):
        store.append_audit({"ts": f"2026-07-16T00:00:0{index}Z", "event": f"event-{index}"})

    events = store.read_audit(limit=3)

    assert [event["event"] for event in events] == ["event-4", "event-3", "event-2"]
    assert store.audit_count() == 5


def test_suppressions_crud():
    store.put_suppression("abc123", {"fingerprint": "abc123", "title": "Noise"})
    store.put_suppression("abc123", {"fingerprint": "abc123", "title": "Noise v2"})

    suppressions = store.get_suppressions()
    assert suppressions["abc123"]["title"] == "Noise v2"

    store.delete_suppression("abc123")
    assert store.get_suppressions() == {}


def test_repos_upsert_by_path_and_delete():
    store.save_repo({"id": "r1", "path": "/repos/a", "name": "A", "updated_at": "2026-01-01"})
    store.save_repo({"id": "r2", "path": "/repos/b", "name": "B", "updated_at": "2026-01-02"})
    # Re-registering the same path replaces the old record even with a new id.
    store.save_repo({"id": "r3", "path": "/repos/a", "name": "A2", "updated_at": "2026-01-03"})

    repos = store.list_repos()
    assert [repo["id"] for repo in repos] == ["r3", "r2"]
    assert store.get_repo_by_path("/repos/a")["name"] == "A2"

    assert store.delete_repo("r2") is True
    assert store.delete_repo("r2") is False
    store.delete_repo_by_path("/repos/a")
    assert store.list_repos() == []


def test_concurrent_audit_appends_are_all_persisted():
    def worker(offset: int) -> None:
        for index in range(20):
            store.append_audit({"ts": "2026-07-16T00:00:00Z", "event": f"w{offset}-{index}"})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert store.audit_count() == 160


def test_migrate_legacy_imports_once(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps({"ts": "2026-07-01T00:00:00Z", "event": "legacy_event"}) + "\n",
        encoding="utf-8",
    )
    suppressions = tmp_path / "suppressions.json"
    suppressions.write_text(json.dumps({"fp1": {"fingerprint": "fp1"}}), encoding="utf-8")
    repos = tmp_path / "repos.json"
    repos.write_text(
        json.dumps([{"id": "legacy-repo", "path": "/repos/legacy"}]), encoding="utf-8"
    )

    assert store.migrate_legacy(audit, suppressions, repos) is True
    # Second call is a no-op — no duplicates.
    assert store.migrate_legacy(audit, suppressions, repos) is False

    assert store.read_audit()[0]["event"] == "legacy_event"
    assert store.audit_count() == 1
    assert "fp1" in store.get_suppressions()
    assert store.get_repo_by_path("/repos/legacy")["id"] == "legacy-repo"


def test_migrate_legacy_handles_missing_files(tmp_path):
    missing = Path(tmp_path / "nope")
    assert store.migrate_legacy(missing / "a.jsonl", missing / "s.json", missing / "r.json") is True
    assert store.audit_count() == 0
