from typing import Optional

from ..platform_types import PlatformType
from ..github import (
    get_gh_secrets_link,
    is_running_in_github_action,
    gh_ci_src_branch,
    get_gh_create_pr_link,
    get_gh_file_link,
)
from .base import BaseGitPlatform


class GitHubPlatform(BaseGitPlatform):
    type: PlatformType = PlatformType.GITHUB

    def is_running_in_ci(self) -> bool:
        return is_running_in_github_action()

    def ci_src_branch(self) -> Optional[str]:
        return gh_ci_src_branch()

    def create_pr_url(self, branch: str) -> Optional[str]:
        return get_gh_create_pr_link(self.repo_base_url, branch)

    def secrets_management_url(self) -> Optional[str]:
        return get_gh_secrets_link(self.repo_base_url)

    def file_url(
        self,
        file: str,
        branch="main",
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Optional[str]:
        return get_gh_file_link(
            repo_or_base_url=self.repo_base_url,
            file=file,
            branch=branch,
            start_line=start_line,
            end_line=end_line,
        )
