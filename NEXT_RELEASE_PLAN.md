# Code Doctor — Next Release Plan (v5.x)

Detailed design and execution plan for the twelve enhancements queued after
v4.3 (commit `24c3585`: cross-file analysis, PR publishing, SQLite store,
verification loop). Every item is specified against the **current** code so it
can be built without breaking what exists.

---

## 0. Ground Rules — How We Keep Existing Code Working

These rules apply to every item below. They are the reason each design section
lists "compatibility" steps explicitly.

**R1 — Additive schemas only.**
Run artifacts (`meta.json`, `code-review-report.json`, `context-pack.json`,
`generated-tests.json`, `pr-draft.json`, `publish.json`) may gain new keys but
never lose or rename existing ones inside a major release. Every reader
already tolerates missing keys (`read_json(..., None)`, `.get(...)` with
defaults) — keep it that way. Old runs on disk must render in the new UI.

**R2 — Feature flags with current behavior as default.**
New review-payload options follow the existing convention:
`payload.get("newThing") is not False` when the feature should default ON, or
`payload.get("newThing") is True` when it must default OFF. Existing callers
(UI, curl scripts, tests) send neither and get today's behavior.

**R3 — New modules, thin integration.**
Each engine lands as a new module in `code_doctor_app/` (like
`context_engine.py`, `publisher.py`, `store.py` did) with its own test file.
`server.py` gets only orchestration calls guarded by try/except that audit a
`*_failed` event and continue — the pattern used by static analysis and
cross-file analysis in `run_review()` today. A crashing new engine must never
fail a review.

**R4 — SQLite migrations are versioned and idempotent.**
`store.py` gains a `schema_version` row in the `kv` table. Every new table or
column ships as `CREATE TABLE IF NOT EXISTS` / guarded `ALTER TABLE` executed
in `_conn()` bootstrap. Never modify existing columns. Downgrade safety:
older code ignores unknown tables.

**R5 — Frontend renders are null-safe.**
Every new panel follows the `renderCrossFile()` pattern: bail out with an
empty-state when the data key is absent, so old runs and in-flight runs never
throw. New API fields consumed with `?.` and `?? default`.

**R6 — Test gate before merge of each phase.**
`.venv/bin/python -m pytest tests/ -q` must show zero new failures (the only
allowed failure is the pre-existing environmental
`test_version.py::test_version_command_shell`). Each item's section lists the
new test file(s) it must ship with. Run the live smoke (seed sample →
review → dismiss → export) after each phase.

**R7 — No new hard dependencies without approval.**
The app is stdlib + gito/microcore today. Items that would benefit from a
library (tree-sitter, watchdog) specify a stdlib fallback and make the
library optional (`try: import ... except ImportError: DEGRADED_MODE`).

---

## 1. Current Architecture Snapshot (anchors for all work below)

```
code_doctor_app/
  server.py           HTTP handler (BaseHTTPRequestHandler), run orchestration
                      key funcs: start_review, run_review, run_verification,
                      start_generation, summarize_report, publish_run,
                      record_finding_feedback, overview, routes in do_GET/do_POST
  static_analysis.py  deterministic diff rules; collect_diff/_diff_args/
                      analyze_repo_changes/merge_into_report (IDs 10000+)
  context_engine.py   import graph, symbol diffing, crossfile findings (IDs 20000+),
                      context-pack.json
  generator.py        subprocess CLI: --kind tests|pr|verify, retry loop,
                      parse_llm_json, --context pack consumption
  publisher.py        GitHub/GitLab publishing, dry-run first, env-only tokens
  store.py            SQLite WAL: audit, suppressions, repos, kv; migrate_legacy
  static/             vanilla JS SPA (app.js), 5s polling, token in localStorage
.code-doctor/
  runs/<run-id>/      meta.json, report, md, gito.log, context-pack.json,
                      verification.json, generated-tests*, pr-draft.json, publish.json
  code-doctor.db      SQLite store (+ audit.jsonl evidence mirror)
```

Review pipeline (in `run_review`): static pass → cross-file pass → gito LLM
subprocess (timeout, PROCESSES dict for cancel) → merge deterministic findings
→ verification subprocess → `summarize_report` (risk, gate, suppressions,
lifecycle) → meta/audit.

Issue identity: `issue_fingerprint()` = sha1(file|tags|normalized title)[:16].
Suppressions and lifecycle both key on it. **Never change this function's
output** — it would orphan every stored suppression. If a better fingerprint
is needed, add `fingerprint_v2` alongside and match on either.

---

## 2. Release Slicing

| Release | Items | Theme |
| --- | --- | --- |
| **v5.0** | QW quick wins · 1. Semantic cross-file (AST) · 2. Incremental re-review · 4. CI webhook mode | Live-in-the-merge-path |
| **v5.1** | 3. Auto-fix apply · 6. SSE live streaming · 9. Ollama watchdog · 14. Per-pass model routing | Reviewer ergonomics |
| **v5.2** | 7. Job queue · 8. Multi-user identity · 10. Retention & trends · 15. Server config file | Scale & operations |
| **v5.3** | 5. Publish upgrades · 11. Supply-chain checks · 12. Custom rule packs · 13. SARIF export | Breadth |

Dependency order inside v5.0: **Item 2 before Item 4** (the webhook re-uses
incremental re-review), Item 1 independent. Inside v5.2: **Item 7 before 8**
(queue rows carry the user id). Item 13 pairs naturally with Item 4 (upload
SARIF from CI mode) but only depends on the export path, so it can land any
time. The Section 7 security backlog and Section 9 schema contract tests
start in v5.0 and carry through every release.

---

## 3. Item-by-Item Design and Execution

### Item 1 — Semantic cross-file analysis (AST-based) — v5.0

**Goal.** Replace regex symbol matching in `context_engine.py` with real
parsing so renamed keyword args, decorator-wrapped defs, class methods,
re-exports, and argument-count mismatches are caught, and false positives from
string/comment matches disappear.

