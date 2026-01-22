import json
import logging
from dataclasses import field, asdict, is_dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Optional
from pkgutil import resolve_name

import textwrap
import microcore as mc
from microcore.utils import file_link
from colorama import Fore, Style, Back
from pydantic.dataclasses import dataclass

from .constants import JSON_REPORT_FILE_NAME, HTML_TEXT_ICON, HTML_CR_COMMENT_MARKER, REFS_VALUE_ALL
from .project_config import ProjectConfig
from .utils.string import block_wrap_lr, max_line_len
from .utils.html import remove_html_comments
from .utils.python import filter_kwargs
from .utils.markdown import syntax_hint
from .utils.git_platform.platform_types import PlatformType
from .utils.git_platform.adapters import (
    get_platform_adapter,
    BaseGitPlatform
)


@dataclass
class ReviewTarget:
    git_platform_type: Optional[PlatformType] = field(default=None)
    repo_url: Optional[str] = field(default=None)
    pull_request_id: Optional[str] = field(default=None)
    what: Optional[str] = field(default=None)
    against: Optional[str] = field(default=None)
    commit_sha: Optional[str] = field(default=None)
    filters: str | list[str] | None = field(default=None)
    use_merge_base: bool = field(default=True)
    active_branch: Optional[str] = field(default=None)

    def is_full_codebase_review(self) -> bool:
        return self.what == REFS_VALUE_ALL

    def get_platform_adapter(self, raise_exceptions: bool = False) -> Optional[BaseGitPlatform]:
        try:
            return get_platform_adapter(self.git_platform_type, repo_or_base_url=self.repo_url)
        except ValueError as e:
            if raise_exceptions:
                raise e
            logging.warning(f"Could not get platform adapter: {e}")
            return None


@dataclass
class RawIssue:
    @dataclass
    class AffectedCode:
        start_line: int = field()
        end_line: Optional[int] = field(default=None)
        proposal: Optional[str] = field(default="")

    title: str = field()
    details: Optional[str] = field(default="")
    severity: Optional[int] = field(default=None)
    confidence: Optional[int] = field(default=None)
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

    def code_link(self, review_target: Optional[ReviewTarget]) -> str:
        """Generate a link to the affected code in the git platform."""
        if not review_target:
            return ""
        if not (platform := review_target.get_platform_adapter(raise_exceptions=False)):
            return ""
        branch = review_target.active_branch or "main"
        if platform.is_running_in_ci() and (ci_branch := platform.ci_src_branch()):
            branch = ci_branch
        return platform.file_url(
            file=self.file,
            branch=branch,
            start_line=self.affected_lines[0].start_line if self.affected_lines else None,
            end_line=self.affected_lines[0].end_line if self.affected_lines else None,
        ) or ""


@dataclass
class ProcessingWarning:
    """
    Warning generated during code review of files
    """
    message: str = field()
    file: str | None = field(default=None)


@dataclass
class Report:
    class Format(StrEnum):
        MARKDOWN = "md"
        CLI = "cli"
        GITLAB_QUALITY_REPORT = "gitlab_quality_report"

    issues: dict[str, list[Issue]] = field(default_factory=dict)
    summary: str = field(default="")
    number_of_processed_files: int = field(default=0)
    total_issues: int = field(init=False)
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    model: str = field(default_factory=lambda: mc.config().MODEL or "")
    pipeline_out: dict = field(default_factory=dict)
    processing_warnings: list[ProcessingWarning] = field(default_factory=list)
    target: Optional[ReviewTarget] = field(default=None)

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
        vars = dict(
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
        template = getattr(config, f"report_template_{report_format}")
        if str(template).startswith("tpl:"):
            template_file = str(template)[4:]
        else:
            template_file = None
        if str(template).startswith("fn:"):
            fn = str(template)[3:]
        else:
            fn = None

        if template_file:
            return mc.tpl(template_file, **vars)
        elif fn:
            fn = resolve_name(fn)
            return fn(**vars)
        else:
            return mc.prompt(template, **vars)

    def to_cli(self, report_format=Format.CLI):
        output = self.render(report_format=report_format)
        print("")
        print(output)
