"""Concurrent-safe persistence for CodePulse.

Audit events, finding suppressions, and the repository registry live in a
SQLite database (WAL mode) so multiple server threads — and multiple server
processes sharing the same data directory — can read and write without
clobbering each other. Run artifacts (reports, logs, generated tests) stay on
disk; they are immutable per run and need no coordination.

Legacy JSON/JSONL files are imported once by ``migrate_legacy`` and left in
place. The audit trail is additionally mirrored to ``audit.jsonl`` by the
server for human-readable evidence exports.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

DB_PATH = Path(__file__).resolve().parents[1] / ".code-doctor" / "code-doctor.db"

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None
_CONN_PATH: Path | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    event   TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit (ts);
CREATE TABLE IF NOT EXISTS suppressions (
    fingerprint TEXT PRIMARY KEY,
    record      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS repos (
    id     TEXT PRIMARY KEY,
    path   TEXT UNIQUE,
    record TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    record   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    record     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (username);
"""


def _conn() -> sqlite3.Connection:
    """Shared connection, reopened if DB_PATH changes (test isolation)."""
    global _CONN, _CONN_PATH
    with _LOCK:
        if _CONN is None or _CONN_PATH != DB_PATH:
            if _CONN is not None:
                _CONN.close()
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            conn.commit()
            _CONN = conn
            _CONN_PATH = DB_PATH
        return _CONN


def close() -> None:
    global _CONN, _CONN_PATH
    with _LOCK:
        if _CONN is not None:
            _CONN.close()
        _CONN = None
        _CONN_PATH = None


# ── Audit trail ──────────────────────────────────────────────────────────────


def append_audit(record: dict[str, Any]) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT INTO audit (ts, event, payload) VALUES (?, ?, ?)",
            (
                str(record.get("ts") or ""),
                str(record.get("event") or ""),
                json.dumps(record, sort_keys=True),
            ),
        )
        conn.commit()


