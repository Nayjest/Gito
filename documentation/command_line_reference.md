# <a href="https://github.com/Nayjest/Gito"><img src="https://raw.githubusercontent.com/Nayjest/Gito/main/press-kit/logo/gito-bot-1_64top.png" align="left" width=64 height=50 title="Gito: AI Code Reviewer"></a>Gito CLI Reference

Gito is an open-source AI code reviewer that works with any language model provider.
It detects issues in GitHub pull requests or local codebase changes—instantly, reliably, and without vendor lock-in.

**Usage**:

```console
$ gito [OPTIONS] COMMAND [ARGS]...
```

**Options**:

* `-v, --verbosity INTEGER`: Set verbosity level. Supported values: 0-3. Default: 1.
[ 0 ]: no additional output, 
[ 1 ]: normal mode, shows warnings, shortened LLM requests and logging.INFO
[ 2 ]: verbose mode, show full LLM requests
[ 3 ]: very verbose mode, also debug information
* `--verbose / --no-verbose`: --verbose is equivalent to -v2, 
--no-verbose is equivalent to -v0. 
(!) Can&#x27;t be used together with -v or --verbosity.
* `--help`: Show this message and exit.

**Commands**:

* `github-comment`: Leave a GitHub PR comment with the review.
* `post-gitlab-comment`: Leaves a comment with the review on the...
* `gitlab-comment`: Leave a GitLab MR comment with the review.
* `linear-comment`: Post a comment with specified text to the...
* `fix`: Fix issues from the code review report...
* `react-to-comment`: Handles direct agent instructions from...
* `repl`: Python REPL with core functionality loaded...
* `ci`: Deploy Gito to repository&#x27;s CI pipeline...
* `connect`: Deploy Gito to repository&#x27;s CI pipeline...
* `init`: Deploy Gito to repository&#x27;s CI pipeline...
* `deploy`: Create and deploy Gito workflows to your...
* `version`: Show Gito version.
* `run`
* `review`: Perform a code review of the target...
* `answer`
* `ask`: Answer questions about the target codebase...
* `setup`: Configure LLM for local usage interactively.
* `render`
* `report`: Render and display code review report.
* `files`: List files in the changeset.

## `gito github-comment`

Leave a GitHub PR comment with the review.

**Usage**:

```console
$ gito github-comment [OPTIONS]
```

**Options**:

* `--md-report-file TEXT`
* `--pr INTEGER`
* `--gh-repo TEXT`: owner/repo
* `--token TEXT`: GitHub token (or set GITHUB_TOKEN env var)
* `--help`: Show this message and exit.

## `gito post-gitlab-comment`

Leaves a comment with the review on the current GitLab merge request.

With --inline, posts an overview comment first, then each issue as a separate
inline comment anchored to the affected diff lines (published together as one review).

Requires a Project Access Token with &#x27;api&#x27; scope.
The default $CI_JOB_TOKEN does not have write access to merge requests.

Examples:
  ```bash
  gito gitlab-comment --token $GITLAB_ACCESS_TOKEN
  --project-id $CI_PROJECT_ID --merge-request-iid $CI_MERGE_REQUEST_IID
  ```

**Usage**:

```console
$ gito post-gitlab-comment [OPTIONS]
```

**Options**:

