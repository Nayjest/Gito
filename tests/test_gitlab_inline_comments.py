import json

import pytest

import gito.commands.gitlab_post_review_comment as gl_cmd
from gito.bootstrap import bootstrap
from gito.constants import HTML_CR_COMMENT_MARKER, HTML_INLINE_CR_COMMENT_MARKER
from gito.project_config import ProjectConfig
from gito.report_struct import Report
from gito.commands.gitlab_post_review_comment import (
    build_gl_mr_line_maps,
    gl_issue_position,
    parse_unified_diff_line_map,
    post_gitlab_cr_comment,
)

DIFF_REFS = {
    "base_sha": "c0e31195",
    "head_sha": "e8f79be9",
    "start_sha": "c0e31195",
}

APP_PY_DIFF = "@@ -1,5 +1,5 @@\n" " # heading\n" "-\n" "+test\n" " \n" " \n" " ## tail\n"


def test_parse_unified_diff_line_map():
    line_map = parse_unified_diff_line_map(APP_PY_DIFF)
    assert line_map == {1: 1, 2: None, 3: 3, 4: 4, 5: 5}


def test_parse_unified_diff_line_map_multiple_hunks():
    diff = (
        "@@ -1,2 +1,3 @@\n"
        " a\n"
        "+b\n"
        " c\n"
        "@@ -10,3 +11,2 @@\n"
        " x\n"
        "-y\n"
        " z\n"
        "\\ No newline at end of file\n"
    )
    line_map = parse_unified_diff_line_map(diff)
    assert line_map == {1: 1, 2: None, 3: 2, 11: 10, 12: 12}


def test_gl_issue_position_anchoring():
    bootstrap()
    line_maps = build_gl_mr_line_maps(
        [
            {
                "old_path": "src/app.py",
                "new_path": "src/app.py",
                "diff": APP_PY_DIFF,
                "deleted_file": False,
            },
            {"old_path": "gone.py", "new_path": "gone.py", "diff": "", "deleted_file": True},
        ]
    )
    assert list(line_maps) == ["src/app.py"]

    def issue(file, start_line):
        return gl_cmd.Issue(
            id=1,
            title="T",
            tags=[],
            file=file,
            affected_lines=[{"start_line": start_line, "file": file}],
        )

    # added line -> new_line only
    position = gl_issue_position(issue("src/app.py", 2), line_maps, DIFF_REFS)
    assert position["new_line"] == 2
    assert "old_line" not in position
    assert position["position_type"] == "text"
    assert position["base_sha"] == DIFF_REFS["base_sha"]
    # context line -> old_line + new_line
    position = gl_issue_position(issue("src/app.py", 1), line_maps, DIFF_REFS)
    assert position["new_line"] == 1
    assert position["old_line"] == 1
    # line outside the diff -> not anchorable
    assert gl_issue_position(issue("src/app.py", 50), line_maps, DIFF_REFS) is None
    # file outside the diff -> not anchorable
    assert gl_issue_position(issue("src/other.py", 2), line_maps, DIFF_REFS) is None


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}
        self.text = json.dumps(json_data)

    def json(self):
        return self._json


