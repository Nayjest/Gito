from gito.report_struct import Issue


def test_affected_lines_count():
    assert Issue.AffectedCode(start_line=1, end_line=2).affected_lines_count == 2
    assert Issue.AffectedCode(start_line=1, end_line=1).affected_lines_count == 1
    assert Issue.AffectedCode(start_line=1).affected_lines_count is None
    block = Issue.AffectedCode(
        start_line=1,
        end_line=2,
        affected_code="1: line1\n2: line2",
        proposal="fixed line1\nfixed line2",
    )
    assert block.affected_lines_count == 2
    assert block.raw_code == "line1\nline2"

    block = Issue.AffectedCode(
        start_line=1,
        end_line=3,
        affected_code="1: line1\n2: line2\n3: \n",
    )
    assert block.raw_code == "line1\nline2\n\n"
