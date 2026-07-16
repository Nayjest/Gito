"""SSE live streaming (release plan Item 6)."""
from __future__ import annotations

import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from code_doctor_app import server
from tests._server_harness import run_test_server


def _seed_running_run(run_id: str) -> None:
    server.run_dir(run_id).mkdir(parents=True, exist_ok=True)
    server.atomic_write_json(
        server.meta_path(run_id), {"id": run_id, "status": "running"}
    )
    server.log_path(run_id).write_text("boot line\n", encoding="utf-8")


def _read_stream(url: str, timeout: float = 10.0) -> str:
    request = urllib.request.Request(url)
    collected = b""
    with urllib.request.urlopen(request, timeout=timeout) as response:
        assert response.headers["Content-Type"] == "text/event-stream"
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = response.read(256)
            if not chunk:
                break
            collected += chunk
            if b"event: done" in collected:
                break
    return collected.decode("utf-8", errors="ignore")


def test_stream_emits_log_meta_and_done(monkeypatch, tmp_path):
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.setattr(server, "SSE_TICK_SECONDS", 0.05)
    with run_test_server(tmp_path) as base:
        run_id = "stream-run"
        _seed_running_run(run_id)

        def finish_soon() -> None:
            time.sleep(0.4)
            with server.log_path(run_id).open("a", encoding="utf-8") as handle:
                handle.write("second line\n")
            time.sleep(0.2)
            server.update_meta(run_id, status="completed")

        threading.Thread(target=finish_soon, daemon=True).start()
        body = _read_stream(f"{base}/api/reviews/{run_id}/events")

    assert "event: log" in body
    assert "boot line" in body
    assert "second line" in body
    assert "event: meta" in body
    assert '"status": "completed"' in body.replace("data: ", "")
    assert "event: done" in body
    assert body.rstrip().endswith("data: completed")


def test_stream_unknown_run_404(monkeypatch, tmp_path):
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    with run_test_server(tmp_path) as base:
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"{base}/api/reviews/nope/events", timeout=5)
        assert excinfo.value.code == 404


def test_stream_cap_returns_429(monkeypatch, tmp_path):
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.setattr(server, "SSE_SEMAPHORE", threading.Semaphore(0))
    with run_test_server(tmp_path) as base:
        _seed_running_run("capped-run")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"{base}/api/reviews/capped-run/events", timeout=5)
        assert excinfo.value.code == 429


def test_stream_requires_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("CODE_DOCTOR_TOKEN", "stream-secret")
    with run_test_server(tmp_path) as base:
        _seed_running_run("auth-run")
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            urllib.request.urlopen(f"{base}/api/reviews/auth-run/events", timeout=5)
        assert excinfo.value.code == 401
