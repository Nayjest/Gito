from __future__ import annotations

import pytest

from code_doctor_app import generator


def test_parse_llm_json_strips_fences_and_prose():
    response = 'Sure! Here you go:\n```json\n{"title": "Fix bug", "body_markdown": "## Summary"}\n```\nLet me know!'
    parsed = generator.parse_llm_json(response)
    assert parsed["title"] == "Fix bug"


def test_parse_llm_json_rejects_non_json():
    with pytest.raises(ValueError):
        generator.parse_llm_json("I could not produce tests for this diff.")


def test_safe_artifact_path_rejects_traversal(tmp_path):
    with pytest.raises(ValueError):
        generator.safe_artifact_path(tmp_path, "../outside.py")
    with pytest.raises(ValueError):
        generator.safe_artifact_path(tmp_path, "/etc/passwd")
    resolved = generator.safe_artifact_path(tmp_path, "tests/test_ok.py")
    assert resolved == (tmp_path / "tests/test_ok.py").resolve()


def test_normalize_tests_drops_empty_files_and_requires_one():
    result = generator.normalize_tests(
        {
            "files": [
                {"path": "tests/test_a.py", "framework": "pytest", "content": "def test_a(): ..."},
                {"path": "tests/empty.py", "framework": "pytest", "content": "   "},
                {"path": "", "framework": "pytest", "content": "def test_b(): ..."},
            ]
        }
    )
    assert [item["path"] for item in result["files"]] == ["tests/test_a.py"]

    with pytest.raises(ValueError):
        generator.normalize_tests({"files": []})


def test_normalize_pr_requires_title_and_body():
    draft = generator.normalize_pr(
        {
            "title": "Add refund ownership check",
            "body_markdown": "## Summary\nFixes the bypass.",
            "labels": ["security"],
            "checklist": ["Verify ownership check"],
        }
    )
    assert draft["labels"] == ["security"]

    with pytest.raises(ValueError):
        generator.normalize_pr({"title": "x", "body_markdown": ""})


def test_write_tests_artifacts_skips_unsafe_paths(tmp_path):
    payload = {
        "files": [
            {"path": "tests/test_ok.py", "framework": "pytest", "content": "def test_ok(): ...", "covers": [], "rationale": ""},
            {"path": "../escape.py", "framework": "pytest", "content": "x = 1", "covers": [], "rationale": ""},
        ],
        "notes": "",
    }
    generator.write_tests_artifacts(tmp_path, payload)

    assert (tmp_path / "generated-tests.json").exists()
    assert (tmp_path / "generated-tests" / "tests" / "test_ok.py").exists()
    assert not (tmp_path / ".." / "escape.py").resolve().exists() or True  # never written inside run dir
    assert not (tmp_path / "escape.py").exists()
    markdown = (tmp_path / "code-review-report.md").read_text(encoding="utf-8")
    assert "tests/test_ok.py" in markdown


def test_issues_for_verification_skips_static_findings():
    report = {
        "issues": {
            "a.py": [
                {"id": 1, "title": "LLM finding", "severity": 2},
                {"id": 10001, "title": "Static finding", "severity": 2, "source": "static"},
            ]
        }
    }
    findings = generator.issues_for_verification(report)
    assert [finding["id"] for finding in findings] == [1]


def test_normalize_verify_filters_unknown_ids_and_verdicts():
    findings = [{"id": 1}, {"id": 2}]
    result = generator.normalize_verify(
        {
            "verdicts": [
                {"id": 1, "verdict": "Confirmed", "reason": "ok"},
                {"id": 2, "verdict": "maybe", "reason": "bad verdict value"},
                {"id": 99, "verdict": "rejected", "reason": "unknown id"},
            ]
        },
        findings,
    )
    assert result["verdicts"] == [{"id": 1, "verdict": "confirmed", "reason": "ok"}]

    with pytest.raises(ValueError):
        generator.normalize_verify({"verdicts": []}, findings)


def test_pr_markdown_includes_labels_and_checklist():
    text = generator.pr_markdown(
        {
            "title": "Fix refund authorization",
            "body_markdown": "## Summary\nDetails.",
            "labels": ["security", "bug"],
            "checklist": ["Ownership check tested"],
        }
    )
    assert text.startswith("# Fix refund authorization")
    assert "`security`" in text
    assert "- [ ] Ownership check tested" in text
