from dataclasses import dataclass, field
from typing import Iterable, TYPE_CHECKING

from unidiff.patch import PatchSet, PatchedFile
import git


if TYPE_CHECKING:
    from .project_config import ProjectConfig
    from .report_struct import Report


@dataclass
class Context:
    report: "Report"
    config: "ProjectConfig"
    diff: PatchSet | Iterable[PatchedFile]
    repo: git.Repo
    pipeline_out: dict = field(default_factory=dict)
