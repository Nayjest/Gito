# Code Doctor Dashboard

Code Doctor is an enterprise-oriented private review cockpit built on top of
Gito. Gito remains the review engine for diff parsing, report generation, and
provider-agnostic LLM calls. Code Doctor adds an Ollama-first control plane,
repository onboarding, run history, risk gates, audit events, evidence exports,
and a mentoring review profile tuned for junior Python and Node.js work.

## Run Locally

```bash
uv venv --python 3.13
uv pip install --python .venv/bin/python -e . pytest
.venv/bin/python -m code_doctor_app --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787`.

Ollama must expose its OpenAI-compatible endpoint at
`http://localhost:11434/v1`. The dashboard discovers local models through
`http://localhost:11434/api/tags`.

## Optional Access Token

Set `CODE_DOCTOR_TOKEN` before starting the server to require bearer-token
access for review, run-history, and audit APIs:

```bash
CODE_DOCTOR_TOKEN="change-me" .venv/bin/python -m code_doctor_app
```

The UI stores the token in browser local storage for the current browser.

## Review Profile

The dashboard injects `code_doctor_app/review_profile.toml` through
`GITO_EXTRA_PROJECT_CONFIG`. This keeps reviewed repositories clean while
adding stricter guidance for:

- Python exception handling, resource cleanup, type assumptions, and tests.
- Node.js and TypeScript async flow, validation, environment handling, and type holes.
- Small, teachable fixes with high-confidence issues only.

Project-level `.gito/config.toml` files still load first; the Code Doctor
profile merges after them.

## Concurrency: Bounded Job Queue

Reviews and generations run through a fixed-size **worker pool**
(`jobqueue.py`) instead of each spawning its own thread. At most
`CODE_DOCTOR_REVIEW_WORKERS` (default 2) run at once; further requests wait in
FIFO order with meta status `queued`, so a burst of reviews can't thrash a
single local GPU or blow a cloud rate limit. Raise the worker count on beefier
hardware or when using a cloud provider. `/api/health` reports live
`queue: {workers, active, queued}`. A failing job never takes down its worker.

## Dependency / Supply-Chain Checks

Code Doctor scans changed manifests (`requirements.txt`, `package.json`)
offline — no CVE feed needed (`dependency_scan.py`):

- **Typosquatting** — a dependency name one edit (insert/delete/substitute/
  adjacent-swap) from a very popular package, e.g. `requets` → `requests`.
- **Unpinned / unbounded versions** — pip deps with no `==` pin, or npm specs
  that are wildcards / open ranges (`*`, `latest`, `>=…`). Conventional npm
  caret/tilde ranges are intentionally left alone to avoid noise.
- **URL / VCS installs** — deps fetched from a git or http URL, bypassing the
  index and its integrity checks.

Findings use IDs 40000+ and the `supply-chain` tag. Opt out with
`"dependencyScan": false`.

## SARIF Export

Any run exports to **SARIF 2.1.0** for GitHub Code Scanning (and other SARIF
tools): `GET /api/reviews/<id>/export?format=sarif`, or the **SARIF** card in
Evidence Exports. Severity maps to SARIF levels (1–2 → `error`, 3 →
`warning`, 4–5 → `note`); each finding becomes a `result` with a
`physicalLocation`, and distinct rules populate the tool driver. Upload the
file to the GitHub Security tab via `github/codeql-action/upload-sarif`.

## LLM Providers (Local and Cloud)

The review/verify/generate passes can run on a local model or a frontier cloud
model. Pick the provider in the Cockpit; keys are read from the **server
environment only**, never the browser or request:

| Provider | `LLM_API_TYPE` | Key env | Default model | Parallelism |
|---|---|---|---|---|
| Ollama (local) | openai | — | `llama3.1:8b` | 4 |
| Anthropic Claude | anthropic | `ANTHROPIC_API_KEY` | `claude-haiku-4-5` | 8 |
| OpenAI | openai | `OPENAI_API_KEY` | `gpt-4o-mini` | 8 |
| Google Gemini | google | `GEMINI_API_KEY` | `gemini-2.0-flash` | 8 |

