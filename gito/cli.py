import os
import asyncio
import logging
import sys
import textwrap

import microcore as mc
import typer
from git import Repo
from gito.constants import REFS_VALUE_ALL

from .core import review, get_diff, filter_diff, answer
from .cli_base import (
    app,
    args_to_target,
    arg_refs,
    arg_what,
    arg_filters,
    arg_out,
    arg_against,
    get_repo_context,
)
from .report_struct import Report
from .constants import HOME_ENV_PATH, GITHUB_MD_REPORT_FILE_NAME
from .bootstrap import bootstrap
from .utils import no_subcommand, extract_gh_owner_repo, remove_html_comments
from .gh_api import resolve_gh_token

# Import fix command to register it
from .commands import fix, gh_react_to_comment, repl, deploy, version  # noqa
from .commands.gh_post_review_comment import post_github_cr_comment
from .commands.linear_comment import linear_comment

app_no_subcommand = typer.Typer(pretty_exceptions_show_locals=False)


def main():
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # Help subcommand alias: if 'help' appears as first non-option arg, replace it with '--help'
    if len(sys.argv) > 1 and sys.argv[1] == "help":
        sys.argv = [sys.argv[0]] + sys.argv[2:] + ["--help"]

    if no_subcommand(app):
        bootstrap()
        app_no_subcommand()
    else:
        app()


@app.callback(
    invoke_without_command=True,
    help="\bGito is an open-source AI code reviewer that works with any language model provider."
         "\nIt detects issues in GitHub pull requests or local codebase changes"
         "—instantly, reliably, and without vendor lock-in."
)
def cli(
    ctx: typer.Context,
    verbosity: int = typer.Option(
        None,
        '--verbosity', '-v',
        show_default=False,
        help="\b"
             "Set verbosity level. Supported values: 0-3. Default: 1."
             "\n [ 0 ]: no additional output, "
             "\n [ 1 ]: normal mode, shows warnings, shortened LLM requests and logging.INFO"
             "\n [ 2 ]: verbose mode, show full LLM requests"
             "\n [ 3 ]: very verbose mode, also debug information"
    ),
    verbose: bool = typer.Option(
        default=None,
        help="\b"
             "--verbose is equivalent to -v2, "
             "\n--no-verbose is equivalent to -v0. "
             "\n(!) Can't be used together with -v or --verbosity."
    ),
):
    if verbose is not None and verbosity is not None:
        raise typer.BadParameter(
            "Please specify either --verbose or --verbosity, not both."
        )
    if verbose is not None:
        verbosity = 2 if verbose else 0
    if verbosity is None:
        verbosity = 1

    if ctx.invoked_subcommand != "setup":
        bootstrap(verbosity)


@app_no_subcommand.command(name="review", help="Perform code review")
@app.command(name="review", help="Perform a code review of the target codebase changes.")
@app.command(name="run", hidden=True)
def cmd_review(
    refs: str = arg_refs(),
    what: str = arg_what(),
    against: str = arg_against(),
    filters: str = arg_filters(),
    merge_base: bool = typer.Option(default=True, help="Use merge base for comparison"),
    url: str = typer.Option("", "--url", help="Git repository URL"),
    path: str = typer.Option("", "--path", help="Git repository path"),
    post_comment: bool = typer.Option(default=False, help="Post review comment to GitHub"),
    pr: int = typer.Option(
        default=None,
        help=textwrap.dedent("""\n
        GitHub Pull Request number to post the comment to
        (for local usage together with --post-comment,
        in the github actions PR is resolved from the environment)
        """)
    ),
    out: str = arg_out(),
    all: bool = typer.Option(default=False, help="Review all codebase"),
):
    if all:
        if refs and refs != REFS_VALUE_ALL:
            raise typer.BadParameter(
                "The --all option overrides the refs argument. "
                "Please remove the refs argument if you want to review all codebase."
            )
        refs = REFS_VALUE_ALL
        merge_base = False
    _what, _against = args_to_target(refs, what, against)
    pr = pr or os.getenv("PR_NUMBER_FROM_WORKFLOW_DISPATCH")
    with get_repo_context(url, _what) as (repo, out_folder):
        asyncio.run(review(
            repo=repo,
            what=_what,
            against=_against,
            filters=filters,
            use_merge_base=merge_base,
            out_folder=out or out_folder,
            pr=pr,
        ))
        if post_comment:
            try:
                owner, repo_name = extract_gh_owner_repo(repo)
            except ValueError as e:
                logging.error(
                    "Error posting comment:\n"
                    "Could not extract GitHub owner and repository name from the local repository."
                )
                raise typer.Exit(code=1) from e
            post_github_cr_comment(
                md_report_file=os.path.join(out or out_folder, GITHUB_MD_REPORT_FILE_NAME),
                pr=pr,
                gh_repo=f"{owner}/{repo_name}",
                token=resolve_gh_token()
            )


