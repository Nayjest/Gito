import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import microcore as mc
from colorama import Fore, Style, Back
from microcore.utils import file_link
import textwrap

from .constants import JSON_REPORT_FILE_NAME, HTML_TEXT_ICON, HTML_CR_COMMENT_MARKER
from .project_config import ProjectConfig
from .utils import syntax_hint, block_wrap_lr, max_line_len, remove_html_comments


@dataclass
class Issue:
    @dataclass
    class AffectedCode:
        start_line: int = field()
        end_line: int | None = field(default=None)
        file: str = field(default="")
        proposal: str = field(default="")
        affected_code: str = field(default="")

        @property
        def syntax_hint(self) -> str:
            return syntax_hint(self.file)

    id: str = field()
    title: str = field()
    details: str = field(default="")
    severity: int | None = field(default=None)
    confidence: int | None = field(default=None)
    tags: list[str] = field(default_factory=list)
    file: str = field(default="")
    affected_lines: list[AffectedCode] = field(default_factory=list)

    def __post_init__(self):
        self.affected_lines = [
            Issue.AffectedCode(**dict(file=self.file) | i)
            for i in self.affected_lines
        ]

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

    def __post_init__(self):
        issue_id: int = 0
        for file in self.issues.keys():
            self.issues[file] = [
                Issue(
                    **{
                        "id": (issue_id := issue_id + 1),
                        "file": file,
                    } | issue
                )
                for issue in self.issues[file]
            ]
        self.total_issues = issue_id

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
