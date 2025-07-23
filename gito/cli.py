import asyncio
import logging
import sys
import textwrap
import tempfile

import microcore as mc
import typer
from git import Repo

from .core import review, get_diff, filter_diff, answer
from .report_struct import Report
from .constants import HOME_ENV_PATH
from .bootstrap import bootstrap, app
from .utils import no_subcommand, parse_refs_pair

# Import fix command to register it
from .commands import fix, gh_post_review_comment, gh_react_to_comment, repl  # noqa


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


@app.callback(invoke_without_command=True)
def cli(ctx: typer.Context, verbose: bool = typer.Option(default=False)):
    if ctx.invoked_subcommand != "setup":
        bootstrap()
    if verbose:
        mc.logging.LoggingConfig.STRIP_REQUEST_LINES = None


def args_to_target(refs, what, against) -> tuple[str | None, str | None]:
    _what, _against = parse_refs_pair(refs)
    if _what:
        if what:
            raise typer.BadParameter(
                "You cannot specify both 'refs' <WHAT>..<AGAINST> and '--what'. Use one of them."
            )
    else:
        _what = what
    if _against:
        if against:
            raise typer.BadParameter(
                "You cannot specify both 'refs' <WHAT>..<AGAINST> and '--against'. Use one of them."
            )
    else:
        _against = against
    return _what, _against


def arg_refs() -> typer.Argument:
    return typer.Argument(
        default=None,
        help="Git refs to review, [what]..[against] e.g. 'HEAD..HEAD~1'"
    )


def arg_what() -> typer.Option:
    return typer.Option(None, "--what", "-w", help="Git ref to review")


def arg_filters() -> typer.Option:
    return typer.Option(
        "", "--filter", "-f", "--filters",
        help="""
            filter reviewed files by glob / fnmatch pattern(s),
            e.g. 'src/**/*.py', may be comma-separated
            """,
    )


def arg_out() -> typer.Option:
    return typer.Option(
        None,
        "--out", "-o", "--output",
        help="Output folder for the code review report"
    )


def arg_against() -> typer.Option:
    return typer.Option(
        None,
        "--against", "-vs", "--vs",
        help="Git ref to compare against"
    )


@app_no_subcommand.command(name="review", help="Perform code review")
@app.command(name="review", help="Perform code review")
@app.command(name="run", hidden=True)
def cmd_review(
    refs: str = arg_refs(),
    what: str = arg_what(),
    against: str = arg_against(),
    filters: str = arg_filters(),
    merge_base: bool = typer.Option(default=True, help="Use merge base for comparison"),
    out: str = arg_out()
):
    _what, _against = args_to_target(refs, what, against)
    from pprint import pprint
    print("Against value")
    pprint(_against)

    asyncio.run(review(
        what=_what,
        against=_against,
        filters=filters,
        use_merge_base=merge_base,
        out_folder=out,
    ))


@app.command(name="ask", help="Answer questions about codebase changes")
@app.command(name="answer", hidden=True)
@app.command(name="talk", hidden=True)
def cmd_answer(
    question: str = typer.Argument(help="Question to ask about the codebase changes"),
    refs: str = arg_refs(),
    what: str = arg_what(),
    against: str = arg_against(),
    filters: str = arg_filters(),
    merge_base: bool = typer.Option(default=True, help="Use merge base for comparison"),
):
    _what, _against = args_to_target(refs, what, against)
    return answer(
        question=question,
        what=_what,
        against=_against,
        filters=filters,
        use_merge_base=merge_base,
    )


@app.command(help="Configure LLM for local usage interactively")
def setup():
    mc.interactive_setup(HOME_ENV_PATH)


@app.command(name="render")
@app.command(name="report", hidden=True)
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


@app.command(help="Review remote code")
def remote(
    url: str = typer.Argument(..., help="Git repository URL"),
    refs: str = arg_refs(),
    what: str = arg_what(),
    against: str = arg_against(),
    filters: str = arg_filters(),
    merge_base: bool = typer.Option(default=True, help="Use merge base for comparison"),
    out: str = arg_out()
):
    _what, _against = args_to_target(refs, what, against)
    with tempfile.TemporaryDirectory() as temp_dir:
        logging.info(f"Cloning [{mc.ui.green(url)}] to {mc.utils.file_link(temp_dir)} ...")
        repo = Repo.clone_from(url, branch=_what, to_path=temp_dir)
        asyncio.run(review(
            repo=repo,
            what=_what,
            against=_against,
            filters=filters,
            use_merge_base=merge_base,
            out_folder=out or '.',
        ))
        repo.close()


@app.command(help="List files in the diff. Might be useful to check what will be reviewed.")
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
    patch_set = get_diff(repo=repo, what=_what, against=_against, use_merge_base=merge_base)
    patch_set = filter_diff(patch_set, filters)
    print(
        f"Changed files: "
        f"{mc.ui.green(_what or 'INDEX')} vs "
        f"{mc.ui.yellow(_against or repo.remotes.origin.refs.HEAD.reference.name)}"
        f"{' filtered by '+mc.ui.cyan(filters) if filters else ''}"
    )
    repo.close()
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
