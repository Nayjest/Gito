import logging
import os
import re
from dataclasses import dataclass, field

import git

from .utils.git_platform.github import is_running_in_github_action, gh_ci_src_branch
from .utils.git_platform.gitlab import is_running_in_gitlab_ci


@dataclass
class IssueTrackerIssue:
    title: str = field(default="")
    description: str = field(default="")
    url: str = field(default="")


def extract_issue_key(branch_name: str, min_len=2, max_len=10) -> str | None:
    """
    Extracts an issue key from the given branch name.
    The issue key is expected to be in the format PROJECT-123,
    where PROJECT is an uppercase alphanumeric string of length between min_len and max_len,
    followed by a hyphen and a numeric identifier.
    Args:
        branch_name (str): The branch name to extract the issue key from.
        min_len (int): Minimum length of the project key part.
        max_len (int): Maximum length of the project key part.
    Returns:
        str | None: The extracted issue key, or None if not found.
    """
    boundary = r'\b|_|-|/|\\'
    pattern = fr"(?:{boundary})([A-Z][A-Z0-9]{{{min_len - 1},{max_len - 1}}}-\d+)(?:{boundary})"
    match = re.search(pattern, branch_name)
    return match.group(1) if match else None


def get_branch(repo: git.Repo):
    if is_running_in_github_action():
        return gh_ci_src_branch()
    elif is_running_in_gitlab_ci():
        # @todo: consider using gitlab_ci_src_branch(),
        # test differences with variables used there for different workflows first
        # See: https://docs.gitlab.com/ci/variables/predefined_variables/
        gitlab_ref = os.getenv('CI_COMMIT_REF_NAME')
        if gitlab_ref:
            return gitlab_ref
    try:
        branch_name = repo.active_branch.name
        return branch_name
    except Exception as e:  # @todo: specify more precise exception
        logging.error("Could not determine the active branch name: %s", e)
        return None


def resolve_issue_key(repo: git.Repo):
    branch_name = get_branch(repo)
    if not branch_name:
        logging.error("No active branch found in the repository, cannot determine issue key.")
        return None

    if not (issue_key := extract_issue_key(branch_name)):
        logging.error(f"No issue key found in branch name: {branch_name}")
        return None
    return issue_key
