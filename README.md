<h1 align="center"><a href="#"><img alt="Gito: AI Code Reviewer" src="https://raw.githubusercontent.com/Nayjest/Gito/main/press-kit/logo/gito-ai-code-reviewer_logo-180.png" align="center" width="180"></a></h1>
<p align="center">
<a href="https://pypi.org/project/gito.bot/" target="_blank"><img src="https://img.shields.io/pypi/v/gito.bot" alt="PYPI Release"></a>
<a href="https://github.com/Nayjest/Gito/actions/workflows/code-style.yml" target="_blank"><img src="https://github.com/Nayjest/Gito/actions/workflows/code-style.yml/badge.svg" alt="PyLint"></a>
<a href="https://github.com/Nayjest/Gito/actions/workflows/tests.yml" target="_blank"><img src="https://github.com/Nayjest/Gito/actions/workflows/tests.yml/badge.svg" alt="Tests"></a>
<img src="https://raw.githubusercontent.com/Nayjest/Gito/main/coverage.svg" alt="Code Coverage">
<a href="https://github.com/Nayjest/Gito/blob/main/LICENSE" target="_blank"><img src="https://img.shields.io/static/v1?label=license&message=MIT&color=d08aff" alt="License"></a>
</p>

**Gito** is an open-source **AI code reviewer** that works with any language model provider.
It detects issues in GitHub pull requests or local codebase changes—instantly, reliably, and without vendor lock-in.

Get consistent, thorough code reviews in seconds—no waiting for human availability.