Cloud providers raise the concurrent-request budget (`MAX_CONCURRENT_TASKS`),
so a whole-repo review that would time out on a single local GPU finishes in
a minute or two — and a frontier model actually catches the deep issues a
small local model misses. A run is rejected up front if its provider has no
key configured. `/api/health` lists providers and whether each is configured;
the chosen provider is recorded in run meta. Per-pass routing still applies —
`verifyModel`/`generateModel` and `models.verify`/`models.generate` — so a
cheap model can verify while a stronger one reviews.

## Taint / Dataflow Analysis

Beyond line-oriented rules, Code Doctor runs an **AST dataflow pass**
(`taint_analysis.py`) that tracks untrusted input from **sources** to
dangerous **sinks** within a function — catching bugs where the taint and the
sink sit on different lines, which regex rules and small LLMs both miss:

- **Sources:** `request.*` (Flask/Django-style), route-handler path
  parameters, `input()`.
- **Sinks:** `open`/`send_file`/`Path.read_text` (path traversal),
  `requests`/`urlopen`/`httpx` (SSRF), `subprocess`/`os.system` (command
  injection), `eval`/`exec`/`pickle.loads` (code execution),
  `cursor.execute` on a built string (SQL injection),
  `render_template_string` (SSTI), `redirect` (open redirect).
- **Propagation** follows assignments, f-strings, `+`/`%` concatenation,
  `.format()`, and wrapper calls (`Path(x)`, `x.strip()`, `os.path.join`);
  `int()`/`float()` and reassignment to a constant clear taint.

It is intentionally conservative — a finding fires only when a tainted value
provably reaches a sink, so false positives are rare. Analysis is
intraprocedural: flows that pass through a helper in another module aren't
followed. Findings carry the `taint`/`dataflow` tags and `/api/health` reports
the engine under `engines.taint`. Disable per run with `"taintAnalysis": false`.

## Reviewing Local (Non-Git) Projects

Code Doctor reviews any local folder, not just git repositories. Point the
**Repository Path** at a plain directory and it just works:

- If the path is a git work tree **with at least one commit**, it is reviewed
  in place as before (diffs, branches, lifecycle tracking — everything).
- If it is **not** under git — or is a freshly `git init`-ed repo with no
  commits yet (an unborn HEAD has nothing to diff against) — Code Doctor takes
  a **snapshot**: your folder is
  copied into `.code-doctor/snapshots/<id>/` (skipping `node_modules`,
  virtualenvs, build output, caches, etc.), `git init` + one baseline commit,
  and the review diffs the git empty tree against that commit so **every file
  is analyzed**. Your folder is never modified and never gets a `.git`.

The whole analysis stack (LLM review, static analysis, cross-file) runs
unchanged against the snapshot. Runs are marked `is_snapshot` in meta and
carry the original `source_path`; the dashboard shows a `local snapshot`
badge and a preflight note. Snapshots refresh on each review, so re-running
picks up your latest edits. A size guardrail (20k files / 300 MB) refuses
runaway copies — point at a git repo or a smaller folder in that case.

Because a snapshot has a single baseline commit, its scope is always the
whole tree; branch/ref options don't apply. Applied fixes (Item 3) write into
the snapshot copy, not your original folder.

## Hybrid Analysis Engine

Every run pairs the LLM review with a deterministic static-analysis pass
(`code_doctor_app/static_analysis.py`) over the added lines of the same diff:

- Secrets: AWS/GitHub/Slack/Stripe key formats, PEM private keys, and
  high-entropy credential assignments (placeholders are ignored, matched
  values are masked in the evidence).
- Danger patterns: `eval`/`exec`, `pickle.load`, unsafe `yaml.load`,
  `shell=True`, XSS sinks, merge-conflict markers, debugger leftovers.
- Findings merge into the same report with `source: "static"`; lines the LLM
  already flagged are deduplicated. If the LLM run fails or times out, the
  static findings still produce a report, so a run is never a total loss.
- Disable per run with `"staticAnalysis": false` in the review payload.

## Dynamic Test Case Generation

Each review detail includes a generated `test_cases` plan. The plan is built
from the review scope, changed files, findings, and lightweight AI-application
signals in the affected code:

