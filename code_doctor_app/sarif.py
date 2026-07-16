"""SARIF 2.1.0 export for GitHub Code Scanning and other SARIF consumers.

Static Analysis Results Interchange Format is the standard GitHub's Security
tab (and many CI tools) ingest. Converting a Code Doctor report to SARIF lets
findings show up as code-scanning alerts on a PR.
"""
from __future__ import annotations

import json
from typing import Any

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
TOOL_URI = "https://github.com/Nayjest/gito"

# Code Doctor severity (1 highest .. 5 lowest) -> SARIF level.
_LEVEL = {1: "error", 2: "error", 3: "warning", 4: "note", 5: "note"}


def _level(severity: Any) -> str:
    try:
        return _LEVEL.get(int(severity), "warning")
    except (TypeError, ValueError):
        return "warning"


def _rule_id(issue: dict[str, Any]) -> str:
    return str(issue.get("rule") or issue.get("source") or "code-doctor") or "code-doctor"


def _region(issue: dict[str, Any]) -> dict[str, Any]:
    blocks = issue.get("affected_lines") or []
    if blocks and isinstance(blocks[0], dict):
        start = blocks[0].get("start_line")
        end = blocks[0].get("end_line") or start
        if isinstance(start, int) and start >= 1:
            region = {"startLine": start}
            if isinstance(end, int) and end >= start:
                region["endLine"] = end
            return region
    return {"startLine": 1}


def to_sarif(issues: list[dict[str, Any]], tool_version: str = "") -> dict[str, Any]:
    """Build a SARIF 2.1.0 document from flattened report issues."""
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for issue in issues:
        rid = _rule_id(issue)
        if rid not in rules:
            rules[rid] = {
                "id": rid,
                "name": rid,
                "shortDescription": {"text": str(issue.get("title") or rid)},
                "properties": {"tags": list(issue.get("tags") or [])},
            }
        message = str(issue.get("title") or "Finding")
        detail = str(issue.get("details") or issue.get("verifier_reason") or "")
        if detail:
            message = f"{message} {detail}"
        results.append({
            "ruleId": rid,
            "level": _level(issue.get("severity")),
            "message": {"text": message},
            "locations": [{
                "physicalLocation": {
                    "artifactLocation": {"uri": str(issue.get("file") or "")},
                    "region": _region(issue),
                }
            }],
            "properties": {
                "severity": issue.get("severity"),
                "confidence": issue.get("confidence"),
                "source": issue.get("source"),
            },
        })
    return {
        "version": "2.1.0",
        "$schema": SARIF_SCHEMA,
        "runs": [{
            "tool": {"driver": {
                "name": "Code Doctor",
                "version": tool_version or "0.0.0",
                "informationUri": TOOL_URI,
                "rules": list(rules.values()),
            }},
            "results": results,
        }],
    }


def dumps(issues: list[dict[str, Any]], tool_version: str = "") -> str:
    return json.dumps(to_sarif(issues, tool_version), indent=2)
