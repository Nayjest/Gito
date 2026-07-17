#!/usr/bin/env bash
# Live API smoke test (release plan §4, step 5). Run against a running server:
#
#   CODEPULSE_TOKEN=... scripts/smoke.sh [base-url]   (legacy CODE_DOCTOR_TOKEN also works)
#
# Env knobs: SMOKE_MODEL (default gemma4:e4b), SMOKE_TIMEOUT (review wait,
# default 900s), SMOKE_SKIP_LLM=1 (skip the live review + test generation —
# still exercises seed/dismiss/export/publish/overview on the seeded run).
# Exits non-zero on the first failed assertion.
set -euo pipefail

BASE="${1:-http://127.0.0.1:8787}" exec "$(dirname "$0")/../.venv/bin/python" - <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BASE", "http://127.0.0.1:8787").rstrip("/")
TOKEN = os.environ.get("CODEPULSE_TOKEN") or os.environ.get("CODE_DOCTOR_TOKEN", "")
MODEL = os.environ.get("SMOKE_MODEL", "gemma4:e4b")
TIMEOUT = int(os.environ.get("SMOKE_TIMEOUT", "900"))
SKIP_LLM = os.environ.get("SMOKE_SKIP_LLM") == "1"

PASSED = 0


def api(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read().decode()
    return json.loads(raw) if raw.lstrip().startswith(("{", "[")) else raw


def check(name, condition, detail=""):
    global PASSED
    if not condition:
        print(f"FAIL  {name}  {detail}")
        sys.exit(1)
    PASSED += 1
    print(f"ok    {name}")


health = api("/api/health")
check("health", isinstance(health, dict) and bool(health), str(health)[:120])

seeded = api("/api/sample/seed", "POST")
run_id = seeded["id"]
check("seed sample", bool(run_id) and seeded.get("status") == "completed")

detail = api(f"/api/reviews/{run_id}")
stats = detail["meta"].get("stats") or {}
for key in ("gate", "risk_score", "severity_counts", "lifecycle", "verification"):
    check(f"stats.{key}", key in stats)

issues = [i for f in (detail["report"].get("issues") or {}).values() for i in f]
check("seeded findings", len(issues) >= 1)
finding = issues[0]
feedback = {"runId": run_id, "issueId": finding["id"], "action": "dismiss"}
api("/api/findings/feedback", "POST", feedback)
check("dismiss finding", True)
api("/api/findings/feedback", "POST", {**feedback, "action": "restore"})
check("restore finding", True)

for fmt in ("json", "md", "csv"):
    body = api(f"/api/reviews/{run_id}/export?format={fmt}")
    check(f"export {fmt}", bool(body))

preview = api(
    f"/api/reviews/{run_id}/publish", "POST",
    {"platform": "github", "repo": "acme/smoke", "pr": 1, "dryRun": True},
)
check("publish dry-run", preview.get("dry_run") is True and preview.get("summary_markdown"))

overview = api("/api/overview")
check("overview", "repositories" in json.dumps(overview) or overview)

if not SKIP_LLM:
    repos = api("/api/repos").get("repos") or []
    sample = next((r for r in repos if "sample" in r.get("path", "")), repos[0] if repos else None)
    check("sample repo registered", sample is not None)
    started = api(
        "/api/reviews", "POST",
        {"repoPath": sample["path"], "mode": "working", "model": MODEL},
    )
    live_id = started["id"]
    print(f"      live review {live_id} with {MODEL} (waiting up to {TIMEOUT}s)…")
    deadline = time.time() + TIMEOUT
    status = "running"
    while time.time() < deadline:
        status = api(f"/api/reviews/{live_id}")["meta"].get("status")
        if status in {"completed", "failed"}:
            break
        time.sleep(5)
    check("live review completed", status == "completed", f"status={status}")
    live_stats = api(f"/api/reviews/{live_id}")["meta"].get("stats") or {}
    check("live stats.gate", "gate" in live_stats)

    generated = api(
        "/api/generate", "POST",
        {"repoPath": sample["path"], "mode": "working", "kind": "tests", "model": MODEL},
    )
    gen_id = generated["id"]
    print(f"      test generation {gen_id} (waiting)…")
    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        status = api(f"/api/reviews/{gen_id}")["meta"].get("status")
        if status in {"completed", "failed"}:
            break
        time.sleep(5)
    check("test generation completed", status == "completed", f"status={status}")

print(f"\nSMOKE PASSED — {PASSED} checks")
PY
