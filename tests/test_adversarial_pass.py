"""Adversarial re-review pass: a second LLM read that hunts for missed defects."""
from __future__ import annotations

from codepulse_app import generator, server


# --- request gating -----------------------------------------------------

def test_adversarial_requested_auto_on_in_deep_scan():
    assert server.adversarial_pass_requested({"deepScan": True})


def test_adversarial_explicit_toggle_overrides():
    # Explicit flag wins over the deep-scan default in both directions.
    assert server.adversarial_pass_requested({"adversarialPass": True})
    assert not server.adversarial_pass_requested({"deepScan": True, "adversarialPass": False})


def test_adversarial_off_by_default():
    assert not server.adversarial_pass_requested({})


# --- generator: prompt + normalize -------------------------------------

def test_existing_findings_digest_lists_all():
    report = {"issues": {"a.py": [
        {"id": 1, "title": "SQLi", "affected_lines": [{"start_line": 4}]},
        {"id": 10000, "title": "debug", "source": "static", "affected_lines": [{"start_line": 9}]},
    ]}}
    digest = generator.existing_findings_digest(report)
    assert {d["title"] for d in digest} == {"SQLi", "debug"}
    assert digest[0]["line"] == 4


def test_build_adversarial_prompt_includes_existing_titles():
    prompt = generator.build_adversarial_prompt(
        "diff", {"a.py": "code"}, [{"file": "a.py", "line": 4, "title": "SQLi"}]
    )
    assert "do not repeat" in prompt.lower()
    assert "SQLi" in prompt


def test_normalize_adversarial_validates_and_clamps():
    parsed = {"findings": [
        {"file": "a.py", "start_line": 12, "title": "Race on shared counter",
         "details": "two requests...", "severity": 9, "confidence": 0, "tags": ["bug"]},
        {"file": "", "start_line": 3, "title": "no file"},         # dropped
        {"file": "b.py", "title": "no line"},                       # dropped
    ]}
    out = generator.normalize_adversarial(parsed)
    assert len(out["findings"]) == 1
    f = out["findings"][0]
    assert f["severity"] == 4 and f["confidence"] == 1      # clamped into range
    assert f["end_line"] == f["start_line"]


def test_normalize_adversarial_empty_is_valid():
    # "found nothing new" is an honest, valid outcome.
    assert generator.normalize_adversarial({"findings": []}) == {"findings": []}


# --- server: merge new findings ----------------------------------------

def test_merge_adversarial_adds_novel_findings():
    report = {"issues": {"a.py": [
        {"id": 1, "title": "existing", "affected_lines": [{"start_line": 4, "end_line": 4}]}
    ]}, "total_issues": 1}
    added = server.merge_adversarial_into_report(report, [
        {"file": "a.py", "start_line": 20, "end_line": 20, "title": "missed race",
         "details": "d", "severity": 2, "confidence": 2, "tags": ["bug"]},
    ])
    assert added == 1
    new = [i for i in report["issues"]["a.py"] if i["id"] >= server.ADVERSARIAL_ISSUE_ID_BASE]
    assert len(new) == 1
    assert new[0]["pass"] == "adversarial" and new[0]["source"] == "adversarial"
    assert "adversarial" not in new[0]["tags"]     # marker stays out of fingerprint tags
    assert report["total_issues"] == 2


def test_merge_adversarial_corroborates_on_overlap():
    report = {"issues": {"a.py": [
        {"id": 1, "title": "existing", "affected_lines": [{"start_line": 4, "end_line": 4}]}
    ]}, "total_issues": 1}
    added = server.merge_adversarial_into_report(report, [
        {"file": "a.py", "start_line": 4, "end_line": 4, "title": "same line",
         "details": "d", "severity": 2, "confidence": 2, "tags": []},
    ])
    assert added == 0  # overlapping line -> corroboration, not a duplicate card
    corr = report["issues"]["a.py"][0]["corroborated_by"]
    assert corr and corr[0]["rule"] == "adversarial-review"


def test_merge_adversarial_id_continues_past_existing_adversarial():
    report = {"issues": {"a.py": [
        {"id": server.ADVERSARIAL_ISSUE_ID_BASE, "title": "prior adv",
         "affected_lines": [{"start_line": 1, "end_line": 1}]}
    ]}, "total_issues": 1}
    server.merge_adversarial_into_report(report, [
        {"file": "a.py", "start_line": 30, "end_line": 30, "title": "new", "tags": []},
    ])
    ids = sorted(i["id"] for i in report["issues"]["a.py"])
    assert ids == [server.ADVERSARIAL_ISSUE_ID_BASE, server.ADVERSARIAL_ISSUE_ID_BASE + 1]