## 📋 Table of Contents
- [Why Gito?](#-why-gito)
- [Perfect For](#-perfect-for)
- [Quickstart](#-quickstart)
  - [1. Review Pull Requests via GitHub Actions](#1-review-pull-requests-via-github-actions)
  - [2. Running Code Analysis Locally](#2-running-code-analysis-locally)
- [Configuration](#-configuration)
- [Guides & Reference](#-guides--reference)
  - [Command Line Reference](https://github.com/Nayjest/Gito/blob/main/documentation/command_line_reference.md) ↗
  - [Configuration Cookbook](https://github.com/Nayjest/Gito/blob/main/documentation/config_cookbook.md) ↗
  - [GitHub Setup Guide](https://github.com/Nayjest/Gito/blob/main/documentation/github_setup.md) ↗
  - Integrations
    - [Linear Integration](https://github.com/Nayjest/Gito/blob/main/documentation/linear_integration.md) ↗ 
    - [Atlassian Jira Integration](https://github.com/Nayjest/Gito/blob/main/documentation/jira_integration.md) ↗
  - [Troubleshooting](https://github.com/Nayjest/Gito/blob/main/documentation/troubleshooting.md) ↗
  - [Documentation generation with Gito](https://github.com/Nayjest/Gito/blob/main/documentation/documentation_generation.md) ↗
- [Known Limitations](#-known-limitations)
- [Development Setup](#-development-setup)
- [Contributing](#-contributing)
- [License](#-license)

## ✨ Why Gito?<a id="-why-gito"></a>

- [⚡] **Lightning Fast:** Get detailed code reviews in seconds, not days — powered by parallelized LLM processing  
- [🔧] **Vendor Agnostic:** Works with any language model provider (OpenAI, Anthropic, Google, local models, etc.)  
- [🌐] **Universal:** Supports all major programming languages and frameworks  
- [🔍] **Comprehensive Analysis:** Detect issues across security, performance, maintainability, best practices, and much more  
- [📈] **Consistent Quality:** Never tired, never biased—consistent review quality every time  
- [🚀] **Easy Integration:** Automatically reviews pull requests via GitHub Actions and posts results as PR comments  
- [🎛️] **Infinitely Flexible:** Adapt to any project's standards—configure review rules, severity levels, and focus areas, build custom workflows 

## 🎯 Perfect For<a id="-perfect-for"></a>

- Solo developers who want expert-level code review without the wait
- Teams looking to catch issues before human review
- Open source projects maintaining high code quality at scale
- CI/CD pipelines requiring automated quality gates

✨ See [code review in action](https://github.com/Nayjest/Gito/pull/99) ✨

## 🚀 Quickstart<a id="-quickstart"></a>

### 1. Review Pull Requests via GitHub Actions<a id="1-review-pull-requests-via-github-actions"></a>

Create a `.github/workflows/gito-code-review.yml` file:
```yaml
name: "Gito: AI Code Review"
on:
  pull_request:
    types: [opened, synchronize, reopened]
  workflow_dispatch:
    inputs:
      pr_number:
        description: "Pull Request number"
        required: true
jobs:
  review:
    runs-on: ubuntu-latest
    permissions: { contents: read, pull-requests: write } # 'write' for leaving the summary comment
    steps:
    - uses: actions/checkout@v6
      with: { fetch-depth: 0 }
    - name: Set up Python
      uses: actions/setup-python@v6
      with: { python-version: "3.13" }
    - name: Install AI Code Review tool
      run: pip install gito.bot~=3.6
    - name: Run AI code analysis
      env:
        LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
        LLM_API_TYPE: openai
        MODEL: "gpt-5.2"
        GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        PR_NUMBER_FROM_WORKFLOW_DISPATCH: ${{ github.event.inputs.pr_number }}
      run: |
        gito --verbose review
        gito github-comment --token ${{ secrets.GITHUB_TOKEN }}
    - uses: actions/upload-artifact@v6
      with:
        name: ai-code-review-results
        path: |
          code-review-report.md
          code-review-report.json
```

> ⚠️ Make sure to add `LLM_API_KEY` to your repository's GitHub secrets.

💪 Done!  
PRs to your repository will now receive AI code reviews automatically. ✨  
See [GitHub Setup Guide](https://github.com/Nayjest/Gito/blob/main/documentation/github_setup.md) for more details.

### 2. Running Code Analysis Locally<a id="2-running-code-analysis-locally"></a>

#### Initial Local Setup

**Prerequisites:** 
- [Python](https://www.python.org/downloads/) 3.11 / 3.12 / 3.13  
- [Git](https://git-scm.com)

#### Option A: Install via pip (recommended)

**Step 1:** Install [gito.bot](https://github.com/Nayjest/Gito) using [pip](https://en.wikipedia.org/wiki/Pip_(package_manager)).
```bash
pip install gito.bot
```

> **Troubleshooting:**  
> pip may also be available via cli as `pip3` depending on your Python installation.

#### Option B: Windows Standalone Installer

Download the latest Windows installer from [Releases](https://github.com/Nayjest/Gito/releases).

The installer includes:
- Standalone executable (no Python required)
- Automatic PATH configuration
- Start Menu shortcuts
- Easy uninstallation

**Step 2:** Perform initial setup

The following command will perform one-time setup using an interactive wizard.
You will be prompted to enter LLM configuration details (API type, API key, etc).
Configuration will be saved to `~/.gito/.env`.
```bash
gito setup
```

> **Troubleshooting:**  
> On some systems, `gito` command may not become available immediately after installation.  
> Try restarting your terminal or running `python -m gito` instead.


#### Perform your first AI code review locally

**Step 1:** Navigate to your repository root directory.  
**Step 2:** Switch to the branch you want to review.  
**Step 3:** Run the following command:
```bash
gito review
```

> **Note:** This will analyze the current branch against the repository main branch by default.  
> Files that are not staged for commit will be ignored.  
> See `gito --help` for more options.

**Reviewing remote repository**
```bash
gito remote git@github.com:owner/repo.git <FEATURE_BRANCH>..<MAIN_BRANCH>
```
Use interactive help for details:
```bash
gito remote --help
```

## 🔧 Configuration<a id="-configuration"></a>

Gito uses a two-layer configuration model:

| Scope | Location | Purpose |
|-------|----------|---------|
| **Environment** | `~/.gito/.env` or OS environment variables | LLM provider, model, API keys, concurrency |
| **Project** | `<repo>/.gito/config.toml` | Review behavior, prompts, templates, integrations |

> **Note:** Environment configuration defines external resources and credentials — it's machine-specific and never committed to version control. Project configuration defines review behavior and can be shared across your team.

### Environment Configuration

Environment settings control LLM inference, API Keys and apply system-wide.

Gito uses [ai-microcore](https://github.com/Nayjest/ai-microcore) for vendor-agnostic LLM access. All settings are configured via OS environment variables or `.env` files.

**Default location:** `~/.gito/.env`  
*(Created automatically via `gito setup`)*

#### Example
```bash
# ~/.gito/.env
LLM_API_TYPE=openai
LLM_API_KEY=sk-...
LLM_API_BASE=https://api.openai.com/v1/
MODEL=gpt-5.2
MAX_CONCURRENT_TASKS=20
```

For all supported options, see the [ai-microcore configuration guide](https://github.com/Nayjest/ai-microcore?tab=readme-ov-file#%EF%B8%8F-configuring).

#### CI/CD Environments

In CI workflows, configure LLM settings via workflow environment variables. Use your platform's secrets management (GitHub Secrets, GitLab CI Variables) for API keys.


### Project Configuration

Gito supports per-repository customization through a `.gito/config.toml` file placed at the root of your project. This allows you to tailor code review behavior to your specific codebase, coding standards, and workflow requirements.

#### Configuration Inheritance Model

Project settings follow a layered override model:

**Bundled Defaults** ([config.toml](https://github.com/Nayjest/Gito/blob/main/gito/config.toml)) → **Project Config** (`<your-repo>/.gito/config.toml`)

Any values defined in your project's `.gito/config.toml` are merged on top of the built-in defaults. You only need to specify the settings you want to change—everything else falls back to sensible defaults.

#### Common Customizations

- **Review prompts** — Tailor AI instructions, review criteria, and quality thresholds
- **Output templates** — Customize report format for GitHub comments and CLI
- **Post-processing** — Python snippets to filter or transform detected issues
- **Bot behavior** — Mention triggers, retries, comment handling
- **Pipeline integrations** — Jira, Linear, etc.

Explore the bundled [config.toml](https://github.com/Nayjest/Gito/blob/main/gito/config.toml) for the complete list of available options.

#### Example
```toml
# .gito/config.toml
mention_triggers = ["gito", "/check"]
collapse_previous_code_review_comments = true

# Files to provide as context
aux_files = [
    'documentation/command_line_reference.md'
]

exclude_files = [
    'poetry.lock',
]

[prompt_vars]
# Custom instructions injected into the system prompts
awards = ""  # Disable awards
requirements = """
- All public functions must have docstrings.
"""
```

For detailed guidance, see the [📖 Configuration Cookbook](https://github.com/Nayjest/Gito/blob/main/documentation/config_cookbook.md).

## 📚 Guides & Reference<a id="-guides--reference"></a>

For more detailed information, check out these articles:

- [Command Line Reference](https://github.com/Nayjest/Gito/blob/main/documentation/command_line_reference.md)
- [Configuration Cookbook](https://github.com/Nayjest/Gito/blob/main/documentation/config_cookbook.md)
- [GitHub Setup Guide](https://github.com/Nayjest/Gito/blob/main/documentation/github_setup.md)
- Integrations
  - [Linear Integration](https://github.com/Nayjest/Gito/blob/main/documentation/linear_integration.md)
  - [Atlassian Jira Integration](https://github.com/Nayjest/Gito/blob/main/documentation/jira_integration.md)
- [Documentation generation with Gito](https://github.com/Nayjest/Gito/blob/main/documentation/documentation_generation.md)
- [Troubleshooting](https://github.com/Nayjest/Gito/blob/main/documentation/troubleshooting.md)

Or browse all documentation in the [`/documentation`](https://github.com/Nayjest/Gito/tree/main/documentation) directory.

## 🚧 Known Limitations<a id="-known-limitations"></a>

Gito cannot modify files inside `.github/workflows` when reacting to GitHub PR comments (e.g., "Gito fix issue 2").  
This is a GitHub security restriction that prevents workflows from modifying other workflow files using the default `GITHUB_TOKEN`.

While using a Personal Access Token (PAT) with the `workflow` scope would bypass this limitation, it is not recommended as a workaround.
PATs have broader permissions, longer lifespans, and are tied to individual user accounts, making them less secure than the default `GITHUB_TOKEN` for CI/CD pipelines.


## 💻 Development Setup<a id="-development-setup"></a>

Cloning the repository:
```bash
git clone https://github.com/Nayjest/Gito.git
cd Gito
```

Install dependencies:
```bash
make install
```

Format code and check style:
```bash
make black
make cs
```

Run tests:
```bash
pytest
```

## 🤝 Contributing<a id="-contributing"></a>

**Looking for a specific feature or having trouble?**  
Contributions are welcome! ❤️  
See [CONTRIBUTING.md](https://github.com/Nayjest/Gito/blob/main/CONTRIBUTING.md) for details.

## 📝 License<a id="-license"></a>

Licensed under the [MIT License](https://github.com/Nayjest/Gito/blob/main/LICENSE).

© 2025–2026 [Vitalii Stepanenko](mailto:mail@vitaliy.in)
