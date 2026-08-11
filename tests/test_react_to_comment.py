"""
Tests for the PR-comment reaction flow (react_to_comment).
"""
from gito.commands.gh_react_to_comment import react_to_comment


class FakeComment:
    body = "@gito hello"
    issue_url = "https://api.github.com/repos/owner/repo/issues/12"
    html_url = "https://github.com/owner/repo/pull/12"
    user = type("User", (), {"login": "tester"})()


class FakeReactions:
    def create_for_issue_comment(self, **kwargs):
        pass


class FakeIssues:
    def get_comment(self, comment_id):
        return FakeComment()


class FakeApi:
    def __init__(self, **kwargs):
        self.issues = FakeIssues()
        self.reactions = FakeReactions()


class FakeConfig:
    mention_triggers = ["@gito"]
    answer_github_comments = True


def _patch_env(monkeypatch, answer_result=None):
    monkeypatch.setattr(
        "gito.commands.gh_react_to_comment.get_cwd_repo_or_fail",
        lambda: object(),
    )
    monkeypatch.setattr(
        "gito.commands.gh_react_to_comment.is_running_in_github_action",
        lambda: False,
    )
    monkeypatch.setattr(
        "gito.commands.gh_react_to_comment.get_repo_owner_and_name",
        lambda repo: ("owner", "repo"),
    )
    monkeypatch.setattr(
        "gito.commands.gh_react_to_comment.GhApi", FakeApi
    )
    monkeypatch.setattr(
        "gito.commands.gh_react_to_comment.resolve_gh_token",
        lambda token: "test-token",
    )
    monkeypatch.setattr(
        "gito.commands.gh_react_to_comment.ProjectConfig.load_for_repo",
        lambda repo: FakeConfig(),
    )
    monkeypatch.setattr(
        "gito.commands.gh_react_to_comment.answer",
        lambda *args, **kwargs: answer_result,
    )
    monkeypatch.setattr(
        "gito.commands.gh_react_to_comment.cleanup_comment_addressed_to_gito",
        lambda text: text,
    )
    posted = {}
    monkeypatch.setattr(
        "gito.commands.gh_react_to_comment.post_gh_comment",
        lambda **kwargs: posted.update(kwargs),
    )
    return posted


def test_react_to_comment_posts_answer(monkeypatch):
    """A normal answer is posted back to the PR."""
    posted = _patch_env(monkeypatch, answer_result="The answer")
    react_to_comment(comment_id=12)
    assert posted.get("text", "").endswith("The answer")


def test_react_to_comment_no_answer_does_not_crash(monkeypatch):
    """
    When answer() returns None (e.g. no changes in context),
    the bot must not crash with AttributeError and must not
    post a comment constructed from None.
    """
    posted = _patch_env(monkeypatch, answer_result=None)
    react_to_comment(comment_id=12)  # must not raise
    assert "text" not in posted
