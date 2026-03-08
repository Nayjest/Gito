"""
Fix issues from code review report
"""

import json
import logging
from pathlib import Path
from typing import Optional

import git
import typer
from microcore import ui

from ..cli_base import app
from ..constants import JSON_REPORT_FILE_NAME
from ..report_struct import Report, Issue
from ..utils.git import get_cwd_repo_or_fail


@app.command(
    help="Fix issues from the code review report "
    "(latest code review results will be used by default). "
    "If no issue number is provided, attempts to fix all fixable issues."
)
def fix(
    issue_numbers: Optional[list[int]] = typer.Argument(
        None, help="Issue number(s) to fix (separated by space, fixes all if omitted)"
    ),
    report_path: Optional[str] = typer.Option(
        None,
        "--report",
        "-r",
        help="Path to the code review report (default: code-review-report.json)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-d", help="Only print changes without applying them"
    ),
    commit: bool = typer.Option(default=False, help="Commit changes after applying them"),
    push: bool = typer.Option(default=False, help="Push changes to the remote repository"),
    src_path: Optional[str] = typer.Option(
        None,
        "--src-path",
        help="Base path to prepend to file paths in the report (if report paths are relative)",
    ),
) -> list[str]:
    """
    Apply fix proposals from a code review report to the affected source files.

    Changes are applied in reverse line order to preserve line numbering.
    Skips changes where file content has drifted from the reviewed version.

    Args:
        issue_numbers: Issue IDs to fix. If omitted, fixes all fixable issues.
        report_path: Path to the report JSON. Defaults to standard report file.
        dry_run: Preview changes without applying them.
        commit: Commit modified files with auto-generated messages.
        push: Push commits to remote (requires ``commit``).
        src_path: Base path to prepend to file paths in the report.

    Returns:
        List of modified file paths.

    Raises:
        typer.Exit(1): On missing report, unknown issue ID, or write failure.
    """
    if dry_run:
        logging.info("Running in dry-run mode: no changes will be applied")
        commit = False
    if push and not commit:
        logging.warning("Push option ignored because commit is not enabled")

    # Load the report
    report_path = report_path or JSON_REPORT_FILE_NAME
    try:
        report = Report.load(report_path)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error(f"Failed to load report from {report_path}: {e}")
        raise typer.Exit(code=1)

    # Collect issue IDs to fix if not specified
    if not issue_numbers:
        issue_numbers = [i.id for i in report.plain_issues if i.have_fix_proposal()]
        if not issue_numbers:
            logging.error("No fixable issues found in the report")
            raise typer.Exit(code=1)
        logging.info(f"Found {len(issue_numbers)} fixable issue(s): {issue_numbers}")

    # Extract and validate issues to fix
    issues_to_fix: list[Issue] = []
    for iss_id in issue_numbers:
        issue = report.get_issue_by_id(iss_id)
        if not issue:
            logging.error(f"Issue #{iss_id} not found in the report")
            raise typer.Exit(code=1)
        if not issue.have_fix_proposal():
            logging.warning(f"Issue #{iss_id} has no proposal for fixing")
            continue
        issues_to_fix.append(issue)

    changes_by_file: dict[str, list[Issue.AffectedCode]] = {}
    issues_by_file: dict[str, list[Issue]] = {}
    for issue in issues_to_fix:
        for code_block in issue.affected_lines:
            if code_block.proposal is None:
                continue
            changes_by_file.setdefault(issue.file, []).append(code_block)
            if issue.file not in issues_by_file:
                issues_by_file[issue.file] = []
            if issue not in issues_by_file[issue.file]:
                issues_by_file[issue.file].append(issue)

    # Sort changes from last to first to avoid line number shifts when applying multiple changes
    for file_path, changes in changes_by_file.items():
        changes.sort(key=lambda x: x.start_line, reverse=True)

    for file_path, changes in changes_by_file.items():
        full_path = (Path(src_path) / str(file_path)) if src_path else Path(file_path)
        if not full_path.exists():
            logging.error(f"File {file_path} not found, skipping changes")
            continue

        try:
            lines = full_path.read_text(encoding="utf-8").split("\n")
        except Exception as e:
            logging.error(f"Failed to read file {file_path}: {e}")
            continue

        for code_block in changes:
            # Check if line numbers are valid
            if code_block.start_line < 1 or code_block.end_line > len(lines):
                logging.error(
                    f"Invalid line range: {code_block.start_line}-{code_block.end_line} "
                    f"(file has {len(lines)} lines)"
                )
                continue

            actual_block = "\n".join(lines[code_block.start_line - 1 : code_block.end_line])
            reported_block = code_block.raw_code
            if reported_block != actual_block:
                logging.warning(
                    f"Content mismatch in {file_path} "
                    f"lines {code_block.start_line}-{code_block.end_line}, skipping change"
                )
                continue
            print(f"\nFile: {ui.blue(file_path)}")
            print(f"Lines: {code_block.start_line}-{code_block.end_line}")
            print(f"Current content:\n{ui.red(actual_block)}")
            print(f"Proposed change:\n{ui.green(code_block.proposal)}")

            lines[code_block.start_line - 1 : code_block.end_line] = code_block.proposal.split("\n")

        if dry_run:
            print(f"{ui.yellow('Dry run')}: Changes not applied")
            continue

        # Write changes back to the file
        try:
            full_path.write_text("\n".join(lines), encoding="utf-8")
            print(f"{ui.green('Success')}: Changes applied to {file_path}")
        except Exception as e:
            logging.error(f"Failed to write changes to {file_path}: {e}")
            raise typer.Exit(code=1)

    if commit:
        for file_path, issues in issues_by_file.items():

            if len(issues) == 1:
                safe_title = issues[0].title.replace("\n", " ").replace('"', "'").strip()
                commit_msg = f"[AI] Fix issue {issues[0].id}: {safe_title}"
            else:
                issue_list = ", ".join(str(i.id) for i in issues)
                commit_msg = f"[AI] Fix issues {issue_list} from code review"
            is_last = file_path == list(issues_by_file.keys())[-1]
            commit_changes([file_path], commit_message=commit_msg, push=is_last and push)
    return list(issues_by_file.keys())


def commit_changes(
    files: list[str], repo: git.Repo = None, commit_message: str = "fix by AI", push: bool = True
) -> None:
    """
    Commit and optionally push changes to the remote repository.
    Raises typer.Exit on failure.
    """
    if opened_repo := not repo:
        repo = get_cwd_repo_or_fail()
    for i in files:
        repo.index.add(i)
    repo.index.commit(commit_message)
    if push:
        origin = repo.remotes.origin
        push_results = origin.push()
        for push_info in push_results:
            if push_info.flags & (
                git.PushInfo.ERROR | git.PushInfo.REJECTED | git.PushInfo.REMOTE_REJECTED
            ):
                logging.error(f"Push failed: {push_info.summary}")
                raise typer.Exit(code=1)
        logging.info(f"Changes pushed to {origin.name}")
    else:
        logging.info("Changes committed but not pushed to remote")
    if opened_repo:
        repo.close()
