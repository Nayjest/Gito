# Changelog

Code Doctor releases. Format follows [Keep a Changelog](https://keepachangelog.com/);
item numbers reference [NEXT_RELEASE_PLAN.md](NEXT_RELEASE_PLAN.md).
The Gito review engine underneath keeps its own upstream versioning.

## [Unreleased] — 5.1 security hardening

Store schema: two additive tables (`users`, `sessions`); existing tables and
`issue_fingerprint()` are unchanged. Fully backward-compatible — an install
with no registered users and no `CODEPULSE_TOKEN` behaves exactly as before
(local admin, "open mode").

### Added
- **Multi-user identity + RBAC (`auth.py`).** Real login accounts with roles
  `viewer` < `reviewer` < `admin`. Passwords are hashed with `hashlib.scrypt`
  (stdlib — no new dependency); login issues a session whose token is stored
  only as a SHA-256 digest. Three modes, chosen automatically: **open** (no
  users, no token → local admin, unchanged), **service token**
  (`CODEPULSE_TOKEN` → admin, unchanged for CI/webhooks/scripts), and
  **multi-user** (once any user exists, anonymous access closes and callers log
  in). Role floors: reads need `viewer`, starting reviews/fixes needs
  `reviewer`, and config/repos/seed/user-management need `admin`.
- **Auth endpoints.** `POST /api/login`, `POST /api/logout`, `GET /api/me`,
  and admin-only `GET/POST /api/users`, `POST/DELETE /api/users/<name>`. The
  login session is also set as a hardened cookie (`HttpOnly`, `SameSite=Strict`,
  `Secure` under TLS). The SPA gains a login gate, a user badge with role, and
  logout; the manual token field is now reserved for service accounts.
- **User CLI.** `codepulse user add|list|passwd|role|disable|enable|delete`
  (passwords prompted securely). Bootstrap an admin non-interactively with
  `CODEPULSE_ADMIN_USER` / `CODEPULSE_ADMIN_PASSWORD`.
- **Optional TLS.** Set `CODEPULSE_TLS_CERT` / `CODEPULSE_TLS_KEY` to serve
  HTTPS directly (stdlib `ssl`, TLS 1.2+); startup fails loudly on a bad
  cert/key rather than silently serving plaintext. The non-loopback bind
  warning now clears when any authentication is configured, not just a token.
- **Wider taint recall.** New deterministic sinks: arbitrary file
  delete/move (`os.remove`, `os.unlink`, `os.rename`, `shutil.rmtree`,
  `shutil.move`, pathlib `Path.unlink`), deserialization RCE (`yaml.load`,
  `marshal.loads`), SSTI (`Environment.from_string`), and shell execution
  (`subprocess.getoutput` / `getstatusoutput`). All fire only on
  request-derived arguments; a self-scan of CodePulse's own source yields zero
  findings.
- **Zyloo LLM provider + `.env` loader.** New OpenAI-compatible `zyloo`
  provider (default model `zyloo/gemini-3-pro-free`); the server loads a
  project-root `.env` at startup (stdlib, no python-dotenv). Configured cloud
  providers now win over local Ollama as the review-form default.
- **Scope gate — token-cost trimming for whole-repo reviews (`scope_gate.py`).**
  A normal git PR only sends changed diff hunks to the model, but a snapshot or
  `--all` review sends every file's full content (each file reads as *added*).
  For that case only, the gate excludes non-source noise from the LLM pass —
  generated code (`*_pb2.py`, `*.generated.*`, `@generated`/`DO NOT EDIT`
  markers), vendored trees (`vendor/`, `third_party/`), minified bundles and
  large single-line blobs, lockfiles, and data assets — while the deterministic
  engines still scan every file, so recall on real source is unchanged. It
  reuses Gito's own `GITO_EXTRA_PROJECT_CONFIG` seam (a per-run merged profile
  that also preserves the deep-review prompt); no Gito code is modified.
  Auto-on for snapshot/whole-repo reviews, opt-out via `scopeGate: false`. The
  Reports panel shows how many files were skipped and the estimated tokens
  saved. Pure stdlib, additive; ordinary PRs are byte-identical to before.

