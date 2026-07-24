from __future__ import annotations

import argparse
import collections
import copy
import fnmatch
import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from . import (
    auth,
    context_engine,
    dependency_scan,
    jobqueue,
    patcher,
    publisher,
    sarif,
    semantic_js,
    snapshot,
    static_analysis,
    store,
    taint_analysis,
)

# ── Production constants ────────────────────────────────────────────────────
MAX_REQUEST_BODY = 16 * 1024 * 1024   # 16 MB hard limit on JSON request bodies
GZIP_MIN_SIZE   = 860                  # compress responses larger than this
STATIC_CACHE_TTL = 3600               # 1-hour cache for static assets
APP_VERSION     = "5.0.0"
REVIEW_TIMEOUT_DEFAULT = 3600         # hard cap on a single review subprocess (seconds)
LARGE_REVIEW_FILE_HINT = 20           # warn a whole-repo review this big may time out on local models
GENERATION_TIMEOUT_DEFAULT = 1200     # hard cap on a test/PR generation subprocess
VERIFY_TIMEOUT_DEFAULT = 900          # hard cap on the finding-verification subprocess
GENERATION_KINDS = {"tests", "pr"}
SESSION_COOKIE  = "cp_session"        # login session cookie name
_ADMIN_POST_PATHS = {
    "/api/policies",
    "/api/repos",
    "/api/sample/seed",
    "/api/demo/seed",
    "/api/users",
}


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
PRESS_KIT_DIR = PROJECT_ROOT / "press-kit"
DATA_DIR = PROJECT_ROOT / ".code-doctor"
RUNS_DIR = DATA_DIR / "runs"
AUDIT_LOG = DATA_DIR / "audit.jsonl"
REPOS_FILE = DATA_DIR / "repos.json"
POLICIES_FILE = DATA_DIR / "policies.json"
SUPPRESSIONS_FILE = DATA_DIR / "suppressions.json"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
REVIEW_PROFILE = Path(__file__).resolve().parent / "review_profile.toml"
DEFAULT_FILTERS = "*.py,*.js,*.jsx,*.ts,*.tsx,*.mjs,*.cjs"
DEFAULT_OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")


def brand_env(suffix: str, default: str = "") -> str:
    """Branded env var: CODEPULSE_<suffix> wins, CODE_DOCTOR_<suffix> is the
    legacy fallback so pre-rename deployments keep working unchanged."""
    return os.getenv(f"CODEPULSE_{suffix}") or os.getenv(f"CODE_DOCTOR_{suffix}") or default

# LLM provider registry. Cloud API keys are read from the SERVER environment
# only (key_env), never from a request payload. `local` providers talk to an
# OpenAI-compatible endpoint (Ollama) and need no key. `concurrency` is the
# default parallel-request budget: cloud APIs handle many in flight, so a
# whole-repo review finishes fast, whereas a single local GPU serializes.
LLM_PROVIDERS: dict[str, dict[str, Any]] = {
    "ollama": {
        "label": "Ollama (local)",
        "api_type": "openai",
        "base": None,  # derived from ollamaBase
        "key_env": (),
        "default_model": DEFAULT_MODEL,
        "local": True,
        "concurrency": 4,
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "api_type": "anthropic",
        "base": None,  # SDK default
        "key_env": ("ANTHROPIC_API_KEY", "CODEPULSE_ANTHROPIC_KEY", "CODE_DOCTOR_ANTHROPIC_KEY"),
        "default_model": "claude-haiku-4-5-20251001",
        "local": False,
        "concurrency": 8,
    },
    "openai": {
        "label": "OpenAI",
        "api_type": "openai",
        "base": "https://api.openai.com/v1/",
        "key_env": ("OPENAI_API_KEY", "CODEPULSE_OPENAI_KEY", "CODE_DOCTOR_OPENAI_KEY"),
        "default_model": "gpt-4o-mini",
        "local": False,
        "concurrency": 8,
    },
    "google": {
        "label": "Google Gemini",
        "api_type": "google",
        "base": None,
        "key_env": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "default_model": "gemini-2.0-flash",
        "local": False,
        "concurrency": 8,
    },
}
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SENSITIVE_PATTERNS = [
    ".env",
    ".env.*",
    "*secret*",
    "*credential*",
    "*token*",
    "id_rsa",
    "*.pem",
    "*.key",
]
DEFAULT_POLICIES = {
    "risk": {
        "blockSeverity": 1,
        "reviewSeverity": 2,
        "maxRiskScore": 24,
        "sensitiveFileReview": True,
    },
    "coverage": {
        "requireTestsForPython": True,
        "requireTestsForNode": True,
        "watchPatterns": ["tests/*", "test/*", "*.spec.ts", "*.test.ts", "*_test.py"],
    },
    "ci": {
        # Webhook-triggered reviews stay dashboard-only unless a human opts in.
        "autoPublish": False,
        "failOn": "block",
    },
    "models": {
        # Workspace defaults for per-pass model routing; empty = inherit the
        # run's main model. Per-run verifyModel/generateModel win over these.
        "verify": "",
        "generate": "",
    },
    "guardrails": [
        {
            "id": "private-model",
            "name": "Private model runtime",
            "enabled": True,
            "evidence": "Ollama/OpenAI-compatible local endpoint",
        },
        {
            "id": "audit-retention",
            "name": "Review audit retention",
            "enabled": True,
            "evidence": "SQLite audit store + JSONL evidence mirror",
        },
        {
            "id": "mentor-profile",
            "name": "Junior developer review profile",
            "enabled": True,
            "evidence": "Python and Node.js high-confidence policy prompt",
        },
        {
            "id": "hybrid-static-analysis",
            "name": "Deterministic static analysis",
            "enabled": True,
            "evidence": "Secret, injection, and debug-leftover rule pack on every run",
        },
        {
            "id": "token-auth",
            "name": "Workspace access token",
            "enabled": bool(brand_env("TOKEN")),
            "evidence": "CODEPULSE_TOKEN",
        },
    ],
}

PROCESS_LOCK = threading.Lock()
PROCESSES: dict[str, subprocess.Popen] = {}

# Item 6: each SSE viewer holds one server thread; cap them so a tab storm
# cannot exhaust the ThreadingHTTPServer. Excess viewers get 429 and the UI
# silently stays on its 5-second polling (kept as the permanent fallback).
SSE_SEMAPHORE = threading.Semaphore(8)
SSE_MAX_SECONDS = 2 * 3600
SSE_TICK_SECONDS = 0.5

# Secrets the review/generation subprocesses never need (QW-5). Gito and the
# generator only consume the LLM_* contract; handing them workspace or
# publishing tokens widens the blast radius of any subprocess compromise.
SUBPROCESS_ENV_STRIP = (
    "CODEPULSE_TOKEN",
    "CODE_DOCTOR_TOKEN",
    "CODEPULSE_GITHUB_TOKEN",
    "CODE_DOCTOR_GITHUB_TOKEN",
    "GITHUB_TOKEN",
    "CODEPULSE_GITLAB_TOKEN",
    "CODE_DOCTOR_GITLAB_TOKEN",
    "GITLAB_TOKEN",
    "CODEPULSE_WEBHOOK_SECRET",
    "CODE_DOCTOR_WEBHOOK_SECRET",
)

# Browser security headers (QW-4): forms never post cross-origin and plugins
# are never embedded, so lock both down explicitly.
CSP_POLICY = (
    "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
    "form-action 'self'; object-src 'none'"
)

# ── Auth-failure throttling (QW-3) ──────────────────────────────────────────
# In-memory per-client-IP counter: 10 consecutive 401s within 60s → 429 for
# 60s. Resets on any successful auth and on restart — adequate for a
# local/team tool without persisting attacker state.
AUTH_THROTTLE_LIMIT = 10
AUTH_THROTTLE_WINDOW = 60.0
_AUTH_THROTTLE_LOCK = threading.Lock()
_AUTH_FAILURES: dict[str, dict[str, float]] = {}


def auth_throttled(client_ip: str, now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    with _AUTH_THROTTLE_LOCK:
        entry = _AUTH_FAILURES.get(client_ip)
        return bool(entry and entry.get("blocked_until", 0.0) > now)


def record_auth_failure(client_ip: str, now: float | None = None) -> bool:
    """Count a 401 for this IP. Returns True when the IP just became throttled."""
    now = time.monotonic() if now is None else now
    with _AUTH_THROTTLE_LOCK:
        entry = _AUTH_FAILURES.setdefault(
            client_ip, {"count": 0.0, "window_start": now, "blocked_until": 0.0}
        )
        if now - entry["window_start"] > AUTH_THROTTLE_WINDOW:
            entry["count"] = 0.0
            entry["window_start"] = now
        entry["count"] += 1
        if entry["count"] >= AUTH_THROTTLE_LIMIT:
            entry["blocked_until"] = now + AUTH_THROTTLE_WINDOW
            entry["count"] = 0.0
            entry["window_start"] = now
            return True
        return False


def clear_auth_failures(client_ip: str) -> None:
    with _AUTH_THROTTLE_LOCK:
        _AUTH_FAILURES.pop(client_ip, None)


def bind_warning(host: str) -> str:
    """QW-2: a non-loopback bind with no authentication exposes every endpoint."""
    if host in {"127.0.0.1", "localhost", "::1"} or auth_required():
        return ""
    return (
        f"WARNING: CodePulse is binding to {host} without authentication. "
        "Anyone who can reach this address can read reviews and start runs. "
        "Set CODEPULSE_TOKEN, register a user, or bind to 127.0.0.1."
    )


def tls_enabled() -> bool:
    """True when both a TLS cert and key are configured (CODEPULSE_TLS_CERT/KEY)."""
    return bool(brand_env("TLS_CERT") and brand_env("TLS_KEY"))


def _ssl_context() -> ssl.SSLContext | None:
    """Build a server TLS context from CODEPULSE_TLS_CERT / CODEPULSE_TLS_KEY,
    or None when TLS is not configured. Raises on a bad cert/key so startup
    fails loudly rather than silently serving plaintext."""
    cert, key = brand_env("TLS_CERT"), brand_env("TLS_KEY")
    if not (cert and key):
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=cert, keyfile=key)
    return context

# ── ETag cache (path → etag string) ────────────────────────────────────────
_ETAG_CACHE: dict[str, str] = {}
_ETAG_LOCK  = threading.Lock()


def static_etag(path: Path) -> str:
    """Return a stable ETag for a static file based on mtime + size."""
    key = str(path)
    try:
        stat = path.stat()
        sig  = f"{stat.st_mtime_ns}-{stat.st_size}"
    except OSError:
        return '""'
    with _ETAG_LOCK:
        cached = _ETAG_CACHE.get(key)
    if cached and cached.startswith(f'"{sig[:8]}'):
        return cached
    digest = hashlib.md5(sig.encode()).hexdigest()[:12]
    etag   = f'"{sig[:8]}-{digest}"'
    with _ETAG_LOCK:
        _ETAG_CACHE[key] = etag
    return etag


def maybe_gzip(body: bytes, accept_encoding: str) -> tuple[bytes, str]:
    """Return (body, content-encoding) — gzip if client accepts and worth it."""
    if len(body) < GZIP_MIN_SIZE or "gzip" not in accept_encoding:
        return body, "identity"
    compressed = gzip.compress(body, compresslevel=6)
    return compressed, "gzip"


def fnmatch_match(value: str, pattern: str) -> bool:
    return fnmatch.fnmatch(value.lower(), pattern.lower())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def merge_dicts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def run_dir(run_id: str) -> Path:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("Invalid review run id.")
    root = RUNS_DIR.resolve(strict=False)
    path = (RUNS_DIR / run_id).resolve(strict=False)
    if path != root and root not in path.parents:
        raise ValueError("Invalid review run id.")
    return path


def meta_path(run_id: str) -> Path:
    return run_dir(run_id) / "meta.json"


def report_path(run_id: str) -> Path:
    return run_dir(run_id) / "code-review-report.json"


def classify_review_outcome(
    exit_code: int, timed_out: bool, have_report: bool, have_findings: bool
) -> tuple[str, bool, str]:
    """Decide a review's final status with graceful degradation.

    A clean LLM run (exit 0) with a report is ``completed``. If the LLM pass
    fails or times out but the deterministic engines still produced findings,
    the run is ``completed`` and ``degraded`` (the caller surfaces the reason)
    rather than a bare ``failed`` that discards real results. Only a run with no
    usable report/findings is ``failed``.
    """
    llm_ok = exit_code == 0
    if have_report and (llm_ok or have_findings):
        if llm_ok:
            return "completed", False, ""
        reason = "llm-timeout" if timed_out else "llm-error"
        return "completed", True, reason
    return "failed", False, ""


def _report_has_findings(run_id: str) -> bool:
    """True when the review report contains at least one issue (from any
    engine). Used to decide whether a failed/timed-out LLM pass still left a
    usable, deterministic result behind."""
    report = read_json(report_path(run_id), None)
    if not isinstance(report, dict):
        return False
    issues = report.get("issues")
    if isinstance(issues, dict):
        return any(bool(v) for v in issues.values())
    if isinstance(issues, list):
        return bool(issues)
    return False


def markdown_path(run_id: str) -> Path:
    return run_dir(run_id) / "code-review-report.md"


def tests_json_path(run_id: str) -> Path:
    return run_dir(run_id) / "generated-tests.json"


def pr_draft_json_path(run_id: str) -> Path:
    return run_dir(run_id) / "pr-draft.json"


def verification_path(run_id: str) -> Path:
    return run_dir(run_id) / "verification.json"


def context_pack_path(run_id: str) -> Path:
    return run_dir(run_id) / "context-pack.json"


def publish_result_path(run_id: str) -> Path:
    return run_dir(run_id) / "publish.json"


def log_path(run_id: str) -> Path:
    return run_dir(run_id) / "gito.log"


def update_meta(run_id: str, **updates: Any) -> dict[str, Any]:
    path = meta_path(run_id)
    meta = read_json(path, {})
    meta.update(updates)
    meta["updated_at"] = utc_now()
    atomic_write_json(path, meta)
    return meta


def audit_event(event: str, **payload: Any) -> None:
    record = {"ts": utc_now(), "event": event, **payload}
    store.append_audit(record)
    # Human-readable JSONL mirror for evidence exports and offline review.
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def read_audit(limit: int = 100) -> list[dict[str, Any]]:
    return store.read_audit(limit)


def load_policies() -> dict[str, Any]:
    stored = read_json(POLICIES_FILE, {})
    policies = merge_dicts(copy.deepcopy(DEFAULT_POLICIES), stored or {})
    for guardrail in policies.get("guardrails", []):
        if guardrail.get("id") == "token-auth":
            guardrail["enabled"] = auth_required()
            guardrail["evidence"] = _auth_mode_label()
    return policies


def save_policies(payload: dict[str, Any]) -> dict[str, Any]:
    policies = merge_dicts(load_policies(), payload)
    atomic_write_json(POLICIES_FILE, policies)
    audit_event("policies_updated", policy_count=len(policies.get("guardrails", [])))
    return policies


