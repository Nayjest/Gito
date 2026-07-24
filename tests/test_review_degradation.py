"""Graceful degradation (v5.1): a whole-repo review whose LLM pass times out or
errors is surfaced as a completed *degraded* run when the deterministic engines
still produced findings — instead of a bare 'failed' that discards them."""
from __future__ import annotations

from codepulse_app.server import classify_review_outcome


def test_clean_llm_run_is_completed_not_degraded():
    assert classify_review_outcome(0, False, True, True) == ("completed", False, "")
    assert classify_review_outcome(0, False, True, False) == ("completed", False, "")


def test_llm_timeout_with_findings_degrades_but_completes():
    status, degraded, reason = classify_review_outcome(-1, True, True, True)
    assert status == "completed"
    assert degraded is True
    assert reason == "llm-timeout"


def test_llm_error_with_findings_degrades_but_completes():
    status, degraded, reason = classify_review_outcome(2, False, True, True)
    assert (status, degraded, reason) == ("completed", True, "llm-error")


def test_no_usable_result_is_failed():
    # Timed out, no findings, but a report exists → nothing usable → failed.
    assert classify_review_outcome(-1, True, True, False) == ("failed", False, "")
    # No report at all → failed.
    assert classify_review_outcome(-1, True, False, False) == ("failed", False, "")
    assert classify_review_outcome(2, False, False, False) == ("failed", False, "")
