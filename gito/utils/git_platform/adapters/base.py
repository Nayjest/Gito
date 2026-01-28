import abc
from typing import ClassVar, Optional

import git

from ..platform_types import PlatformType
from ..shared import get_repo_base_web_url


class BaseGitPlatform(abc.ABC):
    """
    Base class for Git hosting platform interactions.
    """
    type: ClassVar[PlatformType]
    repo_base_url: str

    def __init__(self, repo: git.Repo = None, repo_base_url: str = None):
        if not repo and not repo_base_url:
            raise ValueError("Must specify repo or repo_base_url")
        if repo:
            detected_repo_base_url = get_repo_base_web_url(repo)
            if repo_base_url and detected_repo_base_url != repo_base_url:
                raise ValueError(
                    f"Provided repo_base_url '{repo_base_url}' "
                    f"does not match detected '{detected_repo_base_url}'"
                )
            repo_base_url = detected_repo_base_url
        self.repo_base_url = repo_base_url

    @abc.abstractmethod
    def is_running_in_ci(self) -> bool:
        """
        Check if the current environment is a Continuous Integration (CI) environment
        of target git platform.
        Returns:
            bool: True if running inside a CI, False otherwise.
        """

    @abc.abstractmethod
    def ci_src_branch(self) -> Optional[str]:
        """
        Get the source branch name from the CI environment.
        Returns:
            Optional[str]: The source branch name if available, None otherwise.
        """

    @abc.abstractmethod
    def create_pr_url(self, branch: str) -> Optional[str]:
        """
        Return a URL to create a pull/merge request for the given branch.
        Args:
            branch (str): The branch name for which to create the PR/MR link.
        Returns:
            Optional[str]: The URL to create the PR/MR, or None if not applicable.
        """

    @abc.abstractmethod
    def secrets_management_url(self):
        """
        Return a URL to the secrets management page of the git platform.
        Returns:
            Optional[str]: The URL to the secrets management page, or None if not applicable.
        """

    @abc.abstractmethod
    def file_url(
        self,
        file: str,
        branch="main",
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Optional[str]:
        """
        Return a URL to view a file in the repository.
        Args:
            file (str): The file path in the repository.
            branch (str): The branch name. Defaults to "main".
            start_line (Optional[int]): The starting line number for highlighting. Defaults to None.
            end_line (Optional[int]): The ending line number for highlighting. Defaults to None.
        Returns:
            Optional[str]: The URL to view the file, or None if not applicable.
        """
