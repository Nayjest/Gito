from pathlib import Path
from CRACK.bootstrap import bootstrap
from CRACK.report_struct import Report


def validate(out):
    for i in [1, 2, 3, 4]:
        assert f"ISSUE_{i} TITLE" in out
    assert "ISSUE_1 DESCR\nLINE_2\nLINE_3" in out
    assert "SUMMARY_TEXT" in out
    assert "ISSUE_1" in out
    assert "4" in out  # Total issues
    assert "555" in out  # Number of files


def test_render():
    path = Path(__file__).parent / "fixtures" / "cr-report-1.json"
    bootstrap()
    out = Report.load(path).render(report_format=Report.Format.CLI)
    validate(out)
    out = Report.load(file_name=str(path)).render(None, Report.Format.MARKDOWN)
    validate(out)
