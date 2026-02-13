from gito.commands.gh_react_to_comment import cleanup_comment_addressed_to_gito


def test_remove_comment_prefixes():
    test_cases = [
        "gito please help me with this",
        "AI, can you assist?",
        "Bot what should I do?",
        "@gito please check this out",
        "@ai please review",
        "@bot, run the tests",
        "GITO, this is urgent ",
        "@AI help needed",
        " gito, please fix the bug",
        "Normal text without prefixes",
        "gito,    extra spaces here",
        "  AI  ,  with leading spaces",
        "This has @gito in the middle",  # Should NOT be removed
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
        "This has @gito in the middle",  # Should NOT be removed
        "Text with @ai somewhere",  # Should NOT be removed
        "",
    ]
    for text, expected in zip(test_cases, expected_outputs):
        assert cleanup_comment_addressed_to_gito(text) == expected, f"Failed for: {text}"
