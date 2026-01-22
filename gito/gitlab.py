import hashlib
import json
from typing import Literal

from gito.report_struct import Issue

from .report_struct import Report

GitLabSeverity = Literal["info", "minor", "major", "critical", "blocker"]

SEVERITY_MAP: dict[int, GitLabSeverity] = {
    5: "minor",  # from: Suggestion
    4: "minor",  # from: Trivial
    3: "minor",  # from: Minor
    2: "major",  # from: Major
    1: "critical",  # from: Critical
    0: "blocker",  # not applicable
}


def _generate_fingerprint(*parts: str) -> str:
    """Generate a stable fingerprint hash from the given parts."""
    content = "\0".join(parts)
    return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()


def _map_severity(issue: Issue) -> GitLabSeverity:
    """Map issue severity/confidence to GitLab severity levels."""
    if hasattr(issue, "severity") and issue.severity in SEVERITY_MAP:
        return SEVERITY_MAP[issue.severity]
    return "minor"


def convert_to_gitlab_code_quality_report(report: Report, **kwargs) -> str:
    """
    Convert a code review report into GitLab Code Quality report format.

    Args:
        report: The code review report to convert.

    Returns:
        JSON string in GitLab Code Quality format.

    See:
        https://docs.gitlab.com/ee/ci/testing/code_quality.html#implement-a-custom-tool
    """
    gitlab_report = [
        {
            "description": (
                issue.title
                + (f":\n{issue.details}" if issue.details else "")
                + (f"\n[Tags]: {', '.join(issue.tags)}" if issue.tags else "")
                + (
                    "\n[Proposed change]:"
                    f"\n```{line.syntax_hint}\n{line.proposal.strip()}\n```"
                    if line.proposal else ""
                )
            ),
            "fingerprint": _generate_fingerprint(
                file_path,
                str(line.start_line),
                str(line.end_line),
                issue.title,
            ),
            "severity": _map_severity(issue),
            "location": {
                "path": file_path,
                "lines": {
                    "begin": line.start_line,
                    "end": line.end_line,
                },
            },
        }
        for file_path, issues in report.issues.items()
        for issue in issues
        for line in issue.affected_lines
    ]

    return json.dumps(gitlab_report, indent=2)