def read_audit(limit: int = 100) -> list[dict[str, Any]]:
    """Newest events first."""
    with _LOCK:
        rows = _conn().execute(
            "SELECT payload FROM audit ORDER BY seq DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
    records = []
    for (payload,) in rows:
        try:
            records.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return records


def audit_count() -> int:
    with _LOCK:
        return int(_conn().execute("SELECT COUNT(*) FROM audit").fetchone()[0])


# ── Suppressions (reviewer feedback) ─────────────────────────────────────────


def get_suppressions() -> dict[str, dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute("SELECT fingerprint, record FROM suppressions").fetchall()
    result: dict[str, dict[str, Any]] = {}
    for fingerprint, record in rows:
        try:
            result[fingerprint] = json.loads(record)
        except json.JSONDecodeError:
            continue
    return result


def put_suppression(fingerprint: str, record: dict[str, Any]) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO suppressions (fingerprint, record) VALUES (?, ?)",
            (fingerprint, json.dumps(record, sort_keys=True)),
        )
        conn.commit()


def delete_suppression(fingerprint: str) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM suppressions WHERE fingerprint = ?", (fingerprint,))
        conn.commit()


# ── Repository registry ──────────────────────────────────────────────────────


def list_repos() -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute("SELECT record FROM repos").fetchall()
    repos = []
    for (record,) in rows:
        try:
            repos.append(json.loads(record))
        except json.JSONDecodeError:
            continue
    return sorted(repos, key=lambda item: item.get("updated_at", ""), reverse=True)


def get_repo_by_path(path: str) -> dict[str, Any] | None:
    with _LOCK:
        row = _conn().execute("SELECT record FROM repos WHERE path = ?", (path,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def save_repo(record: dict[str, Any]) -> None:
    repo_id = str(record.get("id") or "")
    path = str(record.get("path") or "")
    with _LOCK:
        conn = _conn()
        # One registration per path and per id, whichever exists.
        conn.execute("DELETE FROM repos WHERE path = ? OR id = ?", (path, repo_id))
        conn.execute(
            "INSERT INTO repos (id, path, record) VALUES (?, ?, ?)",
            (repo_id, path, json.dumps(record, sort_keys=True)),
        )
        conn.commit()


def delete_repo(repo_id: str) -> bool:
    with _LOCK:
        conn = _conn()
        cursor = conn.execute("DELETE FROM repos WHERE id = ?", (repo_id,))
        conn.commit()
        return cursor.rowcount > 0


def delete_repo_by_path(path: str) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM repos WHERE path = ?", (path,))
        conn.commit()


# ── Users (identity) ─────────────────────────────────────────────────────────


def list_users() -> list[dict[str, Any]]:
    with _LOCK:
        rows = _conn().execute("SELECT record FROM users ORDER BY username").fetchall()
    users = []
    for (record,) in rows:
        try:
            users.append(json.loads(record))
        except json.JSONDecodeError:
            continue
    return users


def get_user(username: str) -> dict[str, Any] | None:
    with _LOCK:
        row = _conn().execute(
            "SELECT record FROM users WHERE username = ?", (username,)
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def save_user(record: dict[str, Any]) -> None:
    username = str(record.get("username") or "")
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO users (username, record) VALUES (?, ?)",
            (username, json.dumps(record, sort_keys=True)),
        )
        conn.commit()


def delete_user(username: str) -> bool:
    with _LOCK:
        conn = _conn()
        cursor = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.execute("DELETE FROM sessions WHERE username = ?", (username,))
        conn.commit()
        return cursor.rowcount > 0


def user_count() -> int:
    with _LOCK:
        return int(_conn().execute("SELECT COUNT(*) FROM users").fetchone()[0])


# ── Sessions (login tokens) ──────────────────────────────────────────────────


def put_session(token_hash: str, username: str, expires_at: str, record: dict[str, Any]) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO sessions (token_hash, username, expires_at, record) "
            "VALUES (?, ?, ?, ?)",
            (token_hash, username, expires_at, json.dumps(record, sort_keys=True)),
        )
        conn.commit()


def get_session(token_hash: str) -> dict[str, Any] | None:
    with _LOCK:
        row = _conn().execute(
            "SELECT record FROM sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def delete_session(token_hash: str) -> None:
    with _LOCK:
        conn = _conn()
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
        conn.commit()


def purge_expired_sessions(now_iso: str) -> int:
    with _LOCK:
        conn = _conn()
        cursor = conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso,))
        conn.commit()
        return cursor.rowcount


# ── Legacy migration ─────────────────────────────────────────────────────────


def migrate_legacy(audit_log: Path, suppressions_file: Path, repos_file: Path) -> bool:
    """One-time import of the pre-SQLite JSON/JSONL store. Idempotent."""
    with _LOCK:
        conn = _conn()
        done = conn.execute(
            "SELECT value FROM kv WHERE key = 'legacy_migrated'"
        ).fetchone()
        if done:
            return False
        if audit_log.exists() and audit_count() == 0:
            for line in audit_log.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                conn.execute(
                    "INSERT INTO audit (ts, event, payload) VALUES (?, ?, ?)",
                    (
                        str(record.get("ts") or ""),
                        str(record.get("event") or ""),
                        json.dumps(record, sort_keys=True),
                    ),
                )
        if suppressions_file.exists():
            try:
                stored = json.loads(suppressions_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                stored = {}
            if isinstance(stored, dict):
                for fingerprint, record in stored.items():
                    conn.execute(
                        "INSERT OR IGNORE INTO suppressions (fingerprint, record) VALUES (?, ?)",
                        (fingerprint, json.dumps(record, sort_keys=True)),
                    )
        if repos_file.exists():
            try:
                repos = json.loads(repos_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                repos = []
            if isinstance(repos, list):
                for record in repos:
                    if isinstance(record, dict) and record.get("id"):
                        conn.execute(
                            "INSERT OR IGNORE INTO repos (id, path, record) VALUES (?, ?, ?)",
                            (
                                str(record.get("id")),
                                str(record.get("path") or ""),
                                json.dumps(record, sort_keys=True),
                            ),
                        )
        conn.execute("INSERT INTO kv (key, value) VALUES ('legacy_migrated', 'true')")
        conn.commit()
        return True
