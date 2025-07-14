from dataclasses import dataclass, field
from typing import Iterable, TYPE_CHECKING

from unidiff.patch import PatchSet, PatchedFile
from git import Repo


if TYPE_CHECKING:
    from .project_config import ProjectConfig
    from .report_struct import Report


@dataclass
class Context:
    report: "Report"
    config: "ProjectConfig"
    diff: PatchSet | Iterable[PatchedFile]
    repo: Repo
    pipeline_out: dict = field(default_factory=dict)
