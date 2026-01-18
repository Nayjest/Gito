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

* `fix`: Fix an issue from the code review report...
* `react-to-comment`: Handles direct agent instructions from...
* `repl`: Python REPL with core functionality loaded...
* `init`
* `deploy`: Create and configure Gito GitHub Actions...
* `version`: Show Gito version.
* `github-comment`: Leave a GitHub PR comment with the review.
* `linear-comment`: Post a comment with specified text to the...
* `run`
* `review`: Perform a code review of the target...
* `answer`
* `ask`: Answer questions about the target codebase...
* `setup`: Configure LLM for local usage interactively.
* `render`
* `report`: Render and display code review report.
* `files`: List files in the changeset.

## `gito fix`

Fix an issue from the code review report (latest code review results will be used by default)

**Usage**:

```console
$ gito fix [OPTIONS] ISSUE_NUMBER
```

**Arguments**:

* `ISSUE_NUMBER`: Issue number to fix  [required]

**Options**:

* `-r, --report TEXT`: Path to the code review report (default: code-review-report.json)
* `-d, --dry-run`: Only print changes without applying them
* `--commit / --no-commit`: Commit changes after applying them  [default: no-commit]
* `--push / --no-push`: Push changes to the remote repository  [default: no-push]
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

## `gito init`

**Usage**:

```console
$ gito init [OPTIONS]
```

**Options**:

* `--api-type [openai|azure|anyscale|deep_infra|anthropic|google_vertex_ai|google_ai_studio|function|transformers|none]`
* `--commit / --no-commit`
* `--rewrite / --no-rewrite`: [default: no-rewrite]
* `--to-branch TEXT`: Branch name for new PR containing with Gito workflows commit  [default: gito_deploy]
* `--token TEXT`: GitHub token (or set GITHUB_TOKEN env var)
* `--help`: Show this message and exit.

## `gito deploy`

Create and configure Gito GitHub Actions for current repository.
aliases: init

**Usage**:

```console
$ gito deploy [OPTIONS]
```

**Options**:

* `--api-type [openai|azure|anyscale|deep_infra|anthropic|google_vertex_ai|google_ai_studio|function|transformers|none]`
* `--commit / --no-commit`
* `--rewrite / --no-rewrite`: [default: no-rewrite]
* `--to-branch TEXT`: Branch name for new PR containing with Gito workflows commit  [default: gito_deploy]
* `--token TEXT`: GitHub token (or set GITHUB_TOKEN env var)
* `--help`: Show this message and exit.

## `gito version`

Show Gito version.

**Usage**:

```console
$ gito version [OPTIONS]
```

**Options**:

* `--help`: Show this message and exit.

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

## `gito linear-comment`

Post a comment with specified text to the associated Linear issue.

**Usage**:

```console
$ gito linear-comment [OPTIONS] [TEXT] [REFS]
```

**Arguments**:

* `[TEXT]`
* `[REFS]`: Git refs to review, .. (e.g., &#x27;HEAD..HEAD~1&#x27;). If omitted, the current index (including added but not committed files) will be compared to the repository’s main branch.

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
* `--post-comment / --no-post-comment`: Post review comment to GitHub  [default: no-post-comment]
* `--pr INTEGER`: GitHub Pull Request number to post the comment to
(for local usage together with --post-comment,
in the github actions PR is resolved from the environment)
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
* `--post-comment / --no-post-comment`: Post review comment to GitHub  [default: no-post-comment]
* `--pr INTEGER`: GitHub Pull Request number to post the comment to
(for local usage together with --post-comment,
in the github actions PR is resolved from the environment)
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

* `[FORMAT]`: [default: cli]

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

* `[FORMAT]`: [default: cli]

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
* `--help`: Show this message and exit.