@app.command(name="ask", help="Answer questions about the target codebase changes.")
@app.command(name="answer", hidden=True)
@app.command(name="talk", hidden=True)
def cmd_answer(
    question: str = typer.Argument(help="Question to ask about the codebase changes"),
    refs: str = arg_refs(),
    what: str = arg_what(),
    against: str = arg_against(),
    filters: str = arg_filters(),
    merge_base: bool = typer.Option(default=True, help="Use merge base for comparison"),
    use_pipeline: bool = typer.Option(default=True),
    post_to: str = typer.Option(
        help="Post answer to ... Supported values: linear",
        default=None,
        show_default=False
    ),
    pr: int = typer.Option(
        default=None,
        help="GitHub Pull Request number"
    ),
    aux_files: list[str] = typer.Option(
        default=None,
        help="Auxiliary files that might be helpful"
    )
):
    _what, _against = args_to_target(refs, what, against)
    pr = pr or os.getenv("PR_NUMBER_FROM_WORKFLOW_DISPATCH")
    if str(question).startswith("tpl:"):
        prompt_file = str(question)[4:]
        question = ""
    else:
        prompt_file = None
    out = answer(
        question=question,
        what=_what,
        against=_against,
        filters=filters,
        use_merge_base=merge_base,
        prompt_file=prompt_file,
        use_pipeline=use_pipeline,
        pr=pr,
        aux_files=aux_files,
    )
    if post_to == 'linear':
        logging.info("Posting answer to Linear...")
        linear_comment(remove_html_comments(out))
    return out


@app.command(help="Configure LLM for local usage interactively.")
def setup():
    mc.interactive_setup(HOME_ENV_PATH)


@app.command(name="report", help="Render and display code review report.")
@app.command(name="render", hidden=True)
def render(
    format: str = typer.Argument(default=Report.Format.CLI),
    source: str = typer.Option(
        "",
        "--src",
        "--source",
        help="Source file (json) to load the report from"
    )
):
    Report.load(file_name=source).to_cli(report_format=format)


@app.command(
    help="\bList files in the changeset. "
         "\nMight be useful to check what will be reviewed if run `gito review` "
         "with current CLI arguments and options."
)
def files(
    refs: str = arg_refs(),
    what: str = arg_what(),
    against: str = arg_against(),
    filters: str = arg_filters(),
    merge_base: bool = typer.Option(default=True, help="Use merge base for comparison"),
    diff: bool = typer.Option(default=False, help="Show diff content")
):
    _what, _against = args_to_target(refs, what, against)
    repo = Repo(".")
    try:
        patch_set = get_diff(repo=repo, what=_what, against=_against, use_merge_base=merge_base)
        patch_set = filter_diff(patch_set, filters)
        print(
            f"Changed files: "
            f"{mc.ui.green(_what or 'INDEX')} vs "
            f"{mc.ui.yellow(_against or repo.remotes.origin.refs.HEAD.reference.name)}"
            f"{' filtered by ' + mc.ui.cyan(filters) if filters else ''} --> "
            f"{mc.ui.cyan(len(patch_set or []))} file(s)."
        )

        for patch in patch_set:
            if patch.is_added_file:
                color = mc.ui.green
            elif patch.is_removed_file:
                color = mc.ui.red
            else:
                color = mc.ui.blue
            print(f"- {color(patch.path)}")
            if diff:
                print(mc.ui.gray(textwrap.indent(str(patch), "  ")))
    finally:
        repo.close()
