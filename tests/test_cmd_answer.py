"""
Tests for the `answer` CLI command edge cases.
"""
import pytest
from git import Repo

from gito.cli import cmd_answer
from gito.core import answer


def test_answer_returns_none_when_no_changes(tmp_path, monkeypatch):
    """
    Contract relied upon by react_to_comment: answer() returns None
    (and never calls the LLM) when the diff context is empty.
    """
    repo = Repo.init(tmp_path)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@test.com")
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    repo.index.add(["a.txt"])
    repo.index.commit("init")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "gito.core.mc.llm",
        lambda *args, **kwargs: pytest.fail("LLM must not be called on empty diff"),
    )

    out = answer("q", repo=repo, against=repo.active_branch.name, use_pipeline=False)

    assert out is None


def test_cmd_answer_none_with_linear(monkeypatch):
    """cmd_answer must not crash when answer() returns None
    (no changes in context) and --post-to linear is requested."""
    monkeypatch.setattr(
        "gito.cli.answer", lambda *args, **kwargs: None
    )
    posted = {}
    monkeypatch.setattr(
        "gito.cli.linear_comment",
        lambda text: posted.setdefault("text", text),
    )
    out = cmd_answer(
        question="hello",
        post_to="linear",
        refs=None,
        what=None,
        against=None,
        filters="",
        merge_base=True,
        use_pipeline=True,
        pr=None,
        aux_files=None,
        save_to=None,
        all=False,
    )
    assert out is None
    assert "text" not in posted


def test_cmd_answer_none_with_save_to(monkeypatch, tmp_path):
    """cmd_answer must not crash when answer() returns None
    and --save-to is provided."""
    monkeypatch.setattr(
        "gito.cli.answer", lambda *args, **kwargs: None
    )
    save_file = tmp_path / "answer.md"
    out = cmd_answer(
        question="hello",
        post_to=None,
        refs=None,
        what=None,
        against=None,
        filters="",
        merge_base=True,
        use_pipeline=True,
        pr=None,
        aux_files=None,
        save_to=str(save_file),
        all=False,
    )
    assert out is None
    assert not save_file.exists()
