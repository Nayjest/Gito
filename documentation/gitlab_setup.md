# <a href="https://github.com/Nayjest/Gito"><img src="https://raw.githubusercontent.com/Nayjest/Gito/main/press-kit/logo/gito-bot-1_64top.png" align="left" width=64 height=50 title="Gito: AI Code Reviewer"></a>GitLab Setup Guide: Integrating Gito with Your Repository

Automate code review for all Merge Requests using AI.
This guide shows how to connect [Gito](https://pypi.org/project/gito.bot/) to a GitLab project for **continuous, automated MR reviews** using the `gito deploy` wizard.

---

## Prerequisites

- **Maintainer** or **Owner** access to your GitLab project.
- An **API key** for your preferred language model provider (e.g., OpenAI, Google Gemini, Anthropic Claude, etc).
- The **`gito` CLI installed locally** (`pip install gito.bot`). See the [README](../README.md#-quickstart) for installation options.

---

## 1. Generate the CI files with `gito deploy`

From the root of your locally cloned repository, run:

```bash
gito deploy
```

> Aliases: `gito init`, `gito connect`, `gito ci`.

The interactive wizard will:

1. Detect that the project is hosted on GitLab.
2. Ask which **language model provider** and **model** to use.
3. Write the CI workflow files (see below).
4. Optionally commit and push them to a dedicated `gito-ci` branch and open a Merge Request for you.
5. Print the exact **CI/CD variables** you still need to add.

It creates two files:

- `.gitlab/ci/gito-code-review.yml` — the Gito review job.
- `.gitlab-ci.yml` — a minimal pipeline that adds a `review` stage and includes the job above. If you already have a `.gitlab-ci.yml`, Gito **merges** the `review` stage and `include` into it instead of overwriting.

The generated `.gitlab/ci/gito-code-review.yml` looks like this:

```yaml
# Gito AI Code Review for GitLab
gito-ai-review:
  stage: review
  image: python:3.13
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_PIPELINE_SOURCE == "web" && $MR_NUMBER  # Manual trigger via "Run pipeline"
      when: manual
  variables:
    GIT_DEPTH: 0  # Full history needed for diff
    LLM_API_TYPE: openai
    LLM_API_KEY: $LLM_API_KEY
    MODEL: gpt-5.2
  script:
    - pip install gito.bot~=4.3
    - gito --verbose review --against="origin/$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
    - gito gitlab-comment --token "$GITLAB_ACCESS_TOKEN" --project-id "$CI_PROJECT_ID" --merge-request-iid "$CI_MERGE_REQUEST_IID"
    - gito -v0 render gitlab_code_quality > gitlab_code_quality_report.json
  artifacts:
    reports:
      codequality: gitlab_code_quality_report.json
```

> **Prefer manual setup?** You can copy the [GitLab workflow templates](https://github.com/Nayjest/Gito/tree/main/gito/tpl/workflows/gitlab) directly instead of running `gito deploy`.

---

## 2. Add CI/CD variables

Go to **Settings → CI/CD → Variables** and add:

| Variable | Value | Notes |
|----------|-------|-------|
| `LLM_API_KEY` | Your LLM provider API key | Name must match `LLM_API_KEY` in the generated job. |
| `GITLAB_ACCESS_TOKEN` | A **Project Access Token** | Scope `api`, role **Reporter** (or higher). Create at **Settings → Access Tokens**. |

A dedicated `GITLAB_ACCESS_TOKEN` is required because the default `$CI_JOB_TOKEN` cannot write Merge Request comments.

**For each variable:**

- ☑ **Mask variable**
- ☐ **Protect variable** — leave unchecked, otherwise Merge Request pipelines won't have access.
- For **public** repositories, enable *Require approval* for fork pipelines (CI/CD settings) to prevent secret leaks. See [GitLab CI/CD variables docs](https://docs.gitlab.com/ci/variables/).

---

## 3. Open a Merge Request

Once the CI files are on your default branch and the variables are set, every Merge Request triggers a review. You'll see:

- An **AI-generated overview comment** on the MR.
- A **GitLab Code Quality report** attached to the pipeline (see below).

To re-run a review, push a new commit or trigger the pipeline manually via **Run pipeline** (set the `MR_NUMBER` variable).

---

## Reporting modes

Gito posts review results to the MR via the `gito gitlab-comment` command. Two modes are available:

### Overview comment (default)

A single comment summarizing the review, with all detected issues listed in the body.

### Inline comments — `--inline`

Add the `--inline` flag to post **each issue as a separate inline comment** anchored to the affected diff lines, while the main comment keeps only the review overview:

```yaml
    - gito gitlab-comment --inline --token "$GITLAB_ACCESS_TOKEN" --project-id "$CI_PROJECT_ID" --merge-request-iid "$CI_MERGE_REQUEST_IID"
```

Inline comments are created as draft notes anchored to the MR diff and published together as a single review. Unlike the Code Quality artifact, **inline comments work on all GitLab tiers**. Issues that cannot be anchored to changed lines are included in the overview comment instead.

---

## Code Quality artifacts

The generated job also renders a [GitLab Code Quality](https://docs.gitlab.com/ci/testing/code_quality/) report:

```yaml
    - gito -v0 render gitlab_code_quality > gitlab_code_quality_report.json
  artifacts:
    reports:
      codequality: gitlab_code_quality_report.json
```

GitLab ingests this artifact and surfaces the detected issues directly in the Merge Request widget and the pipeline's **Code Quality** tab:

<img width="1038" height="212" alt="image" src="https://github.com/user-attachments/assets/f6fcc980-fb59-4674-8196-5aec4e7b81b9" />

> **Note:** The Code Quality MR widget is available on GitLab Premium/Ultimate. On all tiers, the report can still be downloaded as a pipeline artifact, and inline comments (`--inline`) remain available.

---

## Customize Review if Needed

- Create a `.gito/config.toml` file at your repository root to override the [default configuration](https://github.com/Nayjest/Gito/blob/main/gito/config.toml).
- You can adjust prompts, filtering, report templates, issue criteria, and more — see the [Configuration Cookbook](config_cookbook.md).

## Troubleshooting

- **No comment on the MR?** Open the pipeline, check the `gito-ai-review` job logs for errors (missing `LLM_API_KEY`, insufficient `GITLAB_ACCESS_TOKEN` scope/role, etc).
- **`403`/permission errors when posting?** Ensure `GITLAB_ACCESS_TOKEN` is a Project Access Token with `api` scope and at least the **Reporter** role, and that the variable is **not** Protected.
- **Empty diff / nothing reviewed?** Confirm `GIT_DEPTH: 0` is set so the full history is available for the diff.

---

## Additional Resources

- More usage documentation: [README.md](../README.md)
- [Command Line Reference](command_line_reference.md)
- For help or bug reports, [open an issue](https://github.com/Nayjest/Gito/issues)

---

**Enjoy fast, LLM-powered merge request reviews and safer merges! 🚀**