**Design.**
- New module `code_doctor_app/semantic_py.py` (stdlib `ast`, zero deps):
  - `module_symbols(source) -> {name: SymbolInfo}` where `SymbolInfo` has
    `kind` (function/class/method), `params` (list of `(name, has_default,
    kind)` from `ast.arguments`, including kw-only/varargs), `decorators`,
    `lineno`, `is_exported` (not underscore-prefixed, or in `__all__`).
  - `call_sites(source, symbol) -> [CallSite(lineno, n_pos_args, kw_names,
    has_star_args)]` via `ast.walk` on `ast.Call`, matching `Name` and
    `Attribute` callees.
  - `signature_break(before: SymbolInfo, after: SymbolInfo, call: CallSite)
    -> str | None` — returns a human reason ("new required parameter
    'currency' not passed", "keyword 'amount' removed") or None if the call
    still binds. Implement by simulating Python's argument binding against
    the new signature.
- New module `code_doctor_app/semantic_js.py`:
  - **Default path (stdlib):** a tolerant tokenizer that improves on the
    current regex (tracks brace depth for exported class methods, follows
    `export { a as b }` aliases). Explicitly *not* a full parser.
  - **Optional path (R7):** `try: import tree_sitter_languages` — if present,
    use real TS/JS grammars. Feature-detect once at import; expose
    `SEMANTIC_JS_MODE = "tree-sitter" | "heuristic"` for the health endpoint.
- `context_engine.py` changes (all internal, public API unchanged):
  - `diff_symbol_changes()` keeps its exact return shape
    `{file: {added, removed, signature_changed}}` but is reimplemented: for
    each changed file, parse the **before** blob (`git show BASE:file` via a
    new `static_analysis._git_stdout` call) and the **after** working copy,
    then diff `module_symbols()` output. This also fixes the current
    limitation that only symbols whose `def` line appears in the diff hunks
    are seen.
  - `find_usages()` → for Python dependents call `semantic_py.call_sites`;
    each usage gains an optional `"break_reason"` key when
    `signature_break()` fires. Regex fallback stays for non-Python.
  - Finding severity: keep severity 2, but raise confidence 2 → 1 when
    `break_reason` is present (a bound-checked break is near-certain), and
    include the reason in `details`.

**Execution steps.**
1. Build `semantic_py.py` + `tests/test_semantic_py.py` in isolation:
   binding simulation cases (positional/kw/defaults/*args/**kwargs,
   kw-only), decorated defs, class methods, `__all__` handling.
2. Add `git_show_blob(repo, ref, path)` helper to `static_analysis.py`
   (wraps `_git_stdout(repo, "show", f"{ref}:{path}")`; empty string on
   failure — same convention as the other git helpers).
3. Rewire `diff_symbol_changes()`; keep the old regex implementation as
   `_diff_symbol_changes_textual()` and use it as fallback whenever parsing
   raises (`SyntaxError` on partial/py2 files) so behavior degrades to
   today's, never worse.
4. Rewire `find_usages()`; extend the usage dict additively
   (`break_reason`, `kind`).
5. `semantic_js.py` heuristic pass; tree-sitter branch behind import guard.
6. Update `tests/test_context_engine.py`: existing five tests must pass
   **unmodified** (they assert the public shapes — this is the compatibility
   proof). Add cases: kwarg removal, new required param, decorated function,
   method on imported class, false-positive check (symbol name inside a
   string/comment must not match).
7. Surface `break_reason` in the UI usage rows (`renderCrossFile`, additive).

**Compatibility.** Return shapes of `analyze_cross_file`, pack schema, and
finding schema unchanged (new optional keys only). ID base stays 20000.
Fingerprints unaffected (title format for the two finding types stays
byte-identical; the richer reason goes into `details`, which is not part of
the fingerprint).

**Acceptance.** Live check: the `crossfile-demo` scratch scenario still yields
both findings; a new scenario where a call site already passes the new
argument yields **zero** findings (today's regex version would still flag it).

---

### Item 2 — Incremental review / re-review — v5.0

**Goal.** Re-running a review on the same repo+scope reuses verdicts and
carries dismissals forward; the report distinguishes *new since last run*
from *carried over*. Cost: only genuinely new findings hit the verifier LLM.

**Design.**
- We already have per-repo baselines: `previous_fingerprints()` and
  `lifecycle` in `summarize_report`. Build on that, don't duplicate it.
- New helper in `server.py`:
  `previous_run_context(repo_path, created_at) -> {"run_id", "verdicts": {fingerprint: {"verdict", "reason"}}, "head": commit}`
  — loads the newest earlier completed run of the repo (same scan loop as
  `previous_fingerprints`), reads its report, and maps
  `issue_fingerprint(issue) -> (verdict, verifier_reason)` for every issue
  that has `verified` set or lives in `rejected_issues`.
- In `run_review()`, **after** `merge_static()` and **before**
  `run_verification()`:
  1. Load the fresh report, compute fingerprints per issue.
  2. For every issue whose fingerprint has a prior **confirmed** verdict:
     stamp `verified: True`, `verifier_reason`, add `"verified"` tag, and
     `"carried_from": prior_run_id` (new additive key).
  3. For fingerprints previously **rejected**: move the issue straight to
     `rejected_issues` with `verifier_reason` + `carried_from`.
  4. Only unmatched issues remain for `run_verification`. Implement by
     passing a `skip_ids` list: `generator.py --kind verify` gains
     `--skip-ids "3,7"` which `issues_for_verification()` filters out
     (additive CLI arg, default empty).
- Flag: `payload.get("reuseVerdicts") is not False` → default ON, opt out per
  run. Meta gains `"reused_verdicts": {"confirmed": n, "rejected": n}`.
- Verification counts in `apply_verification` remain the source of truth for
  *fresh* verdicts; `summarize_report` adds
  `stats["verification"]["carried"] = n` (additive).

**Execution steps.**
1. `previous_run_context()` + unit tests (fixture: two runs on disk, second
   inherits confirmed + rejected verdicts, third run with
   `reuseVerdicts: False` inherits nothing).
2. `--skip-ids` in `generator.py` + test in `tests/test_generator.py`
   (existing tests untouched).
3. `run_review()` integration inside a `try/except` auditing
   `verdict_reuse_failed` (R3).
4. UI: findings with `carried_from` show the existing `✓ verified` chip plus
   a muted `carried` chip (`renderFindings`, additive); run detail header
   shows "N verdicts reused from <run>".
5. Docs section in `CODE_DOCTOR.md`.

**Compatibility.** Old runs lack `carried_from` → UI ignores. Fingerprint
function untouched (R-critical). If the previous report is unreadable, the
try/except falls back to full verification — identical to today.

**Acceptance.** Live: review the sample repo twice; second run's
`gito.log` shows verifier called with fewer findings (or skipped entirely),
stats show `carried > 0`, dismissed findings stay dismissed (already works via
suppressions — regression-check it).

---

### Item 3 — Auto-fix apply + write-tests-into-repo — v5.1

**Goal.** One click applies a finding's `proposal` to the working tree, or
writes generated test files into the repo — with preview and revert.

**Design.**
- New module `code_doctor_app/patcher.py`:
  - `plan_fix(repo_path, issue) -> {"file", "start_line", "end_line",
    "before": [lines], "after": [lines], "applicable": bool, "reason": str}`
    — reads the target file, verifies the `affected_lines` block's
    `affected_code` still matches the file content at those lines
    (strip the `NN: ` prefixes gito uses). If the file drifted →
    `applicable: False, reason: "file changed since review"`. Never guess.
  - `apply_fix(repo_path, plan) -> {"backup": rel_path}` — re-validates the
    plan (TOCTOU guard), writes via temp-file + `os.replace`, and first
    copies the original to
    `.code-doctor/runs/<run-id>/backups/<sha>/<rel_path>`.
  - `revert_fix(repo_path, run_id, backup_rel) -> None` — restores backup.
  - Path safety: resolve target under `repo_path` exactly like
    `_read_file_sample()` does (resolve + parents check). Refuse symlinked
    targets (`Path.is_symlink()` on every component via `resolve(strict)`
    comparison).
  - `write_generated_tests(repo_path, run_id) -> [written]` — reads
    `generated-tests.json`, reuses `generator.safe_artifact_path` logic but
    anchored at `repo_path`; **refuses to overwrite existing files** unless
    payload sets `overwrite: true`; backs up when overwriting.
- Endpoints (in `do_POST`, same auth/except structure as existing routes):
  - `POST /api/reviews/<id>/fixes/plan`   `{issueId}` → plan (no writes)
  - `POST /api/reviews/<id>/fixes/apply`  `{issueId}` → applies, audits
    `fix_applied`, returns plan + backup id
  - `POST /api/reviews/<id>/fixes/revert` `{issueId}` → audits `fix_reverted`
  - `POST /api/reviews/<id>/tests/write`  `{overwrite?: bool}` → audits
    `tests_written`
- Applied-fix ledger: `.code-doctor/runs/<run-id>/fixes.json`
  (`{issueId: {applied_at, backup, reverted_at?}}`) so the UI can show state
  after reloads. Additive artifact — nothing else reads it.
- UI: in the expanded finding body, next to the "Proposed fix" block:
  `Preview fix` → renders before/after; `Apply` enabled only after preview
  (same two-step pattern as publish); `Revert` when ledger says applied.
  In Reports, a `Write into repo` button beside "Copy" on generated tests.

**Execution steps.**
1. `patcher.py` + `tests/test_patcher.py`: exact-match apply, drifted-file
   refusal, symlink/path-escape refusal, revert round-trip, overwrite guard,
   proposal spanning fewer/more lines than the original block.
2. Endpoints + server tests (plan on sample-run data works; apply on a git
   scratch repo mutates the file; `git diff` in the test asserts the change).
3. UI wiring + styles (reuse `.code-block`, `.btn` classes).
4. Docs.

**Compatibility.** Purely additive: no existing endpoint, artifact, or
schema changes. The sample seeded run's proposals reference the sample repo —
verify `plan_fix` correctly returns `applicable: False` when line content
drifted rather than erroring (the seeded report's line numbers are synthetic;
this is the built-in test of the drift guard).

**Risk note.** This is the first feature where Code Doctor **writes to user
repos**. The guards are: exact-content match requirement, backup-before-write,
explicit per-fix user click (no bulk apply in this release), audit events, and
never touching files outside the resolved repo root.

---

### Item 4 — Webhook receiver / CI mode — v5.0

**Goal.** PRs get reviewed automatically on push; the gate posts back as a
commit status / PR review. Code Doctor becomes a pipeline reviewer.

**Design.**
- Two entry paths, one engine:
  - **CLI batch mode** — `python -m code_doctor_app.ci --repo <path> --what
    <sha-or-branch> --against origin/main [--publish pr=N] [--fail-on
    block|review]`. New module `code_doctor_app/ci.py`; it calls the same
    functions the server uses (`start_review` internals refactored — see
    step 1) synchronously, prints the summary markdown to stdout, and exits
    1 when the gate meets `--fail-on`. This alone enables *any* CI system
    today (GitHub Actions step, GitLab job) with zero webhook plumbing.
  - **Webhook endpoint** — `POST /api/hooks/github` and
    `POST /api/hooks/gitlab` on the existing server for teams running Code
    Doctor as a standing service.
- Step 1 (refactor, no behavior change): extract the body of `start_review`
  into `create_review_run(payload) -> (run_id, repo_path, command, options)`
  and `execute_review(run_id, ...)`. `start_review` becomes
  `create + spawn thread` (exactly today's behavior); `ci.py` calls
  `create + execute` inline. All existing tests must pass untouched.
- Webhook security (mandatory, not optional):
  - Env `CODE_DOCTOR_WEBHOOK_SECRET`. GitHub: verify `X-Hub-Signature-256`
    HMAC over the raw body (`hmac.compare_digest`). GitLab: compare
    `X-Gitlab-Token`. Missing secret env → endpoint returns 503 "webhook not
    configured". Webhook routes are **exempt from bearer auth** (GitHub can't
    send our token) — gate them on the HMAC instead; add them to the
    `authorized()` bypass list alongside `/api/health`.
- Event handling: only `pull_request.synchronize|opened` /
  `merge_request` `update|open`. Map webhook repo → registered repo by
  matching `origin` remote URL against `store.list_repos()` metadata; unknown
  repo → 202 + `webhook_ignored` audit (never an error, GitHub retries
  otherwise).
- Flow per event: fetch (`git fetch origin` in the registered clone), then
  enqueue a review with `what=<head_sha> against=<base_sha>`, then on
  completion auto-publish using Item 5/current publisher with
  `dryRun: False`, `pr` from the payload — but **only if**
  `policy.ci.autoPublish` is true (new policy key, default **false**; without
  it the review just runs and is visible in the dashboard).
- Commit status: after publish, POST GitHub
  `/repos/{slug}/statuses/{sha}` with state from gate
  (block→failure, review→pending? No: review→success with description
  "needs human review", block→failure, pass→success) — context
  `code-doctor/gate`. GitLab: commit status API equivalent. Lives in
  `publisher.py` as `post_commit_status()` (env tokens already there).
- Because webhook work must not block the HTTP response: reuse the existing
  thread-spawn pattern now; Item 7's queue replaces it later (design the
  handler as `enqueue_ci_review(payload)` so the queue swap is one function).

**Execution steps.**
1. Refactor `start_review` (behavior-neutral) + run full suite.
2. `ci.py` CLI + `tests/test_ci.py` (scratch repo, `--fail-on block` exit
   code, stdout contains summary markdown; monkeypatch the LLM path by
   pointing at a run with `staticAnalysis` only? No — run with
   `verifyFindings: False` and a fake `gito` via PATH stub, same technique as
   existing generation-command tests).
3. Webhook routes + HMAC verification + `tests/test_webhooks.py`
   (signature pass/fail, unknown repo 202, malformed JSON 400).
4. `post_commit_status()` + tests (dry-run style: assert request payload
   built correctly with `urlopen` monkeypatched).
5. Policy key `ci: {autoPublish: false, failOn: "block"}` merged into
   `DEFAULT_POLICIES` (merge_dicts keeps stored policies valid).
6. Governance UI: render the two new policy fields (additive form fields).
7. Docs: GitHub Actions snippet + webhook setup walkthrough.

**Compatibility.** `start_review` refactor is the only touch to existing
code paths — protected by the full existing server-test suite. New routes are
additive. `DEFAULT_POLICIES` merge is already deep-merge tolerant.

---

### Item 5 — Publish upgrades — v5.3

**Goal.** GitLab inline discussions, update-in-place comments, Bitbucket.

**Design.**
- **Update-in-place:** `publish.json` already stores the result. Extend
  `_publish_github` to first check `publish.json` for a prior
  `comment_id`/`review_id` for the same target; if present, `PATCH
  /repos/{slug}/issues/comments/{id}` (summary path) instead of creating a
  new one. Marker line `<!-- code-doctor:run -->` appended to the body lets
  us also find orphaned comments (`GET .../comments` scan) when
  `publish.json` is missing. Same for GitLab notes (`PUT .../notes/:id`).
- **GitLab inline discussions:** `POST .../merge_requests/:iid/discussions`
  with `position` (requires `base_sha/head_sha/start_sha` from
  `GET .../merge_requests/:iid/diff_refs` — one extra GET). Fall back to the
  plain note on 400 (position outside diff), mirroring the GitHub 422
  fallback that exists today.
- **Bitbucket Cloud:** token env `BITBUCKET_TOKEN` (+ `BITBUCKET_USER` for
  app-password auth), `POST /2.0/repositories/{slug}/pullrequests/{id}/comments`
  with `inline: {path, to}` per comment. Add `"bitbucket"` to
  `publish_config()`, `parse_remote()` platform detection
  (`bitbucket.org`), and the platform whitelist in `publish_review`.

**Execution steps.** Extend `publisher.py` function-by-function with tests
first (`tests/test_publisher.py` grows; every network call already goes
through `_request_json`, so monkeypatching one function covers all
platforms). UI: platform `<select>` gains Bitbucket option gated on config.
`publish.json` gains `posted.comment_id` (additive).

**Compatibility.** `publish_review()` signature and dry-run schema unchanged;
new behavior only activates when a prior `publish.json` exists or the new
platform is selected. Existing publisher tests must pass unmodified.

---

### Item 6 — SSE live streaming — v5.1

**Goal.** Findings and log lines stream into the UI during a run; no more
5-second polling for the selected run.

**Design.**
- Keep it dependency-free: **SSE over the existing ThreadingHTTPServer**.
  `GET /api/reviews/<id>/events` → `Content-Type: text/event-stream`,
  loop: read `gito.log` incrementally (remember offset), stat `meta.json`
  mtime, emit `event: log` / `event: meta` frames, `time.sleep(0.5)`,
  terminate when status becomes final (send `event: done`) or client
  disconnects (`BrokenPipeError` → return).
- Server capacity guard: SSE ties up one thread per viewer.
  ThreadingHTTPServer spawns unbounded threads, so cap concurrent SSE
  connections with a module-level `threading.Semaphore(8)`; when exhausted,
  respond 429 and the client silently stays on polling. This is why polling
  is **kept as the fallback, not removed**.
- Auth: EventSource cannot send headers → accept
  `?token=<bearer>` query param **only for this endpoint** (compare to env
  token; document that the token appears in server logs — acceptable local
  tradeoff, or use `fetch` + `ReadableStream` instead of EventSource to keep
  header auth. **Decision: use fetch-streaming, keep header auth.** No query
  param, no new auth surface.)
- Frontend: `streamRun(runId)` uses `fetch(..., {headers}).body.getReader()`,
  parses SSE frames, appends to `#logOutput`, and triggers `loadSelected()`
  on `meta` frames. Started in `selectRun` when status is running; aborted
  via `AbortController` on run switch. Poll loop unchanged (it already
  no-ops gracefully; it becomes the fallback).

**Execution steps.**
1. Handler method `stream_run_events()` + route in `do_GET`
   (`parts[3] == "events"` in `handle_review_get`).
2. Guard: `send_json` unusable for streams — write frames directly; ensure
   `end_headers()` CSP additions still apply (they do — it's the same
   override).
3. `tests/test_streaming.py` using a real socket: start server on port 0 in a
   thread (there's no existing HTTP-level test harness — add
   `tests/_server_harness.py` with `start_test_server()` context manager;
   this harness gets reused by webhook tests in Item 4).
4. Frontend reader + fallback verification (kill server mid-stream → UI
   returns to polling without console errors).

**Compatibility.** Polling untouched. New endpoint additive. The only shared
code touched is `handle_review_get` (one new branch).

---

### Item 7 — Job queue with worker pool — v5.2

**Goal.** Bounded concurrency: N reviews run, the rest wait in a durable
queue that survives restarts. No more thread-per-request stampede on Ollama.

**Design.**
- `store.py` new table:
  ```sql
  CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,              -- review | tests | pr
    payload TEXT NOT NULL,           -- original request JSON
    state TEXT NOT NULL,             -- queued | running | done | failed | cancelled
    created_at TEXT, started_at TEXT, finished_at TEXT, worker TEXT
  );
  ```
  API: `enqueue_job`, `claim_next_job(worker_id)` (single UPDATE ... WHERE
  state='queued' ORDER BY id LIMIT 1 RETURNING — SQLite ≥3.35 has RETURNING;
  fallback: SELECT+UPDATE inside the store lock), `finish_job`,
  `requeue_stale_jobs()`.
- New module `code_doctor_app/workers.py`: `start_workers(n)` spawns N
  daemon threads; each loops `claim_next_job` → dispatch to
  `execute_review` / `run_generation` (the Item-4 refactor already exposes
  these) → `finish_job`. Poll interval 1s (event-driven wakeup via
  `threading.Event` set by `enqueue_job`).
- `start_review` / `start_generation` change: instead of
  `threading.Thread(...).start()`, they write meta (status `queued`, as
  today) and `enqueue_job`. **Config switch:** env
  `CODE_DOCTOR_WORKERS` (default `2`); value `0` = legacy direct-thread mode
  (escape hatch for one release).
- Restart recovery: on `serve()` boot, `requeue_stale_jobs()` flips `running`
  jobs whose worker is gone back to `queued`, and marks their run meta
  `status="queued"` with an audit `run_requeued`. This finally fixes the
  "unknown" status runs (currently `list_reviews` marks orphaned running
  runs "unknown" — keep that logic; it now only triggers for legacy runs).
- Cancel: `cancel_review` today terminates the PROCESS. Extend: if the run's
  job is still `queued`, set job `cancelled` + meta `status="cancelled"`
  (new terminal status — UI `state-pill` already prints arbitrary status
  strings, and no logic branches on an exhaustive status list; verified in
  `renderRuns`/`renderSelectedRun`).

**Execution steps.**
1. Store table + API + `tests/test_store.py` additions (claim contention
   test: 8 threads claim 8 jobs, no double-claims).
2. `workers.py` + tests with a stub executor (job function recorded).
3. Server switch-over behind `CODE_DOCTOR_WORKERS`, default `2`; existing
   server tests run in legacy mode? **No** — set the fixture env to `0` so
   unit tests that call `start_review` remain synchronous-ish
   (thread-spawned) exactly as today; add separate queue-mode integration
   tests.
4. UI: overview "running" metric already counts queued+running; add queue
   depth to `/api/overview` metrics (`m.queued`) and show it in the cockpit.
5. Docs (env var, sizing guidance: workers ≈ how many parallel models
   Ollama can serve, usually 1–2).

**Compatibility.** Meta/status lifecycle unchanged for the happy path
(queued→running→completed/failed). PROCESSES-based cancel keeps working
because workers register subprocesses the same way. Legacy mode flag
guarantees a one-command rollback.

---

### Item 8 — Multi-user identity — v5.2

**Goal.** Per-user tokens; audit events, dismissals, and publishes record who
did it.

**Design.**
- Token registry in store:
  ```sql
  CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,   -- sha256(token)
    role TEXT NOT NULL DEFAULT 'reviewer',  -- admin | reviewer | viewer
    created_at TEXT, disabled INTEGER DEFAULT 0
  );
  ```
  Plain tokens are never stored; `authorized()` hashes the presented bearer
  and looks it up.
- **Back-compat is the crux:** `CODE_DOCTOR_TOKEN` keeps working exactly as
  today and maps to implicit user `{id: "workspace", role: "admin"}`. No
  users table rows + no env token = open mode (unchanged). The new mode only
  activates when an admin creates users.
- CLI-first admin (avoids building auth-for-auth UI):
  `python -m code_doctor_app.usertool add --name soumya --role admin` prints
  the token once. Module `code_doctor_app/usertool.py`.
- `authorized()` becomes `authenticate() -> User | None`; handler stores
  `self.user`. Every `audit_event(...)` call site gains `actor=self.user.id`
  — mechanical but touches many lines; do it by making `audit_event` read a
  thread-local `current_actor` set at request entry instead of editing 40
  call sites (**thread-local set in `do_GET/do_POST/do_DELETE` prologue,
  cleared in finally**). Background threads (reviews) inherit the actor at
  enqueue time via the job payload (`payload["_actor"]`).
- Role enforcement (minimal, additive): `viewer` → GET only;
  `reviewer` → everything except `/api/policies` POST and user management;
  `admin` → all. One decorator-style check in the three do_* methods.
- Dismissals: suppression records already store free-form fields — add
  `"actor"`. Global-vs-personal dismissals stay **global** this release
  (matches current semantics); per-team scoping is listed as v6 candidate.

**Execution steps.**
1. Store table + user CRUD + tests.
2. `usertool.py` CLI + tests.
3. `authenticate()` + thread-local actor + role gates + tests
   (401 wrong token, 403 viewer POST, env-token still admin, open mode
   unchanged — the last two are the compatibility proofs).
4. Audit enrichment + UI: audit rows show actor chip; token input UI
   unchanged (a user token is just a bearer token).
5. Docs.

**Compatibility.** Open mode and single-env-token mode byte-identical to
today (existing tests must pass with zero edits — they run with no users
table rows). `/api/health` stays unauthenticated.

---

### Item 9 — Ollama supervision / watchdog — v5.1

**Goal.** "Model runtime down" is visible *before* a run fails; optional
auto-restart guidance.

**Design.**
- Server-side watchdog thread (started in `serve()`): every 30s call the
  existing `ollama_health()` against `DEFAULT_OLLAMA_BASE`; keep last N=20
  results in memory (`collections.deque`). State transitions
  (up→down, down→up) emit audit events `ollama_down` / `ollama_recovered`.
- Pre-run guard in `run_review`/`run_generation`: if the latest watchdog
  state is down, still attempt (Ollama may be back), but write a log line
  and meta `"ollama_warning": true` so failures are explainable.
- `/api/health` gains `"ollamaWatch": {"state", "since", "checks": [...last
  5...]}` (additive).
- UI: the existing sidebar Ollama dot already reflects health per-poll; add
  a persistent top banner (reuse `#authBanner` pattern → new
  `#ollamaBanner`) when state is down: *"Ollama is not responding — reviews
  will fail. Start it with `ollama serve` or `brew services start ollama`."*
  Never auto-run anything on the user's machine.
- Explicitly **not** doing: auto-restarting Ollama from the server
  (surprising side effects, permissions); document `brew services start
  ollama` in `CODE_DOCTOR.md` "Operations" section instead.

**Execution steps.** Watchdog class + tests (inject fake `ollama_health`,
assert transitions/audits); health payload; banner; docs. Half a day of work,
high quality-of-life value.

**Compatibility.** Pure addition; the watchdog thread is daemon and guarded
so failure kills nothing.

---

### Item 10 — Retention policy + trends — v5.2

**Goal.** Old runs/audit don't grow unbounded; the cockpit shows trends.

**Design.**
- Policy keys (additive to `DEFAULT_POLICIES`):
  `retention: {maxRunAgeDays: 90, maxRuns: 500, auditMaxAgeDays: 365}`.
- New module `code_doctor_app/retention.py`: `prune() -> report` deletes run
  directories beyond limits (oldest first, **never** deletes runs referenced
  by an unresolved job or with `publish.json` unless age-expired), and
  `DELETE FROM audit WHERE ts < cutoff` (JSONL mirror rotated to
  `audit.jsonl.1`). Runs from `serve()` daily via the watchdog thread's tick
  (piggyback, no new thread) and manually via `POST /api/admin/prune`
  (admin role once Item 8 lands; token-auth before that).
- Deletion prerequisite: lifecycle baselining reads *the most recent
  previous completed run* — pruning old runs is safe because
  `previous_fingerprints` degrades to `baselined: false`, which is an
  existing, tested state.
- Trends: store gains a tiny rollup table filled at review completion
  (`run_metrics(run_id, repo_path, created_at, risk, gate, total, verified,
  rejected, suppressed)` — written in `run_review` next to the audit event).
  Endpoint `GET /api/metrics/trends?repo=<path>&days=90` returns per-day
  aggregates. Cockpit gets a simple inline SVG sparkline for risk-over-time
  and verifier rejection rate (no chart library — hand-rolled polyline like
  everything else in this app).
- Backfill: on first boot with the new table, walk existing
  `runs/*/meta.json` once and insert rollups (guarded by `kv` flag
  `metrics_backfilled`, same pattern as `legacy_migrated`).

**Execution steps.** retention.py + tests (age/count pruning, protected-run
exclusions, JSONL rotation); rollup writes + backfill + tests; trends
endpoint + sparkline; policy UI fields; docs.

**Compatibility.** Nothing reads deleted runs except the runs list (they just
disappear — same as manual folder deletion today, which is already
supported). Rollup writes are fire-and-forget (`try/except` + audit).

---

### Item 11 — Dependency / supply-chain checks — v5.3

**Goal.** Changed `package.json` / lockfiles / `pyproject.toml` /
`requirements.txt` are checked against the OSV vulnerability database.

**Design.**
- New module `code_doctor_app/deps_analysis.py`, same contract as
  `static_analysis`: `analyze_repo_changes(repo_path, **scope) ->
  {file: [findings]}` with ID base **30000** and `source: "deps"`.
- Parsing (stdlib only): `requirements.txt` (regex per line),
  `pyproject.toml` (`tomllib` — py3.11+, already required),
  `package.json` (json), `package-lock.json` v2/v3 (json),
  `poetry.lock`/`uv.lock` (tomllib). Only **changed** manifest files in the
  diff scope are parsed (reuse `collect_changed_files` with a manifest
  filter, ignoring the payload's source-file filters).
- OSV querybatch API: one POST to `https://api.osv.dev/v1/querybatch` with
  up to 1000 `{package: {name, ecosystem}, version}` entries. **This is the
  app's first outbound non-localhost call from a review.** Therefore:
  - Default **OFF**: `payload.get("depsAnalysis") is True` to enable per run,
    or policy `deps: {enabled: false}` to default it on for a workspace.
  - Offline cache: responses cached in store table
    `osv_cache(pkg, ecosystem, version, response, fetched_at)` with 24h TTL;
    network failure → audit `deps_analysis_offline`, zero findings, never an
    error (R3).
  - Clear privacy note in docs: package names+versions leave the machine
    (not code).
- Finding shape: severity from OSV severity (CVSS ≥9 → 1, ≥7 → 2, else 3),
  tags `["security", "supply-chain"]` (security multiplier in
  `compute_risk_score` applies automatically), details include OSV id, fixed
  version, advisory link; `affected_lines` points at the manifest line that
  declares the package (found by simple text search, falls back to line 1).
- Wire into `run_review` beside the other deterministic passes; findings are
  exempt from the LLM verifier like static (`issues_for_verification`
  currently skips only `source == "static"` → change the skip condition to
  `source in {"static", "deps"}`; crossfile stays verifiable — **this edit
  has an existing test to update deliberately**:
  `test_issues_for_verification_skips_static_findings` gains a deps case).

**Execution steps.** Parsers + tests (fixture manifests); OSV client with
injected `urlopen` + cache tests; severity mapping tests; `run_review` wiring
behind the flag; policy + Governance toggle; UI source chip "deps"; docs with
privacy note.

**Compatibility.** Default-off flag means zero change for existing flows
until enabled. New ID base avoids collisions. `summarize_report` gains
`"deps_issues"` count (additive).

---

### Item 12 — Configurable rule packs + per-repo policies — v5.3

**Goal.** Teams add custom static-analysis rules and override severities and
gates per repository, without forking the built-in rule pack.

**Design.**
- Rule pack file, discovered in the **reviewed repo**:
  `.code-doctor/rules.toml` (and workspace-level fallback
  `<data-dir>/rules.toml`):
  ```toml
  [[rule]]
  id = "no-internal-url"            # required, unique, becomes a tag
  languages = ["*.py", "*.ts"]      # filter patterns (default: all)
  pattern = "internal\\.acme\\.com" # Python regex, applied to added lines
  title = "Internal URL committed"
  severity = 2                      # 1–4
  tags = ["policy"]
  ignore_case = true                # optional

  [overrides]
  "hardcoded-secret" = { severity = 1 }   # re-tune built-in rule ids
  disabled = ["debug-leftover"]           # switch off built-ins
  ```
- Loader in `static_analysis.py`: `load_custom_rules(repo_path) ->
  (rules, overrides)`:
  - Regexes compiled inside try/except; invalid rule → skipped +
    collected into `report["processing_warnings"]` (existing field, already
    rendered) — a bad rules file must never fail a review.
  - **ReDoS guard:** compile-time reject patterns longer than 500 chars;
    wrap matching with the same per-line application as built-ins (lines are
    short; catastrophic backtracking risk is bounded but document it).
  - Custom findings: `source: "static"`, tag `custom:<id>`, IDs continue in
    the 10000 static range (merge path unchanged).
  - Overrides applied after rule evaluation: severity swap + disabled-rule
    filter, keyed on the existing `Rule` dataclass's id field.
- Per-repo policy gates: registered repos (`store` records) gain optional
  `"policies"` dict with the same shape as the global `risk` policy;
  `summarize_report` resolution order: repo-specific → global → defaults.
  Implemented by threading `repo_path` (already available from meta) through
  a new `effective_risk_policy(repo_path)` helper. Repositories UI gets an
  "Override gates" expander per repo card.

**Execution steps.**
1. TOML loader + validation + tests (valid pack, invalid regex skipped with
   warning, disabled built-in, severity override).
2. Hook into `analyze_diff` (rules list = built-ins − disabled + custom,
   overrides applied) — the function signature keeps its default arguments
   so every existing call site and test is untouched; the new behavior only
   engages when a rules file exists.
3. `effective_risk_policy()` + tests (repo override wins, absent → global,
   malformed override ignored).
4. Repo card UI + docs.

**Compatibility.** No rules file → byte-identical behavior (assert this with
a test that runs the full static suite against a repo with and without an
empty rules file). Fingerprints of custom findings are naturally distinct via
the `custom:<id>` tag.

---

### Item 13 — SARIF export + GitHub Code Scanning — v5.3

**Goal.** Export reviews as SARIF 2.1.0 — the industry-standard static-analysis
format — so findings flow into GitHub Code Scanning, VS Code SARIF viewers,
and every enterprise aggregation tool. This is table stakes for "best in
industry" evidence handling and costs little.

**Design.**
- Extend `export_review(run_id, "sarif")` in `server.py` (the format switch
  already raises `ValueError` on unknown formats, so adding a branch is
  additive; the UI export grid gains a fourth card).
- Mapping (one pure function `sarif_from_detail(detail) -> dict` in a new
  `code_doctor_app/sarif.py`, fully unit-testable without a server):
  - `runs[0].tool.driver` = "Code Doctor" + `APP_VERSION`; one
    `rules[]` entry per distinct finding source/tag combination
    (`llm-review`, `static:<rule>`, `crossfile`, `deps`).
  - Each issue → `result`: `level` from severity (1–2 → `error`,
    3 → `warning`, 4+ → `note`), `message.text` = title + details,
    `locations[0].physicalLocation` from `file` + `affected_lines[0]`,
    `partialFingerprints.codeDoctor/v1` = `issue_fingerprint(issue)` so
    GitHub's alert dedup aligns with ours, `properties` carries
    `verified`, `suppressed`, `tags`.
  - Suppressed findings map to SARIF's native
    `suppressions: [{kind: "external"}]` rather than being dropped —
    consumers decide.
- Optional upload: `publisher.py` gains
  `upload_sarif(slug, commit_sha, ref, sarif_text)` → gzip + base64 → `POST
  /repos/{slug}/code-scanning/sarifs` (same token/env pattern). Exposed as
  `POST /api/reviews/<id>/publish` with `{"sarif": true}` (additive key) and
  as `--sarif-upload` in Item 4's CI mode; `--sarif out.sarif` writes the
  file for any CI system.

**Execution steps.** `sarif.py` + `tests/test_sarif.py` (schema-shape
assertions: required keys, level mapping table, fingerprint passthrough,
suppression mapping); export branch + UI card; publisher upload with
monkeypatched `_request_json`; CI flags; docs.

**Compatibility.** Pure addition. Existing export formats untouched
(their tests prove it).

---

### Item 14 — Per-pass model routing — v5.1

**Goal.** Different passes have different needs: the reviewer benefits from a
stronger model, the verifier and PR-drafter run fine (and faster) on a small
non-reasoning model. Today one `MODEL` env drives all four passes — this
session's qwen3.5 `<think>`-token incident is exactly the failure mode this
removes.

**Design.**
- Payload keys `verifyModel` and `generateModel`, both defaulting to `model`
  (absent → byte-identical behavior, R2).
- `subprocess_env(payload)` gains an optional `model_override: str = ""`
  parameter appended last; `run_verification` calls
  `subprocess_env(payload, model_override=payload.get("verifyModel") or "")`,
  `run_generation` likewise with `generateModel`. No other call site changes.
- Meta additions: `verify_model`, `generate_model` (additive) so runs are
  reproducible evidence.
- Workspace defaults via policies: `models: {verify: "", generate: ""}` in
  `DEFAULT_POLICIES` (deep-merge safe); payload wins over policy wins over
  `model`.
- UI: "Advanced" collapsible in Run Setup with two datalist inputs fed from
  the same `#modelList`; empty = inherit. Governance shows the workspace
  defaults.
- Guardrail: reasoning-model detection — if the chosen verify/generate model
  name matches a known-reasoning pattern (config list, default
  `["qwen3*", "*r1*", "*think*"]`), show a UI hint ("reasoning models are
  slower for structured JSON output") but never block; `parse_llm_json`
  already strips `<think>` blocks.

**Execution steps.** `subprocess_env` param + tests (override present/absent,
policy fallback ordering); meta fields; UI accordion; docs. Small item —
half a day.

**Compatibility.** All existing tests pass unmodified; the parameter defaults
to today's behavior.

---

### Item 15 — Server config file — v5.2

**Goal.** One `config.toml` instead of a growing pile of env vars
(`CODE_DOCTOR_TOKEN`, `GITHUB_TOKEN`, `GITLAB_BASE`, `CODE_DOCTOR_WORKERS`,
`OLLAMA_MODEL`, webhook secret…), while keeping env vars working for
container/CI use.

**Design.**
- New module `code_doctor_app/config.py`: `load_config(path=None) -> Config`
  (a frozen dataclass). Search order: `--config` CLI flag →
  `<data-dir>/config.toml` → defaults. Read once at boot with `tomllib`.
- **Precedence: CLI flag > environment variable > config file > default.**
  Implemented as one resolution function per key,
  `resolve("workers", cli=args.workers, env="CODE_DOCTOR_WORKERS", file=cfg.workers, default=2)`,
  so the rule is uniform and testable.
- Keys: `host`, `port`, `workers`, `ollama_base`, `default_model`,
  `verify_model`, `generate_model`, `data_dir`, plus a `[secrets]` table for
  tokens. Secrets in the file are **allowed but discouraged**: boot prints a
  warning unless the file mode is `0600` (check `stat.st_mode`), and the docs
  push env vars for anything shared.
- The resolved config is injected instead of scattered `os.getenv` calls —
  but **incrementally**: each `os.getenv` site is replaced only when its
  feature area is already being touched by another item, never in a big-bang
  sweep. Until replaced, `config.py` sets resolved values *into*
  `os.environ` at boot for keys that came from the file, so every existing
  `os.getenv` reader works unchanged. That bridge is the whole compatibility
  story.
- `python -m code_doctor_app --print-config` dumps the resolved effective
  config (secrets masked) for support/debugging.

**Execution steps.** `config.py` + `tests/test_config.py` (precedence matrix,
missing file, malformed file → clear error naming the line, secrets-mode
warning, env bridge); `main()`/`serve()` wiring; `--print-config`; docs with
a full annotated example file.

**Compatibility.** No config file → identical behavior. Env vars always win
over the file, so existing deployments change nothing.

---

## 4. Cross-Cutting Execution Order (per release)

For **every** release, in order:

1. **Branch:** `release/v5.x` off `main`; one PR-sized commit per item.
2. **Store migrations first** (new tables land before code that uses them;
   bump `kv.schema_version`).
3. **Backend modules + unit tests** (new files), then **server wiring**
   (guarded, additive), then **frontend**, then **docs**.
4. **Full gate:** `pytest tests/ -q` → only the known `test_version`
   environmental failure allowed. `git stash` check: run the suite on a tree
   with only the backend half applied to confirm the frontend isn't
   load-bearing for tests.
5. **Live smoke script** (add `scripts/smoke.sh` in v5.0 so this stops being
   manual):
   seed sample → start review (gemma4:e4b) → wait completed → assert stats
   keys → dismiss+restore finding → export json/md/csv → publish dry-run →
   generate tests → `GET /api/overview`. Exits non-zero on any assertion.
6. **Legacy-data check:** boot the new server against a copy of a v4.3
   `.code-doctor/` directory (keep one as a fixture tarball under
   `tests/fixtures/data-v4.3/`); dashboard must render all old runs, and
   migration events must be idempotent on second boot.
7. **Rollback plan:** every feature is flag-gated (R2) or module-isolated
   (R3); `CODE_DOCTOR_WORKERS=0` reverts the queue; deleting a rules file
   reverts custom rules; unset env tokens revert publishing/webhooks.

## 4a. Security Hardening Backlog (starts v5.0, carries through all releases)

Quick wins first — each is a small, isolated change with an existing test to
extend:

- **QW-1 (applied alongside this plan): constant-time token comparison.**
  `authorized()` compared the bearer token with `==`, which leaks timing
  information. Now uses `hmac.compare_digest`. One line; behavior identical.
- **QW-2: warn on non-loopback bind without a token.** `serve()` prints a
  loud warning when `host` is not `127.0.0.1`/`localhost` and
  `CODE_DOCTOR_TOKEN` is unset. Never refuse (labs exist) — just warn.
- **QW-3: auth-failure rate limiting.** In-memory counter per client IP in
  the handler: after 10 consecutive 401s within 60s, respond 429 for that IP
  for 60s. Audit event `auth_throttled`. Stdlib only, resets on restart —
  good enough for a local/team tool; the queue release can persist it if
  ever needed.
- **QW-4: CSP tightening.** Add `form-action 'self'; object-src 'none'` to
  the existing CSP header in `end_headers()`. Verify the SPA in the browser
  smoke afterward (no forms post cross-origin today, so zero expected
  breakage).
- **QW-5: mask tokens in subprocess environments where possible.** Review
  runs inherit the full parent env (`os.environ.copy()` in
  `subprocess_env`), which hands `GITHUB_TOKEN`/`CODE_DOCTOR_TOKEN` to gito
  and the generator, neither of which needs them. Strip
  `CODE_DOCTOR_TOKEN`, `CODE_DOCTOR_GITHUB_TOKEN`, `GITHUB_TOKEN`,
  `GITLAB_TOKEN`, `CODE_DOCTOR_WEBHOOK_SECRET` from the child env. Test:
  assert the built env lacks the keys while `LLM_*` remain.

Structural items (scheduled with their feature releases):

- Webhook HMAC verification and 503-without-secret (Item 4).
- Token hashing at rest and roles (Item 8) — plain tokens never stored.
- TLS guidance: Code Doctor stays plain-HTTP on loopback by design; for any
  network exposure, document a reverse-proxy recipe (Caddy two-liner and
  nginx snippet) in an `docs/OPERATIONS.md` — terminating TLS in a stdlib
  HTTP server is not worth owning.
- Supply-chain honesty for **our own app**: pin runtime deps
  (`uv pip compile` lockfile committed), and run Item 11's OSV check against
  this repo in CI once Item 4's CI mode exists (dogfooding).
- Log hygiene review each release: no Authorization headers are logged today
  (`log_message` only formats method/path/status — keep it that way; add a
  test that greps the access-log format string for header interpolation).

## 4b. Performance Budgets and Benchmarks (enforced from v5.0)

Budgets (measured by `scripts/bench.py`, added in v5.0, run manually per
release — not in the unit-test gate to keep tests fast):

| Operation | Budget | Current risk |
| --- | --- | --- |
| Preflight on a 5k-file repo | < 1.5s | `collect_changed_files` is one git call — fine |
| Import graph build (Item 1), 4k files | < 2.5s cold | AST parse of every file is the new cost |
| `GET /api/overview` with 500 runs | < 150ms | `list_reviews()` reads every `meta.json` per call — **known hot spot** |
| `GET /api/reviews` with 500 runs | < 150ms | same |
| SQLite ops under 8 threads | no lock errors | WAL + store lock — covered by existing test |

Planned mitigations, in order of trigger:

- **Runs index (piggybacks Item 7's store work):** a `runs_index` table
  (`run_id, kind, status, created_at, repo_path, stats_json`) written by
  `update_meta()` (write-through; `meta.json` on disk stays the source of
  truth and the fallback — rebuildable by a `reindex()` walk, guarded by a
  `kv` flag). `list_reviews()` then reads one SQL query instead of N files.
  Do this when overview latency crosses budget or run count crosses ~300,
  not before — the file scan is simpler and correct.
- **Import-graph cache (Item 1 follow-up):** cache
  `{repo, HEAD sha, hash(ls-files)} -> graph` in a store table; invalidate
  on key change. Only build it if bench shows cold-graph cost breaking the
  budget on real repos; the cap (`MAX_GRAPH_FILES = 4000`) already bounds
  worst case.
- **SQLite maintenance:** `PRAGMA wal_checkpoint(TRUNCATE)` in the daily
  retention tick (Item 10) keeps the WAL file bounded; backups use
  `VACUUM INTO` (see runbook) which also compacts.

`scripts/bench.py`: generates a synthetic repo (parameterized file count),
seeds N fake runs, times each budget row, prints a pass/fail table, exits
non-zero on breach. Keep it dependency-free (`time.perf_counter`).

## 4c. Testing Strategy Additions

- **Schema contract tests (v5.0) — mechanical enforcement of rule R1.**
  Check in JSON Schema files under `tests/schemas/` for the five run
  artifacts (`meta`, `report`, `context-pack`, `generated-tests`,
  `pr-draft`) capturing today's shapes with
  `additionalProperties: true` (additive keys always pass) but every
  *existing* key marked required-if-present with its current type.
  `tests/test_artifact_contracts.py` validates the seeded sample run and a
  committed fixture run against them (stdlib validator — write a ~60-line
  subset validator, or vendor nothing and assert key/type pairs directly).
  Any accidental rename/retype of an existing key now fails CI instead of
  breaking old-run rendering silently.
- **HTTP test harness (v5.0, prerequisite for Items 4 and 6):**
  `tests/_server_harness.py` with a `run_test_server()` context manager —
  binds port 0, isolated `DATA_DIR`/`DB_PATH` via the same monkeypatching as
  `_isolated_store`, yields base URL, guarantees shutdown. Today all server
  tests call functions directly; webhook HMAC and SSE cannot be tested that
  way.
- **Legacy-data fixture (v5.0):** `tests/fixtures/data-v4.3.tar.gz` — a
  frozen copy of a real v4.3 `.code-doctor/` directory (sample run, one
  suppression, audit history, pre-SQLite JSON files). A test boots the store
  against an unpacked copy, asserts migration, asserts `get_review` renders
  the old run, and asserts a second boot migrates nothing (idempotency).
  Regenerate only deliberately, never automatically.
- **Browser E2E smoke (manual checklist now, scripted later):**
  documented click-path in `docs/SMOKE.md` — seed sample → open each of the
  7 views → expand a finding → dismiss/restore → export JSON → publish
  preview. Automating with Playwright is deliberately deferred (new heavy
  dep, R7); the checklist plus `scripts/smoke.sh` (API-level) covers the gap
  at this project size.
- **Generator/LLM tests stay network-free:** the established pattern
  (monkeypatch `mc.llm` / `urlopen`, fake `gito` on PATH) is the rule; any
  test needing a live model goes in `scripts/smoke.sh`, never `tests/`.

## 4d. Operations Runbook (ship as `docs/OPERATIONS.md` in v5.0)

Content list — written for the person running Code Doctor, not developing it:

- **Start/stop:** the run command with token, where logs go (server stderr;
  per-run `gito.log`), how to run it under `launchd`/`systemd` (unit file
  examples).
- **Port conflicts:** symptom (`Address already in use`), diagnosis
  (`lsof -ti tcp:8787`), the stale-server trap we hit twice this project
  (an old process serving old code — always check *which* binary owns the
  port before debugging "broken" features).
- **Ollama:** health check (`curl localhost:11434/api/tags`), keep-alive
  (`brew services start ollama` on macOS, `systemctl enable ollama` on
  Linux), the watchdog banner meaning (Item 9), model pull guidance, and the
  reasoning-model caveat for structured output.
- **Backup:** `sqlite3 .code-doctor/code-doctor.db "VACUUM INTO 'backup.db'"`
  plus `tar` of `runs/` — both while the server runs (WAL makes the vacuum
  copy consistent). Restore = stop server, swap files, start.
- **Reset:** what deleting `.code-doctor/` loses (everything) vs deleting
  only `runs/` (history but not registry/suppressions/audit).
- **Token rotation:** today (change env, restart, update browser); after
  Item 8 (usertool disable + add).
- **Upgrade:** `git pull` → `uv pip install -e .` → restart → migrations run
  automatically → check `--print-config` (Item 15) and the readiness panel.
  **Downgrade:** older code ignores newer SQLite tables (R4); run artifacts
  are forward-readable because of R1 — document the one real caveat: runs
  created by newer versions may show fewer details in older UIs, never
  errors.

## 4e. Portability Notes

- **Python floor: 3.11** (already implied by `tomllib` in Item 12/15 and the
  current dev venv on 3.13) — declare `requires-python = ">=3.11"` in
  `pyproject.toml` in v5.0 so failures are upfront, not mid-import.
- **Windows:** the codebase is close to portable (pathlib throughout,
  `signal.signal` already wrapped in try/except, `os.replace` atomic on
  NTFS). Known gaps to fix when a Windows user appears — not before:
  `proc.terminate()`/`kill()` semantics differ (no SIGTERM grace),
  `lsof`-based docs need `netstat` equivalents, and `.code-doctor/token`
  `chmod 600` is a no-op. Track as a labeled backlog item; don't gate
  releases on an untested platform.
- **Locales/encoding:** all file IO already passes `encoding="utf-8"`
  explicitly (plus `errors="ignore"` on repo reads) — keep that as a review
  checklist item; it's what makes non-UTF8 repos degrade instead of crash.

## 4f. Versioning, Changelog, and Release Mechanics

- `APP_VERSION` in `server.py` bumps per release (5.0.0, 5.1.0, …); patch
  releases for fixes between phases. The version already surfaces in the
  server banner; also return it in `/api/health` (additive key `version`)
  so the UI and bug reports can state it.
- `CHANGELOG.md` in Keep-a-Changelog format, one entry per item id
  ("Item 7 — job queue…"), written in the same commit as the feature.
- Git tag `v5.x.y` on the release commit; the release commit message lists
  shipped item numbers and any flag/env switches introduced.
- `kv.schema_version` (R4) maps releases to store schema:
  document the mapping table in the changelog entry whenever it bumps.
- Definition of a "release": all items merged, Section 4 gate green,
  smoke + bench green, `CODE_DOCTOR.md` + `CHANGELOG.md` updated, tag
  pushed.

## 5. Risk Register (top items)

| Risk | Where | Mitigation |
| --- | --- | --- |
| Writing to user repos corrupts work | Item 3 | exact-match precondition, backup-before-write, per-fix click, audit, revert |
| Webhook endpoint becomes an unauth attack surface | Item 4 | HMAC required, 503 without secret, no repo paths from payload (registry match only) |
| Queue deadlock / lost jobs on crash | Item 7 | stale-job requeue on boot, `CODE_DOCTOR_WORKERS=0` escape hatch |
| Fingerprint drift orphans suppressions | Items 1,2 | fingerprint function frozen; titles of existing finding types byte-stable |
| OSV call leaks metadata unexpectedly | Item 11 | default-off, docs privacy note, cache, offline-tolerant |
| tree-sitter dependency friction | Item 1 | optional import, heuristic fallback, mode surfaced in health |
| SSE thread exhaustion | Item 6 | semaphore cap + polling fallback retained |
| Overview latency degrades as runs accumulate | §4b | runs_index write-through table, triggered by budget breach, disk stays source of truth |
| Secrets leak via config file or child processes | Item 15, QW-5 | file-mode warning, env-precedence, token stripping from subprocess env |
| Artifact schema drift breaks old-run rendering | all | §4c schema contract tests fail CI on any existing-key change |

## 6. Definition of Done (applies to every item)

- [ ] New/changed behavior covered by unit tests; existing tests pass unmodified
      (except where a test's change is called out explicitly in the item).
- [ ] Old `.code-doctor/` data renders and migrates idempotently.
- [ ] Feature degrades to current behavior on failure (audit event, no 500s).
- [ ] `CODE_DOCTOR.md` section added; API changes listed there.
- [ ] Smoke script passes against the live server with Ollama up **and**
      behaves sanely with Ollama down.
- [ ] Committed with a message naming the item and its flag/env switches.
