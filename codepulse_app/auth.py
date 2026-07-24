"""Identity, sessions, and role-based access control for CodePulse.

This layer is *additive* and backward-compatible (ground rule R2):

* **Open mode** — no ``CODEPULSE_TOKEN`` and no registered users: every
  request is treated as an ``admin`` (unchanged pre-5.1 behaviour, for local
  single-user use).
* **Static-token mode** — ``CODEPULSE_TOKEN`` set: a matching bearer token is
  an ``admin`` (unchanged behaviour for CI/webhook/scripted callers).
* **Multi-user mode** — once any user is registered, anonymous access is
  closed. Callers log in (``POST /api/login``) to receive a session token and
  are authorised at their role (``viewer`` < ``reviewer`` < ``admin``). The
  static token, if also set, continues to act as an ``admin`` service account.

Passwords are hashed with :func:`hashlib.scrypt` (stdlib — no new dependency,
ground rule R7). Session tokens are random 256-bit strings; only their SHA-256
digest is persisted, so a database leak does not expose live tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from . import store

# ── Roles ────────────────────────────────────────────────────────────────────

ROLES = ("viewer", "reviewer", "admin")
_ROLE_RANK = {"viewer": 1, "reviewer": 2, "admin": 3}
DEFAULT_ROLE = "reviewer"

# scrypt work factors. n*r*128 bytes ≈ 16 MiB of memory per hash — enough to be
# GPU-hostile while staying well under a second on a laptop core.
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SCRYPT_MAXMEM = 64 * 1024 * 1024


def role_rank(role: str) -> int:
    return _ROLE_RANK.get(role, 0)


def role_at_least(role: str, minimum: str) -> bool:
    return role_rank(role) >= role_rank(minimum)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


# ── Password hashing ─────────────────────────────────────────────────────────


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    if salt is None:
        salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = encoded.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(bytes.fromhex(hash_hex)),
            maxmem=_SCRYPT_MAXMEM,
        )
        return hmac.compare_digest(derived, bytes.fromhex(hash_hex))
    except (ValueError, TypeError):
        return False


# ── User management ──────────────────────────────────────────────────────────


class AuthError(ValueError):
    """Raised for invalid user-management input (bad role, dup user, …)."""


def _public_user(record: dict[str, Any]) -> dict[str, Any]:
    """User record without the password hash — safe to return over the API."""
    return {
        "username": record.get("username"),
        "role": record.get("role"),
        "disabled": bool(record.get("disabled")),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
    }


def users_configured() -> bool:
    return store.user_count() > 0


def create_user(username: str, password: str, role: str = DEFAULT_ROLE) -> dict[str, Any]:
    username = (username or "").strip()
    if not username:
        raise AuthError("username is required")
    if len(password or "") < 8:
        raise AuthError("password must be at least 8 characters")
    if role not in ROLES:
        raise AuthError(f"role must be one of {', '.join(ROLES)}")
    if store.get_user(username) is not None:
        raise AuthError(f"user {username!r} already exists")
    now = _iso(_utc_now())
    record = {
        "username": username,
        "role": role,
        "password_hash": hash_password(password),
        "disabled": False,
        "created_at": now,
        "updated_at": now,
    }
    store.save_user(record)
    return _public_user(record)


def set_password(username: str, password: str) -> dict[str, Any]:
    record = store.get_user(username)
    if record is None:
        raise AuthError(f"no such user {username!r}")
    if len(password or "") < 8:
        raise AuthError("password must be at least 8 characters")
    record["password_hash"] = hash_password(password)
    record["updated_at"] = _iso(_utc_now())
    store.save_user(record)
    # Force re-login everywhere after a password change.
    for token_hash in _session_hashes_for(username):
        store.delete_session(token_hash)
    return _public_user(record)


def set_role(username: str, role: str) -> dict[str, Any]:
    if role not in ROLES:
        raise AuthError(f"role must be one of {', '.join(ROLES)}")
    record = store.get_user(username)
    if record is None:
        raise AuthError(f"no such user {username!r}")
    record["role"] = role
    record["updated_at"] = _iso(_utc_now())
    store.save_user(record)
    return _public_user(record)


def set_disabled(username: str, disabled: bool) -> dict[str, Any]:
    record = store.get_user(username)
    if record is None:
        raise AuthError(f"no such user {username!r}")
    record["disabled"] = bool(disabled)
    record["updated_at"] = _iso(_utc_now())
    store.save_user(record)
    if disabled:
        for token_hash in _session_hashes_for(username):
            store.delete_session(token_hash)
    return _public_user(record)


def delete_user(username: str) -> bool:
    return store.delete_user(username)


def list_users() -> list[dict[str, Any]]:
    return [_public_user(r) for r in store.list_users()]


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """Return the public user record on success, else None (constant-ish time)."""
    record = store.get_user(username)
    if record is None:
        # Do the hash work anyway so a missing user is not obviously faster.
        hash_password(password or "")
        return None
    if record.get("disabled"):
        return None
    if not verify_password(password or "", str(record.get("password_hash") or "")):
        return None
    return _public_user(record)


# ── Sessions ─────────────────────────────────────────────────────────────────


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_ttl() -> timedelta:
    try:
        hours = float(os.getenv("CODEPULSE_SESSION_TTL_HOURS", "12"))
    except ValueError:
        hours = 12.0
    return timedelta(hours=max(0.25, hours))


def _session_hashes_for(username: str) -> list[str]:
    conn = store._conn()  # noqa: SLF001 — internal helper, same package
    with store._LOCK:  # noqa: SLF001
        rows = conn.execute(
            "SELECT token_hash FROM sessions WHERE username = ?", (username,)
        ).fetchall()
    return [row[0] for row in rows]


def issue_session(username: str, role: str, ip: str = "") -> tuple[str, dict[str, Any]]:
    """Create a session and return ``(raw_token, record)``. Store the raw token
    nowhere — only its digest is persisted."""
    token = secrets.token_urlsafe(32)
    now = _utc_now()
    expires = now + _session_ttl()
    record = {
        "username": username,
        "role": role,
        "ip": ip,
        "created_at": _iso(now),
        "expires_at": _iso(expires),
    }
    store.put_session(_token_hash(token), username, _iso(expires), record)
    return token, record


def resolve_session(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    record = store.get_session(_token_hash(token))
    if record is None:
        return None
    try:
        expires = datetime.fromisoformat(str(record.get("expires_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    if expires <= _utc_now():
        store.delete_session(_token_hash(token))
        return None
    return record


def revoke_session(token: str) -> None:
    if token:
        store.delete_session(_token_hash(token))


def purge_expired() -> int:
    return store.purge_expired_sessions(_iso(_utc_now()))


# ── Bootstrap ────────────────────────────────────────────────────────────────


def ensure_bootstrap_admin() -> str | None:
    """Create an admin from ``CODEPULSE_ADMIN_USER`` / ``CODEPULSE_ADMIN_PASSWORD``
    if set and that user does not yet exist. Returns the username created, or
    None if nothing was done. Never overwrites an existing user."""
    username = (os.getenv("CODEPULSE_ADMIN_USER") or "").strip()
    password = os.getenv("CODEPULSE_ADMIN_PASSWORD") or ""
    if not username or not password:
        return None
    if store.get_user(username) is not None:
        return None
    try:
        create_user(username, password, role="admin")
    except AuthError:
        return None
    return username
