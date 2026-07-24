"""`.env` loader + the Zyloo (OpenAI-compatible) provider wiring."""
from __future__ import annotations

from codepulse_app import server


def test_load_env_file_parses_and_respects_real_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "ZYLOO_API_KEY=sk-zy-abc123\n"
        'export QUOTED="hello world"\n'
        "ALREADY_SET=from-file\n"
        "novalue-line-without-equals\n"
    )
    monkeypatch.delenv("ZYLOO_API_KEY", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)
    monkeypatch.setenv("ALREADY_SET", "from-real-env")

    applied = server.load_env_file(env_file)

    import os

    assert os.environ["ZYLOO_API_KEY"] == "sk-zy-abc123"
    assert os.environ["QUOTED"] == "hello world"
    # A real environment value is never overridden by the file.
    assert os.environ["ALREADY_SET"] == "from-real-env"
    assert applied == 2


def test_load_env_file_missing_is_noop(tmp_path):
    assert server.load_env_file(tmp_path / "nope.env") == 0


def test_zyloo_provider_builds_openai_compatible_subprocess_env(monkeypatch):
    monkeypatch.setenv("ZYLOO_API_KEY", "sk-zy-xyz")
    env = server.subprocess_env({"provider": "zyloo"})
    assert env["LLM_API_TYPE"] == "openai"
    assert env["LLM_API_BASE"] == "https://api.zyloo.io/v1/"
    assert env["LLM_API_KEY"] == "sk-zy-xyz"
    assert env["MODEL"] == "zyloo/gemini-3-pro-preview-free"
    # The native key var is also exported for microcore.
    assert env["ZYLOO_API_KEY"] == "sk-zy-xyz"


def test_zyloo_configured_only_with_key(monkeypatch):
    spec = server.LLM_PROVIDERS["zyloo"]
    monkeypatch.delenv("ZYLOO_API_KEY", raising=False)
    monkeypatch.delenv("CODEPULSE_ZYLOO_KEY", raising=False)
    assert server.provider_configured(spec) is False
    monkeypatch.setenv("CODEPULSE_ZYLOO_KEY", "sk-zy-fallback")
    assert server.provider_configured(spec) is True
