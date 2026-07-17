"""Deep-rename compatibility: CODEPULSE_* env vars and the package shim."""
from __future__ import annotations

import subprocess
import sys

from codepulse_app import publisher, server


def _clear(monkeypatch, *names):
    for name in names:
        monkeypatch.delenv(name, raising=False)


# ── brand_env precedence ─────────────────────────────────────────────────────

def test_new_name_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("CODEPULSE_TOKEN", "new")
    monkeypatch.setenv("CODE_DOCTOR_TOKEN", "old")
    assert server.brand_env("TOKEN") == "new"


def test_legacy_name_still_works(monkeypatch):
    _clear(monkeypatch, "CODEPULSE_TOKEN")
    monkeypatch.setenv("CODE_DOCTOR_TOKEN", "old")
    assert server.brand_env("TOKEN") == "old"


def test_default_when_neither_set(monkeypatch):
    _clear(monkeypatch, "CODEPULSE_REVIEW_WORKERS", "CODE_DOCTOR_REVIEW_WORKERS")
    assert server.brand_env("REVIEW_WORKERS", "2") == "2"


def test_auth_accepts_token_from_either_name(monkeypatch):
    _clear(monkeypatch, "CODE_DOCTOR_TOKEN")
    monkeypatch.setenv("CODEPULSE_TOKEN", "s3cret")
    assert server.brand_env("TOKEN") == "s3cret"
    assert server.system_health({}, include_ollama_check=False)["authRequired"] is True


def test_webhook_secret_honors_new_name(monkeypatch):
    _clear(monkeypatch, "CODE_DOCTOR_WEBHOOK_SECRET")
    monkeypatch.setenv("CODEPULSE_WEBHOOK_SECRET", "hook")
    assert server.webhook_secret() == "hook"


def test_publisher_tokens_prefer_new_name(monkeypatch):
    _clear(monkeypatch, "GITHUB_TOKEN", "GITLAB_TOKEN")
    monkeypatch.setenv("CODEPULSE_GITHUB_TOKEN", "gh-new")
    monkeypatch.setenv("CODE_DOCTOR_GITHUB_TOKEN", "gh-old")
    monkeypatch.setenv("CODE_DOCTOR_GITLAB_TOKEN", "gl-old")
    config = publisher.publish_config()
    assert config["github"]["configured"] and config["gitlab"]["configured"]
    assert publisher._github_token() == "gh-new"
    assert publisher._gitlab_token() == "gl-old"


def test_subprocess_strip_covers_both_prefixes():
    for name in (
        "CODEPULSE_TOKEN", "CODE_DOCTOR_TOKEN",
        "CODEPULSE_WEBHOOK_SECRET", "CODE_DOCTOR_WEBHOOK_SECRET",
        "CODEPULSE_GITHUB_TOKEN", "CODE_DOCTOR_GITHUB_TOKEN",
    ):
        assert name in server.SUBPROCESS_ENV_STRIP


def test_bind_warning_names_the_new_var(monkeypatch):
    _clear(monkeypatch, "CODEPULSE_TOKEN", "CODE_DOCTOR_TOKEN")
    assert "CODEPULSE_TOKEN" in server.bind_warning("0.0.0.0")


# ── code_doctor_app shim ─────────────────────────────────────────────────────

def test_shim_resolves_submodules_to_canonical_instances():
    import code_doctor_app

    assert code_doctor_app.server is server
    assert code_doctor_app.publisher is publisher


def test_shim_module_entry_point_still_boots():
    # `python -m code_doctor_app --help` must exit 0 through the shim.
    result = subprocess.run(
        [sys.executable, "-m", "code_doctor_app", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "--port" in result.stdout