### Fixed
- **Findings list rendered empty for any review with proposed fixes.** The
  finding-card template called `renderFixControls(issue)` and the click wiring
  called `handleFixAction(...)`, but neither function was ever defined — so
  `issues.map()` threw a `ReferenceError` on every finding that carried a
  proposal (i.e. every LLM review). The throw was swallowed by
  `Promise.allSettled`, so there was no console error and the Findings view just
  showed "No run" with an empty list. Both functions are now defined and wired
  to the backend patcher (preview / apply / revert), so findings render and
  proposed fixes are actionable.
- **Deterministic findings on LLM-flagged lines are now corroborated, not
  dropped.** `merge_into_report` previously discarded a high-confidence static
  finding whenever the LLM flagged the same line. It now records the agreeing
  rule in a `corroborated_by` field on the LLM finding (surfaced as a
  "✓✓ corroborated" chip) instead of hiding it. The field is separate from
  `tags`, so `issue_fingerprint` is unchanged and stored suppressions keep
  matching.

- **Graceful degradation for large reviews.** When the LLM subprocess times
  out or errors but the deterministic engines already produced findings, the
  run is now surfaced as **completed (degraded)** with a `degraded` /
  `degraded_reason` flag and a clear UI note, instead of a bare `failed` that
  discarded the static/cross-file/taint/dependency results. A whole-repo review
  that outgrows a local model still returns usable findings.

## [5.0.0] — 2026-07-16

Store schema: `kv.schema_version` unchanged (tables introduced in 4.3 remain
the full set; no new tables this release).

### Added
- **Quality trends + repository health score.** Every completed review now
  carries `stats.health` — a 0–100 score with a letter grade (A–F), derived
  only from frozen stats keys (risk score, gate, severity counts) so it is
  recomputed identically for historical runs. New `GET /api/trends` aggregates
  completed review runs into time series, globally and per repository
  (risk, issues, severity mix, gate, health, duration; `?repo=` and `?limit=`
  filters). The Cockpit gains a **Quality Trends** chart (inline SVG, no
  external libraries): health line vs risk line with gate-colored run dots,
  per-repo sparklines and grade badges, a health ring gauge on the latest
  review, and a severity-distribution strip. The Review view shows finding
  **lifecycle chips** (new / recurring / resolved vs the previous run).
- **Light theme.** A topbar toggle switches between the dark and a light
  (GitHub-light palette) theme; the choice persists in the browser. The
  topbar also wraps gracefully on narrow windows.
- **Bounded job queue with a worker pool (`jobqueue.py`).** Reviews and
  generations run through a fixed-size pool instead of each spawning its own
  unbounded thread: at most `CODEPULSE_REVIEW_WORKERS` (default 2) run at
  once, the rest wait FIFO with meta status `queued`, so a burst can't thrash a
  single local GPU or a cloud rate limit. `/api/health` reports live
  `queue: {workers, active, queued}`; a failing job never kills its worker.
- **Dependency / supply-chain scanning (`dependency_scan.py`).** Offline
  checks over changed `requirements.txt` / `package.json`: typosquat detection
  (edit-distance-1, including adjacent transpositions, against a bundled
  popular-package set), unpinned/unbounded versions (pip without `==`, npm
  wildcards and open ranges — conventional `^`/`~` left alone), and URL/VCS
  installs that bypass the index. Findings use IDs 40000+ and the
  `supply-chain` tag; opt out with `"dependencyScan": false`.
- **SARIF 2.1.0 export.** `?format=sarif` (and an Evidence Exports card) render
  a run as SARIF for GitHub Code Scanning: severity → level, findings →
  results with physical locations, distinct rules in the tool driver
  (`sarif.py`).
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
- **Item 4 — CI mode and webhooks.** `python -m codepulse_app.ci` runs the
  full server pipeline synchronously, prints the summary markdown, and exits
  by gate (`--fail-on block|review|none`; optional `--publish-pr N`,
  `--json`). `POST /api/hooks/github|gitlab` reviews PRs/MRs on push:
  HMAC-SHA256 / shared-token verification via `CODEPULSE_WEBHOOK_SECRET`
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

