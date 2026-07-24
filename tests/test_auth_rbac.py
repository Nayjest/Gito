"""Multi-user identity, sessions, and RBAC (v5.1 security hardening).

The unit tests isolate the SQLite store to a temp path so they never touch the
shared dev database; the HTTP tests use the standard ``run_test_server`` harness
(which already isolates the store) and exercise the real request pipeline.
"""
from __future__ import annotations

import pytest

from codepulse_app import auth, server, store
from tests._server_harness import http_request, run_test_server


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "auth.db")
    store.close()
    yield
    store.close()


# ── Password hashing ─────────────────────────────────────────────────────────


def test_password_hash_roundtrip_and_uniqueness(isolated_store):
    a = auth.hash_password("hunter2hunter2")
    b = auth.hash_password("hunter2hunter2")
    assert a != b  # random salt per hash
    assert a.startswith("scrypt$")
    assert auth.verify_password("hunter2hunter2", a)
    assert not auth.verify_password("wrong-password", a)


def test_verify_rejects_malformed_encoding(isolated_store):
    assert not auth.verify_password("x", "not-a-valid-hash")
    assert not auth.verify_password("x", "")


# ── User management ──────────────────────────────────────────────────────────


def test_create_user_validation(isolated_store):
    with pytest.raises(auth.AuthError):
        auth.create_user("", "longenough1")
    with pytest.raises(auth.AuthError):
        auth.create_user("bob", "short")
    with pytest.raises(auth.AuthError):
        auth.create_user("bob", "longenough1", role="wizard")
    auth.create_user("bob", "longenough1", role="reviewer")
    with pytest.raises(auth.AuthError):  # duplicate
        auth.create_user("bob", "longenough1")


def test_authenticate_honours_disabled_and_bad_password(isolated_store):
    auth.create_user("carol", "longenough1", role="admin")
    assert auth.authenticate("carol", "longenough1") is not None
    assert auth.authenticate("carol", "nope") is None
    auth.set_disabled("carol", True)
    assert auth.authenticate("carol", "longenough1") is None
    auth.set_disabled("carol", False)
    assert auth.authenticate("carol", "longenough1") is not None


def test_public_user_never_leaks_hash(isolated_store):
    auth.create_user("dave", "longenough1")
    listed = auth.list_users()
    assert listed and "password_hash" not in listed[0]


def test_password_change_revokes_sessions(isolated_store):
    auth.create_user("erin", "longenough1", role="reviewer")
    token, _ = auth.issue_session("erin", "reviewer")
    assert auth.resolve_session(token) is not None
    auth.set_password("erin", "brandnewpass1")
    assert auth.resolve_session(token) is None


# ── Sessions ─────────────────────────────────────────────────────────────────


def test_session_only_digest_persisted(isolated_store):
    auth.create_user("frank", "longenough1")
    token, _ = auth.issue_session("frank", "viewer")
    # The raw token must not appear anywhere in the sessions table.
    rows = store._conn().execute("SELECT token_hash, record FROM sessions").fetchall()
    assert rows
    for token_hash, record in rows:
        assert token not in token_hash
        assert token not in record


def test_expired_session_rejected(isolated_store, monkeypatch):
    auth.create_user("grace", "longenough1")
    monkeypatch.setenv("CODEPULSE_SESSION_TTL_HOURS", "0.25")
    token, record = auth.issue_session("grace", "viewer")
    # Force the stored expiry into the past and confirm resolve rejects + purges.
    past = "2000-01-01T00:00:00Z"
    store.put_session(auth._token_hash(token), "grace", past, {**record, "expires_at": past})
    assert auth.resolve_session(token) is None
    assert store.get_session(auth._token_hash(token)) is None


# ── Bootstrap ────────────────────────────────────────────────────────────────


def test_bootstrap_admin_from_env(isolated_store, monkeypatch):
    monkeypatch.setenv("CODEPULSE_ADMIN_USER", "root")
    monkeypatch.setenv("CODEPULSE_ADMIN_PASSWORD", "longenough1")
    assert auth.ensure_bootstrap_admin() == "root"
    assert auth.ensure_bootstrap_admin() is None  # idempotent, never overwrites
    assert auth.authenticate("root", "longenough1")["role"] == "admin"


# ── HTTP: open mode stays backward-compatible ────────────────────────────────


def test_open_mode_allows_admin_actions(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    with run_test_server(tmp_path) as base:
        status, body = http_request(f"{base}/api/overview")
        assert status == 200
        status, me = http_request(f"{base}/api/me")
        assert me["authenticated"] is True
        assert me["role"] == "admin"
        assert me["authRequired"] is False


# ── HTTP: multi-user mode closes anonymous access + enforces roles ───────────


def test_multiuser_login_and_rbac(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    with run_test_server(tmp_path) as base:
        # Open mode → the very first user can be bootstrapped through the API.
        status, _ = http_request(
            f"{base}/api/users", "POST",
            {"username": "admin1", "password": "longenough1", "role": "admin"},
        )
        assert status == 201

        # Creating the first user closes anonymous access immediately.
        status, _ = http_request(f"{base}/api/overview")
        assert status == 401

        # A second (non-admin) user must be created by an authenticated admin.
        status, alogin0 = http_request(f"{base}/api/login", "POST",
                                       {"username": "admin1", "password": "longenough1"})
        assert status == 200
        ahead0 = {"Authorization": f"Bearer {alogin0['token']}"}
        status, _ = http_request(
            f"{base}/api/users", "POST",
            {"username": "view1", "password": "longenough1", "role": "viewer"}, ahead0,
        )
        assert status == 201

        # Bad login is rejected.
        status, _ = http_request(f"{base}/api/login", "POST",
                                  {"username": "admin1", "password": "wrong"})
        assert status == 401

        # Viewer logs in, can read but not start a review.
        status, login = http_request(f"{base}/api/login", "POST",
                                     {"username": "view1", "password": "longenough1"})
        assert status == 200
        vhead = {"Authorization": f"Bearer {login['token']}"}
        assert http_request(f"{base}/api/overview", headers=vhead)[0] == 200
        status, _ = http_request(f"{base}/api/reviews", "POST", {"repoPath": "."}, vhead)
        assert status == 403  # viewer cannot start reviews
        status, _ = http_request(f"{base}/api/users", "GET", headers=vhead)
        assert status == 403  # viewer cannot list users

        # Admin logs in, can list users and reach admin-only endpoints.
        status, alogin = http_request(f"{base}/api/login", "POST",
                                      {"username": "admin1", "password": "longenough1"})
        ahead = {"Authorization": f"Bearer {alogin['token']}"}
        status, users = http_request(f"{base}/api/users", "GET", headers=ahead)
        assert status == 200
        assert {u["username"] for u in users["users"]} == {"admin1", "view1"}

        # Logout revokes the session.
        assert http_request(f"{base}/api/logout", "POST", headers=vhead)[0] == 200
        assert http_request(f"{base}/api/overview", headers=vhead)[0] == 401


def test_session_cookie_is_hardened(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    with run_test_server(tmp_path) as base:
        http_request(f"{base}/api/users", "POST",
                     {"username": "admin1", "password": "longenough1", "role": "admin"})
        import urllib.request
        req = urllib.request.Request(
            f"{base}/api/login", method="POST",
            data=b'{"username":"admin1","password":"longenough1"}',
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            cookie = resp.headers.get("Set-Cookie")
        assert cookie and cookie.startswith("cp_session=")
        assert "HttpOnly" in cookie
        assert "SameSite=Strict" in cookie
