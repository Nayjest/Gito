"""Security hardening quick wins (release plan §4a: QW-2..QW-5)."""
from __future__ import annotations

import inspect

from codepulse_app import server


# ── QW-5: subprocess env token stripping ─────────────────────────────────────


def test_subprocess_env_strips_workspace_and_publish_tokens(monkeypatch):
    monkeypatch.setenv("CODE_DOCTOR_TOKEN", "workspace-token")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "a" * 40)
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-secret")
    monkeypatch.setenv("CODE_DOCTOR_GITHUB_TOKEN", "override-token")
    monkeypatch.setenv("CODE_DOCTOR_GITLAB_TOKEN", "override-token")
    monkeypatch.setenv("CODE_DOCTOR_WEBHOOK_SECRET", "hook-secret")

    env = server.subprocess_env({"model": "gemma4:e4b"})

    for key in server.SUBPROCESS_ENV_STRIP:
        assert key not in env
    # The LLM contract the subprocesses actually need stays intact.
    assert env["LLM_API_TYPE"] == "openai"
    assert env["MODEL"] == "gemma4:e4b"
    assert env["LLM_API_BASE"].endswith("/v1/")


# ── QW-3: auth-failure throttling ────────────────────────────────────────────


def test_auth_throttle_trips_after_limit_and_expires():
    ip = "203.0.113.7"
    server.clear_auth_failures(ip)
    now = 1000.0

    tripped = [server.record_auth_failure(ip, now=now + i) for i in range(server.AUTH_THROTTLE_LIMIT)]

    assert tripped[:-1] == [False] * (server.AUTH_THROTTLE_LIMIT - 1)
    assert tripped[-1] is True
    assert server.auth_throttled(ip, now=now + server.AUTH_THROTTLE_LIMIT) is True
    # The block expires after the window.
    assert server.auth_throttled(ip, now=now + server.AUTH_THROTTLE_LIMIT + server.AUTH_THROTTLE_WINDOW + 1) is False
    server.clear_auth_failures(ip)


def test_auth_throttle_window_resets_between_slow_failures():
    ip = "203.0.113.8"
    server.clear_auth_failures(ip)
    now = 5000.0
    # Failures spaced beyond the window never accumulate to the limit.
    for i in range(server.AUTH_THROTTLE_LIMIT * 2):
        assert server.record_auth_failure(ip, now=now + i * (server.AUTH_THROTTLE_WINDOW + 1)) is False
    assert server.auth_throttled(ip, now=now) is False
    server.clear_auth_failures(ip)


def test_auth_throttle_clears_on_success():
    ip = "203.0.113.9"
    server.clear_auth_failures(ip)
    for i in range(server.AUTH_THROTTLE_LIMIT - 1):
        server.record_auth_failure(ip, now=100.0 + i)
    server.clear_auth_failures(ip)  # what require_auth does on a good token
    assert server.record_auth_failure(ip, now=200.0) is False
    server.clear_auth_failures(ip)


# ── QW-4: CSP tightening ─────────────────────────────────────────────────────


def test_csp_policy_locks_forms_and_plugins():
    assert "form-action 'self'" in server.CSP_POLICY
    assert "object-src 'none'" in server.CSP_POLICY
    # Existing directives kept (old-UI compatibility).
    assert "default-src 'self'" in server.CSP_POLICY
    assert "frame-ancestors 'none'" in server.CSP_POLICY


# ── QW-2: non-loopback bind warning ──────────────────────────────────────────


def test_bind_warning_only_for_exposed_bind_without_token(monkeypatch):
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    assert server.bind_warning("127.0.0.1") == ""
    assert server.bind_warning("localhost") == ""
    assert "CODEPULSE_TOKEN" in server.bind_warning("0.0.0.0")

    monkeypatch.setenv("CODE_DOCTOR_TOKEN", "token")
    assert server.bind_warning("0.0.0.0") == ""


# ── §4a log hygiene: access log must never interpolate headers ───────────────


def test_access_log_format_has_no_header_interpolation():
    source = inspect.getsource(server.CodeDoctorHandler.log_message)
    assert "headers" not in source
    assert "Authorization" not in source
