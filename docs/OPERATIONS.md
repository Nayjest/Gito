# Code Doctor Operations Runbook

For the person **running** Code Doctor, not developing it. Development docs
live in [CODE_DOCTOR.md](../CODE_DOCTOR.md); the release plan in
[NEXT_RELEASE_PLAN.md](../NEXT_RELEASE_PLAN.md).

## Start / Stop

```bash
CODE_DOCTOR_TOKEN="$(cat .code-doctor/token)" \
  .venv/bin/python -m code_doctor_app --host 127.0.0.1 --port 8787
```

- Server log: stderr of that process (redirect it somewhere under `launchd`/`systemd`).
- Per-run logs: `.code-doctor/runs/<run-id>/gito.log` (review, verification,
  and generation output all append there).
- Stop: SIGTERM/Ctrl-C. In-flight review subprocesses are killed with the server.

macOS `launchd` sketch (`~/Library/LaunchAgents/com.local.codedoctor.plist`):
`ProgramArguments` = the command above, `KeepAlive` = true,
`StandardErrorPath` = a log file. Linux `systemd` unit: `ExecStart=` the same
command, `Restart=on-failure`, `Environment=CODE_DOCTOR_TOKEN=…`.

## Port conflicts — and the stale-server trap

Symptom: `OSError: [Errno 48] Address already in use`, **or** a feature you
just changed "doesn't work".

```bash
lsof -ti tcp:8787          # who owns the port
lsof -ti tcp:8787 | xargs kill
```

The trap (hit twice while building this): an **old** server process keeps
serving **old code** while you debug the new code. Before concluding a
feature is broken, always check *which* process owns the port and when it
was started (`ps -p "$(lsof -ti tcp:8787)" -o pid,etime,command`).

## Ollama

- Health: `curl -s localhost:11434/api/tags` — the dashboard header shows the
  same probe.
- Ollama stops in the background on macOS more often than you'd expect. Keep
  it alive: `brew services start ollama` (macOS) /
  `systemctl enable --now ollama` (Linux).
- Pull models ahead of time (`ollama pull gemma4:e4b`); the first review with
  a cold model otherwise burns most of its timeout loading it.
- Reasoning models (e.g. qwen3.5) spend tokens on `<think>` blocks and can
  starve structured output. Prefer a non-reasoning model for the verify and
  generation passes.

## Concurrency

- Reviews/generations run through a bounded worker pool. Set
  `CODE_DOCTOR_REVIEW_WORKERS` (default 2) to control how many run at once.
- On a single local GPU keep it low (1–2); with a cloud provider or strong
  hardware, raise it. Watch `queue` in `/api/health` — a persistently high
  `queued` count means workers are the bottleneck.

## LLM Providers

- Local (Ollama) needs no key. For cloud, set the provider's key in the
  service environment and pick it in the Cockpit:
  `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`. Keys are read
  from the server environment only — never sent from the browser.
- A whole-repo review that times out on a local model usually finishes in
  a minute or two on a cloud provider (higher parallelism + a faster model).

## Backup and Restore

Both safe **while the server runs** (WAL journaling keeps the copy consistent):

```bash
sqlite3 .code-doctor/code-doctor.db "VACUUM INTO 'backup-$(date +%F).db'"
tar czf runs-$(date +%F).tar.gz .code-doctor/runs/
```

Restore: stop the server, replace `.code-doctor/code-doctor.db` (delete any
`-wal`/`-shm` sidecars) and/or unpack `runs/`, start the server.

## Reset

- Delete `.code-doctor/runs/` → lose run history; **keep** repository
  registry, suppressions, audit trail, policies.
- Delete `.code-doctor/` entirely → lose everything, including reviewer
  dismissals (suppressions) and the audit trail. There is no undo; back up first.

## Token rotation

1. Change `CODE_DOCTOR_TOKEN` in the service environment; restart.
2. Update the token in each user's browser (the UI stores it in local storage).

Webhook secret rotation is the same pattern with
`CODE_DOCTOR_WEBHOOK_SECRET`, plus updating the secret in the GitHub/GitLab
webhook settings. Publishing tokens (`GITHUB_TOKEN`/`GITLAB_TOKEN`) are read
per request — a restart picks up new values, nothing else to do.

## TLS / network exposure

Code Doctor is deliberately plain-HTTP on loopback. If you must expose it,
terminate TLS in a reverse proxy — do not put the stdlib HTTP server on a
network edge. Caddy (two lines):

```
review.internal.example.com
reverse_proxy 127.0.0.1:8787
```

nginx: a standard `proxy_pass http://127.0.0.1:8787;` server block with your
certificates. Always set `CODE_DOCTOR_TOKEN` when binding beyond loopback —
the server prints a loud warning if you don't.

## Upgrade / Downgrade

Upgrade: `git pull` → `uv pip install --python .venv/bin/python -e .` →
restart. Store migrations run automatically and idempotently at startup;
check the readiness panel and `GET /api/health` (`version` key) afterwards.

Downgrade: older code ignores newer SQLite tables and artifact keys, so
stepping back a release is safe. The one caveat: runs created by a newer
version may show fewer details in an older UI — never errors.

## Quick triage

| Symptom | First check |
| --- | --- |
| Review stuck in `running` | `gito.log` for the run; Ollama up? model pulled? |
| Every run fails instantly | server stderr; `git` on PATH; repo path still valid? |
| 401 in the UI | token mismatch — re-enter it; after 10 bad tries an IP is throttled for 60s |
| Webhook returns 503 | `CODE_DOCTOR_WEBHOOK_SECRET` not set on the server |
| Webhook returns 202 but no run | audit trail: `webhook_ignored` says why (event type or unregistered repo) |
| Publish fails | `GITHUB_TOKEN`/`GITLAB_TOKEN` set on the **server** env, not the browser |