def git_output(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def run_git(repo_path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo_path), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def repo_metadata(repo_path: Path) -> dict[str, Any]:
    branch = git_output(repo_path, "branch", "--show-current") or "detached"
    remote = git_output(repo_path, "remote", "get-url", "origin")
    commit = git_output(repo_path, "rev-parse", "--short", "HEAD")
    status = git_output(repo_path, "status", "--short")
    files = git_output(repo_path, "ls-files").splitlines()
    manifests = [name for name in ("pyproject.toml", "requirements.txt", "package.json") if (repo_path / name).exists()]
    languages = []
    if any(file.endswith(".py") for file in files) or any(name in manifests for name in ("pyproject.toml", "requirements.txt")):
        languages.append("Python")
    if any(file.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")) for file in files) or "package.json" in manifests:
        languages.append("Node.js")
    return {
        "branch": branch,
        "remote": remote,
        "commit": commit,
        "dirtyFiles": len(status.splitlines()) if status else 0,
        "trackedFiles": len(files),
        "languages": languages or ["Unknown"],
        "manifests": manifests,
    }


def write_sample_files(repo_path: Path, vulnerable: bool) -> None:
    files = {
        "services/payments/refunds.py": (
            "def create_refund(account_id, amount):\n"
            "    return {\"account_id\": account_id, \"amount\": amount}\n\n"
            "def refund(request, current_user):\n"
            "    account_id = request.json['account_id']\n"
            "    refund_result = create_refund(account_id, request.json['amount'])\n"
            "    return refund_result\n"
            if vulnerable
            else
            "def create_refund(account_id, amount):\n"
            "    return {\"account_id\": account_id, \"amount\": amount}\n\n"
            "def load_account_for_user(user_id, account_id):\n"
            "    return {\"id\": account_id, \"user_id\": user_id}\n\n"
            "def refund(request, current_user):\n"
            "    account = load_account_for_user(current_user.id, request.json['account_id'])\n"
            "    refund_result = create_refund(account['id'], request.json['amount'])\n"
            "    return refund_result\n"
        ),
        "web/src/routes/invite.ts": (
            "export async function invite(req, res) {\n"
            "  const token = createInviteToken(req.body.email)\n"
            "  sendInviteEmail(req.body.email, token)\n"
            "  return res.status(202).json({ ok: true })\n"
            "}\n"
            if vulnerable
            else
            "export async function invite(req, res) {\n"
            "  const token = createInviteToken(req.body.email)\n"
            "  await sendInviteEmail(req.body.email, token)\n"
            "  return res.status(202).json({ ok: true })\n"
            "}\n"
        ),
        ".env.example": (
            "PAYMENT_GATEWAY_KEY=pk_live_123456789\n"
            if vulnerable
            else
            "PAYMENT_GATEWAY_KEY=replace-with-local-test-key\n"
        ),
        "pyproject.toml": "[project]\nname = \"acme-payments-api\"\nversion = \"0.1.0\"\n",
        "package.json": "{\n  \"name\": \"acme-payments-web\",\n  \"private\": true\n}\n",
    }
    for relative_path, content in files.items():
        file_path = repo_path / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")


def ensure_sample_repo() -> Path:
    repo_path = DATA_DIR / "sample-repos" / "acme-payments-api"
    repo_path.mkdir(parents=True, exist_ok=True)
    if not (repo_path / ".git").exists():
        subprocess.run(["git", "init", str(repo_path)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        run_git(repo_path, "config", "user.email", "code-doctor@example.local")
        run_git(repo_path, "config", "user.name", "CodePulse")
        write_sample_files(repo_path, vulnerable=False)
        run_git(repo_path, "add", ".")
        run_git(repo_path, "commit", "-m", "Initial safe payment flow")
    try:
        run_git(repo_path, "checkout", "-B", "feature/refunds-v2")
    except subprocess.CalledProcessError:
        pass
    write_sample_files(repo_path, vulnerable=True)
    return repo_path.resolve()


def list_repos() -> list[dict[str, Any]]:
    repos = store.list_repos()
    reviews = list_reviews()
    for repo in repos:
        repo_reviews = [item for item in reviews if item.get("repo_path") == repo.get("path")]
        repo["lastReview"] = repo_reviews[0] if repo_reviews else None
    return repos


def register_repo(payload: dict[str, Any]) -> dict[str, Any]:
    review_path, source_path, is_snapshot = resolve_review_target(
        payload.get("path") or payload.get("repoPath") or ""
    )
    now = utc_now()
    existing = store.get_repo_by_path(str(source_path))
    metadata = repo_metadata(review_path)
    if is_snapshot:
        metadata = {**metadata, "snapshot": True}
    repo = {
        "id": existing.get("id") if existing else uuid.uuid5(uuid.NAMESPACE_URL, str(source_path)).hex[:12],
        "name": (payload.get("name") or source_path.name).strip(),
        "path": str(source_path),
        "is_snapshot": is_snapshot,
        "owner": (payload.get("owner") or "Engineering").strip(),
        "tier": payload.get("tier") or "production",
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
        "metadata": metadata,
    }
    store.save_repo(repo)
    audit_event("repo_registered", repo_path=str(source_path), repo_id=repo["id"], snapshot=is_snapshot)
    return repo


def delete_repo(repo_id: str) -> None:
    if not store.delete_repo(repo_id):
        raise FileNotFoundError(repo_id)
    audit_event("repo_removed", repo_id=repo_id)


def normalize_ollama_base(value: str | None) -> str:
    base = (value or DEFAULT_OLLAMA_BASE).strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base or DEFAULT_OLLAMA_BASE


def openai_compatible_base(ollama_base: str) -> str:
    return normalize_ollama_base(ollama_base) + "/v1/"


def review_options(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "working").strip()
    refs = str(payload.get("refs") or "").strip()
    what = str(payload.get("what") or "").strip()
    against = str(payload.get("against") or "").strip()
    use_merge_base = payload.get("mergeBase") is not False

    if mode not in {"working", "refs", "all"}:
        raise ValueError("Unsupported review mode.")

    if mode == "all":
        refs = what = against = ""
        use_merge_base = False
    elif mode == "working":
        if not against:
            against = "HEAD"
            use_merge_base = False
    elif refs and against:
        if ".." in refs:
            raise ValueError("Use either a refs pair or a separate Against ref, not both.")
        what = what or refs
        refs = ""

    return {
        "mode": mode,
        "refs": refs,
        "what": what,
        "against": against,
        "filters": (payload.get("filters") or DEFAULT_FILTERS).strip(),
        "use_merge_base": use_merge_base,
    }


def require_git_repo(repo_path: str) -> Path:
    path = Path(repo_path).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError("Repository path does not exist or is not a directory.")
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise ValueError("Repository path is not inside a git work tree.")
    root = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    return Path(root).resolve()


def resolve_review_target(raw_path: str) -> tuple[Path, Path, bool]:
    """Resolve a user path to ``(review_path, source_path, is_snapshot)``.

    A git work tree is reviewed in place. A plain local folder is materialized
    into a managed git snapshot under ``SNAPSHOTS_DIR`` — the user's folder is
    copied, never modified — so the entire diff-based pipeline works unchanged.
    ``review_path`` is always a real git work tree; ``source_path`` is what the
    user pointed at (used for identity and display).
    """
    text = str(raw_path or "").strip()
    if not text:
        raise ValueError("Provide a repository path.")
    source = Path(text).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise ValueError("Repository path does not exist or is not a directory.")
    if snapshot.reviewable_in_place(source):
        top = require_git_repo(str(source))
        return top, top, False
    # Non-git folders and git repos with no commits (unborn HEAD) can't be
    # diffed in place — both are reviewed from a managed snapshot.
    review_path = snapshot.build_snapshot(source, SNAPSHOTS_DIR)
    return review_path, source, True


def resolve_review_payload(
    payload: dict[str, Any],
) -> tuple[Path, Path, bool, dict[str, Any]]:
    """Resolve ``repoPath`` and, for a non-git folder, force whole-tree scope.

    Returns ``(review_path, source_path, is_snapshot, effective_payload)``. A
    snapshot only has one baseline commit, so its review always diffs the empty
    tree against HEAD (every file reads as added) regardless of the requested
    mode.
    """
    review_path, source_path, is_snapshot = resolve_review_target(payload.get("repoPath") or "")
    payload = dict(payload)
    if is_snapshot:
        payload["mode"] = "refs"
        payload["refs"] = snapshot.SNAPSHOT_REFS
        payload["what"] = ""
        payload["against"] = ""
        payload["mergeBase"] = False
    return review_path, source_path, is_snapshot, payload


def build_review_command(
    payload: dict[str, Any],
    out_dir: Path,
    options: dict[str, Any] | None = None,
) -> list[str]:
    cmd = [sys.executable, "-m", "gito", "review"]
    options = options or review_options(payload)
    mode = options["mode"]
    refs = options["refs"]
    what = options["what"]
    against = options["against"]

    if mode == "all":
        cmd.append("--all")
    elif refs:
        cmd.append(refs)
    else:
        if what:
            cmd.extend(["--what", what])
        if against:
            cmd.extend(["--against", against])

    if not options["use_merge_base"]:
        cmd.append("--no-merge-base")

    if options["filters"]:
        cmd.extend(["--filter", options["filters"]])

    cmd.extend(["--out", str(out_dir)])
    return cmd


SECURITY_TAGS = {"security", "secret-handling", "input-validation"}
TEXT_SAMPLE_LIMIT = 200_000
MAX_GENERATED_TEST_CASES = 30
AI_TEST_SIGNAL_TERMS = {
    "prompt": (
        "prompt",
        "system_prompt",
        "developer_prompt",
        "system message",
        "jailbreak",
        "instruction",
    ),
    "rag": (
        "rag",
        "retriever",
        "retrieval",
        "vector",
        "embedding",
        "chunk",
        "citation",
        "grounding",
        "context window",
    ),
    "agent": (
        "agent",
        "tool_call",
        "tool call",
        "function_call",
        "function call",
        "tool schema",
        "tool_choice",
        "planner",
    ),
    "schema": (
        "json_schema",
        "response_format",
        "output_schema",
        "structured output",
        "pydantic",
        "zod",
        "schema validation",
    ),
    "model": (
        "openai",
        "anthropic",
        "ollama",
        "llm",
        "chatcompletion",
        "responses.create",
        "temperature",
        "max_tokens",
        "rate limit",
    ),
    "eval": (
        "evals",
        "evaluation",
        "benchmark",
        "golden set",
        "scorecard",
        "grader",
    ),
}
AI_TEST_TEMPLATES = {
    "prompt": {
        "type": "ai-prompt-injection",
        "title": "Prompt-injection regression for {file}",
        "rationale": (
            "Changed prompt or instruction code should resist hostile user instructions "
            "and avoid leaking hidden context."
        ),
        "steps": [
            "Send a normal task with realistic user input.",
            "Send a jailbreak-style request that asks the model to ignore system instructions.",
            "Compare the response against the expected policy and output contract.",
        ],
        "expected": (
            "The response follows system and developer instructions, does not reveal hidden "
            "context, and keeps the expected response shape."
        ),
        "automation_hint": "Add benign and hostile prompts to the AI eval suite.",
    },
    "rag": {
        "type": "ai-rag-grounding",
        "title": "RAG grounding regression for {file}",
        "rationale": (
            "Retrieval changes should keep answers grounded in retrieved sources and handle "
            "empty or noisy context predictably."
        ),
        "steps": [
            "Run a query with a relevant retrieved document.",
            "Run the same query with irrelevant or empty retrieved context.",
            "Assert citations or source references match the retrieved documents.",
        ],
        "expected": (
            "Answers use only retrieved evidence when available and abstain or ask for more "
            "context when evidence is missing."
        ),
        "automation_hint": "Use a small fixture corpus with positive, noisy, and empty retrieval cases.",
    },
    "agent": {
        "type": "ai-tool-guardrail",
        "title": "Agent tool guardrail regression for {file}",
        "rationale": (
            "Agent and tool-call paths should validate tool names, arguments, and permissions "
            "before side effects happen."
        ),
        "steps": [
            "Invoke an allowed tool with valid arguments.",
            "Invoke a disallowed tool or malformed arguments through a model response.",
            "Assert rejected tool calls do not execute side effects.",
        ],
        "expected": (
            "Only allowlisted tools with schema-valid arguments execute, and rejected calls "
            "return a controlled error."
        ),
        "automation_hint": "Mock tools and assert call counts, arguments, and rejection errors.",
    },
    "schema": {
        "type": "ai-schema-contract",
        "title": "Structured-output contract regression for {file}",
        "rationale": (
            "AI outputs that cross code boundaries should be validated before application "
            "logic trusts them."
        ),
        "steps": [
            "Return a valid model response that matches the schema.",
            "Return malformed JSON or a response missing required fields.",
            "Assert the parser retries, falls back, or returns a controlled validation error.",
        ],
        "expected": "Invalid model output is rejected before downstream code uses it.",
        "automation_hint": "Mock the model client with valid and invalid structured responses.",
    },
    "model": {
        "type": "ai-model-resilience",
        "title": "Model failure-path regression for {file}",
        "rationale": (
            "Model client changes should handle provider errors without duplicate side "
            "effects or silent success."
        ),
        "steps": [
            "Mock a successful model response.",
            "Mock timeout, rate-limit, and malformed-response failures.",
            "Assert retry limits, fallback behavior, and user-facing errors.",
        ],
        "expected": (
            "Failures are bounded, logged, and surfaced without marking the AI workflow as "
            "successful."
        ),
        "automation_hint": "Stub the provider client and assert retry count plus final status.",
    },
    "eval": {
        "type": "ai-eval-coverage",
        "title": "AI eval coverage regression for {file}",
        "rationale": (
            "Evaluation code should keep a stable golden set and fail when output quality or "
            "policy compliance regresses."
        ),
        "steps": [
            "Run the golden set before and after the change.",
            "Include at least one negative or refusal case.",
            "Assert score thresholds and failure reporting are deterministic.",
        ],
        "expected": "The eval run fails on meaningful quality, grounding, or policy regressions.",
        "automation_hint": "Add the case to the local eval harness or CI eval job.",
    },
}


def issue_fingerprint(issue: dict[str, Any]) -> str:
    """Stable identity for an issue across runs: file + tags + normalized title."""
    title = re.sub(r"[^a-z ]+", "", str(issue.get("title", "")).lower())
    title = " ".join(title.split())
    tags = ",".join(sorted(issue.get("tags") or []))
    key = f"{issue.get('file', '')}|{tags}|{title}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def unique_nonempty(values: list[Any] | tuple[Any, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def coverage_watch_patterns() -> list[str]:
    patterns = load_policies().get("coverage", {}).get("watchPatterns", [])
    return [str(pattern) for pattern in patterns] if isinstance(patterns, list) else []


def classify_scope_files(files: list[str]) -> dict[str, list[str]]:
    changed = unique_nonempty(files)
    watch_patterns = coverage_watch_patterns()
    return {
        "changed": changed,
        "sensitive": [
            file
            for file in changed
            if any(fnmatch_match(file, pattern) for pattern in SENSITIVE_PATTERNS)
        ],
        "tests": [
            file
            for file in changed
            if any(fnmatch_match(file, pattern) for pattern in watch_patterns)
        ],
    }


def collect_scope_files(repo_path: Path, options: dict[str, Any]) -> list[str]:
    return static_analysis.collect_changed_files(
        repo_path,
        mode=options["mode"],
        refs=options["refs"],
        what=options["what"],
        against=options["against"],
        use_merge_base=options["use_merge_base"],
        filters=options["filters"],
    )


def _stable_test_case_id(*parts: Any) -> str:
    key = "|".join(str(part or "") for part in parts)
    return "tc-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def _test_runner_hint(file: str) -> str:
    lower = file.lower()
    if lower.endswith(".py"):
        return "pytest"
    if lower.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        return "vitest or jest"
    return "project test runner"


def _issue_priority(issue: dict[str, Any]) -> str:
    severity = issue.get("severity")
    tags = set(issue.get("tags") or [])
    if severity == 1 or SECURITY_TAGS.intersection(tags):
        return "P0"
    if isinstance(severity, int) and severity <= 2:
        return "P1"
    return "P2"


def _issue_location(issue: dict[str, Any]) -> str:
    file = str(issue.get("file") or "")
    affected = issue.get("affected_lines") or []
    if affected and isinstance(affected[0], dict) and affected[0].get("start_line"):
        return f"{file}:{affected[0]['start_line']}"
    return file


def _issue_test_case(issue: dict[str, Any]) -> dict[str, Any]:
    file = str(issue.get("file") or "")
    title = str(issue.get("title") or "Review finding")
    tags = set(issue.get("tags") or [])
    location = _issue_location(issue)
    runner = _test_runner_hint(file)
    if "secret-handling" in tags:
        case_type = "secret-hygiene"
        case_title = f"Secret hygiene regression for {file}"
        rationale = "The reviewed change handles credential-like data and should reject production-looking secrets."
        steps = [
            "Load the affected configuration or template with placeholder credentials.",
            "Load the same path with a production-looking key or token.",
            "Assert the unsafe value is rejected, masked, or never persisted.",
        ]
        expected = "Production-looking credentials do not pass validation or appear in committed output."
    elif "async-flow" in tags:
        case_type = "async-failure-path"
        case_title = f"Async failure-path regression for {file}"
        rationale = "The finding involves async control flow where failures can be reported as success."
        steps = [
            "Mock the asynchronous dependency to resolve successfully.",
            "Mock the same dependency to reject or time out.",
            "Assert the handler awaits the dependency and returns the correct failure status.",
        ]
        expected = "The workflow only reports success after the async dependency succeeds."
    elif SECURITY_TAGS.intersection(tags):
        case_type = "security-regression"
        case_title = f"Security regression for {file}"
        rationale = "The finding changes a trust boundary and needs a negative test, not only a happy path."
        steps = [
            "Exercise the valid path with authorized or well-formed input.",
            "Exercise an unauthorized, malformed, or hostile input at the same boundary.",
            "Assert the unsafe path is rejected before side effects happen.",
        ]
        expected = "Invalid or unauthorized input is rejected at the boundary identified by the review."
    else:
        case_type = "finding-regression"
        case_title = f"Regression test for {title}"
        rationale = "The reviewed behavior should have a targeted test that fails before the fix."
        steps = [
            f"Create a fixture that reaches {location}.",
            "Exercise the behavior described by the review finding.",
            "Assert the corrected behavior and the failure mode.",
        ]
        expected = "The test fails with the reviewed bug present and passes after the fix."
    return {
        "type": case_type,
        "title": case_title,
        "priority": _issue_priority(issue),
        "file": file,
        "source": f"finding:{issue.get('id', '')}",
        "rationale": rationale,
        "steps": steps,
        "expected": expected,
        "automation_hint": f"Automate with {runner} near the affected code path.",
    }


def _read_file_sample(repo_path: Path | None, file: str) -> str:
    if not repo_path or not file:
        return ""
    try:
        root = repo_path.resolve(strict=False)
        target = (Path(file) if Path(file).is_absolute() else root / file).resolve(strict=False)
        if target != root and root not in target.parents:
            return ""
        if not target.exists() or not target.is_file() or target.stat().st_size > TEXT_SAMPLE_LIMIT:
            return ""
        return target.read_text(encoding="utf-8", errors="ignore")[:8000]
    except OSError:
        return ""


def _ai_signals_for_context(file: str, issue: dict[str, Any] | None = None, sample: str = "") -> list[str]:
    issue_text = ""
    if issue:
        issue_text = " ".join(
            [
                str(issue.get("title") or ""),
                str(issue.get("details") or ""),
                " ".join(str(tag) for tag in issue.get("tags") or []),
            ]
        )
    haystack = f"{file}\n{issue_text}\n{sample}".lower()
    signals = [
        signal
        for signal, terms in AI_TEST_SIGNAL_TERMS.items()
        if any(term in haystack for term in terms)
    ]
    return sorted(set(signals))


def _add_case(cases: list[dict[str, Any]], seen: set[tuple[str, str, str]], case: dict[str, Any]) -> None:
    if len(cases) >= MAX_GENERATED_TEST_CASES:
        return
    key = (str(case.get("type")), str(case.get("file")), str(case.get("title")))
    if key in seen:
        return
    case["id"] = _stable_test_case_id(case.get("type"), case.get("file"), case.get("title"), case.get("source"))
    seen.add(key)
    cases.append(case)


def _ai_test_case(signal: str, file: str, priority: str, source: str) -> dict[str, Any] | None:
    template = AI_TEST_TEMPLATES.get(signal)
    if not template:
        return None
    return {
        "type": template["type"],
        "title": template["title"].format(file=file),
        "priority": priority,
        "file": file,
        "source": source,
        "rationale": template["rationale"],
        "steps": list(template["steps"]),
        "expected": template["expected"],
        "automation_hint": template["automation_hint"],
    }


def _review_scope_files(meta: dict[str, Any], report: dict[str, Any] | None) -> list[str]:
    changed = meta.get("changed_files")
    if isinstance(changed, list) and changed:
        return unique_nonempty(changed)

    issue_files = unique_nonempty([issue.get("file") for issue in flatten_report_issues(report)])
    if issue_files:
        return issue_files

    repo_path = meta.get("repo_path")
    if not repo_path:
        return []
    try:
        return collect_scope_files(
            require_git_repo(repo_path),
            {
                "mode": meta.get("mode") or "working",
                "refs": meta.get("refs") or "",
                "what": meta.get("what") or "",
                "against": meta.get("against") or "HEAD",
                "filters": meta.get("filters") or DEFAULT_FILTERS,
                "use_merge_base": meta.get("merge_base") is not False,
            },
        )
    except Exception:
        return []


def generate_review_test_cases(meta: dict[str, Any], report: dict[str, Any] | None) -> dict[str, Any]:
    issues = flatten_report_issues(report)
    scope_files = _review_scope_files(meta, report)
    classified = classify_scope_files(scope_files)
    repo_path = Path(meta["repo_path"]) if meta.get("repo_path") else None
    samples = {file: _read_file_sample(repo_path, file) for file in classified["changed"]}
    cases: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for issue in issues:
        file = str(issue.get("file") or "")
        _add_case(cases, seen, _issue_test_case(issue))
        for signal in _ai_signals_for_context(file, issue, samples.get(file, "")):
            case = _ai_test_case(signal, file, _issue_priority(issue), f"finding:{issue.get('id', '')}")
            if case:
                _add_case(cases, seen, case)

    issue_files = {str(issue.get("file") or "") for issue in issues}
    for file in classified["changed"]:
        for signal in _ai_signals_for_context(file, sample=samples.get(file, "")):
            priority = "P1" if file in issue_files else "P2"
            case = _ai_test_case(signal, file, priority, f"scope:{file}")
            if case:
                _add_case(cases, seen, case)

    if classified["changed"] and not classified["tests"]:
        primary = classified["changed"][0]
        _add_case(
            cases,
            seen,
            {
                "type": "coverage-gap",
                "title": "Changed-scope coverage check",
                "priority": "P2",
                "file": primary,
                "source": "scope",
                "rationale": "The review scope changed application code without a matching test-file change.",
                "steps": [
                    "Identify the highest-risk changed behavior in this review scope.",
                    "Add or update a focused unit, integration, or eval test for that behavior.",
                    "Run the project test command and confirm the new test fails without the fix.",
                ],
                "expected": "The changed behavior has executable coverage tied to the review scope.",
                "automation_hint": f"Use {_test_runner_hint(primary)} or the repository's configured test runner.",
            },
        )

    ai_case_count = sum(1 for case in cases if str(case.get("type", "")).startswith("ai-"))
    return {
        "generated_at": utc_now(),
        "total": len(cases),
        "ai_app_cases": ai_case_count,
        "issue_cases": sum(1 for case in cases if str(case.get("source", "")).startswith("finding:")),
        "scope_files": classified["changed"],
        "test_files": classified["tests"],
        "cases": cases,
    }


def load_suppressions() -> dict[str, dict[str, Any]]:
    """Fingerprint → suppression record, learned from reviewer feedback."""
    return store.get_suppressions()


def record_finding_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    """Dismiss or restore a finding; dismissals suppress the finding's
    fingerprint from risk scoring in every future run."""
    run_id = str(payload.get("runId") or "").strip()
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"dismiss", "restore"}:
        raise ValueError("Unsupported feedback action. Use 'dismiss' or 'restore'.")
    issue_id = payload.get("issueId")
    report = read_json(report_path(run_id), None)
    if not report:
        raise FileNotFoundError(run_id)
    issue = next(
        (
            item | {"file": item.get("file") or file}
            for file, file_issues in (report.get("issues") or {}).items()
            for item in file_issues
            if str(item.get("id")) == str(issue_id)
        ),
        None,
    )
    if issue is None:
        raise FileNotFoundError(f"finding {issue_id}")
    fingerprint = issue_fingerprint(issue)
    if action == "dismiss":
        store.put_suppression(
            fingerprint,
            {
                "fingerprint": fingerprint,
                "file": issue.get("file"),
                "title": issue.get("title"),
                "reason": str(payload.get("reason") or "").strip()[:400],
                "run_id": run_id,
                "issue_id": issue_id,
                "created_at": utc_now(),
            },
        )
    else:
        store.delete_suppression(fingerprint)
    audit_event(
        "finding_feedback",
        run_id=run_id,
        issue_id=issue_id,
        action=action,
        fingerprint=fingerprint,
    )
    # Recompute stats so gates reflect the feedback immediately.
    meta = update_meta(run_id, stats=summarize_report(run_id))
    return {"fingerprint": fingerprint, "suppressed": action == "dismiss", "stats": meta.get("stats", {})}


def apply_verification(report: dict[str, Any], verification: dict[str, Any]) -> dict[str, int]:
    """Apply verifier verdicts to a report in place.

    Rejected findings are quarantined under ``rejected_issues`` (kept for audit
    and export, excluded from risk). Confirmed findings gain a ``verified`` tag.
    Deterministic static findings are never touched.
    """
    verdicts = {
        item.get("id"): item
        for item in (verification or {}).get("verdicts") or []
        if isinstance(item, dict)
    }
    counts = {"confirmed": 0, "rejected": 0, "uncertain": 0}
    rejected: dict[str, list[dict[str, Any]]] = {}
    issues = report.get("issues") or {}
    for file in list(issues):
        kept = []
        for issue in issues[file]:
            verdict_info = verdicts.get(issue.get("id"))
            if issue.get("source") == "static" or not verdict_info:
                kept.append(issue)
                continue
            verdict = str(verdict_info.get("verdict") or "").lower()
            reason = str(verdict_info.get("reason") or "")
            if verdict == "rejected":
                counts["rejected"] += 1
                rejected.setdefault(file, []).append(issue | {"verifier_reason": reason})
                continue
            if verdict == "confirmed":
                counts["confirmed"] += 1
                issue["verified"] = True
                tags = issue.setdefault("tags", [])
                if "verified" not in tags:
                    tags.append("verified")
            else:
                counts["uncertain"] += 1
                issue["verified"] = False
            issue["verifier_reason"] = reason
            kept.append(issue)
        if kept:
            issues[file] = kept
        else:
            issues.pop(file, None)
    if rejected:
        report["rejected_issues"] = rejected
    report["verification"] = counts
    report["total_issues"] = sum(len(file_issues) for file_issues in issues.values())
    return counts


def previous_fingerprints(run_id: str, repo_path: str, created_at: str) -> set[str] | None:
    """Fingerprints from the most recent earlier completed run of the same repo."""
    best: tuple[str, list[str]] | None = None
    for path in RUNS_DIR.glob("*/meta.json"):
        meta = read_json(path, {})
        if meta.get("id") == run_id or meta.get("repo_path") != repo_path:
            continue
        if meta.get("status") != "completed":
            continue
        fingerprints = (meta.get("stats") or {}).get("fingerprints")
        if fingerprints is None:
            continue
        when = meta.get("created_at", "")
        if created_at and when >= created_at:
            continue
        if best is None or when > best[0]:
            best = (when, fingerprints)
    return set(best[1]) if best else None


def verdict_fingerprint(issue: dict[str, Any]) -> str:
    """Fingerprint for verdict matching, ignoring verifier decoration: a prior
    confirmed finding carries a "verified" tag its fresh twin does not."""
    tags = [tag for tag in (issue.get("tags") or []) if tag != "verified"]
    return issue_fingerprint(issue | {"tags": tags})


def issue_line_span(issue: dict[str, Any]) -> tuple[int, int] | None:
    """Overall (first, last) affected line of a finding, or None."""
    spans = [
        (block["start_line"], block.get("end_line") or block["start_line"])
        for block in issue.get("affected_lines") or []
        if isinstance(block, dict) and isinstance(block.get("start_line"), int)
    ]
    if not spans:
        return None
    return min(start for start, _ in spans), max(
        end if isinstance(end, int) else start for start, end in spans
    )


def previous_run_context(run_id: str, repo_path: str, created_at: str) -> dict[str, Any]:
    """Verifier verdicts from the most recent earlier completed review of the
    same repo. ``verdicts`` is keyed by issue fingerprint for exact matches;
    ``by_file`` holds per-file candidates (line span + tags) for the
    position-based fallback — LLM findings rarely keep identical wording
    across runs, so exact fingerprints alone would almost never carry.
    Empty dict when there is no prior run."""
    best: tuple[str, str] | None = None
    for path in RUNS_DIR.glob("*/meta.json"):
        meta = read_json(path, {})
        if meta.get("id") == run_id or meta.get("repo_path") != repo_path:
            continue
        if meta.get("status") != "completed" or (meta.get("kind") or "review") != "review":
            continue
        when = meta.get("created_at", "")
        if created_at and when >= created_at:
            continue
        if best is None or when > best[0]:
            best = (when, str(meta.get("id")))
    if not best:
        return {}
    prior_id = best[1]
    report = read_json(report_path(prior_id), {}) or {}
    verdicts: dict[str, dict[str, str]] = {}
    by_file: dict[str, list[dict[str, Any]]] = {}

    def record(file: str, issue: dict[str, Any], verdict: str) -> None:
        issue = issue | {"file": issue.get("file") or file}
        entry = {"verdict": verdict, "reason": str(issue.get("verifier_reason") or "")}
        verdicts[verdict_fingerprint(issue)] = entry
        by_file.setdefault(file, []).append(
            entry
            | {
                "span": issue_line_span(issue),
                "tags": {tag for tag in issue.get("tags") or [] if tag != "verified"},
            }
        )

    for file, file_issues in (report.get("issues") or {}).items():
        for issue in file_issues:
            if issue.get("verified") is True and issue.get("source") != "static":
                record(file, issue, "confirmed")
    for file, file_issues in (report.get("rejected_issues") or {}).items():
        for issue in file_issues:
            if issue.get("source") != "static":
                record(file, issue, "rejected")
    return {"run_id": prior_id, "verdicts": verdicts, "by_file": by_file}


def positional_verdict_match(
    issue: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, str] | None:
    """Fallback verdict match by location: carries only when exactly one prior
    verdict-bearing finding sits on overlapping lines (±3) of the same file
    and shares at least one tag. Any ambiguity means no carry."""
    span = issue_line_span(issue)
    if span is None:
        return None
    tags = {tag for tag in issue.get("tags") or [] if tag != "verified"}
    hits = [
        candidate
        for candidate in candidates
        if candidate.get("span") is not None
        and candidate["span"][0] - 3 <= span[1]
        and span[0] <= candidate["span"][1] + 3
        and tags & candidate["tags"]
    ]
    if len(hits) != 1:
        return None
    return {"verdict": hits[0]["verdict"], "reason": hits[0]["reason"]}


def maybe_reuse_verdicts(
    run_id: str, repo_path: Path, payload: dict[str, Any]
) -> tuple[dict[str, int], list[Any]]:
    """Item 2: stamp prior confirmed/rejected verdicts onto matching findings
    so only genuinely new findings reach the verifier LLM.

    Returns (counts, skip_ids) where skip_ids are the finding ids whose
    verdicts were carried and must be excluded from fresh verification.
    """
    counts = {"confirmed": 0, "rejected": 0}
    if payload.get("reuseVerdicts") is False:
        return counts, []
    meta = read_json(meta_path(run_id), {}) or {}
    prior = previous_run_context(run_id, str(repo_path), meta.get("created_at", ""))
    verdicts = prior.get("verdicts") or {}
    by_file = prior.get("by_file") or {}
    report = read_json(report_path(run_id), None)
    if not verdicts or not report:
        return counts, []
    prior_run_id = prior["run_id"]
    skip_ids: list[Any] = []
    issues = report.get("issues") or {}
    for file in list(issues):
        kept = []
        for issue in issues[file]:
            prior_verdict = None
            if issue.get("source") != "static":
                prior_verdict = verdicts.get(
                    verdict_fingerprint(issue | {"file": issue.get("file") or file})
                ) or positional_verdict_match(issue, by_file.get(file) or [])
            if not prior_verdict:
                kept.append(issue)
                continue
            reason = prior_verdict.get("reason") or ""
            if prior_verdict.get("verdict") == "confirmed":
                counts["confirmed"] += 1
                issue["verified"] = True
                issue["verifier_reason"] = reason
                issue["carried_from"] = prior_run_id
                tags = issue.setdefault("tags", [])
                if "verified" not in tags:
                    tags.append("verified")
                skip_ids.append(issue.get("id"))
                kept.append(issue)
            else:
                counts["rejected"] += 1
                report.setdefault("rejected_issues", {}).setdefault(file, []).append(
                    issue | {"verifier_reason": reason, "carried_from": prior_run_id}
                )
        if kept:
            issues[file] = kept
        else:
            issues.pop(file, None)
    if not (counts["confirmed"] or counts["rejected"]):
        return counts, []
    report["total_issues"] = sum(len(file_issues) for file_issues in issues.values())
    atomic_write_json(report_path(run_id), report)
    update_meta(run_id, reused_verdicts={**counts, "from_run": prior_run_id})
    audit_event("verdicts_reused", run_id=run_id, from_run=prior_run_id, **counts)
    return counts, skip_ids


def compute_risk_score(plain_issues: list[dict[str, Any]]) -> int:
    """Confidence-weighted severity score with a security multiplier."""
    severity_weights = {1: 10, 2: 6, 3: 3, 4: 1}
    confidence_weights = {1: 1.0, 2: 0.85, 3: 0.6, 4: 0.3}
    score = 0.0
    for issue in plain_issues:
        weight = severity_weights.get(issue.get("severity"), 0)
        weight *= confidence_weights.get(issue.get("confidence"), 0.85)
        if SECURITY_TAGS.intersection(issue.get("tags") or []):
            weight *= 1.25
        score += weight
    return min(100, int(score + 0.5))


def compute_health(stats: dict[str, Any]) -> dict[str, Any]:
    """Repository health for a completed run: 0-100 plus a letter grade.

    Derived only from frozen stats keys (risk_score, gate, severity_counts),
    so it can be recomputed identically for runs that predate the key.
    """
    risk = stats.get("risk_score")
    risk = int(risk) if isinstance(risk, (int, float)) else 0
    counts = stats.get("severity_counts") or {}

    def count(sev: str) -> int:
        value = counts.get(sev)
        return int(value) if isinstance(value, (int, float)) else 0

    score = 100 - risk - 5 * count("1") - 2 * count("2")
    gate = stats.get("gate")
    if gate == "block":
        score = min(score, 45)
    elif gate == "review":
        score = min(score, 75)
    score = max(0, min(100, score))
    grade = (
        "A" if score >= 90
        else "B" if score >= 75
        else "C" if score >= 60
        else "D" if score >= 40
        else "F"
    )
    return {"score": score, "grade": grade}


def summarize_report(run_id: str) -> dict[str, Any]:
    report = read_json(report_path(run_id), {})
    policies = load_policies()
    risk_policy = policies.get("risk", {})
    issues_by_file = report.get("issues") or {}
    plain_issues = [
        issue | {"file": file}
        for file, issues in issues_by_file.items()
        for issue in issues
    ]
    # Reviewer-dismissed fingerprints never count toward risk or gates.
    suppressed_fps = set(load_suppressions())
    scored_issues = [
        issue for issue in plain_issues if issue_fingerprint(issue) not in suppressed_fps
    ]
    severities = [
        severity for issue in scored_issues
        if isinstance((severity := issue.get("severity")), int)
    ]
    risk_score = compute_risk_score(scored_issues)
    tags = sorted({tag for issue in scored_issues for tag in issue.get("tags", [])})
    severity_counts = {
        str(severity): sum(1 for issue in scored_issues if issue.get("severity") == severity)
        for severity in (1, 2, 3, 4, 5)
    }
    sensitive_files = [
        file
        for file in issues_by_file
        if any(fnmatch_match(file, pattern) for pattern in SENSITIVE_PATTERNS)
    ]
    gate = "pass"
    if any(
        isinstance(issue.get("severity"), int)
        and issue.get("severity") <= risk_policy.get("blockSeverity", 1)
        for issue in scored_issues
    ):
        gate = "block"
    elif risk_score >= risk_policy.get("maxRiskScore", 24):
        gate = "block"
    elif any(
        isinstance(issue.get("severity"), int)
        and issue.get("severity") <= risk_policy.get("reviewSeverity", 2)
        for issue in scored_issues
    ):
        gate = "review"
    elif sensitive_files and risk_policy.get("sensitiveFileReview", True):
        gate = "review"

    carried = sum(1 for issue in plain_issues if issue.get("carried_from")) + sum(
        1
        for file_issues in (report.get("rejected_issues") or {}).values()
        for issue in file_issues
        if issue.get("carried_from")
    )
    verification = dict(report.get("verification") or {})
    if carried:
        verification["carried"] = carried

    fingerprints = sorted({issue_fingerprint(issue) for issue in plain_issues})
    meta = read_json(meta_path(run_id), {}) or {}
    baseline = previous_fingerprints(
        run_id, meta.get("repo_path", ""), meta.get("created_at", "")
    ) if meta.get("repo_path") else None
    current = set(fingerprints)
    lifecycle = {
        "new": len(current - baseline) if baseline is not None else len(current),
        "recurring": len(current & baseline) if baseline is not None else 0,
        "resolved": len(baseline - current) if baseline is not None else 0,
        "baselined": baseline is not None,
    }
    stats = {
        "summary": report.get("summary") or "",
        "total_issues": report.get("total_issues", len(plain_issues)),
        "processed_files": report.get("number_of_processed_files", 0),
        "highest_severity": min(severities) if severities else None,
        "risk_score": risk_score,
        "gate": gate,
        "tags": tags,
        "severity_counts": severity_counts,
        "sensitive_files": sensitive_files,
        "warnings": len(report.get("processing_warnings") or []),
        "static_issues": sum(1 for issue in plain_issues if issue.get("source") == "static"),
        "cross_file_issues": sum(1 for issue in plain_issues if issue.get("source") == "crossfile"),
        "taint_issues": sum(1 for issue in plain_issues if issue.get("source") == "taint"),
        "suppressed": len(plain_issues) - len(scored_issues),
        "verification": verification,
        "rejected_issues": sum(
            len(file_issues) for file_issues in (report.get("rejected_issues") or {}).values()
        ),
        "fingerprints": fingerprints,
        "lifecycle": lifecycle,
    }
    stats["health"] = compute_health(stats)
    return stats


def pass_model(payload: dict[str, Any], payload_key: str, policy_key: str) -> str:
    """Item 14: model override for a specific pass (verify/generate).

    Resolution: per-run payload key > workspace policy `models.<key>` > ""
    (empty = inherit the run's main model — today's behavior).
    """
    value = str(payload.get(payload_key) or "").strip()
    if value:
        return value
    models_policy = load_policies().get("models") or {}
    return str(models_policy.get(policy_key) or "").strip()


def resolve_provider(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Provider id + spec for a run. Falls back to policy, then Ollama."""
    name = str(
        payload.get("provider") or (load_policies().get("provider") or "ollama")
    ).strip().lower()
    spec = LLM_PROVIDERS.get(name)
    if spec is None:
        raise ValueError(
            f"Unknown LLM provider {name!r}. Choose one of: {', '.join(LLM_PROVIDERS)}."
        )
    return name, spec


def provider_api_key(spec: dict[str, Any]) -> str:
    """Cloud key from the server environment only — never the request payload."""
    for env_name in spec.get("key_env") or ():
        value = os.getenv(env_name)
        if value:
            return value.strip()
    return ""


def provider_configured(spec: dict[str, Any]) -> bool:
    return bool(spec.get("local")) or bool(provider_api_key(spec))


def subprocess_env(payload: dict[str, Any], model_override: str = "") -> dict[str, str]:
    """Environment for review/generation subprocesses: Gito's LLM_* contract."""
    env = os.environ.copy()
    for key in SUBPROCESS_ENV_STRIP:
        env.pop(key, None)
    python_path = str(PROJECT_ROOT)
    if env.get("PYTHONPATH"):
        python_path += os.pathsep + env["PYTHONPATH"]

    _, spec = resolve_provider(payload)
    model = (model_override or payload.get("model") or spec["default_model"]).strip()
    concurrency = int(payload.get("maxConcurrentTasks") or spec.get("concurrency") or 4)
    llm_env = {
        "PYTHONPATH": python_path,
        "LLM_API_TYPE": spec["api_type"],
        "MODEL": model,
        "MAX_CONCURRENT_TASKS": str(concurrency),
        "GITO_EXTRA_PROJECT_CONFIG": str(REVIEW_PROFILE),
    }
    if spec.get("local"):
        llm_env["LLM_API_KEY"] = "ollama"
        llm_env["LLM_API_BASE"] = openai_compatible_base(payload.get("ollamaBase"))
    else:
        key = provider_api_key(spec)
        llm_env["LLM_API_KEY"] = key
        # Also expose the provider's native key var so whichever microcore reads
        # is populated (the server env may already carry it; this is explicit).
        for env_name in spec.get("key_env") or ():
            if key:
                llm_env[env_name] = key
        if spec.get("base"):
            llm_env["LLM_API_BASE"] = spec["base"]
    env.update(llm_env)
    return env


def note_ollama_warning(run_id: str) -> None:
    """Item 9 pre-run guard: still attempt the run (Ollama may be back), but
    make a later failure explainable."""
    try:
        if OLLAMA_WATCHDOG.snapshot()["state"] != "down":
            return
        update_meta(run_id, ollama_warning=True)
        with log_path(run_id).open("ab") as log_file:
            log_file.write(
                b"CodePulse: warning - the Ollama watchdog reports the model "
                b"runtime down; attempting the run anyway.\n"
            )
    except Exception:  # noqa: BLE001 - advisory only
        pass


def run_review(run_id: str, repo_path: Path, payload: dict[str, Any], command: list[str]) -> None:
    started = time.monotonic()
    env = subprocess_env(payload)

    update_meta(run_id, status="running", started_at=utc_now())
    note_ollama_warning(run_id)
    audit_event(
        "review_started",
        run_id=run_id,
        repo_path=str(repo_path),
        model=(payload.get("model") or DEFAULT_MODEL).strip(),
    )

    # Deterministic passes run up front so their findings survive even a failed LLM run.
    static_issues: dict[str, list[dict[str, Any]]] = {}
    if payload.get("staticAnalysis") is not False:
        try:
            options = review_options(payload)
            static_issues = static_analysis.analyze_repo_changes(
                repo_path,
                mode=options["mode"],
                refs=options["refs"],
                what=options["what"],
                against=options["against"],
                use_merge_base=options["use_merge_base"],
                filters=options["filters"],
            )
        except Exception as exc:
            audit_event("static_analysis_failed", run_id=run_id, error=str(exc))

    crossfile_issues: dict[str, list[dict[str, Any]]] = {}
    if payload.get("crossFileAnalysis") is not False:
        try:
            options = review_options(payload)
            cross = context_engine.analyze_cross_file(
                repo_path,
                mode=options["mode"],
                refs=options["refs"],
                what=options["what"],
                against=options["against"],
                use_merge_base=options["use_merge_base"],
                filters=options["filters"],
            )
            crossfile_issues = cross["findings"]
            atomic_write_json(context_pack_path(run_id), cross["pack"])
        except Exception as exc:
            audit_event("cross_file_analysis_failed", run_id=run_id, error=str(exc))

    taint_issues: dict[str, list[dict[str, Any]]] = {}
    if payload.get("taintAnalysis") is not False:
        try:
            options = review_options(payload)
            taint_issues = taint_analysis.analyze_repo_changes(
                repo_path,
                mode=options["mode"],
                refs=options["refs"],
                what=options["what"],
                against=options["against"],
                use_merge_base=options["use_merge_base"],
                filters=options["filters"],
            )
        except Exception as exc:
            audit_event("taint_analysis_failed", run_id=run_id, error=str(exc))

    dep_issues: dict[str, list[dict[str, Any]]] = {}
    if payload.get("dependencyScan") is not False:
        try:
            options = review_options(payload)
            dep_issues = dependency_scan.analyze_repo_changes(
                repo_path,
                mode=options["mode"],
                refs=options["refs"],
                what=options["what"],
                against=options["against"],
                use_merge_base=options["use_merge_base"],
                filters=options["filters"],
            )
        except Exception as exc:
            audit_event("dependency_scan_failed", run_id=run_id, error=str(exc))

    def merge_static() -> int:
        if not static_issues and not crossfile_issues and not taint_issues and not dep_issues:
            return 0
        report = read_json(report_path(run_id), None) or static_analysis.empty_report()
        added = 0
        if static_issues:
            added += static_analysis.merge_into_report(report, static_issues)
        if crossfile_issues:
            added += context_engine.merge_into_report(report, crossfile_issues)
        if taint_issues:
            added += taint_analysis.merge_into_report(report, taint_issues)
        if dep_issues:
            added += dependency_scan.merge_into_report(report, dep_issues)
        atomic_write_json(report_path(run_id), report)
        return added

    timeout_seconds = int(payload.get("timeoutSeconds") or REVIEW_TIMEOUT_DEFAULT)
    timed_out = False
    with log_path(run_id).open("ab") as log_file:
        try:
            proc = subprocess.Popen(
                command,
                cwd=repo_path,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            with PROCESS_LOCK:
                PROCESSES[run_id] = proc
            try:
                exit_code = proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait()
                exit_code = -1
                log_file.write(
                    f"\nCodePulse: review timed out after {timeout_seconds}s and was killed.\n".encode("utf-8")
                )
        except Exception as exc:
            log_file.write(f"\nCodePulse failed to start Gito: {exc}\n".encode("utf-8"))
            static_count = merge_static()
            update_meta(
                run_id,
                status="failed",
                exit_code=None,
                error=str(exc),
                static_issues=static_count,
                completed_at=utc_now(),
                duration_seconds=round(time.monotonic() - started, 2),
                stats=summarize_report(run_id) if report_path(run_id).exists() else {},
            )
            return
        finally:
            with PROCESS_LOCK:
                PROCESSES.pop(run_id, None)

    static_count = merge_static()
    skip_ids: list[Any] = []
    if report_path(run_id).exists():
        # Verdict reuse must never fail a review (R3): fall back to full verification.
        try:
            _, skip_ids = maybe_reuse_verdicts(run_id, repo_path, payload)
        except Exception as exc:
            audit_event("verdict_reuse_failed", run_id=run_id, error=str(exc))
            skip_ids = []
    verification_counts: dict[str, int] = {}
    if payload.get("verifyFindings") is not False and report_path(run_id).exists():
        verification_counts = run_verification(run_id, repo_path, payload, env, skip_ids)
    # Graceful degradation: when the LLM subprocess fails or times out but the
    # deterministic engines (static / cross-file / taint / dependency) already
    # produced findings, the review is still useful — surface it as a completed
    # *degraded* run instead of throwing the deterministic results away as a
    # bare "failed". A whole-repo review that outgrows a local model no longer
    # comes back empty-handed.
    have_report = report_path(run_id).exists()
    have_findings = have_report and _report_has_findings(run_id)
    status, degraded, degraded_reason = classify_review_outcome(
        exit_code, timed_out, have_report, have_findings
    )
    stats = summarize_report(run_id) if have_report else {}
    update_meta(
        run_id,
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
        degraded=degraded,
        degraded_reason=degraded_reason,
        static_issues=static_count,
        verification=verification_counts,
        completed_at=utc_now(),
        duration_seconds=round(time.monotonic() - started, 2),
        stats=stats,
    )
    audit_event(
        "review_finished",
        run_id=run_id,
        repo_path=str(repo_path),
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
        degraded=degraded,
        degraded_reason=degraded_reason,
        static_issues=static_count,
        verification=verification_counts,
        stats=stats,
    )


def run_verification(
    run_id: str,
    repo_path: Path,
    payload: dict[str, Any],
    env: dict[str, str],
    skip_ids: list[Any] | None = None,
) -> dict[str, int]:
    """Run the skeptical second-pass verifier over the run's LLM findings.

    Findings whose ids are in ``skip_ids`` already carry a verdict reused from
    a previous run and are excluded; when nothing else remains, the verifier
    subprocess is not started at all.
    """
    skipped = {str(item) for item in (skip_ids or [])}
    report = read_json(report_path(run_id), {}) or {}
    has_llm_findings = any(
        issue.get("source") != "static" and str(issue.get("id")) not in skipped
        for file_issues in (report.get("issues") or {}).values()
        for issue in file_issues
    )
    if not has_llm_findings:
        if skipped:
            audit_event("verification_skipped", run_id=run_id, reason="all verdicts reused")
        return {}
    try:
        options = review_options(payload)
    except ValueError:
        return {}
    verify_model = pass_model(payload, "verifyModel", "verify")
    if verify_model:
        # Item 14: the verifier can run on a different (usually smaller) model.
        env = subprocess_env(payload, model_override=verify_model)
        update_meta(run_id, verify_model=verify_model)
    command = build_generation_command("verify", repo_path, run_dir(run_id), options)
    command.extend(["--report", str(report_path(run_id))])
    if skipped:
        command.extend(["--skip-ids", ",".join(sorted(skipped))])
    if context_pack_path(run_id).exists():
        # Cross-file context lets the verifier judge findings beyond the diff.
        command.extend(["--context", str(context_pack_path(run_id))])
    audit_event("verification_started", run_id=run_id, repo_path=str(repo_path))
    timeout_seconds = int(payload.get("verifyTimeoutSeconds") or VERIFY_TIMEOUT_DEFAULT)
    with log_path(run_id).open("ab") as log_file:
        try:
            proc = subprocess.Popen(
                command,
                cwd=repo_path,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            with PROCESS_LOCK:
                PROCESSES[run_id] = proc
            try:
                proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                log_file.write(
                    f"\nCodePulse: verification timed out after {timeout_seconds}s; keeping unverified findings.\n".encode("utf-8")
                )
        except Exception as exc:
            log_file.write(f"\nCodePulse: verification failed to start: {exc}\n".encode("utf-8"))
        finally:
            with PROCESS_LOCK:
                PROCESSES.pop(run_id, None)

    verification = read_json(verification_path(run_id), None)
    if not verification:
        audit_event("verification_skipped", run_id=run_id, reason="no verdicts produced")
        return {}
    counts = apply_verification(report, verification)
    atomic_write_json(report_path(run_id), report)
    audit_event("verification_finished", run_id=run_id, **counts)
    return counts


def create_review_run(payload: dict[str, Any]) -> tuple[str, Path, dict[str, Any], list[str]]:
    """Validate a review request and materialize its run directory + meta.

    Returns (run_id, repo_path, effective_payload, command) so callers decide
    how to execute: `start_review` spawns a thread, `ci.py` runs inline.
    """
    if payload.get("repoId"):
        repo = next((item for item in list_repos() if item.get("id") == payload.get("repoId")), None)
        if not repo:
            raise ValueError("Registered repository not found.")
        payload = dict(payload) | {"repoPath": repo.get("path")}
    review_path, source_path, is_snapshot, payload = resolve_review_payload(payload)
    repo_path = review_path
    provider_name, provider_spec = resolve_provider(payload)
    if not provider_configured(provider_spec):
        envs = " or ".join(provider_spec.get("key_env") or ())
        raise ValueError(
            f"The {provider_spec['label']} provider needs an API key on the server. "
            f"Set {envs} and restart, or choose the Ollama provider."
        )
    options = review_options(payload)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    out_dir = run_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    command = build_review_command(payload, out_dir, options)
    try:
        scope_files = collect_scope_files(repo_path, options)
    except Exception as exc:
        scope_files = []
        audit_event("scope_collection_failed", run_id=run_id, error=str(exc))
    classified_files = classify_scope_files(scope_files)

    meta = {
        "id": run_id,
        "status": "queued",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "repo_path": str(repo_path),
        "source_path": str(source_path),
        "is_snapshot": is_snapshot,
        "provider": provider_name,
        "model": (payload.get("model") or provider_spec["default_model"]).strip(),
        "ollama_base": normalize_ollama_base(payload.get("ollamaBase")),
        "mode": options["mode"],
        "refs": options["refs"],
        "what": options["what"],
        "against": options["against"],
        "filters": options["filters"],
        "merge_base": options["use_merge_base"],
        "changed_files": classified_files["changed"],
        "sensitive_files": classified_files["sensitive"],
        "test_files": classified_files["tests"],
        "command": command,
    }
    atomic_write_json(meta_path(run_id), meta)
    audit_event(
        "review_queued",
        run_id=run_id,
        repo_path=str(repo_path),
        mode=meta["mode"],
        filters=meta["filters"],
    )
    return run_id, repo_path, payload, command


def start_review(payload: dict[str, Any]) -> dict[str, Any]:
    run_id, repo_path, payload, command = create_review_run(payload)
    # Bounded worker pool: at most REVIEW_WORKERS run at once; the rest wait in
    # the queue with meta status "queued".
    JOB_QUEUE.submit(run_id, run_review, run_id, repo_path, payload, command)
    return read_json(meta_path(run_id), {})


def build_generation_command(
    kind: str,
    repo_path: Path,
    out_dir: Path,
    options: dict[str, Any],
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "codepulse_app.generator",
        "--kind",
        kind,
        "--repo",
        str(repo_path),
        "--out",
        str(out_dir),
        "--mode",
        options["mode"],
    ]
    if options["refs"]:
        cmd.extend(["--refs", options["refs"]])
    if options["what"]:
        cmd.extend(["--what", options["what"]])
    if options["against"]:
        cmd.extend(["--against", options["against"]])
    if options["filters"]:
        cmd.extend(["--filters", options["filters"]])
    if not options["use_merge_base"]:
        cmd.append("--no-merge-base")
    return cmd


def generation_artifact_path(run_id: str, kind: str) -> Path:
    return tests_json_path(run_id) if kind == "tests" else pr_draft_json_path(run_id)


def run_generation(
    run_id: str, repo_path: Path, payload: dict[str, Any], command: list[str], kind: str
) -> None:
    started = time.monotonic()
    generate_model = pass_model(payload, "generateModel", "generate")
    env = subprocess_env(payload, model_override=generate_model)
    update_meta(run_id, status="running", started_at=utc_now())
    note_ollama_warning(run_id)
    if generate_model:
        update_meta(run_id, generate_model=generate_model)
    audit_event("generation_started", run_id=run_id, repo_path=str(repo_path), kind=kind)

    timeout_seconds = int(payload.get("timeoutSeconds") or GENERATION_TIMEOUT_DEFAULT)
    timed_out = False
    exit_code: int | None = None
    error = ""
    with log_path(run_id).open("ab") as log_file:
        try:
            proc = subprocess.Popen(
                command,
                cwd=repo_path,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            with PROCESS_LOCK:
                PROCESSES[run_id] = proc
            try:
                exit_code = proc.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                proc.wait()
                exit_code = -1
                log_file.write(
                    f"\nCodePulse: generation timed out after {timeout_seconds}s and was killed.\n".encode("utf-8")
                )
        except Exception as exc:
            error = str(exc)
            log_file.write(f"\nCodePulse failed to start the generator: {exc}\n".encode("utf-8"))
        finally:
            with PROCESS_LOCK:
                PROCESSES.pop(run_id, None)

    artifact_ready = generation_artifact_path(run_id, kind).exists()
    status = "completed" if exit_code == 0 and artifact_ready else "failed"
    update_meta(
        run_id,
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
        **({"error": error} if error else {}),
        completed_at=utc_now(),
        duration_seconds=round(time.monotonic() - started, 2),
        stats={},
    )
    audit_event(
        "generation_finished",
        run_id=run_id,
        repo_path=str(repo_path),
        kind=kind,
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
    )


def start_generation(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "").strip()
    if kind not in GENERATION_KINDS:
        raise ValueError("Unsupported generation kind. Use 'tests' or 'pr'.")
    if payload.get("repoId"):
        repo = next((item for item in list_repos() if item.get("id") == payload.get("repoId")), None)
        if not repo:
            raise ValueError("Registered repository not found.")
        payload = dict(payload) | {"repoPath": repo.get("path")}
    repo_path, _source_path, _is_snapshot, payload = resolve_review_payload(payload)
    options = review_options(payload)
    run_id = (
        f"{kind}-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    )
    out_dir = run_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    command = build_generation_command(kind, repo_path, out_dir, options)

    meta = {
        "id": run_id,
        "kind": kind,
        "status": "queued",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "repo_path": str(repo_path),
        "model": (payload.get("model") or DEFAULT_MODEL).strip(),
        "ollama_base": normalize_ollama_base(payload.get("ollamaBase")),
        "mode": options["mode"],
        "refs": options["refs"],
        "what": options["what"],
        "against": options["against"],
        "filters": options["filters"],
        "merge_base": options["use_merge_base"],
        "command": command,
    }
    atomic_write_json(meta_path(run_id), meta)
    audit_event("generation_queued", run_id=run_id, repo_path=str(repo_path), kind=kind)

    JOB_QUEUE.submit(run_id, run_generation, run_id, repo_path, payload, command, kind)
    return read_json(meta_path(run_id), meta)


def list_reviews() -> list[dict[str, Any]]:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    reviews = []
    for path in RUNS_DIR.glob("*/meta.json"):
        try:
            meta = read_json(path, {})
        except json.JSONDecodeError:
            continue
        if meta.get("status") == "running":
            with PROCESS_LOCK:
                if meta.get("id") not in PROCESSES:
                    meta["status"] = "unknown"
        reviews.append(meta)
    return sorted(reviews, key=lambda item: item.get("created_at", ""), reverse=True)


def preflight_review(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("repoId"):
        repo = next((item for item in list_repos() if item.get("id") == payload.get("repoId")), None)
        if not repo:
            raise ValueError("Registered repository not found.")
        payload = dict(payload) | {"repoPath": repo.get("path")}
    raw_path = str(payload.get("repoPath") or "").strip()
    source = Path(raw_path).expanduser().resolve() if raw_path else None
    if source and source.is_dir() and not snapshot.reviewable_in_place(source):
        return snapshot_preflight(source)
    repo_path = require_git_repo(raw_path)
    options = review_options(payload)
    metadata = repo_metadata(repo_path)
    classified = classify_scope_files(collect_scope_files(repo_path, options))
    return {
        "repo_path": str(repo_path),
        "scope": options,
        "metadata": metadata,
        "changedFiles": classified["changed"],
        "sensitiveFiles": classified["sensitive"],
        "testFiles": classified["tests"],
        "ready": bool(classified["changed"]),
        "warnings": [
            *(["No changed files detected for the selected scope."] if not classified["changed"] else []),
            *(["Sensitive file changes require review."] if classified["sensitive"] else []),
            *(["No matching test changes detected."] if classified["changed"] and not classified["tests"] else []),
        ],
    }


def snapshot_preflight(source: Path) -> dict[str, Any]:
    """Preview scope for a non-git local folder without building a snapshot.

    Reviewing this folder will materialize a git snapshot and analyze every
    file; here we just list what that scope would be, cheaply.
    """
    files = snapshot.list_files(source)
    classified = classify_scope_files(files)
    over_limit = len(files) >= snapshot.MAX_SNAPSHOT_FILES
    # A snapshot review covers the whole tree; on a slow local model each file
    # is a separate call, so a big repo can exceed the review timeout.
    heavy_review = len(classified["changed"]) > LARGE_REVIEW_FILE_HINT
    return {
        "repo_path": str(source),
        "scope": {
            "mode": "refs",
            "refs": snapshot.SNAPSHOT_REFS,
            "what": "",
            "against": "",
            "filters": DEFAULT_FILTERS,
            "use_merge_base": False,
        },
        "metadata": {"snapshot": True, "trackedFiles": len(files)},
        "changedFiles": classified["changed"],
        "sensitiveFiles": classified["sensitive"],
        "testFiles": classified["tests"],
        "ready": bool(classified["changed"]) and not over_limit,
        "warnings": [
            "Not a git repository — CodePulse will analyze a local snapshot "
            "(your folder is copied, never modified) and review every file.",
            *(["No reviewable files detected in this folder."] if not classified["changed"] else []),
            *([
                "Folder is too large to snapshot; point at a git repository or a "
                "smaller folder."
            ] if over_limit else []),
            *([
                f"Whole-repo review of {len(classified['changed'])} files: on a local "
                "model each file is a separate call, so this can exceed the default "
                "1-hour timeout. Raise 'timeoutSeconds', use a faster model, or commit "
                "the code and review the diff instead."
            ] if heavy_review else []),
            *(["Sensitive files present — review carefully."] if classified["sensitive"] else []),
        ],
    }


def review_detail(run_id: str) -> dict[str, Any]:
    meta = read_json(meta_path(run_id))
    if not meta:
        raise FileNotFoundError(run_id)
    return {
        "meta": meta,
        "report": read_json(report_path(run_id), None),
        "markdown": markdown_path(run_id).read_text(encoding="utf-8")
        if markdown_path(run_id).exists()
        else "",
    }


def get_review(run_id: str) -> dict[str, Any]:
    detail = review_detail(run_id)
    report = detail.get("report")
    if report:
        suppressed_fps = set(load_suppressions())
        if suppressed_fps:
            for file, file_issues in (report.get("issues") or {}).items():
                for issue in file_issues:
                    fp = issue_fingerprint(issue | {"file": issue.get("file") or file})
                    if fp in suppressed_fps:
                        issue["suppressed"] = True
    detail["test_cases"] = generate_review_test_cases(detail["meta"], report)
    detail["generated_tests"] = read_json(tests_json_path(run_id), None)
    detail["pr_draft"] = read_json(pr_draft_json_path(run_id), None)
    detail["context_pack"] = read_json(context_pack_path(run_id), None)
    detail["publish_result"] = read_json(publish_result_path(run_id), None)
    detail["fixes"] = patcher.load_ledger(run_dir(run_id))
    return detail


def find_report_issue(run_id: str, issue_id: Any) -> dict[str, Any]:
    report = read_json(report_path(run_id), None)
    if not report:
        raise FileNotFoundError(run_id)
    issue = next(
        (
            item | {"file": item.get("file") or file}
            for file, file_issues in (report.get("issues") or {}).items()
            for item in file_issues
            if str(item.get("id")) == str(issue_id)
        ),
        None,
    )
    if issue is None:
        raise FileNotFoundError(f"finding {issue_id}")
    return issue


def run_repo_path(run_id: str) -> Path:
    meta = read_json(meta_path(run_id), {}) or {}
    repo = str(meta.get("repo_path") or "")
    if not repo:
        raise ValueError("This run has no repository path.")
    path = Path(repo)
    if not path.is_dir():
        raise ValueError("The run's repository path no longer exists.")
    return path


def fix_plan(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    issue = find_report_issue(run_id, payload.get("issueId"))
    plan = patcher.plan_fix(run_repo_path(run_id), issue)
    ledger = patcher.load_ledger(run_dir(run_id))
    return plan | {"ledger": ledger.get(str(issue.get("id"))) or {}}


def fix_apply(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    issue = find_report_issue(run_id, payload.get("issueId"))
    result = patcher.apply_fix(run_repo_path(run_id), run_dir(run_id), issue)
    ledger = patcher.load_ledger(run_dir(run_id))
    ledger[str(issue.get("id"))] = {
        "applied_at": utc_now(),
        "backup": result["backup"],
        "file": result["file"],
    }
    patcher.save_ledger(run_dir(run_id), ledger)
    audit_event("fix_applied", run_id=run_id, issue_id=issue.get("id"), file=result["file"])
    return result | {"applied": True}


def fix_revert(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    issue = find_report_issue(run_id, payload.get("issueId"))
    ledger = patcher.load_ledger(run_dir(run_id))
    entry = ledger.get(str(issue.get("id")))
    if not entry or entry.get("reverted_at"):
        raise ValueError("This fix is not currently applied.")
    file = patcher.revert_fix(run_repo_path(run_id), run_dir(run_id), issue, entry)
    entry["reverted_at"] = utc_now()
    patcher.save_ledger(run_dir(run_id), ledger)
    audit_event("fix_reverted", run_id=run_id, issue_id=issue.get("id"), file=file)
    return {"file": file, "reverted": True}


def tests_write(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    written = patcher.write_generated_tests(
        run_repo_path(run_id), run_dir(run_id), overwrite=payload.get("overwrite") is True
    )
    audit_event("tests_written", run_id=run_id, count=len(written))
    return {"written": written}


def get_review_tests(run_id: str) -> dict[str, Any]:
    detail = review_detail(run_id)
    return generate_review_test_cases(detail["meta"], detail.get("report"))


def flatten_report_issues(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not report:
        return []
    issues = []
    for file, file_issues in (report.get("issues") or {}).items():
        for issue in file_issues:
            issues.append(issue | {"file": issue.get("file") or file})
    return issues


def overview() -> dict[str, Any]:
    reviews = list_reviews()
    repos = list_repos()
    policies = load_policies()
    # Gate/risk metrics only make sense for review runs, not generation runs.
    review_runs = [run for run in reviews if (run.get("kind") or "review") == "review"]
    completed = [review for review in review_runs if review.get("status") == "completed"]
    running = [review for review in reviews if review.get("status") in {"queued", "running"}]
    failed = [review for review in reviews if review.get("status") == "failed"]
    generations = [run for run in reviews if (run.get("kind") or "review") != "review"]
    gate_counts = {"block": 0, "review": 0, "pass": 0}
    tag_counts: dict[str, int] = {}
    for review in completed:
        stats = review.get("stats") or {}
        gate = stats.get("gate")
        if gate in gate_counts:
            gate_counts[gate] += 1
        for tag in stats.get("tags") or []:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    total_issues = sum((review.get("stats") or {}).get("total_issues", 0) for review in completed)
    avg_risk = (
        round(sum((review.get("stats") or {}).get("risk_score", 0) for review in completed) / len(completed), 1)
        if completed
        else 0
    )
    guardrails = policies.get("guardrails", [])
    readiness_items = [
        {"label": "Private model path", "ready": True, "detail": "Ollama compatible runtime"},
        {"label": "Policy gates", "ready": bool(policies.get("risk")), "detail": "Severity and risk score thresholds"},
        {"label": "Audit evidence", "ready": store.audit_count() > 0, "detail": "SQLite store + JSONL mirror"},
        {"label": "Repository onboarding", "ready": bool(repos), "detail": f"{len(repos)} registered"},
        {"label": "Access control", "ready": auth_required(), "detail": _auth_mode_label()},
        {"label": "Evidence exports", "ready": True, "detail": "JSON, Markdown, CSV"},
        {"label": "Test & PR generation", "ready": True, "detail": "LLM unit tests and PR drafts"},
        {"label": "Cross-file impact analysis", "ready": True, "detail": "Import graph + API-contract checks"},
        {
            "label": "PR publishing",
            "ready": any(item["configured"] for item in publisher.publish_config().values()),
            "detail": "GITHUB_TOKEN / GITLAB_TOKEN",
        },
    ]
    latest_review = next(
        (run for run in review_runs if run.get("status") == "completed"),
        review_runs[0] if review_runs else None,
    )
    return {
        "metrics": {
            "repos": len(repos),
            "reviews": len(reviews),
            "completed": len(completed),
            "running": len(running),
            "failed": len(failed),
            "issues": total_issues,
            "avgRisk": avg_risk,
            "gateCounts": gate_counts,
            "topTags": sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[:6],
            "generations": len(generations),
        },
        "latestReview": latest_review,
        "readiness": readiness_items,
        "guardrails": guardrails,
    }


def trends(query: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Time series over completed review runs, globally and per repository.

    Health is recomputed from frozen stats keys when a run predates the
    `health` key, so historical runs chart alongside new ones.
    """
    query = query or {}
    try:
        limit = int((query.get("limit") or ["30"])[0])
    except (TypeError, ValueError):
        limit = 30
    limit = max(1, min(limit, 200))
    repo_filter = str((query.get("repo") or [""])[0]).strip()

    completed = [
        run
        for run in list_reviews()
        if (run.get("kind") or "review") == "review" and run.get("status") == "completed"
    ]
    completed.sort(key=lambda run: run.get("created_at", ""))

    def to_point(run: dict[str, Any]) -> dict[str, Any]:
        stats = run.get("stats") or {}
        return {
            "id": run.get("id"),
            "created_at": run.get("created_at"),
            "repo_path": run.get("repo_path"),
            "risk_score": stats.get("risk_score", 0),
            "total_issues": stats.get("total_issues", 0),
            "severity_counts": stats.get("severity_counts") or {},
            "gate": stats.get("gate"),
            "health": stats.get("health") or compute_health(stats),
            "duration_seconds": run.get("duration_seconds"),
        }

    points = [to_point(run) for run in completed]
    if repo_filter:
        points = [point for point in points if point["repo_path"] == repo_filter]
    points = points[-limit:]

    by_repo: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        by_repo.setdefault(str(point["repo_path"] or ""), []).append(point)
    repos = sorted(
        (
            {
                "repo_path": path,
                "name": Path(path).name if path else "—",
                "points": repo_points,
                "latest": repo_points[-1],
            }
            for path, repo_points in by_repo.items()
        ),
        key=lambda item: item["latest"].get("created_at") or "",
        reverse=True,
    )
    return {"runs": points, "repos": repos, "count": len(points)}


def export_review(run_id: str, export_format: str) -> tuple[str, str]:
    detail = get_review(run_id)
    report = detail.get("report")
    audit_event("review_exported", run_id=run_id, export_format=export_format)
    if export_format == "json":
        return json.dumps(detail, indent=2), "application/json; charset=utf-8"
    if export_format == "md":
        return detail.get("markdown") or "# CodePulse Review\n\nNo markdown report found.\n", "text/markdown; charset=utf-8"
    if export_format == "csv":
        rows = ["id,file,severity,confidence,title,tags"]
        for issue in flatten_report_issues(report):
            row = [
                str(issue.get("id", "")),
                str(issue.get("file", "")),
                str(issue.get("severity", "")),
                str(issue.get("confidence", "")),
                str(issue.get("title", "")).replace('"', '""'),
                ";".join(issue.get("tags") or []),
            ]
            rows.append(",".join(f'"{cell}"' for cell in row))
        return "\n".join(rows) + "\n", "text/csv; charset=utf-8"
    if export_format == "sarif":
        return (
            sarif.dumps(flatten_report_issues(report), APP_VERSION),
            "application/sarif+json; charset=utf-8",
        )
    raise ValueError("Unsupported export format.")


def seed_sample_data() -> dict[str, Any]:
    sample_repo = ensure_sample_repo()
    run_id = "sample-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = run_dir(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "issues": {
            "services/payments/refunds.py": [
                {
                    "id": 1,
                    "file": "services/payments/refunds.py",
                    "title": "Refund path trusts caller-supplied account ownership.",
                    "details": "The refund handler accepts an account id from the request and uses it before verifying that the authenticated user owns that account. In a junior PR this is easy to miss because the happy-path test passes, but it creates an authorization bypass.",
                    "severity": 1,
                    "confidence": 1,
                    "tags": ["security", "bug", "input-validation"],
                    "affected_lines": [
                        {
                            "file": "services/payments/refunds.py",
                            "start_line": 42,
                            "end_line": 49,
                            "affected_code": "42: account_id = request.json['account_id']\n43: refund = create_refund(account_id, amount)\n44: return jsonify(refund)",
                            "proposal": "account = load_account_for_user(current_user.id, request.json['account_id'])\nrefund = create_refund(account.id, amount)\nreturn jsonify(refund)",
                        }
                    ],
                }
            ],
            "web/src/routes/invite.ts": [
                {
                    "id": 2,
                    "file": "web/src/routes/invite.ts",
                    "title": "Invite endpoint does not await the email send promise.",
                    "details": "The route returns success before the invite email promise settles. Failures become unhandled promise rejections and the UI tells a manager the invite was sent even when the email provider rejects it.",
                    "severity": 2,
                    "confidence": 1,
                    "tags": ["bug", "async-flow", "maintainability"],
                    "affected_lines": [
                        {
                            "file": "web/src/routes/invite.ts",
                            "start_line": 27,
                            "end_line": 31,
                            "affected_code": "27: sendInviteEmail(user.email, token)\n28: return res.status(202).json({ ok: true })",
                            "proposal": "await sendInviteEmail(user.email, token)\nreturn res.status(202).json({ ok: true })",
                        }
                    ],
                }
            ],
            ".env.example": [
                {
                    "id": 3,
                    "file": ".env.example",
                    "title": "Environment template includes a production-looking secret.",
                    "details": "A value that looks like a real API credential was added to the shared environment template. Even if it is inactive, this trains interns to copy secrets into git and should be replaced with an obvious placeholder.",
                    "severity": 2,
                    "confidence": 1,
                    "tags": ["security", "secret-handling"],
                    "affected_lines": [
                        {
                            "file": ".env.example",
                            "start_line": 8,
                            "end_line": 8,
                            "affected_code": "8: PAYMENT_GATEWAY_KEY=pk_live_123456789",
                            "proposal": "PAYMENT_GATEWAY_KEY=replace-with-local-test-key",
                        }
                    ],
                }
            ],
        },
        "summary": "The PR should not merge until account ownership checks, awaited invite delivery, and secret hygiene are fixed.",
        "number_of_processed_files": 3,
        "total_issues": 3,
        "created_at": utc_now(),
        "model": "sample-local-model",
        "pipeline_out": {},
        "processing_warnings": [],
        "target": None,
    }
    atomic_write_json(report_path(run_id), report)
    markdown_path(run_id).write_text(
        "# CodePulse Sample Review\n\n"
        "The PR should not merge until account ownership checks, awaited invite delivery, and secret hygiene are fixed.\n",
        encoding="utf-8",
    )
    meta = {
        "id": run_id,
        "status": "completed",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "repo_path": str(sample_repo),
        "model": "sample-local-model",
        "ollama_base": DEFAULT_OLLAMA_BASE,
        "mode": "working",
        "refs": "",
        "what": "",
        "against": "HEAD",
        "filters": DEFAULT_FILTERS,
        "merge_base": False,
        "command": ["sample-data"],
        "started_at": utc_now(),
        "exit_code": 0,
        "completed_at": utc_now(),
        "duration_seconds": 42.7,
        "stats": summarize_report(run_id),
    }
    atomic_write_json(meta_path(run_id), meta)
    # Drop legacy fake demo entries that predate the real sample repository.
    for stale_path in ("/demo/acme-payments-api", "/sample/acme-payments-api"):
        store.delete_repo_by_path(stale_path)
    existing = store.get_repo_by_path(str(sample_repo))
    store.save_repo(
        {
            "id": existing.get("id") if existing else "sample-acme",
            "name": "Acme Payments API",
            "path": str(sample_repo),
            "owner": "Platform Engineering",
            "tier": "production",
            "created_at": existing.get("created_at") if existing else utc_now(),
            "updated_at": utc_now(),
            "metadata": repo_metadata(sample_repo),
        }
    )
    audit_event("sample_data_seeded", run_id=run_id)
    return meta


def publish_run(run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Preview (dry run) or post a run's review to a GitHub PR / GitLab MR."""
    meta = read_json(meta_path(run_id))
    if not meta:
        raise FileNotFoundError(run_id)
    report = read_json(report_path(run_id), None)
    if not report:
        raise ValueError("This run has no review report to publish.")
    stats = meta.get("stats") or summarize_report(run_id)
    remote = ""
    if meta.get("repo_path"):
        remote = git_output(Path(meta["repo_path"]), "remote", "get-url", "origin")
    result = publisher.publish_review(meta, report, stats, payload, remote)
    if result.get("dry_run"):
        audit_event(
            "review_publish_previewed",
            run_id=run_id,
            platform=result["platform"],
            target=result["target"],
        )
    else:
        atomic_write_json(publish_result_path(run_id), result)
        audit_event(
            "review_published",
            run_id=run_id,
            platform=result["platform"],
            target=result["target"],
            mode=(result.get("posted") or {}).get("mode"),
        )
    return result


def webhook_secret() -> str:
    return brand_env("WEBHOOK_SECRET") or ""


def verify_webhook_signature(
    platform: str, headers: Any, raw_body: bytes, secret: str
) -> bool:
    """GitHub: HMAC-SHA256 over the raw body; GitLab: shared-token header."""
    if platform == "github":
        presented = headers.get("X-Hub-Signature-256") or ""
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(presented, expected)
    return hmac.compare_digest(headers.get("X-Gitlab-Token") or "", secret)


GITHUB_PR_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}
GITLAB_MR_ACTIONS = {"open", "update", "reopen"}


def parse_webhook_event(
    platform: str, event_name: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Normalize a PR/MR update event to {slug, head_sha, base_ref, pr}.

    Returns None for anything that is not a reviewable pull/merge request
    change (pings, comments, pushes, closes) — those are ignored, never errors.
    """
    if platform == "github":
        if event_name != "pull_request" or payload.get("action") not in GITHUB_PR_ACTIONS:
            return None
        pr = payload.get("pull_request") or {}
        return {
            "slug": str((payload.get("repository") or {}).get("full_name") or ""),
            "head_sha": str((pr.get("head") or {}).get("sha") or ""),
            "base_ref": str((pr.get("base") or {}).get("ref") or ""),
            "pr": pr.get("number"),
        }
    if payload.get("object_kind") != "merge_request":
        return None
    attrs = payload.get("object_attributes") or {}
    if attrs.get("action") not in GITLAB_MR_ACTIONS:
        return None
    return {
        "slug": str((payload.get("project") or {}).get("path_with_namespace") or ""),
        "head_sha": str((attrs.get("last_commit") or {}).get("id") or ""),
        "base_ref": str(attrs.get("target_branch") or ""),
        "pr": attrs.get("iid"),
    }


def match_registered_repo(slug: str) -> dict[str, Any] | None:
    """Map a webhook repository slug onto a registered local clone by its
    origin remote. Repo paths never come from the webhook payload itself."""
    want = slug.strip().strip("/").lower()
    if not want:
        return None
    for repo in list_repos():
        path = str(repo.get("path") or "")
        if not path or not Path(path).is_dir():
            continue
        remote = git_output(Path(path), "remote", "get-url", "origin")
        parsed = publisher.parse_remote(remote) if remote else None
        if parsed and parsed["slug"].lower() == want:
            return repo
    return None


def handle_webhook_event(
    platform: str, event_name: str, payload: dict[str, Any]
) -> dict[str, Any]:
    event = parse_webhook_event(platform, event_name, payload)
    if not event:
        audit_event("webhook_ignored", platform=platform, reason="unsupported event", received=event_name)
        return {"status": "ignored", "reason": "unsupported event"}
    repo = match_registered_repo(event["slug"])
    if not repo:
        audit_event("webhook_ignored", platform=platform, reason="unknown repository", slug=event["slug"])
        return {"status": "ignored", "reason": "repository not registered", "slug": event["slug"]}
    audit_event(
        "webhook_accepted",
        platform=platform,
        slug=event["slug"],
        pr=event["pr"],
        head_sha=event["head_sha"],
    )
    enqueue_ci_review(platform, repo, event)
    return {"status": "accepted", "slug": event["slug"], "pr": event["pr"]}


def enqueue_ci_review(platform: str, repo: dict[str, Any], event: dict[str, Any]) -> None:
    """Run the CI review off the webhook response thread. (The job queue in a
    later release replaces exactly this function.)"""
    thread = threading.Thread(
        target=execute_ci_review,
        args=(platform, repo, event),
        name=f"code-doctor-webhook-{event.get('pr')}",
        daemon=True,
    )
    thread.start()


def execute_ci_review(platform: str, repo: dict[str, Any], event: dict[str, Any]) -> None:
    repo_path = Path(str(repo.get("path")))
    try:
        subprocess.run(
            ["git", "-C", str(repo_path), "fetch", "origin", "--quiet"],
            check=False,
            timeout=180,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception as exc:
        audit_event("webhook_fetch_failed", slug=event["slug"], error=str(exc))

    review_payload: dict[str, Any] = {
        "repoPath": str(repo_path),
        "mode": "refs",
        "what": event["head_sha"],
        "against": f"origin/{event['base_ref']}" if event.get("base_ref") else "",
    }
    try:
        run_id, path, review_payload, command = create_review_run(review_payload)
    except (ValueError, FileNotFoundError) as exc:
        audit_event("webhook_review_failed", platform=platform, slug=event["slug"], error=str(exc))
        return
    update_meta(
        run_id,
        trigger={
            "source": f"{platform}-webhook",
            "slug": event["slug"],
            "pr": event["pr"],
            "head_sha": event["head_sha"],
        },
    )
    run_review(run_id, path, review_payload, command)

    ci_policy = load_policies().get("ci") or {}
    if not ci_policy.get("autoPublish"):
        return
    meta = read_json(meta_path(run_id), {}) or {}
    if meta.get("status") != "completed":
        audit_event("webhook_publish_skipped", run_id=run_id, reason="review did not complete")
        return
    if event.get("pr"):
        try:
            publish_run(
                run_id,
                {"platform": platform, "repo": event["slug"], "pr": event["pr"], "dryRun": False},
            )
        except Exception as exc:
            audit_event("webhook_publish_failed", run_id=run_id, error=str(exc))
    if event.get("head_sha"):
        gate = (meta.get("stats") or {}).get("gate") or "pass"
        try:
            status = publisher.post_commit_status(platform, event["slug"], event["head_sha"], gate)
            audit_event("commit_status_posted", run_id=run_id, **status)
        except Exception as exc:
            audit_event("commit_status_failed", run_id=run_id, error=str(exc))


def cancel_review(run_id: str) -> dict[str, Any]:
    with PROCESS_LOCK:
        proc = PROCESSES.get(run_id)
    if not proc:
        meta = update_meta(run_id, status="cancel_requested")
        audit_event("review_cancel_requested", run_id=run_id, active_process=False)
        return meta
    proc.terminate()
    meta = update_meta(run_id, status="cancel_requested")
    audit_event("review_cancel_requested", run_id=run_id, active_process=True)
    return meta


def ollama_health(base: str | None) -> dict[str, Any]:
    ollama_base = normalize_ollama_base(base)
    parsed = urlparse(ollama_base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "ok": False,
            "base": ollama_base,
            "models": [],
            "error": "Ollama URL must be an http(s) URL.",
        }
    url = ollama_base + "/api/tags"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=2.5) as response:
            data = json.loads(response.read().decode("utf-8"))
        models = [item.get("name") for item in data.get("models", []) if item.get("name")]
        return {"ok": True, "base": ollama_base, "models": models}
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "base": ollama_base, "models": [], "error": str(exc)}


class OllamaWatchdog:
    """Item 9: background Ollama health sampling.

    State transitions audit `ollama_down` / `ollama_recovered` so a dead model
    runtime is visible before a run fails, never only after. The watchdog only
    observes — it never (re)starts anything on the user's machine.
    """

    def __init__(self, interval: float = 30.0, max_history: int = 20):
        self.interval = interval
        self.checks: collections.deque = collections.deque(maxlen=max_history)
        self.state = "unknown"  # unknown | up | down
        self.since = ""
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._on_tick: list[Any] = []  # extra periodic work (retention piggybacks here)

    def sample(self) -> dict[str, Any]:
        result = ollama_health(None)
        check = {
            "ts": utc_now(),
            "ok": bool(result.get("ok")),
            "error": str(result.get("error") or ""),
        }
        emit = ""
        with self._lock:
            previous = self.state
            self.checks.append(check)
            new_state = "up" if check["ok"] else "down"
            if new_state != previous:
                self.state = new_state
                self.since = check["ts"]
                if new_state == "down":
                    emit = "ollama_down"
                elif previous == "down":
                    emit = "ollama_recovered"
        if emit:
            audit_event(emit, base=result.get("base", ""), error=check["error"])
        return check

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"state": self.state, "since": self.since, "checks": list(self.checks)[-5:]}

    def add_tick_hook(self, hook: Any) -> None:
        self._on_tick.append(hook)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.sample()
            except Exception:  # noqa: BLE001 - the watchdog must never die
                pass
            for hook in self._on_tick:
                try:
                    hook()
                except Exception:  # noqa: BLE001
                    pass

    def start(self) -> None:
        try:
            self.sample()
        except Exception:  # noqa: BLE001
            pass
        threading.Thread(target=self._run, name="code-doctor-ollama-watchdog", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


OLLAMA_WATCHDOG = OllamaWatchdog()

# Bounded worker pool for reviews/generations. Sized from the environment so a
# server on beefier hardware (or pointed at a cloud provider) can run more in
# parallel; defaults to 2 to stay gentle on a single local GPU.
REVIEW_WORKERS = max(1, int(brand_env("REVIEW_WORKERS", "2")))
JOB_QUEUE = jobqueue.JobQueue(workers=REVIEW_WORKERS, name="review")


def system_health(
    query: dict[str, list[str]],
    include_ollama_check: bool = True,
) -> dict[str, Any]:
    git_path = shutil.which("git")
    git_version = ""
    if git_path:
        result = subprocess.run(
            ["git", "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        git_version = result.stdout.strip()
    ollama_base = (query.get("ollamaBase") or [DEFAULT_OLLAMA_BASE])[0]
    if include_ollama_check:
        ollama = ollama_health(ollama_base)
    else:
        ollama = {
            "ok": False,
            "base": normalize_ollama_base(ollama_base),
            "models": [],
            "skipped": True,
            "error": "Authorization required for model probe.",
        }
    return {
        "version": APP_VERSION,
        "git": {"ok": bool(git_path), "version": git_version},
        "ollama": ollama,
        "ollamaWatch": OLLAMA_WATCHDOG.snapshot(),
        "engines": {"semanticJs": semantic_js.SEMANTIC_JS_MODE, "taint": "ast-dataflow"},
        "queue": JOB_QUEUE.stats(),
        "providers": [
            {
                "id": name,
                "label": spec["label"],
                "local": bool(spec.get("local")),
                "configured": provider_configured(spec),
                "defaultModel": spec["default_model"],
            }
            for name, spec in LLM_PROVIDERS.items()
        ],
        "defaults": {
            "repoPath": str(Path.cwd()),
            "model": DEFAULT_MODEL,
            "ollamaBase": DEFAULT_OLLAMA_BASE,
            "filters": DEFAULT_FILTERS,
        },
        "authRequired": auth_required(),
    }


def auth_required() -> bool:
    """True when anonymous access is closed — a static token is set or at least
    one user is registered."""
    return bool(brand_env("TOKEN")) or auth.users_configured()


def list_users_api() -> dict[str, Any]:
    return {"users": auth.list_users()}


def create_user_api(payload: dict[str, Any]) -> dict[str, Any]:
    user = auth.create_user(
        str(payload.get("username") or ""),
        str(payload.get("password") or ""),
        str(payload.get("role") or auth.DEFAULT_ROLE),
    )
    audit_event("user_created", username=user["username"], role=user["role"])
    return {"user": user}


def update_user_api(username: str, payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] | None = None
    if "role" in payload:
        result = auth.set_role(username, str(payload.get("role") or ""))
        audit_event("user_role_changed", username=username, role=payload.get("role"))
    if payload.get("password"):
        result = auth.set_password(username, str(payload.get("password")))
        audit_event("user_password_changed", username=username)
    if "disabled" in payload:
        result = auth.set_disabled(username, bool(payload.get("disabled")))
        audit_event("user_disabled" if payload.get("disabled") else "user_enabled", username=username)
    if result is None:
        raise ValueError("no changes requested (role, password, or disabled)")
    return {"user": result}


def delete_user_api(username: str) -> dict[str, Any]:
    if not auth.delete_user(username):
        raise FileNotFoundError(f"no such user {username!r}")
    audit_event("user_deleted", username=username)
    return {"ok": True}


class CodeDoctorHandler(BaseHTTPRequestHandler):
    server_version = "CodeDoctor/0.1"
    _head_only = False

    def log_message(self, fmt: str, *args: Any) -> None:
        # Structured single-line log: timestamp  METHOD path  status  size
        sys.stderr.write(
            f"{self.log_date_time_string()}  {self.command if hasattr(self, 'command') else '-'}  "
            f"{fmt % args}\n"
        )

    def do_HEAD(self) -> None:
        self._head_only = True
        self.do_GET()
        self._head_only = False

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/me":
                self.handle_me()
                return
            if (
                path.startswith("/api/")
                and path not in ("/api/health", "/api/me")
                and not self.require_auth("admin" if path == "/api/users" else "viewer")
            ):
                return
            if path == "/api/health":
                self.send_json(system_health(query, include_ollama_check=self.authorized()))
            elif path == "/api/users":
                self.send_json(list_users_api())
            elif path == "/api/overview":
                self.send_json(overview())
            elif path == "/api/trends":
                self.send_json(trends(query))
            elif path == "/api/repos":
                self.send_json({"repos": list_repos()})
            elif path == "/api/policies":
                self.send_json(load_policies())
            elif path == "/api/preflight":
                self.send_json(preflight_review({key: values[0] for key, values in query.items()}))
            elif path == "/api/audit":
                limit = int((query.get("limit") or ["100"])[0])
                self.send_json({"events": read_audit(limit)})
            elif path == "/api/reviews":
                self.send_json({"reviews": list_reviews()})
            elif path == "/api/publish/config":
                self.send_json(publisher.publish_config())
            elif path.startswith("/api/reviews/"):
                self.handle_review_get(path)
            else:
                self.serve_static(path)
        except FileNotFoundError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/hooks/"):
                # Webhooks authenticate with the HMAC secret, not the bearer
                # token (GitHub/GitLab cannot send our Authorization header).
                self.handle_webhook(path)
                return
            if path == "/api/login":
                self.handle_login()
                return
            if path == "/api/logout":
                if not self.require_auth("viewer"):
                    return
                self.handle_logout()
                return
            if path.startswith("/api/") and not self.require_auth(self._post_min_role(path)):
                return
            if path == "/api/users":
                self.send_json(create_user_api(self.read_json_body()), HTTPStatus.CREATED)
            elif path.startswith("/api/users/"):
                username = unquote(path.split("/", 3)[3])
                self.send_json(update_user_api(username, self.read_json_body()))
            elif path == "/api/reviews":
                payload = self.read_json_body()
                self.send_json(start_review(payload), HTTPStatus.CREATED)
            elif path == "/api/generate":
                payload = self.read_json_body()
                self.send_json(start_generation(payload), HTTPStatus.CREATED)
            elif path == "/api/findings/feedback":
                payload = self.read_json_body()
                self.send_json(record_finding_feedback(payload))
            elif path == "/api/repos":
                payload = self.read_json_body()
                self.send_json(register_repo(payload), HTTPStatus.CREATED)
            elif path == "/api/policies":
                payload = self.read_json_body()
                self.send_json(save_policies(payload))
            elif path == "/api/preflight":
                payload = self.read_json_body()
                self.send_json(preflight_review(payload))
            elif path in {"/api/sample/seed", "/api/demo/seed"}:
                self.send_json(seed_sample_data(), HTTPStatus.CREATED)
            elif path.startswith("/api/reviews/") and path.endswith("/cancel"):
                run_id = unquote(path.split("/")[3])
                self.send_json(cancel_review(run_id))
            elif path.startswith("/api/reviews/") and path.endswith("/publish"):
                run_id = unquote(path.split("/")[3])
                payload = self.read_json_body()
                self.send_json(publish_run(run_id, payload))
            elif path.startswith("/api/reviews/") and path.endswith("/fixes/plan"):
                run_id = unquote(path.split("/")[3])
                self.send_json(fix_plan(run_id, self.read_json_body()))
            elif path.startswith("/api/reviews/") and path.endswith("/fixes/apply"):
                run_id = unquote(path.split("/")[3])
                self.send_json(fix_apply(run_id, self.read_json_body()))
            elif path.startswith("/api/reviews/") and path.endswith("/fixes/revert"):
                run_id = unquote(path.split("/")[3])
                self.send_json(fix_revert(run_id, self.read_json_body()))
            elif path.startswith("/api/reviews/") and path.endswith("/tests/write"):
                run_id = unquote(path.split("/")[3])
                self.send_json(tests_write(run_id, self.read_json_body()))
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")
        except FileNotFoundError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")
        except ValueError as exc:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path.startswith("/api/") and not self.require_auth("admin"):
                return
            if path.startswith("/api/repos/"):
                repo_id = unquote(path.split("/")[3])
                delete_repo(repo_id)
                self.send_json({"ok": True})
            elif path.startswith("/api/users/"):
                username = unquote(path.split("/", 3)[3])
                self.send_json(delete_user_api(username))
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")
        except FileNotFoundError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def handle_webhook(self, path: str) -> None:
        platform = path.removeprefix("/api/hooks/").strip("/")
        if platform not in {"github", "gitlab"}:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")
            return
        secret = webhook_secret()
        if not secret:
            self.send_error_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "Webhook not configured. Set CODEPULSE_WEBHOOK_SECRET on the server.",
            )
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_REQUEST_BODY:
            self.send_error_json(
                HTTPStatus.BAD_REQUEST,
                f"Request body too large ({length} bytes; limit {MAX_REQUEST_BODY}).",
            )
            return
        raw = self.rfile.read(length)
        if not verify_webhook_signature(platform, self.headers, raw, secret):
            audit_event("webhook_rejected", platform=platform, reason="signature mismatch")
            self.send_error_json(HTTPStatus.UNAUTHORIZED, "Webhook signature verification failed.")
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("payload must be a JSON object")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.send_error_json(HTTPStatus.BAD_REQUEST, "Malformed JSON body.")
            return
        event_name = (
            self.headers.get("X-GitHub-Event") or ""
            if platform == "github"
            else str(payload.get("object_kind") or "")
        )
        self.send_json(handle_webhook_event(platform, event_name, payload), HTTPStatus.ACCEPTED)

    def _bearer_token(self) -> str:
        header = self.headers.get("Authorization") or ""
        if header.startswith("Bearer "):
            return header[len("Bearer "):]
        return ""

    def _cookie_token(self) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE:
                return unquote(value)
        return ""

    def current_principal(self) -> dict[str, Any] | None:
        """Resolve who is calling, honouring three modes (see auth.py):

        session token (bearer or cookie) → that user's role; static
        ``CODEPULSE_TOKEN`` → admin service account; open mode (no token, no
        users) → admin. Cached per request. Returns None when unauthenticated.
        """
        if getattr(self, "_principal_resolved", False):
            return self._principal_cache
        self._principal_resolved = True
        self._principal_cache = None

        presented = self._bearer_token() or self._cookie_token()
        # A real login session wins first (works via bearer or cookie).
        if presented:
            session = auth.resolve_session(presented)
            if session is not None:
                self._principal_cache = {
                    "username": session.get("username"),
                    "role": session.get("role", "viewer"),
                    "via": "session",
                }
                return self._principal_cache

        # Static service token (CI, webhooks, scripts) → admin.
        expected = brand_env("TOKEN")
        if expected and hmac.compare_digest(self._bearer_token(), expected):
            self._principal_cache = {"username": "token", "role": "admin", "via": "token"}
            return self._principal_cache

        # Open mode: no users registered and no static token → local admin.
        if not expected and not auth.users_configured():
            self._principal_cache = {"username": "local", "role": "admin", "via": "open"}
            return self._principal_cache

        return None

    def authorized(self) -> bool:
        return self.current_principal() is not None

    def require_auth(self, minimum: str = "viewer") -> bool:
        """Auth + RBAC gate with per-IP 401 throttling (QW-3). Sends its own
        error. ``minimum`` is the least-privileged role allowed (viewer <
        reviewer < admin)."""
        client_ip = self.client_address[0] if self.client_address else ""
        if auth_throttled(client_ip):
            self.send_error_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                "Too many failed authorization attempts. Try again in a minute.",
            )
            return False
        principal = self.current_principal()
        if principal is None:
            if record_auth_failure(client_ip):
                audit_event("auth_throttled", client=client_ip)
            self.send_error_json(HTTPStatus.UNAUTHORIZED, "Authorization required.")
            return False
        clear_auth_failures(client_ip)
        if not auth.role_at_least(str(principal.get("role", "viewer")), minimum):
            self.send_error_json(
                HTTPStatus.FORBIDDEN,
                f"This action requires the {minimum!r} role or higher.",
            )
            return False
        return True

    @staticmethod
    def _post_min_role(path: str) -> str:
        """Least role allowed to POST to ``path``. Admin work (config, repos,
        seed, user management) needs admin; other writes need reviewer;
        read-shaped previews are open to viewers."""
        if path in _ADMIN_POST_PATHS or path.startswith("/api/users/"):
            return "admin"
        if path == "/api/preflight":
            return "viewer"
        return "reviewer"

    def _session_cookie(self, token: str) -> str:
        max_age = int(auth._session_ttl().total_seconds())  # noqa: SLF001 — same package
        parts = [
            f"{SESSION_COOKIE}={token}",
            "Path=/",
            "HttpOnly",
            "SameSite=Strict",
            f"Max-Age={max_age}",
        ]
        if tls_enabled():
            parts.append("Secure")
        return "; ".join(parts)

    def handle_login(self) -> None:
        client_ip = self.client_address[0] if self.client_address else ""
        if auth_throttled(client_ip):
            self.send_error_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                "Too many failed login attempts. Try again in a minute.",
            )
            return
        payload = self.read_json_body()
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        user = auth.authenticate(username, password)
        if user is None:
            if record_auth_failure(client_ip):
                audit_event("auth_throttled", client=client_ip)
            audit_event("login_failed", username=username, client=client_ip)
            self.send_error_json(HTTPStatus.UNAUTHORIZED, "Invalid username or password.")
            return
        clear_auth_failures(client_ip)
        token, session = auth.issue_session(user["username"], str(user["role"]), client_ip)
        audit_event("login", username=user["username"], role=user["role"], client=client_ip)
        self.send_json(
            {"user": user, "token": token, "expiresAt": session["expires_at"]},
            extra_headers=[("Set-Cookie", self._session_cookie(token))],
        )

    def handle_logout(self) -> None:
        token = self._bearer_token() or self._cookie_token()
        auth.revoke_session(token)
        expired = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"
        self.send_json({"ok": True}, extra_headers=[("Set-Cookie", expired)])

    def handle_me(self) -> None:
        principal = self.current_principal()
        if principal is None:
            self.send_json({"authenticated": False, "authRequired": auth_required()})
            return
        self.send_json(
            {
                "authenticated": True,
                "username": principal.get("username"),
                "role": principal.get("role"),
                "via": principal.get("via"),
                "authRequired": auth_required(),
            }
        )

    def handle_review_get(self, path: str) -> None:
        parts = [unquote(part) for part in path.split("/") if part]
        if len(parts) == 3:
            self.send_json(get_review(parts[2]))
            return
        if len(parts) == 4 and parts[3] == "log":
            text = log_path(parts[2]).read_text(encoding="utf-8") if log_path(parts[2]).exists() else ""
            self.send_text(text, "text/plain; charset=utf-8")
            return
        if len(parts) == 4 and parts[3] == "tests":
            self.send_json(get_review_tests(parts[2]))
            return
        if len(parts) == 4 and parts[3] == "events":
            self.stream_run_events(parts[2])
            return
        if len(parts) == 4 and parts[3] == "export":
            export_format = (parse_qs(urlparse(self.path).query).get("format") or ["json"])[0]
            body, content_type = export_review(parts[2], export_format)
            self.send_text(body, content_type)
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")

    def _write_sse(self, event: str, data: str) -> None:
        lines = data.splitlines() or [""]
        frame = f"event: {event}\n" + "".join(f"data: {line}\n" for line in lines) + "\n"
        self.wfile.write(frame.encode("utf-8"))
        self.wfile.flush()

    def stream_run_events(self, run_id: str) -> None:
        """Item 6: SSE stream of log increments + meta changes for one run.

        Terminates with `event: done` when the run reaches a final status,
        on client disconnect, or after SSE_MAX_SECONDS. Auth is the normal
        bearer header — the client uses fetch-streaming, not EventSource.
        """
        if not meta_path(run_id).exists():
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")
            return
        if not SSE_SEMAPHORE.acquire(blocking=False):
            self.send_error_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                "Too many live streams; keep polling instead.",
            )
            return
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            deadline = time.monotonic() + SSE_MAX_SECONDS
            log_offset = 0
            meta_stamp = -1.0
            while time.monotonic() < deadline:
                log_file = log_path(run_id)
                if log_file.exists():
                    size = log_file.stat().st_size
                    if size > log_offset:
                        with log_file.open("rb") as handle:
                            handle.seek(log_offset)
                            chunk = handle.read(size - log_offset)
                        log_offset = size
                        self._write_sse("log", chunk.decode("utf-8", errors="ignore"))
                meta: dict[str, Any] = {}
                if meta_path(run_id).exists():
                    stamp = meta_path(run_id).stat().st_mtime
                    if stamp != meta_stamp:
                        meta_stamp = stamp
                        meta = read_json(meta_path(run_id), {}) or {}
                        self._write_sse("meta", json.dumps(meta))
                status = str(
                    (meta or read_json(meta_path(run_id), {}) or {}).get("status") or ""
                )
                if status in {"completed", "failed", "cancelled", "unknown"}:
                    self._write_sse("done", status)
                    return
                time.sleep(SSE_TICK_SECONDS)
            self._write_sse("done", "timeout")
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # client went away — normal
        finally:
            SSE_SEMAPHORE.release()

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_REQUEST_BODY:
            raise ValueError(f"Request body too large ({length} bytes; limit {MAX_REQUEST_BODY}).")
        raw = self.rfile.read(length).decode("utf-8")
        if not raw:
            return {}
        return json.loads(raw)

    def serve_static(self, request_path: str) -> None:
        if request_path in ("", "/"):
            return self.send_file(STATIC_DIR / "index.html")
        if request_path == "/favicon.ico":
            return self.send_file(PRESS_KIT_DIR / "logo" / "gito-ai-code-reviewer_logo-180.png")
        if request_path.startswith("/assets/press-kit/"):
            rel = request_path.removeprefix("/assets/press-kit/")
            return self.send_file(PRESS_KIT_DIR / unquote(rel))
        rel = unquote(request_path.lstrip("/"))
        return self.send_file(STATIC_DIR / rel)

    def send_file(self, path: Path) -> None:
        resolved = path.resolve()
        allowed_roots = [STATIC_DIR.resolve(), PRESS_KIT_DIR.resolve()]
        if not any(resolved == root or root in resolved.parents for root in allowed_roots):
            raise FileNotFoundError(str(path))
        if not resolved.exists() or not resolved.is_file():
            raise FileNotFoundError(str(path))

        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        etag = static_etag(resolved)

        # Conditional GET — return 304 if client has the current version
        if_none_match = self.headers.get("If-None-Match", "")
        if if_none_match and if_none_match == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.end_headers()
            return

        data = resolved.read_bytes()
        accept_enc = self.headers.get("Accept-Encoding", "")
        body, enc = maybe_gzip(data, accept_enc)

        # Everything revalidates via ETag on each request. A TTL cache breaks the
        # SPA whenever the server updates mid-session: browsers keep hour-old JS
        # against a changed API. 304 responses keep revalidation cheap locally.
        cache_ctrl = "no-cache, must-revalidate"

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache_ctrl)
        if enc != "identity":
            self.send_header("Content-Encoding", enc)
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)

    def send_json(
        self,
        data: Any,
        status: HTTPStatus = HTTPStatus.OK,
        extra_headers: list[tuple[str, str]] | None = None,
    ) -> None:
        raw  = json.dumps(data, separators=(",", ":")).encode("utf-8")
        accept_enc = self.headers.get("Accept-Encoding", "")
        body, enc  = maybe_gzip(raw, accept_enc)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if enc != "identity":
            self.send_header("Content-Encoding", enc)
        for name, value in (extra_headers or []):
            self.send_header(name, value)
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)

    def send_text(self, text: str, content_type: str) -> None:
        raw  = text.encode("utf-8")
        accept_enc = self.headers.get("Accept-Encoding", "")
        body, enc  = maybe_gzip(raw, accept_enc)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if enc != "identity":
            self.send_header("Content-Encoding", enc)
        self.end_headers()
        if not self._head_only:
            self.wfile.write(body)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP_POLICY)
        super().end_headers()


def _auth_mode_label() -> str:
    if auth.users_configured():
        extra = " + service token" if brand_env("TOKEN") else ""
        return f"users ({store.user_count()} registered){extra}"
    if brand_env("TOKEN"):
        return "service token (CODEPULSE_TOKEN set)"
    return "open (no token, no users — local admin)"


def serve(host: str, port: int) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if store.migrate_legacy(AUDIT_LOG, SUPPRESSIONS_FILE, REPOS_FILE):
        sys.stderr.write("CodePulse: migrated legacy JSON store into SQLite.\n")
    bootstrapped = auth.ensure_bootstrap_admin()
    if bootstrapped:
        sys.stderr.write(f"CodePulse: created bootstrap admin {bootstrapped!r} from environment.\n")
    auth.purge_expired()
    warning = bind_warning(host)
    if warning:
        sys.stderr.write(warning + "\n")
    OLLAMA_WATCHDOG.start()
    JOB_QUEUE.start()
    httpd = ThreadingHTTPServer((host, port), CodeDoctorHandler)
    httpd.daemon_threads = True   # threads exit when main thread exits

    tls = _ssl_context()
    if tls is not None:
        httpd.socket = tls.wrap_socket(httpd.socket, server_side=True)
    scheme = "https" if tls is not None else "http"

    url = f"{scheme}://{host}:{port}"
    print(
        f"\n  CodePulse v{APP_VERSION}  →  {url}\n"
        f"  Data directory : {DATA_DIR}\n"
        f"  Transport      : {'TLS (CODEPULSE_TLS_CERT/KEY)' if tls is not None else 'plain HTTP'}\n"
        f"  Auth           : {_auth_mode_label()}\n",
        flush=True,
    )

    _stop = threading.Event()

    def _signal_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
        sys.stderr.write("\nCodePulse: shutting down gracefully…\n")
        _stop.set()
        # Shutdown in a background thread so the signal handler returns fast
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _signal_handler)
        except (OSError, ValueError):
            pass  # signal registration may fail on some platforms / threads

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        sys.stderr.write("CodePulse: stopped.\n")


def _run_user_command(args: argparse.Namespace) -> int:
    import getpass

    def _prompt_password() -> str:
        pw = getattr(args, "password", None)
        if pw:
            return pw
        pw = getpass.getpass("Password: ")
        if pw != getpass.getpass("Confirm password: "):
            sys.stderr.write("Passwords do not match.\n")
            raise SystemExit(2)
        return pw

    try:
        if args.user_command == "add":
            user = auth.create_user(args.username, _prompt_password(), args.role)
            print(f"Created {user['role']} {user['username']!r}.")
        elif args.user_command == "list":
            users = auth.list_users()
            if not users:
                print("No users registered (open mode).")
            for user in users:
                flag = " [disabled]" if user["disabled"] else ""
                print(f"  {user['username']:<20} {user['role']:<10}{flag}")
        elif args.user_command == "passwd":
            auth.set_password(args.username, _prompt_password())
            print(f"Updated password for {args.username!r}.")
        elif args.user_command == "role":
            auth.set_role(args.username, args.role)
            print(f"{args.username!r} is now {args.role}.")
        elif args.user_command == "disable":
            auth.set_disabled(args.username, True)
            print(f"Disabled {args.username!r}.")
        elif args.user_command == "enable":
            auth.set_disabled(args.username, False)
            print(f"Enabled {args.username!r}.")
        elif args.user_command == "delete":
            print(f"Deleted {args.username!r}." if auth.delete_user(args.username)
                  else f"No such user {args.username!r}.")
        else:
            sys.stderr.write("Unknown user command. Try: add, list, passwd, role, disable, enable, delete.\n")
            return 2
    except auth.AuthError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local CodePulse web app.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8787, help="TCP port (default 8787)")
    sub = parser.add_subparsers(dest="command")

    user_p = sub.add_parser("user", help="Manage login accounts (RBAC).")
    user_sub = user_p.add_subparsers(dest="user_command")
    for name in ("add", "passwd"):
        p = user_sub.add_parser(name)
        p.add_argument("username")
        p.add_argument("--password", help="Password (omit to be prompted securely).")
        if name == "add":
            p.add_argument("--role", choices=auth.ROLES, default=auth.DEFAULT_ROLE)
    role_p = user_sub.add_parser("role")
    role_p.add_argument("username")
    role_p.add_argument("role", choices=auth.ROLES)
    for name in ("disable", "enable", "delete"):
        p = user_sub.add_parser(name)
        p.add_argument("username")
    user_sub.add_parser("list")

    args = parser.parse_args()
    if args.command == "user":
        raise SystemExit(_run_user_command(args))
    serve(args.host, args.port)
