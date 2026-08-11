"""
Regression test for: silent loss of LLM review responses.

Bug: when mc.llm_parallel(..., allow_failures=True) returns None for a file
(meaning JSON parsing or validation failed), the old code only checked
`isinstance(res_or_error, Exception)`. None is not an Exception, so it passed
through, then `if issues` filtered it out — resulting in 0 issues reported with
no warning. The user sees "No issues found" when the model actually returned
issues that failed to parse.

Fix: check for None explicitly and emit a ProcessingWarning.
"""

import os
import tempfile

import git
from unittest.mock import AsyncMock, patch

from gito.bootstrap import bootstrap
from gito.core import review
from gito.report_struct import ReviewTarget


def _make_temp_repo_with_commit():
    """Create a temporary git repo with one file committed on main."""
    tmpdir = tempfile.mkdtemp()
    repo = git.Repo.init(tmpdir, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()

    filepath = os.path.join(tmpdir, "app.py")
    with open(filepath, "w") as f:
        f.write('print("hello")\n')
    repo.index.add(["app.py"])
    repo.index.commit("initial")

    # create a feature branch with an extra commit (so there's a diff to review)
    repo.create_head("feature")
    repo.heads.feature.checkout()
    with open(filepath, "w") as f:
        f.write('print("hello")\nprint("world")\n')
    repo.index.add(["app.py"])
    repo.index.commit("add world")

    return tmpdir, repo


def test_review_surfaces_none_response_as_warning():
    """
    When llm_parallel returns None (JSON parse/validation failure),
    the report must show 0 issues BUT record a processing warning
    indicating the failure — not silently claim nothing is wrong.
    """
    bootstrap()
    tmpdir, repo = _make_temp_repo_with_commit()
    try:
        # Mock llm_parallel to return [None] — simulating JSON parse failure
        mock_llm = AsyncMock(return_value=[None])

        with patch("gito.core.mc.llm_parallel", mock_llm):
            import asyncio

            target = ReviewTarget(
                what="HEAD",
                against="main",
                use_merge_base=False,
                filters="",
            )
            asyncio.run(review(target=target, repo=repo, out_folder=tmpdir))

        # Load the saved report
        report_path = os.path.join(tmpdir, "code-review-report.json")
        assert os.path.exists(report_path), "Report file should be saved"

        import json
        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)

        # Core assertions
        assert data["total_issues"] == 0, "No issues should be registered (model output failed)"
        assert len(data["processing_warnings"]) > 0, (
            "A processing warning MUST be recorded when LLM response is None"
        )

        warning = data["processing_warnings"][0]
        warning_msg = warning["message"].lower()
        assert "app.py" in warning["file"], "Warning should reference the affected file"
        assert any(
            kw in warning_msg for kw in ["json", "parsing", "validation", "validator"]
        ), f"Warning should mention JSON parsing/validation failure, got: {warning['message']}"

    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_review_still_handles_exception_response():
    """
    Sanity check: the existing Exception handling path still works.
    When llm_parallel returns an Exception, a warning should be recorded.
    """
    bootstrap()
    tmpdir, repo = _make_temp_repo_with_commit()
    try:
        mock_llm = AsyncMock(return_value=[RuntimeError("LLM API timeout")])

        with patch("gito.core.mc.llm_parallel", mock_llm):
            import asyncio

            target = ReviewTarget(
                what="HEAD",
                against="main",
                use_merge_base=False,
                filters="",
            )
            asyncio.run(review(target=target, repo=repo, out_folder=tmpdir))

        import json
        report_path = os.path.join(tmpdir, "code-review-report.json")
        with open(report_path, encoding="utf-8") as f:
            data = json.load(f)

        assert data["total_issues"] == 0
        assert len(data["processing_warnings"]) == 1
        assert "app.py" in data["processing_warnings"][0]["file"]
        assert "LLM API timeout" in data["processing_warnings"][0]["message"]

    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
