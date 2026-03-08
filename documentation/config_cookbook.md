
# <a href="https://github.com/Dishank422/CRACK"><img src="https://raw.githubusercontent.com/Dishank422/CRACK/main/press-kit/logo/CRACK-bot-1_64top.png" align="left" width=64 height=50 title="CRACK: AI Code Reviewer"></a>Configuration Cookbook

This document provides a comprehensive guide on how to configure and tune [CRACK AI Code Reviewer](https://pypi.org/project/CRACK.bot/) using project-specific configuration.

## Project-specific configuration
When run locally or via GitHub/GitLab actions, [CRACK](https://pypi.org/project/CRACK.bot/)
looks for `.CRACK/config.toml` file in the root directory of reviewed project / repository.  
Then it merges project-specific configuration (if exists) with the
[bundled configuration defaults](https://github.com/Dishank422/CRACK/blob/main/CRACK/config.toml).  
This allows you to customize the behavior of the AI code review tool according to your project's needs.


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
https://github.com/Dishank422/CRACK/blob/main/CRACK/config.toml

## How do I configure advanced language model settings?

CRACK uses the [ai-microcore](https://github.com/Dishank422/ai-microcore) package for vendor-agnostic LLM inference.  
All language model settings are configured via OS environment variables or `.env` files.

**Default configuration file:** `~/.CRACK/.env`  
*(Created automatically via `CRACK setup`)*

This file is used for local setups and applies across all projects unless overridden.

In the CI workflows you typically can define OS environment variables in the workflow file itself. For passing API keys securely, use GitHub / GitLab secrets functionality within the workflow.

For the full list of supported configuration options, see:
- ai-microcore configuration guide:  
  https://github.com/Dishank422/ai-microcore?tab=readme-ov-file#%EF%B8%8F-configuring
- ai-microcore configuration schema:  
  https://github.com/Dishank422/ai-microcore/blob/main/microcore/configuration.py