### Added
- **Interprocedural, cross-module taint analysis.** The dataflow engine
  (`taint_analysis.py`) is no longer limited to a single function. Every
  function and method in the repository is summarized (which parameters reach
  a sink, whether the return value is untrusted, which parameters flow to the
  return), a fixpoint resolves helper chains, and call sites apply those
  summaries — so the common "thin route handler → manager/helper module"
  shape is now analyzed end to end. A handler that passes a path parameter to
  `store.read_note(path)`, where `read_note` in another file does
  `open(self.vault / path)`, is now flagged at the call site
  (`interprocedural` tag). Taint also propagates through `/` pathlib joins
  (`base / user_input`), the dominant path-traversal pattern. Route handlers
  are excluded as call targets and ambiguous helper names are never assumed
  dangerous, keeping false positives rare (zero on this codebase). Validated
  against a real Flask app: deterministic findings rose from 5 to 8, the three
  new ones being genuine cross-module arbitrary file read/write vulnerabilities
  the previous engine and a local LLM both missed.
- **Production-reliability and deeper security static rules
  (`static_analysis.py`).** New high-precision checks: outbound HTTP calls
  without a timeout (worker-exhaustion outages), TLS verification disabled
  (`verify=False` / `rejectUnauthorized:false`), MD5/SHA-1 and non-crypto
  `random`/`Math.random` used for credentials or tokens (context-gated to
  avoid flagging checksums or dice rolls), SQL built by f-string/concat/
  template-literal, JS `exec`/`execSync` on interpolated commands, silently
  swallowed exceptions (`except…: pass`, empty `catch{}`), JWT decoded without
  signature verification, `tempfile.mktemp` races, and world-writable chmod.
- **Deep production-review profile.** The LLM review persona
  (`review_profile.toml`) is now a principal engineer doing a
  production-readiness pass with an explicit trace-the-flow checklist
  (authorization/tenancy, injection across function boundaries, data-loss and
  atomicity, timeouts/retries/resource lifecycle, concurrency, performance
  under load), replacing the previous "small teachable fixes only" cap that
  held the model back on production code.

### Changed
- **Deep rename: package and environment variables.** The Python package is
  now `codepulse_app` (`python -m codepulse_app`, console script `codepulse`),
  and configuration reads `CODEPULSE_*` environment variables first:
  `CODEPULSE_TOKEN`, `CODEPULSE_WEBHOOK_SECRET`, `CODEPULSE_REVIEW_WORKERS`,
  `CODEPULSE_GITHUB_TOKEN`/`CODEPULSE_GITLAB_TOKEN`, and
  `CODEPULSE_ANTHROPIC_KEY`/`CODEPULSE_OPENAI_KEY`. **Nothing breaks:** every
  legacy `CODE_DOCTOR_*` variable still works as a fallback (the new name wins
  when both are set), `python -m code_doctor_app` and `import code_doctor_app`
  keep working through a deprecation shim, the `code-doctor` console script
  remains as an alias, and the `.code-doctor/` data directory is unchanged, so
  existing runs, suppressions, and tokens carry over untouched. Secret
  stripping for review subprocesses (QW-5) covers both prefixes.
- **Rebranded to CodePulse.** New name and a new logo — an ECG pulse mark on a
  blue→violet gradient (inline SVG, plus a matching favicon; the old gito
  press-kit PNG is no longer referenced). The rename covers every user-facing
  surface: UI branding, page title, export filenames (`codepulse-<run>.<ext>`),
  SARIF tool driver name, PR/MR review headings, LLM prompt personas, and CLI
  output (`codepulse:` prefix in CI mode). **Machine identifiers are
  unchanged** so existing setups keep working: the `codepulse_app` package,
  `CODE_DOCTOR_*` environment variables, the `.code-doctor/` data directory,
  and the `code-doctor/gate` commit-status context.

### Security
- QW-1: constant-time bearer-token comparison (`hmac.compare_digest`).
- QW-2: loud warning when binding beyond loopback without a token.
- QW-3: per-IP throttling after repeated 401s (10 failures/60s → 429 for 60s,
  audited as `auth_throttled`).
- QW-4: CSP tightened with `form-action 'self'; object-src 'none'`.
- QW-5: `CODEPULSE_TOKEN`, publish tokens, and the webhook secret are
  stripped from review/generation subprocess environments.

### Compatibility
- All run artifacts evolved additively (rule R1); `issue_fingerprint()` is
  frozen, so stored suppressions keep matching.
- New behaviors are flag-gated to current defaults: `reuseVerdicts` (on, opt
  out), `ci.autoPublish` (off, opt in), webhooks inert without
  `CODEPULSE_WEBHOOK_SECRET`, model routing empty-inherits the main model,
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