* `--md-report-file TEXT`: Path to the markdown review file. Gito&#x27;s standard report file will be used by default.
* `--json-report-file TEXT`: Path to the JSON review report (used with --inline). Gito&#x27;s standard report file will be used by default.
* `--project-id TEXT`: GitLab project ID (numeric) or URL-encoded path
* `--merge-request-iid INTEGER`: Merge Request IID
* `--token TEXT`: GitLab access token (or set GITLAB_ACCESS_TOKEN env var)
* `--base-url TEXT`: GitLab base URL (default env GITLAB_BASE_URL or https://gitlab.com)
* `--inline`: Post each issue as a separate inline comment on the MR diff (works on all GitLab tiers); the main comment then contains only the review overview.
* `--help`: Show this message and exit.

## `gito gitlab-comment`

Leave a GitLab MR comment with the review.

**Usage**:

```console
$ gito gitlab-comment [OPTIONS]
```

**Options**:

* `--md-report-file TEXT`: Path to the markdown review file. Gito&#x27;s standard report file will be used by default.
* `--json-report-file TEXT`: Path to the JSON review report (used with --inline). Gito&#x27;s standard report file will be used by default.
* `--project-id TEXT`: GitLab project ID (numeric) or URL-encoded path
* `--merge-request-iid INTEGER`: Merge Request IID
* `--token TEXT`: GitLab access token (or set GITLAB_ACCESS_TOKEN env var)
* `--base-url TEXT`: GitLab base URL (default env GITLAB_BASE_URL or https://gitlab.com)
* `--inline`: Post each issue as a separate inline comment on the MR diff (works on all GitLab tiers); the main comment then contains only the review overview.
* `--help`: Show this message and exit.

## `gito linear-comment`

Post a comment with specified text to the associated Linear issue.

**Usage**:

```console
$ gito linear-comment [OPTIONS] [TEXT]
```

**Arguments**:

* `[TEXT]`: Comment text (supports Markdown). Use &#x27;-&#x27; to read from stdin.

**Options**:

* `-k, --issue-key TEXT`: Linear issue key (if not provided, will be resolved from the current repo branch)
* `--help`: Show this message and exit.

## `gito fix`

Fix issues from the code review report (latest code review results will be used by default). If no issue number is provided, attempts to fix all fixable issues.

**Usage**:

```console
$ gito fix [OPTIONS] [ISSUE_NUMBERS]...
```

**Arguments**:

* `[ISSUE_NUMBERS]...`: Issue number(s) to fix (separated by space, fixes all if omitted)

**Options**:

* `-r, --report TEXT`: Path to the code review report (default: code-review-report.json)
* `-d, --dry-run`: Only print changes without applying them
* `--commit / --no-commit`: Commit changes after applying them  [default: no-commit]
* `--push / --no-push`: Push changes to the remote repository  [default: no-push]
* `--src-path TEXT`: Base path to prepend to file paths in the report (if report paths are relative)
* `--help`: Show this message and exit.

## `gito react-to-comment`

Handles direct agent instructions from pull request comments.

Note: Not for local usage. Designed for execution within GitHub Actions workflows.

Fetches the PR comment by ID, parses agent directives, and executes the requested
actions automatically to enable seamless code review workflow integration.

**Usage**:

```console
$ gito react-to-comment [OPTIONS] COMMENT_ID
```

**Arguments**:

* `COMMENT_ID`: [required]

**Options**:

* `-t, --gh-token, --token, --github-token TEXT`: GitHub token for authentication
* `-d, --dry-run`: Only print changes without applying them
* `--help`: Show this message and exit.

## `gito repl`

Python REPL with core functionality loaded for quick testing/debugging and exploration.

**Usage**:

```console
$ gito repl [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `gito ci`

Deploy Gito to repository&#x27;s CI pipeline for automatic code reviews.

**Usage**:

```console
$ gito ci [OPTIONS]
```

**Options**:

* `--api-type [openai|azure|anyscale|deep_infra|anthropic|google_vertex_ai|google_ai_studio|google|function|transformers|cli|none]`: LLM API type (interactive if omitted)
* `--commit / --no-commit`: Commit and push changes
* `--rewrite / --no-rewrite`: Overwrite existing configuration  [default: no-rewrite]
* `--to-branch TEXT`: Branch name for new PR containing Gito CI workflows  [default: gito-ci]
* `--token TEXT`: GitHub token (or set GITHUB_TOKEN env var)
* `--model TEXT`: Language model to use (interactive if omitted; &quot;default&quot; selects the recommended model)
* `--help`: Show this message and exit.

## `gito connect`

Deploy Gito to repository&#x27;s CI pipeline for automatic code reviews.

**Usage**:

```console
$ gito connect [OPTIONS]
```

**Options**:

* `--api-type [openai|azure|anyscale|deep_infra|anthropic|google_vertex_ai|google_ai_studio|google|function|transformers|cli|none]`: LLM API type (interactive if omitted)
* `--commit / --no-commit`: Commit and push changes
* `--rewrite / --no-rewrite`: Overwrite existing configuration  [default: no-rewrite]
* `--to-branch TEXT`: Branch name for new PR containing Gito CI workflows  [default: gito-ci]
* `--token TEXT`: GitHub token (or set GITHUB_TOKEN env var)
* `--model TEXT`: Language model to use (interactive if omitted; &quot;default&quot; selects the recommended model)
* `--help`: Show this message and exit.

## `gito init`

Deploy Gito to repository&#x27;s CI pipeline for automatic code reviews.

**Usage**:

```console
$ gito init [OPTIONS]
```

**Options**:

* `--api-type [openai|azure|anyscale|deep_infra|anthropic|google_vertex_ai|google_ai_studio|google|function|transformers|cli|none]`: LLM API type (interactive if omitted)
* `--commit / --no-commit`: Commit and push changes
* `--rewrite / --no-rewrite`: Overwrite existing configuration  [default: no-rewrite]
* `--to-branch TEXT`: Branch name for new PR containing Gito CI workflows  [default: gito-ci]
* `--token TEXT`: GitHub token (or set GITHUB_TOKEN env var)
* `--model TEXT`: Language model to use (interactive if omitted; &quot;default&quot; selects the recommended model)
* `--help`: Show this message and exit.

## `gito deploy`

Create and deploy Gito workflows to your CI pipeline for automatic code reviews.
Run this command from your repository root.
aliases: init, connect, ci

**Usage**:

```console
$ gito deploy [OPTIONS]
```

**Options**:

* `--api-type [openai|azure|anyscale|deep_infra|anthropic|google_vertex_ai|google_ai_studio|google|function|transformers|cli|none]`: LLM API type (interactive if omitted)
* `--commit / --no-commit`: Commit and push changes
* `--rewrite / --no-rewrite`: Overwrite existing configuration  [default: no-rewrite]
* `--to-branch TEXT`: Branch name for new PR containing Gito CI workflows  [default: gito-ci]
* `--token TEXT`: GitHub token (or set GITHUB_TOKEN env var)
* `--model TEXT`: Language model to use (interactive if omitted; &quot;default&quot; selects the recommended model)
* `--help`: Show this message and exit.

## `gito version`

Show Gito version.

**Usage**:

```console
$ gito version [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `gito run`

**Usage**:

```console
$ gito run [OPTIONS] [REFS]
```

**Arguments**:

* `[REFS]`: Git refs to review, .. (e.g., &#x27;HEAD..HEAD~1&#x27;). If omitted, the current index (including added but not committed files) will be compared to the repository’s main branch.

**Options**:

* `-w, --what TEXT`: Git ref to review
* `-vs, --against, --vs TEXT`: Git ref to compare against
* `-f, --filter, --filters TEXT`: filter reviewed files by glob / fnmatch pattern(s),
e.g. &#x27;src/**/*.py&#x27;, may be comma-separated
* `--merge-base / --no-merge-base`: Use merge base for comparison  [default: merge-base]
* `--url TEXT`: Git repository URL
* `--path TEXT`: Git repository path
* `--post-comment / --no-post-comment`: Post review comment to git platform (GitHub, GitLab, etc.)  [default: no-post-comment]
* `--pr INTEGER`: GitHub Pull Request number or GitLab Merge Request ID to post the comment to
(for local usage together with --post-comment,
in the GitHub/GitLab actions PR/MR is resolved from the environment)
* `-o, --out, --output TEXT`: Output folder for the code review report
* `--all / --no-all`: Review whole codebase  [default: no-all]
* `--help`: Show this message and exit.

## `gito review`

Perform a code review of the target codebase changes.

**Usage**:

```console
$ gito review [OPTIONS] [REFS]
```

**Arguments**:

* `[REFS]`: Git refs to review, .. (e.g., &#x27;HEAD..HEAD~1&#x27;). If omitted, the current index (including added but not committed files) will be compared to the repository’s main branch.

**Options**:

* `-w, --what TEXT`: Git ref to review
* `-vs, --against, --vs TEXT`: Git ref to compare against
* `-f, --filter, --filters TEXT`: filter reviewed files by glob / fnmatch pattern(s),
e.g. &#x27;src/**/*.py&#x27;, may be comma-separated
* `--merge-base / --no-merge-base`: Use merge base for comparison  [default: merge-base]
* `--url TEXT`: Git repository URL
* `--path TEXT`: Git repository path
* `--post-comment / --no-post-comment`: Post review comment to git platform (GitHub, GitLab, etc.)  [default: no-post-comment]
* `--pr INTEGER`: GitHub Pull Request number or GitLab Merge Request ID to post the comment to
(for local usage together with --post-comment,
in the GitHub/GitLab actions PR/MR is resolved from the environment)
* `-o, --out, --output TEXT`: Output folder for the code review report
* `--all / --no-all`: Review whole codebase  [default: no-all]
* `--help`: Show this message and exit.

## `gito answer`

**Usage**:

```console
$ gito answer [OPTIONS] QUESTION [REFS]
```

**Arguments**:

* `QUESTION`: Question to ask about the codebase changes  [required]
* `[REFS]`: Git refs to review, .. (e.g., &#x27;HEAD..HEAD~1&#x27;). If omitted, the current index (including added but not committed files) will be compared to the repository’s main branch.

**Options**:

* `-w, --what TEXT`: Git ref to review
* `-vs, --against, --vs TEXT`: Git ref to compare against
* `-f, --filter, --filters TEXT`: filter reviewed files by glob / fnmatch pattern(s),
e.g. &#x27;src/**/*.py&#x27;, may be comma-separated
* `--merge-base / --no-merge-base`: Use merge base for comparison  [default: merge-base]
* `--use-pipeline / --no-use-pipeline`: [default: use-pipeline]
* `--post-to TEXT`: Post answer to ... Supported values: linear
* `--pr INTEGER`: GitHub Pull Request number
* `--aux-files TEXT`: Auxiliary files that might be helpful
* `--save-to TEXT`: Write the answer to the target file
* `--all / --no-all`: Review whole codebase  [default: no-all]
* `--help`: Show this message and exit.

## `gito ask`

Answer questions about the target codebase changes.

**Usage**:

```console
$ gito ask [OPTIONS] QUESTION [REFS]
```

**Arguments**:

* `QUESTION`: Question to ask about the codebase changes  [required]
* `[REFS]`: Git refs to review, .. (e.g., &#x27;HEAD..HEAD~1&#x27;). If omitted, the current index (including added but not committed files) will be compared to the repository’s main branch.

**Options**:

* `-w, --what TEXT`: Git ref to review
* `-vs, --against, --vs TEXT`: Git ref to compare against
* `-f, --filter, --filters TEXT`: filter reviewed files by glob / fnmatch pattern(s),
e.g. &#x27;src/**/*.py&#x27;, may be comma-separated
* `--merge-base / --no-merge-base`: Use merge base for comparison  [default: merge-base]
* `--use-pipeline / --no-use-pipeline`: [default: use-pipeline]
* `--post-to TEXT`: Post answer to ... Supported values: linear
* `--pr INTEGER`: GitHub Pull Request number
* `--aux-files TEXT`: Auxiliary files that might be helpful
* `--save-to TEXT`: Write the answer to the target file
* `--all / --no-all`: Review whole codebase  [default: no-all]
* `--help`: Show this message and exit.

## `gito setup`

Configure LLM for local usage interactively.

**Usage**:

```console
$ gito setup [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

## `gito render`

**Usage**:

```console
$ gito render [OPTIONS] [FORMAT]
```

**Arguments**:

* `[FORMAT]`: Report format: md (Markdown), cli (terminal)  [default: cli]

**Options**:

* `--src, --source TEXT`: Source file (json) to load the report from
* `--help`: Show this message and exit.

## `gito report`

Render and display code review report.

**Usage**:

```console
$ gito report [OPTIONS] [FORMAT]
```

**Arguments**:

* `[FORMAT]`: Report format: md (Markdown), cli (terminal)  [default: cli]

**Options**:

* `--src, --source TEXT`: Source file (json) to load the report from
* `--help`: Show this message and exit.

## `gito files`

List files in the changeset. 
Might be useful to check what will be reviewed if run `gito review` with current CLI arguments and options.

**Usage**:

```console
$ gito files [OPTIONS] [REFS]
```

**Arguments**:

* `[REFS]`: Git refs to review, .. (e.g., &#x27;HEAD..HEAD~1&#x27;). If omitted, the current index (including added but not committed files) will be compared to the repository’s main branch.

**Options**:

* `-w, --what TEXT`: Git ref to review
* `-vs, --against, --vs TEXT`: Git ref to compare against
* `-f, --filter, --filters TEXT`: filter reviewed files by glob / fnmatch pattern(s),
e.g. &#x27;src/**/*.py&#x27;, may be comma-separated
* `--merge-base / --no-merge-base`: Use merge base for comparison  [default: merge-base]
* `--diff / --no-diff`: Show diff content  [default: no-diff]
* `--all / --no-all`: Review whole codebase  [default: no-all]
* `--help`: Show this message and exit.
