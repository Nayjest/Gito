# Contributing to Gito

First off, thank you for considering contributing to Gito! Contributions are welcome, deeply appreciated, and will be fully credited.

## Quick Start

**Prerequisites:** Python 3.11–3.13.
*Note: Please read [AGENTS.md](AGENTS.md) for architecture, conventions, and common commands before making non-trivial changes.*

### Local Setup

1. **Clone your fork:**
   ```bash
   git clone git@github.com:<your-username>/Gito.git
   cd Gito
   ```

2. **Install dependencies:**
   ```bash
   # Recommended: Using Poetry
   poetry install

   # Alternative: Using pip
   pip install -e .
   pip install black flake8 pytest pytest-asyncio pytest-mock
   ```

3. **Verify your setup:**
   ```bash
   pytest
   ```
   *All tests must pass without requiring any LLM credentials.*

## Development Commands

Use the following commands to format, lint, and test your code during development:

| Command | Purpose |
|---|---|
| `make black` | Format code (Black, line length 100) |
| `make cs` | Run linter (Flake8) |
| `make test` | Run the test suite (`pytest --log-cli-level=INFO`) |
| `pytest tests/test_core.py::test_name` | Run a specific test |
| `gito review` (or `python -m gito`) | Run the tool locally against your working copy |

## Codebase Overview

Familiarize yourself with the core structure of the repository:

- **Core Logic:** `gito/core.py` handles the review logic.
- **CLI Commands:** `gito/cli.py` and the `gito/commands/` directory.
- **Configuration (`gito/config.toml`):** This is where prompts, report templates, tags, and severity scales live. **Behavioral changes usually belong here**, rather than in the Python code.
- **Tests & Documentation:** Located in `tests/` and `documentation/`, respectively.

## Testing Guidelines

- **Keep it green:** Ensure all tests pass and that new changes are covered by tests.
- **No live LLM calls:** Tests must *never* hit a real LLM API. All inference routes through microcore (`mc.llm`, `mc.allm`, `mc.llm_parallel`, `mc.prompt(...).to_llm()`). You must mock requests at this level.
  *Tip: A single code path may hit microcore multiple times (e.g., `review()` runs per-file prompts and a summary prompt).*
- **Verify mocking:** A missed mock might silently pass locally if you have real credentials in `~/.gito/.env`, only to fail in CI. Validate your mocks against a strictly empty environment:
  ```bash
  HOME=/tmp/empty USERPROFILE=/tmp/empty LLM_API_KEY= LLM_API_TYPE= pytest
  ```

## Pull Request Process

1. **Branch out:** Create a new branch from `main`. Keep it to one feature or bugfix per PR.
2. **Format and check:** Run `make black`, `make cs`, and `make test` before pushing.
3. **Update docs:** If your change alters behavior, update `README.md` and/or the `documentation/` folder.
4. **Clean up commits:** Squash intermediate or fixup commits. Each commit in your PR should represent a meaningful, standalone change.
5. **Write a descriptive PR:** Clearly explain the *what* and *why* in your PR body, and link to any relevant open issues.

> **Note on CI Security:** CI jobs requiring repository secrets (like AI code review) will only run on fork PRs *after* a maintainer approves them. Don't be alarmed if the "review" check on your PR fails or stays pending on the `external-pr` environment — that's expected behavior and doesn't reflect your code.

### Validating CI on Your Fork (Optional)

GitHub disables GitHub Actions on new forks by default. To run CI privately before opening an upstream PR:
1. Navigate to the **Actions** tab in your fork.
2. Enable workflows.
3. Push your branch (or open an internal PR to your fork's `main`) and iterate until tests are green.

What to expect on a fork:
- **`Tests` and `Code Style` run out of the box** — they need no secrets, and the test suite is designed to pass without LLM credentials. These are the checks worth validating.
- **The AI-review workflows (`Gito: AI Code Reviewer`, react-to-comments) will fail** — they need LLM credentials your fork doesn't have. In this repo they are configured for Anthropic (`LLM_API_TYPE: anthropic` + an `ANTHROPIC_API_KEY` secret), but Gito is vendor-agnostic: either add that secret in your fork's *Settings → Secrets and variables → Actions*, edit the workflow's `env` block to point at your own LLM provider, or simply ignore/disable these workflows on the fork.

<img width="560" alt="Enabling workflows in the Actions tab of a fork" src="https://github.com/user-attachments/assets/d37eac21-4aaf-4013-b24f-be5f8ec5a063" />

🚀 **Happy coding!**
