"""HTTP-level test harness (release plan §4c).

Most server tests call functions directly; webhook HMAC verification and
streaming can only be tested over a real socket. ``run_test_server`` boots the
actual ``CodeDoctorHandler`` on port 0 with an isolated data directory and
guarantees shutdown.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

from codepulse_app import server, store

_ISOLATED_ATTRS = (
    "DATA_DIR",
    "RUNS_DIR",
    "AUDIT_LOG",
    "REPOS_FILE",
    "POLICIES_FILE",
    "SUPPRESSIONS_FILE",
)


@contextmanager
def run_test_server(tmp_path: Path) -> Iterator[str]:
    """Yield the base URL of a live Code Doctor server bound to port 0."""
    data_dir = tmp_path / "code-doctor"
    saved = {name: getattr(server, name) for name in _ISOLATED_ATTRS}
    saved_db = store.DB_PATH
    server.DATA_DIR = data_dir
    server.RUNS_DIR = data_dir / "runs"
    server.AUDIT_LOG = data_dir / "audit.jsonl"
    server.REPOS_FILE = data_dir / "repos.json"
    server.POLICIES_FILE = data_dir / "policies.json"
    server.SUPPRESSIONS_FILE = data_dir / "suppressions.json"
    store.DB_PATH = data_dir / "code-doctor.db"
    server.RUNS_DIR.mkdir(parents=True, exist_ok=True)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.CodeDoctorHandler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        store.close()
        store.DB_PATH = saved_db
        for name, value in saved.items():
            setattr(server, name, value)


def http_request(
    url: str,
    method: str = "GET",
    body: bytes | dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | str]:
    """Small urllib wrapper returning (status, parsed JSON or text)."""
    data = body
    request_headers = dict(headers or {})
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    text = raw.decode("utf-8", errors="ignore")
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, text
