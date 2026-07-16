from __future__ import annotations

import argparse
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

from . import context_engine, publisher, static_analysis, store

# ── Production constants ────────────────────────────────────────────────────
MAX_REQUEST_BODY = 16 * 1024 * 1024   # 16 MB hard limit on JSON request bodies
GZIP_MIN_SIZE   = 860                  # compress responses larger than this
STATIC_CACHE_TTL = 3600               # 1-hour cache for static assets
APP_VERSION     = "4.3.1"
REVIEW_TIMEOUT_DEFAULT = 3600         # hard cap on a single review subprocess (seconds)
GENERATION_TIMEOUT_DEFAULT = 1200     # hard cap on a test/PR generation subprocess
VERIFY_TIMEOUT_DEFAULT = 900          # hard cap on the finding-verification subprocess
GENERATION_KINDS = {"tests", "pr"}


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
PRESS_KIT_DIR = PROJECT_ROOT / "press-kit"
DATA_DIR = PROJECT_ROOT / ".code-doctor"
RUNS_DIR = DATA_DIR / "runs"
AUDIT_LOG = DATA_DIR / "audit.jsonl"
REPOS_FILE = DATA_DIR / "repos.json"
POLICIES_FILE = DATA_DIR / "policies.json"
SUPPRESSIONS_FILE = DATA_DIR / "suppressions.json"
REVIEW_PROFILE = Path(__file__).resolve().parent / "review_profile.toml"
DEFAULT_FILTERS = "*.py,*.js,*.jsx,*.ts,*.tsx,*.mjs,*.cjs"
DEFAULT_OLLAMA_BASE = "http://localhost:11434"
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
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
            "enabled": bool(os.getenv("CODE_DOCTOR_TOKEN")),
            "evidence": "CODE_DOCTOR_TOKEN",
        },
    ],
}

PROCESS_LOCK = threading.Lock()
PROCESSES: dict[str, subprocess.Popen] = {}

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
            guardrail["enabled"] = bool(os.getenv("CODE_DOCTOR_TOKEN"))
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
        run_git(repo_path, "config", "user.name", "Code Doctor")
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
    repo_path = require_git_repo(payload.get("path") or payload.get("repoPath") or "")
    now = utc_now()
    existing = store.get_repo_by_path(str(repo_path))
    metadata = repo_metadata(repo_path)
    repo = {
        "id": existing.get("id") if existing else uuid.uuid5(uuid.NAMESPACE_URL, str(repo_path)).hex[:12],
        "name": (payload.get("name") or repo_path.name).strip(),
        "path": str(repo_path),
        "owner": (payload.get("owner") or "Engineering").strip(),
        "tier": payload.get("tier") or "production",
        "created_at": existing.get("created_at") if existing else now,
        "updated_at": now,
        "metadata": metadata,
    }
    store.save_repo(repo)
    audit_event("repo_registered", repo_path=str(repo_path), repo_id=repo["id"])
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
    return {
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
        "suppressed": len(plain_issues) - len(scored_issues),
        "verification": report.get("verification") or {},
        "rejected_issues": sum(
            len(file_issues) for file_issues in (report.get("rejected_issues") or {}).values()
        ),
        "fingerprints": fingerprints,
        "lifecycle": lifecycle,
    }


def subprocess_env(payload: dict[str, Any]) -> dict[str, str]:
    """Environment for review/generation subprocesses: Gito's LLM_* contract."""
    env = os.environ.copy()
    python_path = str(PROJECT_ROOT)
    if env.get("PYTHONPATH"):
        python_path += os.pathsep + env["PYTHONPATH"]
    env.update(
        {
            "PYTHONPATH": python_path,
            "LLM_API_TYPE": "openai",
            "LLM_API_KEY": payload.get("apiKey") or "ollama",
            "LLM_API_BASE": openai_compatible_base(payload.get("ollamaBase")),
            "MODEL": (payload.get("model") or DEFAULT_MODEL).strip(),
            "MAX_CONCURRENT_TASKS": str(payload.get("maxConcurrentTasks") or 4),
            "GITO_EXTRA_PROJECT_CONFIG": str(REVIEW_PROFILE),
        }
    )
    return env


