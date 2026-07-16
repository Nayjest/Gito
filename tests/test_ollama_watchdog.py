"""Ollama watchdog (release plan Item 9)."""
from __future__ import annotations

from pathlib import Path

from code_doctor_app import server, store


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


def _health_sequence(monkeypatch, results: list[bool]) -> None:
    it = iter(results)
    monkeypatch.setattr(
        server, "ollama_health",
        lambda base: (
            {"ok": True, "base": "http://localhost:11434", "models": ["m"]}
            if next(it)
            else {"ok": False, "base": "http://localhost:11434", "models": [], "error": "refused"}
        ),
    )


def test_watchdog_transitions_emit_audits(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    _health_sequence(monkeypatch, [True, True, False, False, True])
    dog = server.OllamaWatchdog()

    dog.sample()  # unknown → up: silent (normal boot)
    assert dog.snapshot()["state"] == "up"
    dog.sample()  # up → up: silent
    dog.sample()  # up → down: ollama_down
    assert dog.snapshot()["state"] == "down"
    dog.sample()  # down → down: silent
    dog.sample()  # down → up: ollama_recovered
    assert dog.snapshot()["state"] == "up"

    events = [e["event"] for e in server.read_audit() if e["event"].startswith("ollama")]
    assert events == ["ollama_recovered", "ollama_down"]  # newest first


def test_watchdog_boot_straight_to_down_audits(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    _health_sequence(monkeypatch, [False])
    dog = server.OllamaWatchdog()
    dog.sample()
    assert dog.snapshot()["state"] == "down"
    assert any(e["event"] == "ollama_down" for e in server.read_audit())


def test_snapshot_caps_check_history(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    _health_sequence(monkeypatch, [True] * 30)
    dog = server.OllamaWatchdog(max_history=20)
    for _ in range(30):
        dog.sample()
    snap = dog.snapshot()
    assert len(snap["checks"]) == 5  # health payload shows only the tail
    assert len(dog.checks) == 20


def test_note_ollama_warning_marks_meta_when_down(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-warn"
    server.run_dir(run_id).mkdir(parents=True, exist_ok=True)
    server.atomic_write_json(server.meta_path(run_id), {"id": run_id})

    monkeypatch.setattr(
        server.OLLAMA_WATCHDOG, "snapshot",
        lambda: {"state": "down", "since": "now", "checks": []},
    )
    server.note_ollama_warning(run_id)
    assert server.read_json(server.meta_path(run_id))["ollama_warning"] is True
    assert b"watchdog" in server.log_path(run_id).read_bytes()

    monkeypatch.setattr(
        server.OLLAMA_WATCHDOG, "snapshot",
        lambda: {"state": "up", "since": "now", "checks": []},
    )
    server.atomic_write_json(server.meta_path("run-ok"), {"id": "run-ok"})
    server.note_ollama_warning("run-ok")
    assert "ollama_warning" not in server.read_json(server.meta_path("run-ok"))
