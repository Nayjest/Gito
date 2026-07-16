# Changelog

Code Doctor releases. Format follows [Keep a Changelog](https://keepachangelog.com/);
item numbers reference [NEXT_RELEASE_PLAN.md](NEXT_RELEASE_PLAN.md).
The Gito review engine underneath keeps its own upstream versioning.

## [5.0.0] — 2026-07-16

Store schema: `kv.schema_version` unchanged (tables introduced in 4.3 remain
the full set; no new tables this release).

### Added
- **Cloud LLM providers (Anthropic / OpenAI / Google) alongside Ollama.** A
  provider registry (`LLM_PROVIDERS`) selects the LLM backend per run; cloud
  API keys are read from the **server environment only**
  (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY`), never the request
  payload (the old `apiKey` payload field is ignored). Cloud providers default
  to a higher `MAX_CONCURRENT_TASKS` so whole-repo reviews finish fast instead
  of timing out on a single local GPU, and a frontier model catches the deep
  issues a small local model misses. Runs are rejected up front when the chosen
  provider has no key; `/api/health` lists providers with a `configured` flag;
  the Cockpit gains a provider selector; run meta records `provider`.
- **AST taint / dataflow analysis (`taint_analysis.py`).** A new deterministic
  engine tracks untrusted input (request data, route-handler params, `input()`)
  to dangerous sinks — path traversal (`open`/`send_file`/`Path.read_text`),
  SSRF (`requests`/`urlopen`/`httpx`), command injection
  (`subprocess`/`os.system`), code execution (`eval`/`exec`/`pickle`), SQL
  injection (`cursor.execute` on a built string), SSTI, and open redirect —
  through assignments, f-strings, concatenation, `.format()`, and wrapper
  calls, with `int()`/reassignment clearing taint. Intraprocedural and
  conservative (few false positives). Findings use IDs 30000+ and the
  `taint`/`dataflow` tags; runs report `stats.taint_issues` and
  `/api/health` reports `engines.taint`. Opt out with `"taintAnalysis": false`.
- **Item 1 — Semantic cross-file analysis.** Python symbol extraction moved
  from regexes to real AST parsing (`semantic_py.py`) with argument-binding
  simulation at call sites: provably breaking calls carry a `break_reason`
  and raised confidence; findings are suppressed when every call site already
  satisfies the new signature. JS/TS moved to a brace-depth tolerant
  tokenizer (`semantic_js.py`), self-upgrading to tree-sitter when
  `tree_sitter_languages` is installed (mode reported in `/api/health` under
  `engines.semanticJs`). Regex extraction remains the automatic fallback.
- **Item 2 — Incremental re-review (verdict reuse).** Re-running a review on
  the same repository carries the previous run's verifier verdicts — by exact
  fingerprint, with an unambiguous position-based fallback (same file,
  overlapping lines, shared tag) since LLMs reword findings between runs:
  confirmed findings are stamped `verified` + `carried_from`,
  rejected ones quarantine immediately, and only genuinely new findings reach
  the verifier LLM (`--skip-ids`; subprocess skipped entirely when nothing
  remains). Meta gains `reused_verdicts`, stats gain `verification.carried`,
  the UI shows a muted `carried` chip. Opt out with `"reuseVerdicts": false`.
- **Item 4 — CI mode and webhooks.** `python -m code_doctor_app.ci` runs the
  full server pipeline synchronously, prints the summary markdown, and exits
  by gate (`--fail-on block|review|none`; optional `--publish-pr N`,
  `--json`). `POST /api/hooks/github|gitlab` reviews PRs/MRs on push:
  HMAC-SHA256 / shared-token verification via `CODE_DOCTOR_WEBHOOK_SECRET`
  (503 when unset), events map to **registered** repositories by origin
  remote only, unknown events/repos answer 202 and audit `webhook_ignored`.
  Opt-in `ci.autoPublish` policy posts the review to the PR/MR and the gate
  as a `code-doctor/gate` commit status. New governance fields for
  `ci.autoPublish` and `ci.failOn`.
- **Item 3 (early, from the v5.1 slice) — Auto-fix apply + write tests into
  repo.** `POST /api/reviews/<id>/fixes/plan|apply|revert` and
  `/tests/write`, backed by `patcher.py`: a fix applies only when the
  finding's recorded snippet still matches the file exactly (drifted files
  are refused, never guessed at), originals are backed up under the run
  directory before any write, writes are atomic, paths resolve strictly
  inside the repo with symlinks refused, and every apply is an explicit
  per-fix request — no bulk apply.
- **Item 6 (early, v5.1) — SSE live streaming.** `GET
  /api/reviews/<id>/events` streams log increments and meta changes,
  ending with `event: done` at a final status; normal header auth
  (fetch-streaming client), concurrency-capped and time-capped server-side.
- **Item 9 (early, v5.1) — Ollama watchdog.** A daemon thread samples the
  local Ollama endpoint every 30s; `/api/health` exposes the rolling state
  under `ollamaWatch`, and runs started while it is down are stamped
  `ollama_warning` instead of failing cryptically mid-review.
- **Item 14 (early, v5.1) — Per-pass model routing.** The verifier and
  generator passes can run on their own model: per-run `verifyModel` /
  `generateModel` payload keys win over workspace policy `models.verify` /
  `models.generate`; empty inherits the run's main model (today's
  behavior).
- **Review any local folder, git or not.** Pointing the repository path at a
  non-git directory — or a freshly `git init`-ed repo with no commits yet
  (unborn HEAD, nothing to diff against) — now works: Code Doctor
  materializes a managed git
  **snapshot** under `.code-doctor/snapshots/` (heavy dirs like
  `node_modules`, virtualenvs, and build output skipped), `git init` +
  baseline commit, and reviews the empty-tree diff so every file is analyzed
  — reusing the entire diff-based pipeline unchanged. The user's folder is
  never modified and never gets a `.git`. Runs carry `is_snapshot` /
  `source_path`; the dashboard shows a `local snapshot` badge and preflight
  note; a 20k-file / 300 MB guardrail refuses runaway copies
  (`snapshot.py`).
- `/api/health` now reports the app `version`.
- `scripts/smoke.sh` (live API smoke) and `scripts/bench.py` (performance
  budgets, §4b) for the per-release gate.
- Test infrastructure (§4c): artifact schema contract tests
  (`tests/schemas/` + subset validator) enforcing additive-only run
  artifacts; HTTP harness (`tests/_server_harness.py`) for socket-level
  tests; frozen v4.3 data fixture proving legacy migration + idempotency.
- `docs/OPERATIONS.md` runbook and `docs/SMOKE.md` browser checklist.

### Security
- QW-1: constant-time bearer-token comparison (`hmac.compare_digest`).
- QW-2: loud warning when binding beyond loopback without a token.
- QW-3: per-IP throttling after repeated 401s (10 failures/60s → 429 for 60s,
  audited as `auth_throttled`).
- QW-4: CSP tightened with `form-action 'self'; object-src 'none'`.
- QW-5: `CODE_DOCTOR_TOKEN`, publish tokens, and the webhook secret are
  stripped from review/generation subprocess environments.

### Compatibility
- All run artifacts evolved additively (rule R1); `issue_fingerprint()` is
  frozen, so stored suppressions keep matching.
- New behaviors are flag-gated to current defaults: `reuseVerdicts` (on, opt
  out), `ci.autoPublish` (off, opt in), webhooks inert without
  `CODE_DOCTOR_WEBHOOK_SECRET`, model routing empty-inherits the main model,
  fix apply is per-finding opt-in only, SSE is a new endpoint existing
  clients never call.
- Items 3, 6, 9, and 14 from the v5.1 slice shipped early in this release;
  all are additive and inert until used.

## [4.3.x] — earlier

Pre-changelog history: hybrid static analysis, test-case planner and
generators, verification pass with reviewer feedback loop, risk engine and
lifecycle tracking, SQLite store with legacy JSON migration, cross-file
context engine (regex era), GitHub/GitLab publishing from the dashboard.
See `git log` for details.
