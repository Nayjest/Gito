# <a href="https://github.com/Nayjest/Gito"><img src="https://raw.githubusercontent.com/Nayjest/Gito/main/press-kit/logo/gito-bot-1_64top.png" align="left" width=64 height=50 title="Gito: AI Code Reviewer"></a>Linear Integration

Gito can automatically **detect the [Linear](https://linear.app/) issue associated with your branch** (e.g., `ENG-123`) 
and use it as extra context during code review and question answering.

It can also **post answers back to Linear as comments**.

This integration is implemented via a pipeline step (`gito.pipeline_steps.linear.fetch_associated_issue`) and the `gito linear-comment` / `gito ask --post-to linear` CLI features.

---

## What the Linear integration does

### 1) Fetch “associated issue” context
When enabled, Gito will:

1. Extract an issue key from the current branch name (e.g., `feature/ENG-123-something` → `ENG-123`)
2. Query Linear GraphQL API for that issue
3. Attach the result into the pipeline output as:

- `pipeline_out.associated_issue.title`
- `pipeline_out.associated_issue.description`
- `pipeline_out.associated_issue.url`

This allows summary prompts (and custom prompts) to reference the issue requirements.

### 2) Post text to Linear as a comment
Gito can post a comment to the associated Linear issue using GraphQL mutation `commentCreate`.

This is used by:
- `gito linear-comment`
- `gito ask --post-to linear` (posts the produced answer)

---

## Requirements

### Linear API Key
Set `LINEAR_API_KEY` in the environment where Gito runs:

- **Local:** export it in your shell or store it in your `~/.gito/.env`
- **GitHub Actions:** add it as a secret and pass it as env var

Example:

```bash
export LINEAR_API_KEY="lin_api_xxx"
```

> Note: Gito uses the value as the `Authorization` header when calling `https://api.linear.app/graphql`.

### Branch naming convention
To auto-detect the issue key, your branch name must contain a token like:

- `ABC-123` (uppercase team key + dash + number)

Examples that work:
- `feature/ENG-123`
- `bugfix/PLAT-9-fix-timeouts`
- `ENG-123_word_word`

If the branch does not contain an issue key, Gito will log an error and skip the association step.

---

## How it works internally (implementation overview)

### Issue key extraction
Issue keys are extracted by `gito.issue_trackers.extract_issue_key()` using a regex that matches:

- `([A-Z][A-Z0-9]{min_len-1,max_len-1}-\d+)`

…and ensures it is bounded by typical separators (`/`, `_`, `-`, word boundary, etc.).

### Linear fetch pipeline step
The pipeline step configured in `gito/config.toml`:

```toml
[pipeline_steps.linear]
call="gito.pipeline_steps.linear.fetch_associated_issue"
envs=["local","gh-action"]
```

Implementation is in:

- `gito/pipeline_steps/linear.py`

It executes a GraphQL query that fetches issue data based on:
- `teamKey` (e.g., `ENG`)
- `issueNumber` (e.g., `123`)

and returns:

```python
{"associated_issue": IssueTrackerIssue(...)}
```

### Posting comments to Linear
Posting uses a GraphQL mutation in:

- `gito/commands/linear_comment.py`

It resolves the issue key from the branch and calls `commentCreate`.


## Usage

### Use Linear context automatically during review
If `LINEAR_API_KEY` is set and your branch contains a Linear key:

```bash
gito review
```

The fetched issue is made available to summary generation (and other prompts) as `pipeline_out.associated_issue`.

### Ask a question and post the answer to Linear
This runs the Q&A prompt and then posts the answer as a Linear comment:

```bash
gito ask "What are the risks of this change?" --post-to linear
```

### Post an arbitrary comment to Linear
You can post any text directly:

```bash
gito linear-comment "Deployed to staging, please verify."
```

Or pipe input:

```bash
echo "QA notes: looks good" | gito linear-comment -
```

---

## GitHub Actions setup

If you use the provided workflows/templates, ensure your repository has a secret:

- `LINEAR_API_KEY`

And the workflow sets:

```yaml
env:
  LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}
```

This is already included in the repo’s workflow templates under:

- `gito/tpl/github_workflows/components/env-vars.j2`

## Security notes

- Treat `LINEAR_API_KEY` as a secret. Store it in secret managers / GitHub Secrets.
- Limit workflow permissions to what you need (posting comments is done via Linear API key, not GitHub permissions).
