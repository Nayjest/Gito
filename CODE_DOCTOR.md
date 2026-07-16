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
- Disable per run with `"crossFileAnalysis": false`.

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