- Finding-driven cases cover security, input validation, async failure paths,
  secret hygiene, and generic regressions.
- AI-application cases cover prompt injection, RAG grounding, agent/tool
  guardrails, structured-output contracts, model failure paths, and eval
  coverage.
- The plan is included in JSON exports and is available directly at
  `/api/reviews/<run-id>/tests`.

## Test-Case Planner, Unit-Test Generator, and PR Drafts

Every review computes a deterministic test-case plan (`test_cases` in the run
detail): regression cases derived from each finding, coverage-gap cases when
application code changes without test changes, and AI-app cases (prompt
injection, RAG grounding, tool guardrails, schema contracts, model resilience,
eval coverage) when the change surface touches LLM-related code.

On demand, `POST /api/generate` runs an LLM pass over the same diff scope:

- `{"kind": "tests"}` writes runnable pytest/vitest files to
  `.code-doctor/runs/<run-id>/generated-tests/` plus `generated-tests.json`
  and a Markdown preview. Model-proposed paths are sandboxed to the run
  directory.
- `{"kind": "pr"}` writes `pr-draft.json` and a ready-to-paste Markdown PR
  (title, Summary/Changes/Risk/Test Plan, labels, reviewer checklist).

Both appear in the Reports view with copy buttons, run under the same
private-model env contract as reviews, respect `timeoutSeconds` (default
1200), and can be cancelled through the existing cancel endpoint.

## Cross-File Impact Analysis

Each review also runs a repo-wide cross-file pass (`code_doctor_app/context_engine.py`):

- An import graph over all tracked Python and JS/TS files maps every changed
  file to its dependents (files that import it).
- Symbols the diff removed or re-signed are checked against call sites in
  those dependents. Contract breaks become deterministic findings with
  `source: "crossfile"` (removed-symbol-still-referenced, signature-changed
  callers) and usage evidence pointing into the dependent files.
- The full context pack (imports, dependents, symbol changes, usage sites) is
  stored as `context-pack.json` per run, shown in the Review view's
  "Cross-File Impact" panel, and fed to the LLM verification pass so verdicts
  can see beyond the diff.
- Symbol extraction is AST-level for Python (`semantic_py.py`) and
  brace-depth tokenizer-level for JS/TS (`semantic_js.py`); the original
  regex pass remains as an automatic fallback for unparseable files. For
  Python signature changes, every call site in dependents is checked with
  real argument-binding simulation: calls that provably break carry a
  `break_reason` (confidence raised to 1), and when **every** call site
  already satisfies the new signature the finding is suppressed entirely.
  If `tree_sitter_languages` is installed, JS/TS parsing upgrades itself —
  the active mode is reported in `/api/health` as `engines.semanticJs`.
- Disable per run with `"crossFileAnalysis": false`.

## Incremental Re-Review (Verdict Reuse)

Re-running a review on the same repository reuses the previous run's verifier
verdicts, so only genuinely new findings hit the verification LLM:

- Findings whose fingerprint was **confirmed** in the most recent earlier
  completed run are stamped `verified` immediately with a `carried_from`
  key naming the source run; previously **rejected** fingerprints move
  straight to `rejected_issues`. Uncertain verdicts are always re-verified.
- Because LLMs reword findings between runs, a position-based fallback covers
  fingerprint misses: a verdict also carries when **exactly one** prior
  verdict-bearing finding sits on overlapping lines (±3) of the same file and
  shares at least one tag. Any ambiguity — zero or multiple candidates — means
  the finding is re-verified instead.
- Carried findings are excluded from the verifier subprocess via
  `--skip-ids`; when every finding carries a verdict the subprocess is not
  started at all (audited as `verification_skipped`).
- Run stats gain `verification.carried`, meta gains
  `reused_verdicts: {confirmed, rejected, from_run}`, and the UI shows a
  muted `carried` chip on those findings.
- Opt out per run with `"reuseVerdicts": false`. Reviewer dismissals are
  independent of this: suppressions already persist across runs.

## Apply Fixes and Write Generated Tests

A finding's proposed fix can be applied to the working tree, and a run's
generated test files written into the repo, through per-run endpoints (UI
buttons land in v5.1; the API is stable now):

