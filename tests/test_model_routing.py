"""Per-pass model routing (release plan Item 14)."""
from __future__ import annotations

from pathlib import Path

from codepulse_app import server, store


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


def test_subprocess_env_override_absent_keeps_main_model():
    env = server.subprocess_env({"model": "gemma4:e4b"})
    assert env["MODEL"] == "gemma4:e4b"


def test_subprocess_env_override_wins():
    env = server.subprocess_env({"model": "qwen3.5:9B"}, model_override="gemma4:e4b")
    assert env["MODEL"] == "gemma4:e4b"


def test_pass_model_precedence_payload_over_policy(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    server.save_policies({"models": {"verify": "policy-model", "generate": ""}})

    # Payload wins over policy.
    assert server.pass_model({"verifyModel": "payload-model"}, "verifyModel", "verify") == "payload-model"
    # Policy fills in when payload is silent.
    assert server.pass_model({}, "verifyModel", "verify") == "policy-model"
    # Nothing set → empty = inherit the run's main model.
    assert server.pass_model({}, "generateModel", "generate") == ""


def test_run_verification_uses_verify_model(monkeypatch, tmp_path):
    """The verifier env carries the override, and meta records it."""
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-routing"
    server.atomic_write_json(server.meta_path(run_id), {"id": run_id})
    server.atomic_write_json(
        server.report_path(run_id),
        {"issues": {"a.py": [{"id": 1, "title": "Finding", "severity": 2}]}},
    )
    captured: dict = {}

    class FakeProc:
        def wait(self, timeout=None):
            return 0

    def fake_popen(command, cwd=None, env=None, stdout=None, stderr=None):
        captured["env"] = env
        return FakeProc()

    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)

    base_env = server.subprocess_env({"model": "qwen3.5:9B"})
    server.run_verification(
        run_id, tmp_path, {"model": "qwen3.5:9B", "verifyModel": "gemma4:e4b"}, base_env
    )

    assert captured["env"]["MODEL"] == "gemma4:e4b"
    assert server.read_json(server.meta_path(run_id))["verify_model"] == "gemma4:e4b"


def test_run_verification_without_override_keeps_env(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-plain"
    server.atomic_write_json(server.meta_path(run_id), {"id": run_id})
    server.atomic_write_json(
        server.report_path(run_id),
        {"issues": {"a.py": [{"id": 1, "title": "Finding", "severity": 2}]}},
    )
    captured: dict = {}

    class FakeProc:
        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(
        server.subprocess, "Popen",
        lambda command, cwd=None, env=None, stdout=None, stderr=None: (
            captured.update(env=env) or FakeProc()
        ),
    )

    base_env = server.subprocess_env({"model": "qwen3.5:9B"})
    server.run_verification(run_id, tmp_path, {"model": "qwen3.5:9B"}, base_env)

    assert captured["env"]["MODEL"] == "qwen3.5:9B"
    assert "verify_model" not in server.read_json(server.meta_path(run_id))
