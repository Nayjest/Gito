"""Test the fix command functionality."""

import tempfile

import git
import microcore as mc
import pytest

from gito.commands.fix import fix
from gito.core import provide_affected_code_blocks
from gito.report_struct import Report


@pytest.fixture()
def temp_repo() -> tuple[mc.file_storage.Storage, git.Repo]:

    with tempfile.TemporaryDirectory() as tmpdir:
        yield mc.storage(tmpdir), git.Repo.init(tmpdir)


def test_fix_all_issues(temp_repo: tuple[mc.file_storage.Storage, git.Repo]):
    """Test that fix command can fix all issues when no issue_number is provided."""
    storage, repo = temp_repo

    storage.write(file1 := "test1.py", "line1\nline2\nline3\nline4\nline5\n")
    storage.write(file2 := "test2.py", "lineA\nlineB\nlineC")
    storage.write(file3 := "test3.py", "")
    storage.write(file4 := "test4.py", "no-nl")
    issues = {
        file1: [
            {
                "title": "Issue 1",
                # proposal adds 2 new lines after line 2
                "affected_lines": [{"start_line": 2, "end_line": 2, "proposal": "fixed_line2\n\n"}],
            },
            {
                "title": "Issue 1.2",
                "affected_lines": [
                    {"start_line": 3, "end_line": 4, "proposal": "fixed_line3\nfixed_line4"}
                ],
            },
        ],
        file2: [
            {
                "title": "Issue 2",
                "affected_lines": [{"start_line": 1, "end_line": 1, "proposal": "fixed_lineA"}],
            }
        ],
        file3: [
            {
                "title": "Empty file",
                "affected_lines": [{"start_line": 1, "end_line": 1, "proposal": "#header"}],
            },
        ],
        file4: [
            {
                "title": "No newline at end of file",
                "affected_lines": [{"start_line": 1, "end_line": 1, "proposal": "no-nl\n"}],
            },
        ],
    }

    provide_affected_code_blocks(issues, repo)
    # Create a report with multiple fixable issues
    report = Report(summary="Test report")
    report.register_issues(issues)
    report.save(storage.path / "report.json")

    # Test fixing all issues (no issue_number provided)
    changed_files = fix(
        None,
        # [1, 2],  # Fix all issues
        report_path=storage.path / "report.json",
        dry_run=False,
        commit=False,
        push=False,
        src_path=storage.path,
    )

    # Verify both files were changed
    assert len(changed_files) == 4
    assert file1 in changed_files
    assert file2 in changed_files

    # Verify the fixes were applied
    assert storage.read(file1) == "line1\nfixed_line2\n\n\nfixed_line3\nfixed_line4\nline5\n"
    assert storage.read(file2) == "fixed_lineA\nlineB\nlineC"
    assert storage.read(file3) == "#header"
    assert storage.read(file4) == "no-nl\n"
