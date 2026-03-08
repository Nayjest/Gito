from CRACK.commands.gh_react_to_comment import cleanup_comment_addressed_to_CRACK


def test_remove_comment_prefixes():
    test_cases = [
        "CRACK please help me with this",
        "AI, can you assist?",
        "Bot what should I do?",
        "@CRACK please check this out",
        "@ai please review",
        "@bot, run the tests",
        "CRACK, this is urgent ",
        "@AI help needed",
        " CRACK, please fix the bug",
        "Normal text without prefixes",
        "CRACK,    extra spaces here",
        "  AI  ,  with leading spaces",
        "This has @CRACK in the middle",  # Should NOT be removed
        "Text with @ai somewhere",  # Should NOT be removed
        "",
    ]
    expected_outputs = [
        "please help me with this",
        "can you assist?",
        "what should I do?",
        "please check this out",
        "please review",
        "run the tests",
        "this is urgent",
        "help needed",
        "please fix the bug",
        "Normal text without prefixes",
        "extra spaces here",
        "with leading spaces",
        "This has @CRACK in the middle",  # Should NOT be removed
        "Text with @ai somewhere",  # Should NOT be removed
        "",
    ]
    for text, expected in zip(test_cases, expected_outputs):
        assert cleanup_comment_addressed_to_CRACK(text) == expected, f"Failed for: {text}"
