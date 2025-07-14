import logging
import os
import re
from dataclasses import dataclass, field

import git
from gito.utils import is_running_in_github_action


@dataclass
class IssueTrackerIssue:
    title: str = field(default="")
    description: str = field(default="")
    url: str = field(default="")


def extract_issue_key(branch_name: str, min_len=2, max_len=10) -> str | None:
    boundary = r'\b|_|-|/|\\'
    pattern = fr"(?:{boundary})([A-Z][A-Z0-9]{{{min_len - 1},{max_len - 1}}}-\d+)(?:{boundary})"
    match = re.search(pattern, branch_name)
    return match.group(1) if match else None


def get_branch(repo: git.Repo):
    if is_running_in_github_action():
        branch_name = os.getenv('GITHUB_HEAD_REF')
        if branch_name:
            return branch_name

        github_ref = os.getenv('GITHUB_REF', '')
        if github_ref.startswith('refs/heads/'):
            return github_ref.replace('refs/heads/', '')
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