- `POST /api/reviews/<id>/fixes/plan` `{issueId}` — dry preview. A fix is
  `applicable` only when the finding's recorded snippet still matches the
  file **exactly**; a drifted file is refused with a reason, never guessed
  at.
- `POST /api/reviews/<id>/fixes/apply` `{issueId}` — re-validates, backs the
  original up under the run directory, then writes atomically. Audited as
  `fix_applied`; the per-run ledger (`fixes.json`) records the backup.
- `POST /api/reviews/<id>/fixes/revert` `{issueId}` — restores the backup
  (audited `fix_reverted`).
- `POST /api/reviews/<id>/tests/write` `{overwrite?}` — writes the run's
  generated tests into the repo; existing files are skipped unless
  `overwrite: true`, and overwrites are backed up first.

Safety: paths resolve strictly inside the repo root, symlinked targets are
refused, every apply is one explicit finding at a time — there is no bulk
apply.

## Publish to GitHub / GitLab

The Reports view can post a completed review to a pull request (GitHub) or
merge request (GitLab):

- Configure tokens on the **server** (they never pass through the browser):
  `GITHUB_TOKEN` (or `CODE_DOCTOR_GITHUB_TOKEN`), `GITLAB_TOKEN` (or
  `CODE_DOCTOR_GITLAB_TOKEN`), and `GITLAB_BASE` for self-hosted GitLab.
- Publishing is two-step: **Preview** (`dryRun`) renders the exact summary and
  inline comments; **Publish** posts them only after that explicit confirm.
- GitHub gets a PR review with inline line comments (falling back to a summary
  comment if line positions are outside the diff); GitLab gets an MR note.
- Platform and repository slug default from the run repository's `origin`
  remote. Results are stored as `publish.json` per run and audited.
- API: `GET /api/publish/config`, `POST /api/reviews/<run-id>/publish` with
  `{platform, repo, pr, dryRun, lineComments}`.

## CI Mode and Webhooks

Code Doctor plugs into pipelines through two entry paths that share the exact
server review engine:

**CLI batch mode** — run a review synchronously and gate the pipeline on it:

```bash
python -m code_doctor_app.ci --repo . --what "$GITHUB_SHA" --against origin/main \
    --fail-on block --publish-pr "$PR_NUMBER"
```

Prints the summary markdown to stdout and exits `0` (gate ok), `1` (gate met
`--fail-on`: `block`, `review`, or `none`), or `2` (the review itself failed).
`--json result.json` writes a machine-readable result. A GitHub Actions step:

```yaml
- run: |
    pip install -e .
    python -m code_doctor_app.ci --repo . --what ${{ github.event.pull_request.head.sha }} \
        --against origin/${{ github.base_ref }} --fail-on block
```

**Webhook receiver** — for a standing Code Doctor service. Set
`CODE_DOCTOR_WEBHOOK_SECRET` on the server (without it the endpoints answer
503), then point GitHub at `POST /api/hooks/github` (secret = the same value;
events: pull requests) or GitLab at `POST /api/hooks/gitlab` (secret token
field). Signatures are verified on the raw body (GitHub HMAC-SHA256 /
GitLab shared token); the webhook routes are exempt from the bearer token
since the platforms cannot send it.

Incoming PR/MR events are mapped to a **registered** repository by matching
the event's slug against each registry entry's `origin` remote — repository
paths never come from the webhook payload. Unknown repos and non-PR events
answer 202 and are audited as `webhook_ignored`. Matched events fetch
`origin`, review `head_sha` against `origin/<base>`, and show up in the
dashboard like any other run (meta gains a `trigger` block).

Posting back is opt-in via the Governance policy `ci.autoPublish` (default
off): when enabled and tokens are configured, the finished review is
published to the PR/MR and the gate lands as a `code-doctor/gate` commit
status (`block` → failure, otherwise success).

## Live Streaming, Ollama Watchdog, and Per-Pass Models

- **Live run streaming** — `GET /api/reviews/<id>/events` is a Server-Sent
  Events stream of log increments and meta changes for a running review,
  closed with `event: done` when the run reaches a final status. The
  dashboard uses it to tail runs without polling; auth is the normal bearer
  header (fetch-streaming, no token in the URL).
