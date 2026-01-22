from typing import Optional

from ..platform_types import PlatformType
from ..gitlab import (
    get_gitlab_secrets_link,
    is_running_in_gitlab_ci,
    gitlab_ci_src_branch,
    get_gitlab_create_mr_link,
    get_gitlab_file_link
)
from .base import BaseGitPlatform


class GitLabPlatform(BaseGitPlatform):
    type: PlatformType = PlatformType.GITLAB

    def is_running_in_ci(self) -> bool:
        return is_running_in_gitlab_ci()

    def ci_src_branch(self) -> Optional[str]:
        return gitlab_ci_src_branch()

    def create_pr_url(self, branch: str) -> Optional[str]:
        return get_gitlab_create_mr_link(self.repo_base_url, branch)

    def secrets_management_url(self) -> Optional[str]:
        return get_gitlab_secrets_link(self.repo_base_url)

    def file_url(
        self,
        file: str,
        branch="main",
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Optional[str]:
        return get_gitlab_file_link(
            repo_or_base_url=self.repo_base_url,
            file=file,
            branch=branch,
            start_line=start_line,
            end_line=end_line,
        )
