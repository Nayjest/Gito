from __future__ import annotations

import pytest

from codepulse_app import publisher


@pytest.fixture(autouse=True)
def _no_tokens(monkeypatch):
    for var in ("GITHUB_TOKEN", "CODE_DOCTOR_GITHUB_TOKEN", "GITLAB_TOKEN", "CODE_DOCTOR_GITLAB_TOKEN"):
        monkeypatch.delenv(var, raising=False)


def _meta() -> dict:
    return {"id": "run-1", "model": "gemma4:e4b"}


def _report() -> dict:
    return {
        "issues": {
            "svc/refunds.py": [
                {
                    "id": 1,
                    "severity": 1,
                    "confidence": 1,
                    "title": "Missing ownership check.",
                    "details": "Refund path trusts caller-supplied account id.",
                    "tags": ["security"],
                    "verified": True,
                    "affected_lines": [
                        {"start_line": 42, "end_line": 44, "proposal": "check ownership"}
                    ],
                },
                {
                    "id": 2,
                    "severity": 3,
                    "confidence": 2,
                    "title": "No line info.",
                    "tags": ["style"],
                },
            ]
        }
    }


def _stats() -> dict:
    return {
        "gate": "block",
        "risk_score": 13,
        "static_issues": 0,
        "cross_file_issues": 0,
        "verification": {"confirmed": 1, "uncertain": 0},
        "rejected_issues": 0,
        "summary": "Do not merge until ownership is checked.",
    }


def test_parse_remote_handles_https_and_ssh():
    assert publisher.parse_remote("https://github.com/acme/payments.git") == {
        "host": "github.com",
        "slug": "acme/payments",
        "platform": "github",
    }
    assert publisher.parse_remote("git@gitlab.example.com:team/api.git")["platform"] == "gitlab"
    assert publisher.parse_remote("not a url") is None


def test_summary_markdown_includes_gate_verification_and_findings():
    text = publisher.build_summary_markdown(_meta(), _report(), _stats())

    assert "`block`" in text
    assert "1 confirmed" in text
    assert "svc/refunds.py:42" in text
    assert "Missing ownership check." in text
    assert "never left the reviewer's machine" in text


def test_line_comments_skip_suppressed_and_lineless_findings():
    report = _report()
    report["issues"]["svc/refunds.py"][0]["suppressed"] = True

    comments = publisher.build_line_comments(report)
    assert comments == []  # one suppressed, one without line info

    report["issues"]["svc/refunds.py"][0]["suppressed"] = False
    comments = publisher.build_line_comments(report)
    assert len(comments) == 1
    assert comments[0]["path"] == "svc/refunds.py"
    assert comments[0]["line"] == 42
    assert "Suggested fix" in comments[0]["body"]


def test_publish_review_dry_run_never_touches_network(monkeypatch):
    def explode(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("dry run must not open a connection")

    monkeypatch.setattr(publisher, "urlopen", explode)

    preview = publisher.publish_review(
        _meta(),
        _report(),
        _stats(),
        {"platform": "github", "repo": "acme/payments", "pr": 7, "dryRun": True},
    )

    assert preview["dry_run"] is True
    assert preview["target"] == "acme/payments#7"
    assert len(preview["line_comments"]) == 1
    assert "posted" not in preview


def test_publish_review_defaults_platform_and_slug_from_remote():
    preview = publisher.publish_review(
        _meta(), _report(), _stats(), {"pr": 3, "dryRun": True},
        remote_url="git@github.com:acme/payments.git",
    )
    assert preview["platform"] == "github"
    assert preview["target"] == "acme/payments#3"


def test_publish_review_validates_inputs():
    with pytest.raises(ValueError, match="platform"):
        publisher.publish_review(_meta(), _report(), _stats(), {"pr": 1})
    with pytest.raises(ValueError, match="slug"):
        publisher.publish_review(_meta(), _report(), _stats(), {"platform": "github", "pr": 1})
    with pytest.raises(ValueError, match="number"):
        publisher.publish_review(
            _meta(), _report(), _stats(), {"platform": "github", "repo": "a/b"}
        )


def test_publish_review_requires_server_side_token():
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        publisher.publish_review(
            _meta(),
            _report(),
            _stats(),
            {"platform": "github", "repo": "acme/payments", "pr": 7, "dryRun": False},
        )


def test_publish_config_reports_configured_platforms(monkeypatch):
    config = publisher.publish_config()
    assert config["github"]["configured"] is False

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    assert publisher.publish_config()["github"]["configured"] is True
