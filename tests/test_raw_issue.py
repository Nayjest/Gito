import pytest
from gito.report_struct import RawIssue
from gito.core import _llm_response_validator


def test_raw_issue():
    def base_data():
        return {
            "title": "Bug",
            "details": "desc",
            "tags": ["bug"],
            "severity": 1,
            "confidence": 1,
            "affected_lines": [],
        }

    raw = [base_data(), base_data()]
    assert _llm_response_validator(raw) is True
    issue1 = RawIssue(**raw[0])
    assert issue1.title == "Bug"
    assert issue1.tags == ["bug"]

    del raw[0]["affected_lines"]
    del raw[0]["details"]
    del raw[0]["tags"]
    del raw[0]["severity"]
    issue1 = RawIssue(**raw[0])
    assert _llm_response_validator(raw) is True
    assert issue1.tags == []
    assert issue1.affected_lines == []
    del raw[0]["title"]  # required field
    # raises
    with pytest.raises(Exception):
        issue1 = RawIssue(**raw[0])
    with pytest.raises(Exception):
        _llm_response_validator(raw)
