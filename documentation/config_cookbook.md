
# <a href="https://github.com/Nayjest/Gito"><img src="https://raw.githubusercontent.com/Nayjest/Gito/main/press-kit/logo/gito-bot-1_64top.png" align="left" width=64 height=50 title="Gito: AI Code Reviewer"></a>Configuration Cookbook

This document provides a comprehensive guide on how to configure and tune [Gito AI Code Reviewer](https://pypi.org/project/gito.bot/) using project-specific configuration.

## Project-specific configuration
When run locally or via GitHub/GitLab actions, [Gito](https://pypi.org/project/gito.bot/)
loads the [bundled configuration defaults](https://github.com/Nayjest/Gito/blob/main/gito/config.toml),
then `~/.gito/config.toml`, then `.gito/config.toml` in the root directory of the reviewed repository.
An explicit `--project-config` file is merged last and must exist.
Missing global and repository files are skipped.
This allows you to customize the behavior of the AI code review tool according to your project's needs.

## How to set personal defaults for every repository?

Create `~/.gito/config.toml` with only the settings you want to override:

```toml
# ~/.gito/config.toml
retries = 5

[prompt_vars]
summary_requirements = "Keep the review summary concise."

[pipeline_steps.jira]
enabled = false
```

Repository settings take precedence over these personal defaults. For example,
`retries = 2` in `<repo>/.gito/config.toml` overrides the global value without
discarding the global summary instruction or Jira setting.

`prompt_vars` merges by key, and `pipeline_steps` merges by step name and field,
so setting `enabled = false` keeps an inherited step's `call` intact.
Other fields, including lists such as `exclude_files`, are replaced rather than appended.
Both configuration files support UTF-8, with or without a BOM.
Keep LLM provider settings and credentials in `~/.gito/.env` or environment variables.

## How to get a project configuration file to start from?
```bash
gito populate-project-config
```
It copies the bundled defaults into `<repo>/.gito/config.toml`
(use `--force` to overwrite an existing one).

Keep in mind that such a copy pins **all** settings to the current Gito version,
so improved prompts and templates from future releases will not reach your project.
Prefer keeping only the options you actually override, and let the rest fall back to the defaults.

## How to use a shared configuration file for multiple projects?
Use the `--project-config` (`-c`) option to point Gito at a configuration file
outside the reviewed repository. Its settings override both `~/.gito/config.toml`
and the repository's `.gito/config.toml`, retaining settings the override file does not define:
```bash
gito --project-config ~/.gito/nestjs-review-config.toml review
```


## How to add custom code review rule?
```toml
[prompt_vars]
requirements = """
- Issue descriptions should be written on Ukrainian language
  (Опис виявлених проблем має бути Українською мовою)
"""
# this instruction affects only summary text generation, not the issue detection itself
summary_requirements = """
- Rate the code quality of introduced changes on a scale from 1 to 100, where 1 is the worst and 100 is the best.
"""
```

## Where can I see all available configuration options?
Check **bundled configuration defaults** here:  
https://github.com/Nayjest/Gito/blob/main/gito/config.toml

## How do I configure advanced language model settings?

Gito uses the [ai-microcore](https://github.com/Nayjest/ai-microcore) package for vendor-agnostic LLM inference.  
All language model settings are configured via OS environment variables or `.env` files.

**Default configuration file:** `~/.gito/.env`  
*(Created automatically via `gito setup`)*

This file is used for local setups and applies across all projects unless overridden.

In the CI workflows you typically can define OS environment variables in the workflow file itself. For passing API keys securely, use GitHub / GitLab secrets functionality within the workflow.

For the full list of supported configuration options, see:
- ai-microcore configuration guide:  
  https://github.com/Nayjest/ai-microcore?tab=readme-ov-file#%EF%B8%8F-configuring
- ai-microcore configuration schema:  
  https://github.com/Nayjest/ai-microcore/blob/main/microcore/configuration.py
