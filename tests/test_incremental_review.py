"""Incremental re-review / verdict reuse (release plan Item 2)."""
from __future__ import annotations

from pathlib import Path

from code_doctor_app import server, store


def _isolated_store(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "code-doctor"
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "RUNS_DIR", data_dir / "runs")
    monkeypatch.setattr(server, "AUDIT_LOG", data_dir / "audit.jsonl")
    monkeypatch.setattr(server, "REPOS_FILE", data_dir / "repos.json")
    monkeypatch.setattr(server, "POLICIES_FILE", data_dir / "policies.json")
    monkeypatch.setattr(server, "SUPPRESSIONS_FILE", data_dir / "suppressions.json")
    monkeypatch.setattr(store, "DB_PATH", data_dir / "code-doctor.db")
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)


CONFIRMED_ISSUE = {
    "id": 1,
    "file": "svc/refunds.py",
    "severity": 1,
    "confidence": 1,
    "title": "Missing ownership check.",
    "tags": ["security", "verified"],
    "verified": True,
    "verifier_reason": "Diff removes the ownership check.",
    "affected_lines": [{"start_line": 42, "end_line": 49}],
}
REJECTED_ISSUE = {
    "id": 2,
    "file": "svc/refunds.py",
    "severity": 3,
    "confidence": 2,
    "title": "Speculative style nit.",
    "tags": ["readability"],
    "verifier_reason": "Pure style preference.",
}
STATIC_ISSUE = {
    "id": 10001,
    "file": "svc/refunds.py",
    "severity": 2,
    "confidence": 1,
    "title": "shell=True usage.",
    "tags": ["security", "static-analysis"],
    "source": "static",
}


def _fresh_issue(issue: dict) -> dict:
    """The same finding as it would appear in a brand-new report (no verdict)."""
    fresh = {
        key: value
        for key, value in issue.items()
        if key not in {"verified", "verifier_reason"}
    }
    fresh["tags"] = [tag for tag in fresh.get("tags", []) if tag != "verified"]
    return fresh


def _write_prior_run(repo: str) -> None:
    server.atomic_write_json(
        server.meta_path("run-old"),
        {
            "id": "run-old",
            "status": "completed",
            "repo_path": repo,
            "created_at": "2026-07-10T00:00:00Z",
            "stats": {},
        },
    )
    server.atomic_write_json(
        server.report_path("run-old"),
        {
            "issues": {"svc/refunds.py": [CONFIRMED_ISSUE, STATIC_ISSUE]},
            "rejected_issues": {"svc/refunds.py": [REJECTED_ISSUE]},
            "total_issues": 2,
        },
    )


def _write_new_run(run_id: str, repo: str) -> None:
    server.atomic_write_json(
        server.meta_path(run_id),
        {
            "id": run_id,
            "status": "running",
            "repo_path": repo,
            "created_at": "2026-07-16T00:00:00Z",
        },
    )
    server.atomic_write_json(
        server.report_path(run_id),
        {
            "issues": {
                "svc/refunds.py": [
                    _fresh_issue(CONFIRMED_ISSUE),
                    _fresh_issue(REJECTED_ISSUE),
                    dict(STATIC_ISSUE),
                    {
                        "id": 3,
                        "file": "svc/refunds.py",
                        "severity": 2,
                        "confidence": 1,
                        "title": "Brand new finding.",
                        "tags": ["bug"],
                    },
                ]
            },
            "total_issues": 4,
        },
    )


