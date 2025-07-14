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


@app.command(help="Fix an issue from the code review report")
def fix(
    issue_number: int = typer.Argument(..., help="Issue number to fix"),
    report_path: Optional[str] = typer.Option(
        None,
        "--report",
        "-r",
        help="Path to the code review report (default: code-review-report.json)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-d", help="Only print changes without applying them"
    ),
    commit: bool = typer.Option(default=False, help="Commit changes after applying them"),
    push: bool = typer.Option(default=False, help="Push changes to the remote repository"),
) -> list[str]:
    """
    Applies the proposed change for the specified issue number from the code review report.
    """
    # Load the report
    report_path = report_path or JSON_REPORT_FILE_NAME
    try:
        report = Report.load(report_path)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logging.error(f"Failed to load report from {report_path}: {e}")
        raise typer.Exit(code=1)

    # Find the issue by number
    issue: Optional[Issue] = None
    for file_issues in report.issues.values():
        for i in file_issues:
            if i.id == issue_number:
                issue = i
                break
        if issue:
            break

    if not issue:
        logging.error(f"Issue #{issue_number} not found in the report")
        raise typer.Exit(code=1)

    if not issue.affected_lines:
        logging.error(f"Issue #{issue_number} has no affected lines specified")
        raise typer.Exit(code=1)

    if not any(affected_line.proposal for affected_line in issue.affected_lines):
        logging.error(f"Issue #{issue_number} has no proposal for fixing")
        raise typer.Exit(code=1)

    # Apply the fix
    logging.info(f"Fixing issue #{issue_number}: {ui.cyan(issue.title)}")

    for affected_line in issue.affected_lines:
        if not affected_line.proposal:
            continue

        file_path = Path(issue.file)
        if not file_path.exists():
            logging.error(f"File {file_path} not found")
            continue

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except Exception as e:
            logging.error(f"Failed to read file {file_path}: {e}")
            continue

        # Check if line numbers are valid
        if affected_line.start_line < 1 or affected_line.end_line > len(lines):
            logging.error(
                f"Invalid line range: {affected_line.start_line}-{affected_line.end_line} "
                f"(file has {len(lines)} lines)"
            )
            continue

        # Get the affected line content for display
        affected_content = "".join(lines[affected_line.start_line - 1:affected_line.end_line])
        print(f"\nFile: {ui.blue(issue.file)}")
        print(f"Lines: {affected_line.start_line}-{affected_line.end_line}")
        print(f"Current content:\n{ui.red(affected_content)}")
        print(f"Proposed change:\n{ui.green(affected_line.proposal)}")

        if dry_run:
            print(f"{ui.yellow('Dry run')}: Changes not applied")
            continue

        # Apply the change
        proposal_lines = affected_line.proposal.splitlines(keepends=True)
        if not proposal_lines:
            proposal_lines = [""]
        elif not proposal_lines[-1].endswith(("\n", "\r")):
            # Ensure the last line has a newline if the original does
            if (
                affected_line.end_line < len(lines)
                and lines[affected_line.end_line - 1].endswith(("\n", "\r"))
            ):
                proposal_lines[-1] += "\n"

        lines[affected_line.start_line - 1:affected_line.end_line] = proposal_lines

        # Write changes back to the file
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            print(f"{ui.green('Success')}: Changes applied to {file_path}")
        except Exception as e:
            logging.error(f"Failed to write changes to {file_path}: {e}")
            raise typer.Exit(code=1)

    print(f"\n{ui.green('✓')} Issue #{issue_number} fixed successfully")

    changed_files = [file_path.as_posix()]
    if commit:
        commit_changes(
            changed_files,
            commit_message=f"[AI] Fix issue {issue_number}:{issue.title}",
            push=push
        )
    return changed_files


def commit_changes(
    files: list[str],
    repo: git.Repo = None,
    commit_message: str = "fix by AI",
    push: bool = True
) -> None:
    if opened_repo := not repo:
        repo = git.Repo(".")
    for i in files:
        repo.index.add(i)
    repo.index.commit(commit_message)
    if push:
        origin = repo.remotes.origin
        origin.push()
        logging.info(f"Changes pushed to {origin.name}")
    else:
        logging.info("Changes committed but not pushed to remote")
    if opened_repo:
        repo.close()
