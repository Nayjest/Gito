from gito.review_context import discover_project_instructions


def test_discovers_parent_and_scoped_instructions_in_precedence_order(tmp_path):
    (tmp_path / "AGENTS.md").write_text("root rules", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "AGENTS.md").write_text("src rules", encoding="utf-8")
    instructions = tmp_path / ".github" / "instructions"
    instructions.mkdir(parents=True)
    (instructions / "python.instructions.md").write_text(
        "---\napplyTo: **/*.py\n---\npython rules", encoding="utf-8"
    )
    (instructions / "javascript.instructions.md").write_text(
        "---\napplyTo: **/*.js\n---\njavascript rules", encoding="utf-8"
    )

    result = discover_project_instructions(tmp_path, "src/service.py")

    assert [item.path for item in result] == [
        "AGENTS.md",
        "src/AGENTS.md",
        ".github/instructions/python.instructions.md",
    ]
    assert result[0].content == "root rules"
    assert result[2].applies_to == ["**/*.py"]


def test_skips_unmatched_and_missing_instruction_files(tmp_path):
    (tmp_path / "CODE_GUIDELINES.md").write_text("guidelines", encoding="utf-8")

    result = discover_project_instructions(
        tmp_path,
        "src/service.py",
        patterns=["CODE_GUIDELINES.md", "MISSING.md", ".github/instructions/**/*.md"],
    )

    assert [item.path for item in result] == ["CODE_GUIDELINES.md"]


def test_applies_instruction_token_budget(tmp_path):
    (tmp_path / "AGENTS.md").write_text("one two three four", encoding="utf-8")

    result = discover_project_instructions(tmp_path, "service.py", max_tokens=2)

    assert len(result) == 1
    assert result[0].content == "one two"
