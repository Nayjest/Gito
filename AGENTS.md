# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this is

Gito (`gito.bot` on PyPI) is an open-source, vendor-agnostic AI code reviewer. It diffs git changes (local working copy, a branch, or a GitHub/GitLab PR/MR), sends each changed file to an LLM in parallel, and produces a structured report rendered to the CLI, Markdown (for PR comments), or GitLab Code Quality JSON. It can also answer free-form questions about a changeset and react to PR comments.

The package is `gito/`; tests are in `tests/`. The CLI entrypoint is `gito.entrypoint:main` (commands `gito` and `gito.bot`).

## Common commands

```bash
make install      # pip install -e . (dev: pip install -e ".[dev]")
make test         # pytest --log-cli-level=INFO   (alias: make tests)
pytest tests/test_core.py::test_name   # run a single test
make black        # format (black ., line-length 100)
make cs           # lint (flake8 ., max-line-length 100)
make cli-reference  # regenerate documentation/command_line_reference.md (NOT on Windows — needs PYTHONUTF8)
```

This is a Poetry project (`pyproject.toml`), but the Makefile uses plain `pip`/`pytest`. Python 3.11–3.13. Tests use `pytest-asyncio`.

Running the tool locally: `gito review` (current branch vs base), `gito ask "<question>"`, `gito files` (preview the changeset), `gito setup` (interactive LLM config wizard). Use `python -m gito` if the `gito` command isn't on PATH.

## Architecture

**LLM access is fully delegated to [ai-microcore](https://github.com/Nayjest/ai-microcore)** (imported as `microcore as mc`). Gito never talks to a provider SDK directly — `mc.llm()`, `mc.llm_parallel()`, `mc.prompt()`, `mc.tpl()`, and `mc.tokenizing` handle inference, templating (Jinja2), parallelism, retries, and JSON parsing. Provider/model/keys come from env vars (`LLM_API_TYPE`, `LLM_API_KEY`, `MODEL`, `MAX_CONCURRENT_TASKS`), loaded from `~/.gito/.env`. Adding a provider is a microcore concern, not a Gito one.

**Two-layer configuration:**
- *Environment* (`~/.gito/.env` or OS env) → LLM credentials/model. Machine-specific, never committed. See `gito/env.py`.
- *Project* (`<repo>/.gito/config.toml`) → review behavior, prompts, templates, pipeline steps. Merged on top of the bundled defaults in **`gito/config.toml`**, which is the canonical source of all prompt text, report templates, tags, severity/confidence scales, and `post_process`/`prompt_vars`. `ProjectConfig` (`gito/project_config.py`) loads and merges these.

**Review flow** (`gito/core.py`, the heart of the codebase):
1. `get_diff` / `get_target_diff` build a `unidiff.PatchSet` from git. Most complexity lives in `get_base_branch` and the merge-base logic in `get_diff` — it resolves the comparison base across local branches, already-merged branches (walks merge commits to find the first common ancestor), GitHub Actions env, and full-codebase reviews (`--all` → `REFS_VALUE_ALL`). Binary files are filtered out; `filter_diff` applies fnmatch include/exclude filters.
2. `review()` builds one prompt per changed file and runs them through `mc.llm_parallel(..., allow_failures=True)`. Per-file failures become `ProcessingWarning`s rather than aborting the run.
3. LLM returns JSON validated against `RawIssue`; issues are post-processed by the user-supplied `post_process` Python snippet via `exec` (default keeps only confidence==1, severity<=3).
4. Results assemble into a `Report` (`gito/report_struct.py`), which renders via the Jinja templates in config (`report_template_md`, `report_template_cli`, `report_template_gitlab_code_quality`). Outputs: `code-review-report.json` + `code-review-report.md`.

**Pipeline steps** (`gito/pipeline.py`, `gito/pipeline_steps/`): configurable post-diff hooks declared in config under `[pipeline_steps.*]` as `call = "module.path.func"`, resolved dynamically via `resolve_callable`. Each runs gated by environment (`local` vs `ci`, see `PipelineEnv`), and its return dict is merged into `ctx.pipeline_out` for use in prompts (e.g. `pipeline_out.associated_issue`). This is how Jira/Linear issue-tracker context gets injected.

**CLI** (`gito/cli.py` + `gito/cli_base.py`): Typer app. Commands are registered across `cli.py` and `gito/commands/` (the `from .commands import ...` line in `cli.py` exists to trigger registration). There's a dual-mode setup: `gito review ...` (subcommand) and a bare `gito` invocation (`app_no_subcommand`) handled in `entrypoint`/`main`. `bootstrap()` (`gito/bootstrap.py`) runs before commands to configure microcore, logging, verbosity, and UTF-8 stdout on Windows.

**Git platform adapters** (`gito/utils/git_platform/`): GitHub (`ghapi`) and GitLab abstractions for identifying the platform, resolving repo URLs, and posting comments. `gito/gh_api.py`, `gito/gitlab.py`, `gito/issue_trackers.py` are the integration surfaces.

## Conventions

- `Context` (`gito/context.py`) is the dataclass passed through review/answer/pipeline carrying `repo`, `diff`, `config`, `report`, `pipeline_out`.
- Prompt/template strings live in config TOML and `gito/tpl/*.j2`, not in Python. To change review behavior, edit `gito/config.toml` (or document how users override it via `.gito/config.toml`) rather than hardcoding in `core.py`.
- `post_process` and prompts are executed/rendered with user-controlled strings by design — this is the extensibility model, not a bug.
- Windows is a first-class target (standalone PyInstaller installer via `gito.spec` / `make windows-build`); keep encoding-sensitive code UTF-8 safe.
