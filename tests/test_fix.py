"""Test the fix command functionality."""
import tempfile
from pathlib import Path
from unittest.mock import patch

from gito.commands.fix import fix
from gito.report_struct import Report, Issue


def test_fix_all_issues():
    """Test that fix command can fix all issues when no issue_number is provided."""
    # Create a temporary report file with multiple fixable issues
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # Create test files
        test_file1 = tmpdir_path / "test1.py"
        test_file1.write_text("line1\nline2\nline3\n")
        
        test_file2 = tmpdir_path / "test2.py"
        test_file2.write_text("lineA\nlineB\nlineC\n")
        
        # Create a report with multiple fixable issues
        report = Report(summary="Test report")
        report.register_issue("test1.py", {
            "title": "Issue 1",
            "details": "Test issue 1",
            "severity": 1,
            "confidence": 1,
            "affected_lines": [{
                "start_line": 2,
                "end_line": 2,
                "proposal": "fixed_line2\n"
            }]
        })
        report.register_issue("test2.py", {
            "title": "Issue 2",
            "details": "Test issue 2",
            "severity": 1,
            "confidence": 1,
            "affected_lines": [{
                "start_line": 1,
                "end_line": 1,
                "proposal": "fixed_lineA\n"
            }]
        })
        
        # Update file paths to point to temporary files
        report.issues["test1.py"][0].file = str(test_file1)
        report.issues["test2.py"][0].file = str(test_file2)
        
        # Save report
        report_path = tmpdir_path / "report.json"
        report.save(str(report_path))
        
        # Test fixing all issues (no issue_number provided)
        with patch('gito.commands.fix.commit_changes'):  # Don't actually commit
            changed_files = fix(
                issue_number=None,  # Fix all issues
                report_path=str(report_path),
                dry_run=False,
                commit=False,
                push=False
            )
        
        # Verify both files were changed
        assert len(changed_files) == 2
        assert str(test_file1) in changed_files or test_file1.as_posix() in changed_files
        assert str(test_file2) in changed_files or test_file2.as_posix() in changed_files
        
        # Verify the fixes were applied
        assert test_file1.read_text() == "line1\nfixed_line2\nline3\n"
        assert test_file2.read_text() == "fixed_lineA\nlineB\nlineC\n"


def test_fix_single_issue():
    """Test that fix command can fix a single issue when issue_number is provided."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        
        # Create test file
        test_file = tmpdir_path / "test.py"
        test_file.write_text("line1\nline2\nline3\n")
        
        # Create a report with a fixable issue
        report = Report(summary="Test report")
        report.register_issue("test.py", {
            "title": "Issue 1",
            "details": "Test issue",
            "severity": 1,
            "confidence": 1,
            "affected_lines": [{
                "start_line": 2,
                "end_line": 2,
                "proposal": "fixed_line2\n"
            }]
        })
        
        # Update file path to point to temporary file
        report.issues["test.py"][0].file = str(test_file)
        
        # Save report
        report_path = tmpdir_path / "report.json"
        report.save(str(report_path))
        
        # Test fixing a specific issue
        with patch('gito.commands.fix.commit_changes'):
            changed_files = fix(
                issue_number=1,
                report_path=str(report_path),
                dry_run=False,
                commit=False,
                push=False
            )
        
        # Verify the file was changed
        assert len(changed_files) == 1
        assert str(test_file) in changed_files or test_file.as_posix() in changed_files
        
        # Verify the fix was applied
        assert test_file.read_text() == "line1\nfixed_line2\nline3\n"
