import json
import logging
from dataclasses import field, asdict, is_dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import textwrap
import microcore as mc
from microcore.utils import file_link
from colorama import Fore, Style, Back
from pydantic.dataclasses import dataclass

from .constants import JSON_REPORT_FILE_NAME, HTML_TEXT_ICON, HTML_CR_COMMENT_MARKER
from .project_config import ProjectConfig
from .utils import syntax_hint, block_wrap_lr, max_line_len, remove_html_comments, filter_kwargs


@dataclass
class RawIssue:
    @dataclass
    class AffectedCode:
        start_line: int = field()
        end_line: int | None = field(default=None)
        proposal: str | None = field(default="")

    title: str = field()
    details: str | None = field(default="")
    severity: int | None = field(default=None)
    confidence: int | None = field(default=None)
    tags: list[str] = field(default_factory=list)
    affected_lines: list[AffectedCode] = field(default_factory=list)


@dataclass
class Issue(RawIssue):
    @dataclass
    class AffectedCode(RawIssue.AffectedCode):
        file: str = field(default="")
        affected_code: str = field(default="")

        @property
        def syntax_hint(self) -> str:
            return syntax_hint(self.file)

    id: int | str = field(kw_only=True)
    file: str = field(default="")
    affected_lines: list[AffectedCode] = field(default_factory=list)

    @staticmethod
    def from_raw_issue(file: str, raw_issue: RawIssue | dict, issue_id: int | str) -> "Issue":
        if is_dataclass(raw_issue):
            raw_issue = asdict(raw_issue)
        params = filter_kwargs(Issue, raw_issue | {"file": file, "id": issue_id})
        for i, obj in enumerate(params.get("affected_lines") or []):
            d = obj if isinstance(obj, dict) else asdict(obj)
            params["affected_lines"][i] = Issue.AffectedCode(
                **filter_kwargs(Issue.AffectedCode, {"file": file} | d)
            )
        return Issue(**params)

    def github_code_link(self, github_env: dict) -> str:
        url = (
            f"https://github.com/{github_env['github_repo']}"
            f"/blob/{github_env['github_pr_sha_or_branch']}"
            f"/{self.file}"
        )
        if self.affected_lines:
            url += f"#L{self.affected_lines[0].start_line}"
            if self.affected_lines[0].end_line:
                url += f"-L{self.affected_lines[0].end_line}"
        return url


@dataclass
class Report:
    class Format(StrEnum):
        MARKDOWN = "md"
        CLI = "cli"

    issues: dict[str, list[Issue]] = field(default_factory=dict)
    summary: str = field(default="")
    number_of_processed_files: int = field(default=0)
    total_issues: int = field(init=False)
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    model: str = field(default_factory=lambda: mc.config().MODEL)
    pipeline_out: dict = field(default_factory=dict)

    @property
    def plain_issues(self):
        return [
            issue
            for file, issues in self.issues.items()
            for issue in issues
        ]

    def register_issues(self, issues: dict[str, list[RawIssue | dict]]):
        for file, file_issues in issues.items():
            for issue in file_issues:
                self.register_issue(file, issue)

    def register_issue(self, file: str, issue: RawIssue | dict):
        if file not in self.issues:
            self.issues[file] = []
        total = len(self.plain_issues)
        self.issues[file].append(Issue.from_raw_issue(file, issue, issue_id=total + 1))
        self.total_issues = total + 1

    def __post_init__(self):
        self.total_issues = len(self.plain_issues)

    def save(self, file_name: str = ""):
        file_name = file_name or JSON_REPORT_FILE_NAME
        with open(file_name, "w") as f:
            json.dump(asdict(self), f, indent=4)
        logging.info(f"Report saved to {mc.utils.file_link(file_name)}")

    @staticmethod
    def load(file_name: str | Path = ""):
        with open(file_name or JSON_REPORT_FILE_NAME, "r") as f:
            data = json.load(f)
        data.pop("total_issues", None)
        return Report(**data)

    def render(
        self,
        config: ProjectConfig = None,
        report_format: Format = Format.MARKDOWN,
    ) -> str:
        config = config or ProjectConfig.load()
        template = getattr(config, f"report_template_{report_format}")
        return mc.prompt(
            template,
            report=self,
            ui=mc.ui,
            Fore=Fore,
            Style=Style,
            Back=Back,
            file_link=file_link,
            textwrap=textwrap,
            block_wrap_lr=block_wrap_lr,
            max_line_len=max_line_len,
            HTML_TEXT_ICON=HTML_TEXT_ICON,
            HTML_CR_COMMENT_MARKER=HTML_CR_COMMENT_MARKER,
            remove_html_comments=remove_html_comments,
            **config.prompt_vars
        )

    def to_cli(self, report_format=Format.CLI):
        output = self.render(report_format=report_format)
        print("")
        print(output)
