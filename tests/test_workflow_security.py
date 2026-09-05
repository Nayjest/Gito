"""
Guards the fork-PR secret-exposure rule enforced by .github/workflows.

Workflows triggered by `pull_request_target` or `issue_comment` run in the *base*
repository's trusted context: base-branch secrets, a writable GITHUB_TOKEN, and the
default-branch cache. If such a job also checks out and runs code from the pull
request, an external contributor controls what executes against those secrets.

The mitigation used in this repository is a GitHub Environment with required
reviewers, which pauses the job until a maintainer approves it. This test fails if a
job that needs the gate is missing it.
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS_DIR = Path(__file__).parent.parent / ".github" / "workflows"

# Triggers that run with the base repository's secrets even for pull requests
# opened from forks. `pull_request` is absent deliberately: it runs in the fork's
# own context with no access to secrets.
UNTRUSTED_TRIGGERS = {"pull_request_target", "issue_comment"}


def load_workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def triggers_of(workflow: dict) -> set[str]:
    """Names of the events a workflow reacts to.

    PyYAML follows YAML 1.1, where the unquoted key `on:` is the boolean True rather
    than the string "on" -- hence the two lookups. The value itself is one of three
    shapes, all of which yield trigger names when iterated:

        on: push                          -> str
        on: [push, pull_request]          -> list
        on:                               -> dict
          pull_request_target:
            types: [opened]
    """
    events = workflow.get(True, workflow.get("on"))
    if isinstance(events, str):
        return {events}
    return set(events)


def uses_secrets(job: dict) -> bool:
    """Whether any expression in the job dereferences `secrets.*`."""
    return "secrets." in yaml.dump(job)


def gated_jobs_needing_review() -> list[tuple[str, str, dict]]:
    """(workflow name, job name, job) for every job running untrusted-trigger code."""
    jobs = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        workflow = load_workflow(path)
        if not triggers_of(workflow) & UNTRUSTED_TRIGGERS:
            continue
        for job_name, job in (workflow.get("jobs") or {}).items():
            jobs.append((path.name, job_name, job))
    return jobs


@pytest.mark.parametrize(
    "workflow_name, job_name, job",
    gated_jobs_needing_review(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_secret_using_job_on_untrusted_trigger_declares_environment(
    workflow_name, job_name, job
):
    if not uses_secrets(job):
        return
    assert job.get("environment"), (
        f"{workflow_name}: job '{job_name}' runs on an untrusted trigger "
        f"({' or '.join(sorted(UNTRUSTED_TRIGGERS))}) and uses secrets, but declares "
        f"no `environment:`. Without an environment that has required reviewers, code "
        f"from a fork pull request executes against those secrets with no approval. "
        f"See .github/workflows/gito-code-review.yml for the expected pattern."
    )
