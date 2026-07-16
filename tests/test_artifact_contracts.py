"""Artifact schema contracts (release plan §4c) — mechanical enforcement of
rule R1: existing keys of run artifacts may never be renamed or retyped.

Schemas under ``tests/schemas/`` list today's keys with their types. Unknown
keys always pass (artifacts evolve additively); a known key with a changed
type fails. The validator is a deliberate ~50-line JSON-Schema subset:
``type`` (string or list), ``properties``, ``items``, ``values`` (schema for
every value of a map), and root-level ``definitions`` + ``$ref`` by name.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from code_doctor_app import context_engine, generator, server, store

SCHEMA_DIR = Path(__file__).parent / "schemas"

PYTHON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


def _type_ok(value: object, expected: str | list[str]) -> bool:
    names = expected if isinstance(expected, list) else [expected]
    for name in names:
        if isinstance(value, bool) and name in {"integer", "number"}:
            continue  # bool is an int subclass; a retype to bool must fail
        if isinstance(value, PYTHON_TYPES[name]):
            return True
    return False


def validate(instance, schema: dict, definitions: dict, path: str = "$") -> list[str]:
    if "$ref" in schema:
        schema = definitions[schema["$ref"]]
    expected = schema.get("type")
    if expected is not None and not _type_ok(instance, expected):
        return [f"{path}: expected {expected}, got {type(instance).__name__}"]
    errors: list[str] = []
    if isinstance(instance, dict):
        for key, subschema in (schema.get("properties") or {}).items():
            if key in instance:
                errors += validate(instance[key], subschema, definitions, f"{path}.{key}")
        values_schema = schema.get("values")
        if values_schema:
            for key, value in instance.items():
                errors += validate(value, values_schema, definitions, f"{path}.{key}")
    elif isinstance(instance, list):
        items_schema = schema.get("items")
        if items_schema:
            for index, item in enumerate(instance):
                errors += validate(item, items_schema, definitions, f"{path}[{index}]")
    return errors


def assert_conforms(instance, schema_name: str) -> None:
    schema = json.loads((SCHEMA_DIR / f"{schema_name}.schema.json").read_text(encoding="utf-8"))
    errors = validate(instance, schema, schema.get("definitions") or {})
    assert not errors, f"{schema_name} contract violated:\n" + "\n".join(errors)


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


def test_validator_flags_retyped_and_ignores_added_keys():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    assert validate({"id": "abc", "brand_new_key": 1}, schema, {}) == []
    errors = validate({"id": 123}, schema, {})
    assert errors and "expected string" in errors[0]


def test_seeded_run_meta_and_report_conform(monkeypatch, tmp_path):
    _isolated_store(monkeypatch, tmp_path)
    meta = server.seed_sample_data()
    run_id = meta["id"]
    assert_conforms(server.read_json(server.meta_path(run_id), {}), "meta")
    assert_conforms(server.read_json(server.report_path(run_id), {}), "report")


def test_live_review_meta_and_report_conform(monkeypatch, tmp_path):
    """A real pipeline run (canned gito, verdict-reuse metadata included)."""
    from tests.test_ci import BLOCKING_REPORT, _fake_review_command, _scratch_repo

    _isolated_store(monkeypatch, tmp_path)
    repo = _scratch_repo(tmp_path)
    _fake_review_command(monkeypatch, tmp_path, BLOCKING_REPORT)
    payload = {"repoPath": str(repo), "mode": "working", "verifyFindings": False}
    run_id, repo_path, payload, command = server.create_review_run(payload)
    server.run_review(run_id, repo_path, payload, command)

    meta = server.read_json(server.meta_path(run_id), {})
    assert meta["status"] == "completed"
    assert_conforms(meta, "meta")
    assert_conforms(server.read_json(server.report_path(run_id), {}), "report")
    pack_path = server.context_pack_path(run_id)
    if pack_path.exists():
        assert_conforms(server.read_json(pack_path, {}), "context-pack")


def test_context_pack_conforms(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "billing.py").write_text(
        "def charge(amount):\n    return amount\n\n\ndef legacy_charge(a):\n    return a\n",
        encoding="utf-8",
    )
    (repo / "pkg" / "api.py").write_text(
        "from pkg.billing import charge, legacy_charge\n\n\ndef handle():\n"
        "    return charge(1) + legacy_charge(2)\n",
        encoding="utf-8",
    )
    for args in (
        ["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "T"],
        ["add", "."], ["commit", "-q", "-m", "base"],
    ):
        subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE)
    (repo / "pkg" / "billing.py").write_text(
        "def charge(amount, currency):\n    return amount\n", encoding="utf-8"
    )

    result = context_engine.analyze_cross_file(repo, mode="working")
    assert result["pack"]["files"], "scenario should produce a non-empty pack"
    assert_conforms(result["pack"], "context-pack")


def test_generator_artifacts_conform(tmp_path):
    generator.write_tests_artifacts(
        tmp_path,
        generator.normalize_tests(
            {
                "files": [
                    {
                        "path": "tests/test_refunds.py",
                        "framework": "pytest",
                        "content": "def test_refund_ownership():\n    assert True\n",
                        "covers": ["finding-1"],
                        "rationale": "Regression for the ownership bypass.",
                    }
                ],
                "notes": "One regression case.",
            }
        ),
    )
    assert_conforms(
        json.loads((tmp_path / "generated-tests.json").read_text(encoding="utf-8")),
        "generated-tests",
    )

    generator.write_pr_artifacts(
        tmp_path,
        generator.normalize_pr(
            {
                "title": "Fix refund ownership check",
                "body_markdown": "## Summary\nAdds the missing ownership check.",
                "labels": ["bug"],
                "checklist": ["Ownership verified before refund"],
            }
        ),
    )
    assert_conforms(
        json.loads((tmp_path / "pr-draft.json").read_text(encoding="utf-8")), "pr-draft"
    )