def test_previous_run_context_maps_confirmed_and_rejected(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    repo = "/repos/payments"
    _write_prior_run(repo)

    context = server.previous_run_context("run-new", repo, "2026-07-16T00:00:00Z")

    assert context["run_id"] == "run-old"
    verdicts = set()
    for record in context["verdicts"].values():
        verdicts.add(record["verdict"])
    assert verdicts == {"confirmed", "rejected"}
    # Verdicts are keyed on the undecorated fingerprint: the prior confirmed
    # issue carries a "verified" tag its fresh twin will not.
    confirmed_fp = server.issue_fingerprint(_fresh_issue(CONFIRMED_ISSUE))
    assert confirmed_fp == server.verdict_fingerprint(CONFIRMED_ISSUE)
    assert context["verdicts"][confirmed_fp]["reason"] == "Diff removes the ownership check."
    # Static findings never contribute verdicts.
    assert server.verdict_fingerprint(STATIC_ISSUE) not in context["verdicts"]


def test_previous_run_context_empty_without_prior_run(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    assert server.previous_run_context("run-new", "/repos/none", "2026-07-16T00:00:00Z") == {}


def test_maybe_reuse_verdicts_carries_confirmed_and_rejected(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    repo = "/repos/payments"
    _write_prior_run(repo)
    _write_new_run("run-new", repo)

    counts, skip_ids = server.maybe_reuse_verdicts("run-new", Path(repo), {})

    assert counts == {"confirmed": 1, "rejected": 1}
    assert skip_ids == [1]

    report = server.read_json(server.report_path("run-new"))
    remaining = report["issues"]["svc/refunds.py"]
    assert [issue["id"] for issue in remaining] == [1, 10001, 3]
    confirmed = remaining[0]
    assert confirmed["verified"] is True
    assert confirmed["carried_from"] == "run-old"
    assert "verified" in confirmed["tags"]
    rejected = report["rejected_issues"]["svc/refunds.py"][0]
    assert rejected["id"] == 2
    assert rejected["carried_from"] == "run-old"
    assert report["total_issues"] == 3

    meta = server.read_json(server.meta_path("run-new"))
    assert meta["reused_verdicts"] == {"confirmed": 1, "rejected": 1, "from_run": "run-old"}
    assert any(event["event"] == "verdicts_reused" for event in server.read_audit())

    stats = server.summarize_report("run-new")
    assert stats["verification"]["carried"] == 2


def test_maybe_reuse_verdicts_disabled_by_flag(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    repo = "/repos/payments"
    _write_prior_run(repo)
    _write_new_run("run-new", repo)

    counts, skip_ids = server.maybe_reuse_verdicts("run-new", Path(repo), {"reuseVerdicts": False})

    assert counts == {"confirmed": 0, "rejected": 0}
    assert skip_ids == []
    report = server.read_json(server.report_path("run-new"))
    assert report["total_issues"] == 4
    assert "rejected_issues" not in report


def test_maybe_reuse_verdicts_noop_without_prior_run(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    repo = "/repos/payments"
    _write_new_run("run-new", repo)

    counts, skip_ids = server.maybe_reuse_verdicts("run-new", Path(repo), {})

    assert counts == {"confirmed": 0, "rejected": 0}
    assert skip_ids == []


def test_reuse_matches_reworded_finding_by_position(monkeypatch, tmp_path):
    """LLMs reword findings between runs; same file + overlapping lines +
    shared tag still carries the verdict when the match is unambiguous."""
    _isolated_store(monkeypatch, tmp_path)
    repo = "/repos/payments"
    _write_prior_run(repo)
    reworded = {
        "id": 5,
        "file": "svc/refunds.py",
        "severity": 1,
        "confidence": 1,
        "title": "Broken access control: ownership never validated.",  # new wording
        "tags": ["authorization", "security"],
        "affected_lines": [{"start_line": 43, "end_line": 47}],
    }
    server.atomic_write_json(
        server.meta_path("run-new"),
        {"id": "run-new", "status": "running", "repo_path": repo,
         "created_at": "2026-07-16T00:00:00Z"},
    )
    server.atomic_write_json(
        server.report_path("run-new"),
        {"issues": {"svc/refunds.py": [reworded]}, "total_issues": 1},
    )

    counts, skip_ids = server.maybe_reuse_verdicts("run-new", Path(repo), {})

    assert counts["confirmed"] == 1
    assert skip_ids == [5]
    carried = server.read_json(server.report_path("run-new"))["issues"]["svc/refunds.py"][0]
    assert carried["verified"] is True
    assert carried["carried_from"] == "run-old"


def test_positional_match_requires_unambiguous_overlap_and_shared_tag():
    base = {
        "id": 9,
        "tags": ["security"],
        "affected_lines": [{"start_line": 40, "end_line": 45}],
    }
    hit = {"verdict": "confirmed", "reason": "r", "span": (42, 49), "tags": {"security"}}
    assert server.positional_verdict_match(base, [hit]) == {"verdict": "confirmed", "reason": "r"}
    # No tag overlap → no carry.
    assert server.positional_verdict_match(base, [hit | {"tags": {"style"}}]) is None
    # Disjoint lines (beyond the ±3 slack) → no carry.
    assert server.positional_verdict_match(base, [hit | {"span": (90, 95)}]) is None
    # Two overlapping candidates → ambiguous → no carry.
    assert server.positional_verdict_match(base, [hit, dict(hit)]) is None
    # Findings without line info never fuzzy-match.
    assert server.positional_verdict_match({"id": 9, "tags": ["security"]}, [hit]) is None


def test_run_verification_skips_subprocess_when_all_verdicts_reused(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-skip"
    server.atomic_write_json(server.meta_path(run_id), {"id": run_id})
    server.atomic_write_json(
        server.report_path(run_id),
        {
            "issues": {
                "svc/refunds.py": [
                    {"id": 1, "title": "Carried", "severity": 2, "verified": True, "carried_from": "run-old"},
                    dict(STATIC_ISSUE),
                ]
            }
        },
    )

    def fail_popen(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("verifier subprocess must not start when everything is carried")

    monkeypatch.setattr(server.subprocess, "Popen", fail_popen)

    counts = server.run_verification(run_id, tmp_path, {}, {}, skip_ids=[1])

    assert counts == {}
    assert any(
        event["event"] == "verification_skipped" and event.get("reason") == "all verdicts reused"
        for event in server.read_audit()
    )


def test_issues_for_verification_respects_skip_ids():
    from code_doctor_app import generator

    report = {
        "issues": {
            "a.py": [
                {"id": 1, "title": "Carried", "severity": 2},
                {"id": 3, "title": "Fresh", "severity": 2},
                {"id": 10001, "title": "Static", "severity": 2, "source": "static"},
            ]
        }
    }
    findings = generator.issues_for_verification(report, skip_ids=[1])
    assert [finding["id"] for finding in findings] == [3]