def run_review(run_id: str, repo_path: Path, payload: dict[str, Any], command: list[str]) -> None:
    started = time.monotonic()
    env = subprocess_env(payload)

    update_meta(run_id, status="running", started_at=utc_now())
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

    def merge_static() -> int:
        if not static_issues and not crossfile_issues:
            return 0
        report = read_json(report_path(run_id), None) or static_analysis.empty_report()
        added = 0
        if static_issues:
            added += static_analysis.merge_into_report(report, static_issues)
        if crossfile_issues:
            added += context_engine.merge_into_report(report, crossfile_issues)
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
                    f"\nCode Doctor: review timed out after {timeout_seconds}s and was killed.\n".encode("utf-8")
                )
        except Exception as exc:
            log_file.write(f"\nCode Doctor failed to start Gito: {exc}\n".encode("utf-8"))
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
    verification_counts: dict[str, int] = {}
    if payload.get("verifyFindings") is not False and report_path(run_id).exists():
        verification_counts = run_verification(run_id, repo_path, payload, env)
    status = "completed" if exit_code == 0 and report_path(run_id).exists() else "failed"
    stats = summarize_report(run_id) if report_path(run_id).exists() else {}
    update_meta(
        run_id,
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
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
        static_issues=static_count,
        verification=verification_counts,
        stats=stats,
    )


def run_verification(
    run_id: str, repo_path: Path, payload: dict[str, Any], env: dict[str, str]
) -> dict[str, int]:
    """Run the skeptical second-pass verifier over the run's LLM findings."""
    report = read_json(report_path(run_id), {}) or {}
    has_llm_findings = any(
        issue.get("source") != "static"
        for file_issues in (report.get("issues") or {}).values()
        for issue in file_issues
    )
    if not has_llm_findings:
        return {}
    try:
        options = review_options(payload)
    except ValueError:
        return {}
    command = build_generation_command("verify", repo_path, run_dir(run_id), options)
    command.extend(["--report", str(report_path(run_id))])
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
                    f"\nCode Doctor: verification timed out after {timeout_seconds}s; keeping unverified findings.\n".encode("utf-8")
                )
        except Exception as exc:
            log_file.write(f"\nCode Doctor: verification failed to start: {exc}\n".encode("utf-8"))
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


