"""Webhook receiver + commit statuses (release plan Item 4, service entry path)."""
from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import time
from pathlib import Path

import pytest

from codepulse_app import publisher, server
from tests._server_harness import http_request, run_test_server

SECRET = "wh-secret-for-tests"


def _github_headers(body: bytes, event: str = "pull_request", secret: str = SECRET) -> dict:
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "X-Hub-Signature-256": signature,
        "X-GitHub-Event": event,
        "Content-Type": "application/json",
    }


def _github_pr_event(slug: str = "acme/payments", action: str = "synchronize") -> dict:
    return {
        "action": action,
        "repository": {"full_name": slug},
        "pull_request": {
            "number": 42,
            "head": {"sha": "a" * 40},
            "base": {"ref": "main", "sha": "b" * 40},
        },
    }


def _gitlab_mr_event(slug: str = "acme/payments") -> dict:
    return {
        "object_kind": "merge_request",
        "project": {"path_with_namespace": slug},
        "object_attributes": {
            "action": "update",
            "iid": 7,
            "target_branch": "main",
            "last_commit": {"id": "c" * 40},
        },
    }


def test_webhook_503_without_secret(monkeypatch, tmp_path):
    monkeypatch.delenv("CODE_DOCTOR_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("CODEPULSE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    with run_test_server(tmp_path) as base:
        status, body = http_request(f"{base}/api/hooks/github", "POST", b"{}", {})
    assert status == 503
    assert "CODEPULSE_WEBHOOK_SECRET" in body["error"]


def test_webhook_rejects_bad_signature(monkeypatch, tmp_path):
    monkeypatch.setenv("CODE_DOCTOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    body = json.dumps(_github_pr_event()).encode()
    with run_test_server(tmp_path) as base:
        status, payload = http_request(
            f"{base}/api/hooks/github", "POST", body,
            _github_headers(body, secret="wrong-secret"),
        )
        assert status == 401
        assert "signature" in payload["error"].lower()
        # GitLab uses a shared-token header instead of an HMAC.
        status, _ = http_request(
            f"{base}/api/hooks/gitlab", "POST",
            json.dumps(_gitlab_mr_event()).encode(),
            {"X-Gitlab-Token": "wrong", "Content-Type": "application/json"},
        )
        assert status == 401


def test_webhook_rejects_malformed_json(monkeypatch, tmp_path):
    monkeypatch.setenv("CODE_DOCTOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    body = b"not json {"
    with run_test_server(tmp_path) as base:
        status, payload = http_request(
            f"{base}/api/hooks/github", "POST", body, _github_headers(body)
        )
    assert status == 400
    assert "Malformed" in payload["error"]


def test_webhook_ignores_unsupported_event(monkeypatch, tmp_path):
    monkeypatch.setenv("CODE_DOCTOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    body = json.dumps({"zen": "Design for failure."}).encode()
    with run_test_server(tmp_path) as base:
        status, payload = http_request(
            f"{base}/api/hooks/github", "POST", body, _github_headers(body, event="ping")
        )
    assert status == 202
    assert payload == {"status": "ignored", "reason": "unsupported event"}


def test_webhook_ignores_unknown_repo(monkeypatch, tmp_path):
    monkeypatch.setenv("CODE_DOCTOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    body = json.dumps(_github_pr_event(slug="nobody/registered-this")).encode()
    with run_test_server(tmp_path) as base:
        status, payload = http_request(
            f"{base}/api/hooks/github", "POST", body, _github_headers(body)
        )
    assert status == 202
    assert payload["status"] == "ignored"
    assert payload["reason"] == "repository not registered"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _registered_clone(tmp_path: Path, remote: str) -> Path:
    repo = tmp_path / "clone"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "wh@test")
    _git(repo, "config", "user.name", "Webhook Test")
    (repo / "app.py").write_text("print('hi')\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "remote", "add", "origin", remote)
    return repo


def test_webhook_accepts_registered_repo_and_enqueues(monkeypatch, tmp_path):
    monkeypatch.setenv("CODE_DOCTOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    clone = _registered_clone(tmp_path, "git@github.com:acme/payments.git")
    enqueued: list[tuple[str, dict, dict]] = []
    monkeypatch.setattr(
        server, "enqueue_ci_review",
        lambda platform, repo, event: enqueued.append((platform, repo, event)),
    )
    body = json.dumps(_github_pr_event(slug="acme/payments")).encode()
    with run_test_server(tmp_path) as base:
        server.register_repo({"path": str(clone)})
        status, payload = http_request(
            f"{base}/api/hooks/github", "POST", body, _github_headers(body)
        )
        assert status == 202
        assert payload == {"status": "accepted", "slug": "acme/payments", "pr": 42}
        assert any(e["event"] == "webhook_accepted" for e in server.read_audit())
    platform, repo, event = enqueued[0]
    assert platform == "github"
    assert Path(repo["path"]) == clone
    assert event == {"slug": "acme/payments", "head_sha": "a" * 40, "base_ref": "main", "pr": 42}


def test_gitlab_webhook_accepts_with_shared_token(monkeypatch, tmp_path):
    monkeypatch.setenv("CODE_DOCTOR_WEBHOOK_SECRET", SECRET)
    monkeypatch.delenv("CODE_DOCTOR_TOKEN", raising=False)
    monkeypatch.delenv("CODEPULSE_TOKEN", raising=False)
    clone = _registered_clone(tmp_path, "https://gitlab.com/acme/payments.git")
    enqueued: list[tuple[str, dict, dict]] = []
    monkeypatch.setattr(
        server, "enqueue_ci_review",
        lambda platform, repo, event: enqueued.append((platform, repo, event)),
    )
    with run_test_server(tmp_path) as base:
        server.register_repo({"path": str(clone)})
        status, payload = http_request(
            f"{base}/api/hooks/gitlab", "POST",
            json.dumps(_gitlab_mr_event()).encode(),
            {"X-Gitlab-Token": SECRET, "Content-Type": "application/json"},
        )
    assert status == 202
    assert payload["status"] == "accepted"
    assert enqueued[0][0] == "gitlab"
    assert enqueued[0][2]["pr"] == 7


def test_parse_webhook_event_shapes():
    github = server.parse_webhook_event("github", "pull_request", _github_pr_event())
    assert github == {"slug": "acme/payments", "head_sha": "a" * 40, "base_ref": "main", "pr": 42}
    assert server.parse_webhook_event("github", "pull_request", {"action": "closed"}) is None
    assert server.parse_webhook_event("github", "push", {}) is None

    gitlab = server.parse_webhook_event("gitlab", "merge_request", _gitlab_mr_event())
    assert gitlab == {"slug": "acme/payments", "head_sha": "c" * 40, "base_ref": "main", "pr": 7}
    assert server.parse_webhook_event("gitlab", "", {"object_kind": "push"}) is None


def test_post_commit_status_builds_platform_payloads(monkeypatch):
    requests: list[tuple[str, dict, dict]] = []
    monkeypatch.setattr(
        publisher, "_request_json",
        lambda url, headers, payload: requests.append((url, headers, payload)) or {},
    )
    monkeypatch.setenv("GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("GITLAB_TOKEN", "gl-token")

    result = publisher.post_commit_status("github", "acme/payments", "a" * 40, "block")
    assert result["state"] == "failure"
    url, headers, payload = requests[-1]
    assert url == f"https://api.github.com/repos/acme/payments/statuses/{'a' * 40}"
    assert payload["context"] == "code-doctor/gate"
    assert payload["state"] == "failure"

    result = publisher.post_commit_status("gitlab", "acme/payments", "c" * 40, "block")
    assert result["state"] == "failed"
    url, headers, payload = requests[-1]
    assert "/projects/acme%2Fpayments/statuses/" in url
    assert headers == {"PRIVATE-TOKEN": "gl-token"}
    assert payload["name"] == "code-doctor/gate"

    result = publisher.post_commit_status("github", "acme/payments", "a" * 40, "review")
    assert result["state"] == "success"
    assert "human review" in result["description"]


def test_post_commit_status_requires_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("CODE_DOCTOR_GITHUB_TOKEN", raising=False)
    with pytest.raises(ValueError, match="not configured"):
        publisher.post_commit_status("github", "acme/payments", "a" * 40, "pass")
