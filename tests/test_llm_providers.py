"""Cloud LLM provider support (Direction 2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from codepulse_app import server, store


def _isolate(monkeypatch, tmp_path: Path) -> None:
    data = tmp_path / "code-doctor"
    for attr, sub in {
        "DATA_DIR": "", "RUNS_DIR": "runs", "SNAPSHOTS_DIR": "snapshots",
        "AUDIT_LOG": "audit.jsonl", "REPOS_FILE": "repos.json",
        "POLICIES_FILE": "policies.json", "SUPPRESSIONS_FILE": "suppressions.json",
    }.items():
        monkeypatch.setattr(server, attr, data / sub if sub else data)
    monkeypatch.setattr(store, "DB_PATH", data / "code-doctor.db")
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
                "GOOGLE_API_KEY", "CODE_DOCTOR_ANTHROPIC_KEY", "CODE_DOCTOR_OPENAI_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_default_provider_is_local_ollama(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    name, spec = server.resolve_provider({})
    assert name == "ollama" and spec["local"] is True
    env = server.subprocess_env({})
    assert env["LLM_API_TYPE"] == "openai"
    assert env["LLM_API_KEY"] == "ollama"
    assert env["LLM_API_BASE"].endswith("/v1/")
    assert env["MAX_CONCURRENT_TASKS"] == "4"


def test_unknown_provider_rejected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        server.resolve_provider({"provider": "hal9000"})


def test_anthropic_needs_key_and_reads_it_from_env(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _, spec = server.resolve_provider({"provider": "anthropic"})
    assert server.provider_configured(spec) is False  # no key yet
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-xyz")
    assert server.provider_configured(spec) is True
    env = server.subprocess_env({"provider": "anthropic"})
    assert env["LLM_API_TYPE"] == "anthropic"
    assert env["LLM_API_KEY"] == "sk-ant-xyz"
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-xyz"  # native var also set
    assert env["MODEL"].startswith("claude")
    assert env["MAX_CONCURRENT_TASKS"] == "8"  # cloud parallelism


def test_api_key_never_taken_from_payload(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real")
    env = server.subprocess_env({"provider": "anthropic", "apiKey": "sk-payload-leak"})
    assert "sk-payload-leak" not in env.values()
    assert env["LLM_API_KEY"] == "sk-ant-real"


def test_create_review_run_rejects_unconfigured_cloud_provider(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    import subprocess
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "i"], check=True,
    )
    with pytest.raises(ValueError, match="needs an API key"):
        server.create_review_run({"repoPath": str(repo), "provider": "openai"})


def test_policy_provider_used_when_payload_silent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    server.save_policies({"provider": "openai"})
    name, _ = server.resolve_provider({})
    assert name == "openai"
    env = server.subprocess_env({})
    assert env["LLM_API_TYPE"] == "openai"
    assert env["LLM_API_BASE"] == "https://api.openai.com/v1/"
    assert env["LLM_API_KEY"] == "sk-openai"


def test_health_lists_providers_with_configured_flags(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    health = server.system_health({}, include_ollama_check=False)
    provs = {p["id"]: p for p in health["providers"]}
    assert provs["ollama"]["configured"] is True and provs["ollama"]["local"] is True
    assert provs["anthropic"]["configured"] is True
    assert provs["openai"]["configured"] is False