def start_review(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("repoId"):
        repo = next((item for item in list_repos() if item.get("id") == payload.get("repoId")), None)
        if not repo:
            raise ValueError("Registered repository not found.")
        payload = dict(payload) | {"repoPath": repo.get("path")}
    repo_path = require_git_repo(payload.get("repoPath") or "")
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
        "model": (payload.get("model") or DEFAULT_MODEL).strip(),
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

    thread = threading.Thread(
        target=run_review,
        args=(run_id, repo_path, payload, command),
        name=f"code-doctor-review-{run_id}",
        daemon=True,
    )
    thread.start()
    return read_json(meta_path(run_id), meta)


def build_generation_command(
    kind: str,
    repo_path: Path,
    out_dir: Path,
    options: dict[str, Any],
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "code_doctor_app.generator",
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
    env = subprocess_env(payload)
    update_meta(run_id, status="running", started_at=utc_now())
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
                    f"\nCode Doctor: generation timed out after {timeout_seconds}s and was killed.\n".encode("utf-8")
                )
        except Exception as exc:
            error = str(exc)
            log_file.write(f"\nCode Doctor failed to start the generator: {exc}\n".encode("utf-8"))
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
    repo_path = require_git_repo(payload.get("repoPath") or "")
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

    thread = threading.Thread(
        target=run_generation,
        args=(run_id, repo_path, payload, command, kind),
        name=f"code-doctor-generate-{run_id}",
        daemon=True,
    )
    thread.start()
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
    repo_path = require_git_repo(payload.get("repoPath") or "")
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
    return detail


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
        {"label": "Access control", "ready": bool(os.getenv("CODE_DOCTOR_TOKEN")), "detail": "CODE_DOCTOR_TOKEN"},
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


def export_review(run_id: str, export_format: str) -> tuple[str, str]:
    detail = get_review(run_id)
    report = detail.get("report")
    audit_event("review_exported", run_id=run_id, export_format=export_format)
    if export_format == "json":
        return json.dumps(detail, indent=2), "application/json; charset=utf-8"
    if export_format == "md":
        return detail.get("markdown") or "# Code Doctor Review\n\nNo markdown report found.\n", "text/markdown; charset=utf-8"
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
        "# Code Doctor Sample Review\n\n"
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
        "git": {"ok": bool(git_path), "version": git_version},
        "ollama": ollama,
        "defaults": {
            "repoPath": str(Path.cwd()),
            "model": DEFAULT_MODEL,
            "ollamaBase": DEFAULT_OLLAMA_BASE,
            "filters": DEFAULT_FILTERS,
        },
        "authRequired": bool(os.getenv("CODE_DOCTOR_TOKEN")),
    }


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
            if path.startswith("/api/") and path != "/api/health" and not self.authorized():
                return self.send_error_json(HTTPStatus.UNAUTHORIZED, "Authorization required.")
            if path == "/api/health":
                self.send_json(system_health(query, include_ollama_check=self.authorized()))
            elif path == "/api/overview":
                self.send_json(overview())
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
            if path.startswith("/api/") and not self.authorized():
                return self.send_error_json(HTTPStatus.UNAUTHORIZED, "Authorization required.")
            if path == "/api/reviews":
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
            if path.startswith("/api/") and not self.authorized():
                return self.send_error_json(HTTPStatus.UNAUTHORIZED, "Authorization required.")
            if path.startswith("/api/repos/"):
                repo_id = unquote(path.split("/")[3])
                delete_repo(repo_id)
                self.send_json({"ok": True})
            else:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")
        except FileNotFoundError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")
        except Exception as exc:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def authorized(self) -> bool:
        expected = os.getenv("CODE_DOCTOR_TOKEN")
        if not expected:
            return True
        presented = self.headers.get("Authorization") or ""
        return hmac.compare_digest(presented, f"Bearer {expected}")

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
        if len(parts) == 4 and parts[3] == "export":
            export_format = (parse_qs(urlparse(self.path).query).get("format") or ["json"])[0]
            body, content_type = export_review(parts[2], export_format)
            self.send_text(body, content_type)
            return
        self.send_error_json(HTTPStatus.NOT_FOUND, "Not found.")

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

    def send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw  = json.dumps(data, separators=(",", ":")).encode("utf-8")
        accept_enc = self.headers.get("Accept-Encoding", "")
        body, enc  = maybe_gzip(raw, accept_enc)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if enc != "identity":
            self.send_header("Content-Encoding", enc)
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
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        super().end_headers()


def serve(host: str, port: int) -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    if store.migrate_legacy(AUDIT_LOG, SUPPRESSIONS_FILE, REPOS_FILE):
        sys.stderr.write("Code Doctor: migrated legacy JSON store into SQLite.\n")
    httpd = ThreadingHTTPServer((host, port), CodeDoctorHandler)
    httpd.daemon_threads = True   # threads exit when main thread exits

    url = f"http://{host}:{port}"
    print(
        f"\n  Code Doctor v{APP_VERSION}  →  {url}\n"
        f"  Data directory : {DATA_DIR}\n"
        f"  Auth           : {'TOKEN (CODE_DOCTOR_TOKEN set)' if os.getenv('CODE_DOCTOR_TOKEN') else 'open (no token)'}\n",
        flush=True,
    )

    _stop = threading.Event()

    def _signal_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
        sys.stderr.write("\nCode Doctor: shutting down gracefully…\n")
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
        sys.stderr.write("Code Doctor: stopped.\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Code Doctor web app.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8787, help="TCP port (default 8787)")
    args = parser.parse_args()
    serve(args.host, args.port)
