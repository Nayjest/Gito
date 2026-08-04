"""
Tests for the `answer` CLI command edge cases.
"""
from gito.cli import cmd_answer


def test_cmd_answer_none_with_linear(monkeypatch):
    """cmd_answer must not crash when answer() returns None
    (no changes in context) and --post-to linear is requested."""
    monkeypatch.setattr(
        "gito.cli.answer", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        "gito.cli.remove_html_comments", lambda text: text
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
