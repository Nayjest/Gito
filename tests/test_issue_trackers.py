from gito.issue_trackers import extract_issue_key
import gito

def test_extract_issue_key():
    assert extract_issue_key("feature/PROJ-123") == "PROJ-123"
    assert extract_issue_key("hotfix/AA-99") == "AA-99"
    assert extract_issue_key("bugfix/XYZ-1001-fix") == "XYZ-1001"
    assert extract_issue_key("improvement/TOOLONGKEY-1", max_len=8) is None
    assert extract_issue_key("somebranch/ab-1") is None  # lowercase key
    assert extract_issue_key("misc/no-key-here") is None
    assert extract_issue_key("feature/PR1-100", min_len=2, max_len=4) == "PR1-100"
    assert extract_issue_key("IS-811_word_word-_word-word_is_word") == "IS-811"
    assert extract_issue_key("fix_ISS-811__ISS-812") == "ISS-811"
