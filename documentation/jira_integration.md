# <a href="https://github.com/Nayjest/Gito"><img src="https://raw.githubusercontent.com/Nayjest/Gito/main/press-kit/logo/gito-bot-1_64top.png" align="left" width=64 height=50 title="Gito: AI Code Reviewer"></a>Jira Integration

Gito can automatically **detect a Jira issue key from your branch name**, fetch the Jira issue details, and inject that context into:

- AI code review summaries (the “issue alignment” line in the summary)
- `gito ask` / Q&A prompts (via `pipeline_out.associated_issue`)

This integration is implemented as a **pipeline step**: `gito.pipeline_steps.jira.fetch_associated_issue`.

## What the Jira integration does

### 1) Fetch “associated issue” context
When enabled and configured, Gito will:

1. Extract an issue key from the current branch name (e.g., `feature/PROJ-123-add-auth` → `PROJ-123`)
2. Call Jira API using the Python `jira` client
3. Attach the result into pipeline output as:

- `pipeline_out.associated_issue.title`
- `pipeline_out.associated_issue.description`
- `pipeline_out.associated_issue.url`

That context is then available to summary generation via the default `summary_prompt` (see `gito/config.toml`).

### 2) Affect summary output (“issue alignment”)
If an associated issue is found, Gito’s summary prompt includes a special section that asks the model to add an issue-alignment sentence like:

```md
<!-- issue_alignment -->
✅  Implementation Satisfies [PROJ-123](https://your-domain.atlassian.net/browse/PROJ-123).
```

(or ⚠️ with concrete gaps when requirements appear not fully covered).

## Requirements

### Jira credentials
Gito reads Jira credentials from environment variables (or passed explicitly to the pipeline step):

- `JIRA_URL` — Base Jira URL, e.g. `https://your-domain.atlassian.net`
- `JIRA_USER` / `JIRA_USERNAME` / `JIRA_EMAIL` — Username/email for Jira auth
- `JIRA_TOKEN` / `JIRA_API_TOKEN` / `JIRA_API_KEY` — Jira API token

Resolution order (as implemented in `gito/pipeline_steps/jira.py`):

- Username: `jira_username` arg → `JIRA_USERNAME` → `JIRA_USER` → `JIRA_EMAIL`
- Token: `jira_api_token` arg → `JIRA_API_TOKEN` → `JIRA_API_KEY` → `JIRA_TOKEN`

### Branch naming convention
Gito extracts the issue key using `gito.issue_trackers.extract_issue_key()`, which expects:

- Uppercase project key, then dash, then digits (e.g., `ABC-123`)

Examples that work:

- `feature/PROJ-123`
- `bugfix/AA-9-fix-timeouts`
- `PROJ-123_some_text`

Examples that do **not** work:

- `proj-123` (lowercase)
- branches with no issue key token

---

## How it works internally (implementation overview)

### Pipeline step configuration
In the bundled default config (`gito/config.toml`) Jira is enabled by default:

```toml
[pipeline_steps.jira] # Jira integration step, fetches associated issue details for the review context.
call="gito.pipeline_steps.jira.fetch_associated_issue"
envs=["local","gh-action"]
```

### Implementation
The pipeline step lives here:

- `gito/pipeline_steps/jira.py`

Key logic:

- `resolve_issue_key(repo)` determines the current branch (supports GitHub Actions env) and extracts the issue key.
- `fetch_issue(...)` uses `jira.JIRA(..., basic_auth=(username, api_token))` and loads `jira.issue(issue_key)`.

The returned value is normalized into:

```python
IssueTrackerIssue(
  title=issue.fields.summary,
  description=issue.fields.description or "",
  url=f"{jira_url.rstrip('/')}/browse/{issue_key}",
)
```

and exposed as:

```python
{"associated_issue": IssueTrackerIssue(...)}
```

## Usage

### Use Jira context automatically during review
If Jira env vars are configured and your branch contains a Jira key:

```bash
gito review
```

The fetched issue becomes available to the summary prompt as `pipeline_out.associated_issue`.

### Use Jira context during Q&A
Ask a question about the change; Jira context is included automatically (pipeline is enabled by default):

```bash
gito ask "What risks do these changes introduce?"
```

## GitHub Actions setup

If you use the repo’s workflow templates, pass Jira secrets as environment variables.

This repo already includes placeholders in workflow env sections (see `gito/tpl/github_workflows/components/env-vars.j2`), and examples exist in `.github/workflows/gito-code-review.yml`:

```yaml
env:
  JIRA_TOKEN: ${{ secrets.JIRA_TOKEN }}
  JIRA_URL: ${{ secrets.JIRA_URL }}
  JIRA_USER: ${{ secrets.JIRA_USER }}
```

### Required GitHub Secrets
Add these secrets under:

**Settings → Secrets and variables → Actions → New repository secret**

- `JIRA_URL`
- `JIRA_USER`
- `JIRA_TOKEN`

## Disabling Jira integration

If you don’t use Jira, disable only the Jira pipeline step in your repo’s `.gito/config.toml`:

```toml
[pipeline_steps.jira]
enabled=false
```

## Troubleshooting

### “Jira configuration error: JIRA_URL is not set” (or username/token)
Your environment doesn’t provide the required variables. Fix by exporting them locally or adding GitHub Secrets and passing them in workflow `env:`.

### “No issue key found in branch name”
Rename your branch to include a Jira key token, e.g.:

- `feature/PROJ-123-description`

### Jira issue fetch fails
Common causes:

- Wrong `JIRA_URL` (must match your Jira base URL)
- Token is invalid/expired
- User lacks permission to read the issue/project

Check workflow logs; errors are surfaced from `jira.JIRAError` with status code + response text.

## Security notes

- Treat `JIRA_TOKEN` as a secret. Store it in GitHub Secrets or a secret manager.
- Use the least-privileged Jira account/token that can read issues needed for review context.
