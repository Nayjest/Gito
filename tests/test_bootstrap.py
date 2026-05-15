import os
from pathlib import Path

from gito.bootstrap import _sanitize_env_vars, _sanitized_home_env_path


def test_sanitized_home_env_path_drops_invalid_platform(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_API_TYPE=openai\n"
        "LLM_API_PLATFORM=None\n"
        "MODEL=deepseek-v4-flash\n",
        encoding="utf-8",
    )

    sanitized_path = _sanitized_home_env_path(env_file)

    assert sanitized_path != env_file
    assert sanitized_path.read_text(encoding="utf-8") == (
        "LLM_API_TYPE=openai\n"
        "MODEL=deepseek-v4-flash\n"
    )


def test_sanitized_home_env_path_keeps_valid_platform(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_API_TYPE=openai\n"
        "LLM_API_PLATFORM=azure\n"
        "MODEL=gpt-5.2\n",
        encoding="utf-8",
    )

    sanitized_path = _sanitized_home_env_path(env_file)

    assert sanitized_path == env_file


def test_sanitize_env_vars_removes_invalid_platform(monkeypatch):
    monkeypatch.setenv("LLM_API_PLATFORM", "None")

    _sanitize_env_vars()

    assert "LLM_API_PLATFORM" not in os.environ
