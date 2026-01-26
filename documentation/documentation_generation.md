# <a href="https://github.com/Nayjest/Gito"><img src="https://raw.githubusercontent.com/Nayjest/Gito/main/press-kit/logo/gito-bot-1_64top.png" align="left" width=64 height=50 title="Gito: AI Code Reviewer"></a>Documentation generation with Gito (using `gito ask`)

Gito isn't only for AI code reviews—it can also generate **project documentation** directly from your repository context.  
The workhorse for that is the `gito ask` command, which can read your code changes (or the whole repo)
and produce a Markdown document you can review/edit and add to your project documentation.

Below is a practical guide showing the patterns that work well in real projects.

## Why use `gito ask` for documentation?

When you write docs manually, you typically:
- forget edge cases,
- miss configuration details,
- drift away from the actual implementation.

`gito ask` solves this by generating docs **from the codebase context** that Gito loads (diff + full file content, plus optional "aux files" you want the model to reference).

---

## The core command pattern

Your example is exactly the right shape:

```bash
gito ask --all 'Create an article on Linear integration (target: documentation within folder)' \
  --save-to='documentation/linear_integration.md'
```

What's happening here:
- `ask` — asks a question about the repository context (not just generic LLM chat).
- `--all` — tells Gito to use the **whole codebase** as context (not just a diff).
- the quoted prompt — describes what you want to generate.
- `--save-to=...` — writes the final answer directly to a Markdown file.

---

## Recommended workflow for generating docs

### 1) Generate the document from the full codebase

Use `--all` for documentation tasks that should reflect the actual implementation.

```bash
gito ask --all "Create documentation for <TOPIC>. Save-ready Markdown for /documentation." \
  --save-to="<generated-doc-file>.md"
```

### 2) Iteratively refine (fast loop)

If the output is close but not perfect, re-run with a tighter instruction:

```bash
gito ask --all "Rewrite documentation/<topic>.md focusing on: prerequisites, setup steps, troubleshooting. Keep it concise." \
  --save-to="documentation/<topic>-1.md"
```

This works well because Gito re-reads current repository state and regenerates consistently.

---

## Generating docs from *only* recent changes (diff-based docs)

If you're documenting a feature introduced in a branch/PR, you often want the doc to describe *what changed*, not everything.

Examples:

### Compare your branch to main

```bash
gito ask "Write release notes for the changes in this branch." \
  --save-to="documentation/release_notes.md"
```

### Explicit refs (what..against)

```bash
gito ask "Create a migration guide for these changes." "HEAD..origin/main" \
  --save-to="documentation/migration_guide.md"
```

---

## Filtering scope (useful in large repos)

To generate docs only from a subsystem:

```bash
gito ask --all \
  --filters="src/my_feature/*,documentation/*" \
  "Create an article explaining <FEATURE>, including env vars and workflows." \
  --save-to="documentation/<feature>.md"
```

This reduces noise and makes the output more focused.

---

## Automating release notes with GitHub Actions

Gito can automatically generate release notes and post them to Linear or GitHub PR comments when a PR is merged.
This keeps your issue tracker up-to-date without manual effort.

### Workflow overview

The workflow below:
- **Triggers automatically** when a PR is merged to `main` or `master`
- **Can be triggered manually** via the Actions tab for any branch
- **Generates release notes** by comparing the branch against its merge target
- **Posts the notes** as a comment on the associated Linear issue

### Setup

1. Add the following secrets to your repository (Settings → Secrets and variables → Actions):
   - `LINEAR_API_KEY` — your Linear API key
   - `ANTHROPIC_API_KEY` — your Anthropic API key (or other LLM provider, see [GitHub Setup Guide](https://github.com/Nayjest/Gito/blob/main/documentation/github_setup.md))

2. Create `.github/workflows/release-notes-linear.yml`:

```yaml
name: Post Release Notes to Linear

on:
  pull_request:
    types: [closed]
    branches: [main, master]

  workflow_dispatch:
    inputs:
      issue_key:
        description: 'Linear issue key (e.g., ISS-123)'
        required: false
        type: string
      target_branch:
        description: 'Merge target branch (e.g., main)'
        required: true
        default: 'main'
        type: string

jobs:
  post-release-notes:
    if: >
      github.event_name == 'workflow_dispatch' ||
      (github.event.pull_request.merged == true)
    runs-on: ubuntu-latest

    steps:
      - name: Checkout repository
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6

      - name: Install Gito
        run: pip install gito.bot~=4.0.1

      - name: Generate and post release notes
        env:
          LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY }}
          LLM_API_TYPE: anthropic
          LLM_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          MODEL: claude-opus-4-5
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          TARGET_BRANCH: ${{ github.event_name == 'pull_request' && github.event.pull_request.base.ref || inputs.target_branch }}
          ISSUE_KEY: ${{ inputs.issue_key }}
        run: |
          gito -v0 ask "tpl:questions/release_notes.j2" --against=$TARGET_BRANCH > release_notes.txt
          if [ -n "$ISSUE_KEY" ]; then
            gito linear-comment - --issue-key=$ISSUE_KEY < release_notes.txt
          else
            gito linear-comment - < release_notes.txt
          fi
```

### How it works

| Trigger | Target branch | Issue key |
|---------|---------------|-----------|
| PR merge | PR base branch (automatic) | Auto-detected from branch name |
| Manual run | User input (defaults to `main`) | User input or auto-detected |

### Branch naming convention

For automatic issue key detection, name your branches with the Linear issue key:
- `feature/ISS-123-add-login`
- `ISS-456-fix-bug`
- `bugfix/ISS-789-resolve-crash`

Gito extracts the issue key (e.g., `ISS-123`) from the branch name automatically.

### Manual execution

1. Go to **Actions** → **Post Release Notes to Linear** → **Run workflow**
2. Select the feature branch from the dropdown
3. Enter the target branch (defaults to `main`)
4. Optionally enter the issue key (leave empty for auto-detection)
5. Click **Run workflow**

### Customizing the release notes template

The workflow uses bundled template: [questions/release_notes.j2](https://github.com/Nayjest/Gito/blob/main/gito/tpl/questions/release_notes.j2) to generate release notes. You can customize this template or create your own in your repository and reference it instead:

```yaml
run: |
  gito -v0 ask "tpl:my_templates/custom_release_notes.j2" --against=$TARGET_BRANCH > release_notes.txt
```

Or use a plain prompt:

```yaml
run: |
  gito -v0 ask "Summarize the changes in this branch as release notes. Use Markdown formatting." --against=$TARGET_BRANCH > release_notes.txt
```