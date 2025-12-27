# Documentation generation with Gito (using `gito ask`)

Gito isn’t only for AI code reviews—it can also generate **project documentation** directly from your repository context.  
The workhorse for that is the `gito ask` command, which can read your code changes (or the whole repo)
and produce a Markdown document you can review/edit and add to your project documentation.

Below is a practical guide showing the patterns that work well in real projects.


## Why use `gito ask` for documentation?

When you write docs manually, you typically:
- forget edge cases,
- miss configuration details,
- drift away from the actual implementation.

`gito ask` solves this by generating docs **from the codebase context** that Gito loads (diff + full file content, plus optional “aux files” you want the model to reference).

---

## The core command pattern

Your example is exactly the right shape:

```bash
gito ask --all 'Create an article on Linear integration (target: documentation within folder)' \
  --save-to='documentation/linear_integration.md'
```

What’s happening here:

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

If you’re documenting a feature introduced in a branch/PR, you often want the doc to describe *what changed*, not everything.

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
