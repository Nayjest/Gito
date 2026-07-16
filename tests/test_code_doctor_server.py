from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from code_doctor_app import server, store


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _make_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _git(path, "config", "user.email", "code-doctor@example.local")
    _git(path, "config", "user.name", "Code Doctor")
    return path


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


def test_run_dir_rejects_path_escape(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        server.run_dir("../outside")
    with pytest.raises(ValueError):
        server.run_dir("/tmp/outside")
    with pytest.raises(ValueError):
        server.run_dir("run/../../outside")


def test_system_health_skips_ollama_probe_when_unauthorized(monkeypatch):
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)

    def fail_urlopen(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("urlopen should not run for unauthenticated health")

    monkeypatch.setattr(server, "urlopen", fail_urlopen)

    health = server.system_health(
        {"ollamaBase": ["http://169.254.169.254"]},
        include_ollama_check=False,
    )

    assert health["ollama"]["skipped"] is True
    assert health["authRequired"] is False


def test_review_options_uses_separate_refs_and_against(tmp_path):
    options = server.review_options(
        {
            "mode": "refs",
            "refs": "feature/refunds",
            "against": "origin/main",
            "filters": "*.py",
        }
    )
    command = server.build_review_command({}, tmp_path / "out", options)

    assert options["refs"] == ""
    assert options["what"] == "feature/refunds"
    assert options["against"] == "origin/main"
    assert command[:6] == [
        server.sys.executable,
        "-m",
        "gito",
        "review",
        "--what",
        "feature/refunds",
    ]
    assert "--against" in command


def test_review_options_rejects_refs_pair_with_against():
    with pytest.raises(ValueError):
        server.review_options({"mode": "refs", "refs": "feature..main", "against": "main"})


def test_preflight_uses_selected_scope_and_filters(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    repo = _make_repo(tmp_path / "repo")
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (repo / "web.js").write_text("console.log('ok')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
    (repo / "web.js").write_text("console.log('changed')\n", encoding="utf-8")

    preflight = server.preflight_review(
        {
            "repoPath": str(repo),
            "mode": "working",
            "filters": "*.py",
        }
    )

    assert preflight["changedFiles"] == ["app.py"]
    assert preflight["scope"]["filters"] == "*.py"


def test_review_detail_generates_ai_application_test_cases(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    repo = _make_repo(tmp_path / "ai-repo")
    files = {
        "app/prompts/support_prompt.py": (
            "SYSTEM_PROMPT = 'Answer with grounded support steps only.'\n"
            "def build_prompt(user_instruction):\n"
            "    return SYSTEM_PROMPT + user_instruction\n"
        ),
        "app/rag/retriever.py": (
            "class Retriever:\n"
            "    def retrieve(self, query):\n"
            "        return vector_store.similarity_search(query)\n"
        ),
        "app/agents/tools.py": (
            "TOOL_SCHEMA = {'name': 'refund_user', 'parameters': {'account_id': 'string'}}\n"
            "def run_agent(tool_call):\n"
            "    return dispatch_tool(tool_call)\n"
        ),
        "app/llm/client.py": (
            "from openai import OpenAI\n"
            "client = OpenAI()\n"
            "def complete(messages):\n"
            "    return client.responses.create(model='gpt-test', input=messages, response_format={'type': 'json_schema'})\n"
        ),
    }
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    run_id = "run-ai-tests"
    server.atomic_write_json(
        server.meta_path(run_id),
        {
            "id": run_id,
            "status": "completed",
            "repo_path": str(repo),
            "changed_files": list(files),
            "test_files": [],
            "stats": {},
        },
    )
    server.atomic_write_json(
        server.report_path(run_id),
        {
            "issues": {
                "app/prompts/support_prompt.py": [
                    {
                        "id": 42,
                        "severity": 2,
                        "confidence": 1,
                        "title": "Prompt builder trusts caller-supplied instruction override.",
                        "details": "A user instruction can override the system prompt.",
                        "tags": ["security", "input-validation"],
                        "affected_lines": [{"start_line": 3}],
                    }
                ]
            }
        },
    )

    detail = server.get_review(run_id)
    plan = detail["test_cases"]
    case_types = {case["type"] for case in plan["cases"]}

    assert plan["ai_app_cases"] >= 5
    assert "security-regression" in case_types
    assert "ai-prompt-injection" in case_types
    assert "ai-rag-grounding" in case_types
    assert "ai-tool-guardrail" in case_types
    assert "ai-schema-contract" in case_types
    assert "ai-model-resilience" in case_types
    assert "coverage-gap" in case_types
    assert any(case["source"] == "finding:42" for case in plan["cases"])


def test_review_test_cases_are_exported_in_json(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-tests-export"
    server.atomic_write_json(
        server.meta_path(run_id),
        {
            "id": run_id,
            "status": "completed",
            "repo_path": str(tmp_path),
            "changed_files": ["ai/client.py"],
            "test_files": [],
            "stats": {},
        },
    )
    server.atomic_write_json(server.report_path(run_id), {"issues": {}})

    body, content_type = server.export_review(run_id, "json")
    exported = server.json.loads(body)

    assert content_type == "application/json; charset=utf-8"
    assert exported["test_cases"]["total"] >= 1
    assert exported["test_cases"]["cases"][0]["type"] == "coverage-gap"


def test_summarize_report_blocks_on_critical_issue(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-block"
    server.atomic_write_json(
        server.report_path(run_id),
        {
            "summary": "Authorization bypass found.",
            "number_of_processed_files": 1,
            "total_issues": 1,
            "issues": {
                "services/refunds.py": [
                    {
                        "id": 1,
                        "severity": 1,
                        "confidence": 1,
                        "title": "Missing ownership check.",
                        "tags": ["security"],
                    }
                ]
            },
        },
    )

    stats = server.summarize_report(run_id)

    assert stats["gate"] == "block"
    # severity 1 (10) × confidence 1 (1.0) × security multiplier (1.25) → 13
    assert stats["risk_score"] == 13
    assert stats["highest_severity"] == 1
    assert stats["severity_counts"]["1"] == 1
    assert stats["lifecycle"]["new"] == 1
    assert stats["lifecycle"]["baselined"] is False


def test_summarize_report_escalates_sensitive_files(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-sensitive"
    server.atomic_write_json(
        server.report_path(run_id),
        {
            "summary": "Secret hygiene issue.",
            "number_of_processed_files": 1,
            "total_issues": 1,
            "issues": {
                ".env.example": [
                    {
                        "id": 1,
                        "severity": 3,
                        "confidence": 1,
                        "title": "Production-looking key committed.",
                        "tags": ["secret-handling"],
                    }
                ]
            },
        },
    )

    stats = server.summarize_report(run_id)

    assert stats["gate"] == "review"
    assert stats["sensitive_files"] == [".env.example"]
    # severity 3 (3) × confidence 1 (1.0) × security multiplier (1.25) → 4
    assert stats["risk_score"] == 4


def test_export_review_csv_quotes_findings(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-export"
    server.atomic_write_json(
        server.meta_path(run_id),
        {"id": run_id, "status": "completed", "stats": {}},
    )
    server.atomic_write_json(
        server.report_path(run_id),
        {
            "issues": {
                "web/src/invite.ts": [
                    {
                        "id": 7,
                        "severity": 2,
                        "confidence": 1,
                        "title": 'Missing await before "send"',
                        "tags": ["async-flow", "bug"],
                    }
                ]
            }
        },
    )

    body, content_type = server.export_review(run_id, "csv")

    assert content_type == "text/csv; charset=utf-8"
    assert '"7","web/src/invite.ts","2","1","Missing await before ""send""","async-flow;bug"' in body
    assert server.read_audit()[-1]["event"] == "review_exported"


def test_summarize_report_tracks_issue_lifecycle(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    repo = "/repos/payments"
    persisting_issue = {
        "id": 1,
        "severity": 2,
        "confidence": 1,
        "title": "Missing ownership check.",
        "tags": ["security"],
    }
    fixed_issue = {
        "id": 2,
        "severity": 3,
        "confidence": 1,
        "title": "Bare except hides failures.",
        "tags": ["bug"],
    }
    brand_new_issue = {
        "id": 3,
        "severity": 2,
        "confidence": 1,
        "title": "Unawaited promise in invite flow.",
        "tags": ["bug"],
    }

    server.atomic_write_json(
        server.meta_path("run-1"),
        {"id": "run-1", "repo_path": repo, "created_at": "2026-07-10T00:00:00Z"},
    )
    server.atomic_write_json(
        server.report_path("run-1"),
        {"issues": {"a.py": [persisting_issue, fixed_issue]}},
    )
    stats_one = server.summarize_report("run-1")
    server.update_meta("run-1", status="completed", stats=stats_one)

    server.atomic_write_json(
        server.meta_path("run-2"),
        {"id": "run-2", "repo_path": repo, "created_at": "2026-07-11T00:00:00Z"},
    )
    server.atomic_write_json(
        server.report_path("run-2"),
        {"issues": {"a.py": [persisting_issue], "b.ts": [brand_new_issue]}},
    )

    stats_two = server.summarize_report("run-2")

    assert stats_one["lifecycle"] == {"new": 2, "recurring": 0, "resolved": 0, "baselined": False}
    assert stats_two["lifecycle"] == {"new": 1, "recurring": 1, "resolved": 1, "baselined": True}


def test_build_generation_command_mirrors_review_scope():
    # working mode with no explicit `against` defaults to HEAD without merge-base
    options = server.review_options({"mode": "working", "filters": "*.py"})
    command = server.build_generation_command(
        "tests", Path("/repo"), Path("/out"), options
    )

    assert command[1:3] == ["-m", "code_doctor_app.generator"]
    assert "--kind" in command and "tests" in command
    assert command[command.index("--against") + 1] == "HEAD"
    assert command[command.index("--filters") + 1] == "*.py"
    # working-mode default targets the working tree without merge-base
    assert "--no-merge-base" in command


def test_start_generation_rejects_unknown_kind(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    try:
        server.start_generation({"kind": "poetry", "repoPath": str(tmp_path)})
    except ValueError as exc:
        assert "generation kind" in str(exc).lower()
    else:
        raise AssertionError("start_generation accepted an unknown kind")


def test_get_review_includes_generation_artifacts(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "tests-run"
    server.atomic_write_json(
        server.meta_path(run_id),
        {"id": run_id, "kind": "tests", "status": "completed"},
    )
    server.atomic_write_json(
        server.tests_json_path(run_id),
        {"kind": "tests", "files": [{"path": "tests/test_x.py", "content": "def test_x(): ..."}]},
    )
    server.atomic_write_json(
        server.pr_draft_json_path(run_id),
        {"kind": "pr", "title": "Fix bug", "body_markdown": "## Summary"},
    )

    detail = server.get_review(run_id)

    assert detail["generated_tests"]["files"][0]["path"] == "tests/test_x.py"
    assert detail["pr_draft"]["title"] == "Fix bug"


def test_overview_excludes_generation_runs_from_gate_metrics(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    server.atomic_write_json(
        server.meta_path("rev-1"),
        {
            "id": "rev-1",
            "status": "completed",
            "created_at": "2026-07-10T00:00:00Z",
            "stats": {"gate": "block", "total_issues": 2, "risk_score": 30},
        },
    )
    server.atomic_write_json(
        server.meta_path("tests-1"),
        {
            "id": "tests-1",
            "kind": "tests",
            "status": "completed",
            "created_at": "2026-07-11T00:00:00Z",
            "stats": {},
        },
    )

    overview = server.overview()

    assert overview["metrics"]["completed"] == 1
    assert overview["metrics"]["avgRisk"] == 30
    assert overview["metrics"]["generations"] == 1


def _report_with_issues() -> dict:
    return {
        "issues": {
            "svc/refunds.py": [
                {
                    "id": 1,
                    "severity": 1,
                    "confidence": 1,
                    "title": "Missing ownership check.",
                    "tags": ["security"],
                },
                {
                    "id": 2,
                    "severity": 3,
                    "confidence": 2,
                    "title": "Speculative style nit.",
                    "tags": ["readability"],
                },
                {
                    "id": 10001,
                    "severity": 2,
                    "confidence": 1,
                    "title": "shell=True usage.",
                    "tags": ["security", "static-analysis"],
                    "source": "static",
                },
            ]
        },
        "total_issues": 3,
    }


def test_apply_verification_quarantines_rejected_and_tags_confirmed():
    report = _report_with_issues()
    counts = server.apply_verification(
        report,
        {
            "verdicts": [
                {"id": 1, "verdict": "confirmed", "reason": "Diff removes the ownership check."},
                {"id": 2, "verdict": "rejected", "reason": "Pure style preference."},
                {"id": 10001, "verdict": "rejected", "reason": "Should be ignored for static."},
            ]
        },
    )

    assert counts == {"confirmed": 1, "rejected": 1, "uncertain": 0}
    remaining = report["issues"]["svc/refunds.py"]
    assert [issue["id"] for issue in remaining] == [1, 10001]  # static finding untouched
    assert remaining[0]["verified"] is True
    assert "verified" in remaining[0]["tags"]
    rejected = report["rejected_issues"]["svc/refunds.py"]
    assert rejected[0]["id"] == 2
    assert rejected[0]["verifier_reason"] == "Pure style preference."
    assert report["total_issues"] == 2


def test_finding_feedback_suppresses_fingerprint_from_risk(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-feedback"
    server.atomic_write_json(server.meta_path(run_id), {"id": run_id, "status": "completed"})
    server.atomic_write_json(server.report_path(run_id), _report_with_issues())

    before = server.summarize_report(run_id)
    result = server.record_finding_feedback(
        {"runId": run_id, "issueId": 1, "action": "dismiss", "reason": "By design here."}
    )
    after = server.summarize_report(run_id)

    assert result["suppressed"] is True
    assert after["suppressed"] == 1
    assert after["risk_score"] < before["risk_score"]
    assert before["gate"] == "block" and after["gate"] != "block"

    server.record_finding_feedback({"runId": run_id, "issueId": 1, "action": "restore"})
    restored = server.summarize_report(run_id)
    assert restored["risk_score"] == before["risk_score"]
    assert restored["suppressed"] == 0


def test_finding_feedback_rejects_unknown_action_and_finding(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-feedback-errors"
    server.atomic_write_json(server.report_path(run_id), _report_with_issues())

    with pytest.raises(ValueError):
        server.record_finding_feedback({"runId": run_id, "issueId": 1, "action": "shrug"})
    with pytest.raises(FileNotFoundError):
        server.record_finding_feedback({"runId": run_id, "issueId": 999, "action": "dismiss"})


def test_publish_run_dry_run_previews_without_posting(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-publish"
    server.atomic_write_json(
        server.meta_path(run_id),
        {"id": run_id, "status": "completed", "model": "gemma4:e4b", "stats": {}},
    )
    server.atomic_write_json(server.report_path(run_id), _report_with_issues())

    result = server.publish_run(
        run_id, {"platform": "github", "repo": "acme/payments", "pr": 12, "dryRun": True}
    )

    assert result["dry_run"] is True
    assert result["target"] == "acme/payments#12"
    assert "Missing ownership check." in result["summary_markdown"]
    assert not server.publish_result_path(run_id).exists()
    assert server.read_audit()[0]["event"] == "review_publish_previewed"

    with pytest.raises(FileNotFoundError):
        server.publish_run("missing-run", {"platform": "github", "repo": "a/b", "pr": 1})


def test_summarize_report_counts_crossfile_issues_and_get_review_exposes_pack(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    run_id = "run-crossfile"
    report = _report_with_issues()
    report["issues"]["svc/refunds.py"].append(
        {
            "id": 20000,
            "severity": 2,
            "confidence": 2,
            "title": "Removed symbol `legacy_charge` is still referenced elsewhere.",
            "tags": ["cross-file", "breaking-change"],
            "source": "crossfile",
        }
    )
    server.atomic_write_json(server.meta_path(run_id), {"id": run_id, "status": "completed"})
    server.atomic_write_json(server.report_path(run_id), report)
    server.atomic_write_json(
        server.context_pack_path(run_id),
        {"generated_at": "2026-07-16T00:00:00Z", "files": {"svc/refunds.py": {"dependents": ["svc/api.py"]}}, "graph_files": 2},
    )

    stats = server.summarize_report(run_id)
    detail = server.get_review(run_id)

    assert stats["cross_file_issues"] == 1
    assert detail["context_pack"]["files"]["svc/refunds.py"]["dependents"] == ["svc/api.py"]


def test_seed_sample_data_populates_enterprise_overview(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)

    meta = server.seed_sample_data()
    overview = server.overview()

    assert meta["id"].startswith("sample-")
    assert overview["metrics"]["repos"] == 1
    assert overview["metrics"]["completed"] == 1
    assert overview["metrics"]["gateCounts"]["block"] == 1
    assert any(item["label"] == "Evidence exports" and item["ready"] for item in overview["readiness"])