- **Ollama watchdog** — a background thread samples the local Ollama
  endpoint every 30 seconds. `/api/health` exposes the rolling state under
  `ollamaWatch`, the dashboard shows a banner while Ollama is down, and runs
  started during an outage are stamped `ollama_warning` in meta so a failed
  run explains itself.
- **Per-pass model routing** — the verifier and generator passes can run on
  a different model than the main review: per-run payload keys
  `verifyModel` / `generateModel` (in the Cockpit under *Advanced*), or
  workspace-wide Governance defaults `models.verify` / `models.generate`.
  Empty inherits the run's main model, which remains the default behavior.

## Verification Pass and Feedback Loop

After every review, a skeptical second-pass verifier re-checks each LLM
finding against the diff (deterministic static findings are exempt).
Confirmed findings gain a `verified` tag; rejected findings are quarantined
under `rejected_issues` — kept for audit, excluded from risk. Disable per run
with `"verifyFindings": false`; cap it with `verifyTimeoutSeconds`.

Reviewers can dismiss any finding in the UI (or via
`POST /api/findings/feedback`). Dismissals store the finding's fingerprint in
`.code-doctor/suppressions.json` and exclude it from risk scores and gates in
**every** run — recurring noise only has to be dismissed once. Restore undoes
it. All feedback lands in the audit trail.

## Risk Engine and Issue Lifecycle

- Risk scores weight severity by model confidence and apply a multiplier to
  security-tagged findings, instead of counting severity alone.
- Each finding gets a stable fingerprint (file + tags + normalized title).
  Run stats report `lifecycle: {new, recurring, resolved}` against the
  previous completed run of the same repository, so reviewers see what a PR
  actually introduced versus inherited.
- Review subprocesses are killed after `timeoutSeconds` (default 3600) and
  the run is marked failed with `timed_out: true`.

## Product Controls

The dashboard exposes the main operator workflows from the left navigation:

- Cockpit: repository coverage, review volume, risk gates, readiness, and recurring risk tags.
- Review: Ollama-backed review execution, preflight checks, risk gate, findings, generated test cases, and logs.
- Repositories: production repository registry with owner, tier, language, branch, and last-review metadata.
- Reports: evidence export in JSON, Markdown, and CSV.
- Governance: severity thresholds, sensitive-file escalation, and guardrail status.
- Audit: JSONL-backed event history for review, export, repository, and policy actions.

The `Sample Data` action creates a real local git repository under
`.code-doctor/sample-repos/acme-payments-api` with a working diff that includes
Python, Node.js, and sensitive-file changes. This lets preflight, repository
coverage, risk gates, and evidence exports run against an actual git worktree.

## Runtime Data

Shared mutable state — the audit trail, finding suppressions, and the
repository registry — lives in a SQLite database in WAL mode
(`.code-doctor/code-doctor.db`), so concurrent server threads and multiple
server processes sharing the data directory read and write safely. Legacy
`audit.jsonl` / `suppressions.json` / `repos.json` files are imported once at
startup; the audit trail is additionally mirrored to `audit.jsonl` as
human-readable evidence.

Per-run artifacts stay on disk under `.code-doctor/` (ignored by git):

- `.code-doctor/runs/<run-id>/meta.json`
- `.code-doctor/runs/<run-id>/code-review-report.json`
- `.code-doctor/runs/<run-id>/code-review-report.md`
- `.code-doctor/runs/<run-id>/gito.log`
- `.code-doctor/runs/<run-id>/context-pack.json`
- `.code-doctor/runs/<run-id>/verification.json`
- `.code-doctor/runs/<run-id>/generated-tests/`, `generated-tests.json`
- `.code-doctor/runs/<run-id>/pr-draft.json`, `publish.json`
- `.code-doctor/code-doctor.db` (+ WAL/SHM sidecars)
- `.code-doctor/policies.json`
- `.code-doctor/audit.jsonl` (evidence mirror)
- `.code-doctor/sample-repos/`

For a scale-out enterprise deployment, swap the SQLite store for Postgres, put
the app behind TLS and SSO, and run review jobs through a managed queue. The
local edition exercises the same review path with a private Ollama runtime.