@pytest.fixture
def report_file(tmp_path):
    report = {
        "issues": {
            "src/app.py": [
                {
                    "id": 1,
                    "title": "ADDED_LINE_ISSUE",
                    "details": "DETAILS_1",
                    "severity": 1,
                    "confidence": 1,
                    "tags": ["bug"],
                    "file": "src/app.py",
                    "affected_lines": [
                        {
                            "start_line": 2,
                            "end_line": 2,
                            "file": "src/app.py",
                            "proposal": "PROPOSED_FIX",
                        }
                    ],
                },
                {
                    "id": 2,
                    "title": "CONTEXT_LINE_ISSUE",
                    "details": "DETAILS_2",
                    "severity": 2,
                    "confidence": 1,
                    "tags": [],
                    "file": "src/app.py",
                    "affected_lines": [{"start_line": 1, "end_line": 1, "file": "src/app.py"}],
                },
            ],
            "src/other.py": [
                {
                    "id": 3,
                    "title": "UNANCHORED_ISSUE",
                    "details": "DETAILS_3",
                    "severity": 3,
                    "confidence": 1,
                    "tags": [],
                    "file": "src/other.py",
                    "affected_lines": [{"start_line": 10, "end_line": 10, "file": "src/other.py"}],
                }
            ],
        },
        "summary": "REVIEW_SUMMARY",
        "number_of_processed_files": 2,
        "created_at": "2026-01-01 00:00:00",
        "model": "test-model",
        "pipeline_out": {},
        "processing_warnings": [],
        "target": None,
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def test_md_report_include_issues_flag(report_file):
    bootstrap()
    report = Report.load(report_file)
    config = ProjectConfig.load()
    titles = ("ADDED_LINE_ISSUE", "CONTEXT_LINE_ISSUE", "UNANCHORED_ISSUE")

    # default markdown rendering is unchanged: all issues are included
    full = report.render(config, Report.Format.MARKDOWN)
    assert all(title in full for title in titles)

    # include_issues=False keeps only the overview + issues passed as unanchored_issues
    overview = report.render(
        config,
        Report.Format.MARKDOWN,
        include_issues=False,
        unanchored_issues=[report.get_issue_by_id(3)],
    )
    assert "UNANCHORED_ISSUE" in overview
    assert "ADDED_LINE_ISSUE" not in overview
    assert "CONTEXT_LINE_ISSUE" not in overview
    assert "REVIEW_SUMMARY" in overview


def test_post_gitlab_inline_review(monkeypatch, report_file):
    bootstrap()
    calls = []
    old_discussion = {
        "id": "abc123",
        "notes": [
            {
                "body": f"Old gito note{HTML_INLINE_CR_COMMENT_MARKER}",
                "resolvable": True,
                "resolved": False,
            }
        ],
    }

    def fake_get(url, headers=None, timeout=None):
        calls.append(("GET", url, None))
        if "/diffs?" in url:
            return FakeResponse(
                200,
                [
                    {
                        "old_path": "src/app.py",
                        "new_path": "src/app.py",
                        "diff": APP_PY_DIFF,
                        "deleted_file": False,
                    }
                ],
            )
        if "/discussions?" in url:
            return FakeResponse(200, [old_discussion])
        if "/notes?" in url:
            return FakeResponse(200, [])
        if url.endswith("/merge_requests/5"):
            return FakeResponse(200, {"diff_refs": DIFF_REFS})
        raise AssertionError(f"Unexpected GET {url}")

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(("POST", url, json))
        if url.endswith("/draft_notes/bulk_publish"):
            return FakeResponse(204)
        if url.endswith("/draft_notes") or url.endswith("/notes"):
            return FakeResponse(201, {"id": 1})
        raise AssertionError(f"Unexpected POST {url}")

    def fake_put(url, headers=None, json=None, timeout=None):
        calls.append(("PUT", url, json))
        return FakeResponse(200, {"resolved": True})

    monkeypatch.setattr(gl_cmd.requests, "get", fake_get)
    monkeypatch.setattr(gl_cmd.requests, "post", fake_post)
    monkeypatch.setattr(gl_cmd.requests, "put", fake_put)
    monkeypatch.setattr(gl_cmd, "sleep", lambda seconds: None)

    post_gitlab_cr_comment(
        md_report_file=None,
        json_report_file=str(report_file),
        project_id="42",
        merge_request_iid=5,
        token="glpat-test",
        base_url=None,
        inline=True,
    )

    posts = [(url, body) for method, url, body in calls if method == "POST"]

    # 1. Overview comment is posted first and aggregates what can't be inlined
    overview_url, overview_body = posts[0]
    assert overview_url.endswith("/merge_requests/5/notes")
    overview_text = overview_body["body"]
    assert "REVIEW_SUMMARY" in overview_text
    assert HTML_CR_COMMENT_MARKER in overview_text
    assert "UNANCHORED_ISSUE" in overview_text
    assert "ADDED_LINE_ISSUE" not in overview_text
    assert "CONTEXT_LINE_ISSUE" not in overview_text

    # 2. Outdated inline discussions from previous runs are resolved
    puts = [(url, body) for method, url, body in calls if method == "PUT"]
    assert len(puts) == 1
    assert puts[0][0].endswith("/discussions/abc123")
    assert puts[0][1] == {"resolved": True}

    # 3. One draft note per anchorable issue, with correct positions
    draft_posts = [p for p in posts if p[0].endswith("/draft_notes")]
    assert len(draft_posts) == 2
    by_title = {}
    for _, payload in draft_posts:
        assert HTML_INLINE_CR_COMMENT_MARKER in payload["note"]
        if "ADDED_LINE_ISSUE" in payload["note"]:
            by_title["ADDED_LINE_ISSUE"] = payload
        else:
            by_title["CONTEXT_LINE_ISSUE"] = payload
    added = by_title["ADDED_LINE_ISSUE"]
    assert added["position"]["new_line"] == 2
    assert "old_line" not in added["position"]
    assert "PROPOSED_FIX" in added["note"]
    context = by_title["CONTEXT_LINE_ISSUE"]
    assert context["position"]["new_line"] == 1
    assert context["position"]["old_line"] == 1
    # unanchored issue is not posted inline
    assert all("UNANCHORED_ISSUE" not in p[1]["note"] for p in draft_posts)

    # 4. Draft notes are published in bulk, after creation
    publish_indexes = [i for i, p in enumerate(posts) if p[0].endswith("/bulk_publish")]
    assert len(publish_indexes) == 1
    last_draft_index = max(i for i, p in enumerate(posts) if p[0].endswith("/draft_notes"))
    assert publish_indexes[0] > last_draft_index
