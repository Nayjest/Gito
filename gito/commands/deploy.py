import logging
from pathlib import Path

import microcore as mc
from microcore import ApiType, ui, utils
from git import Repo, GitCommandError
import typer

from ..core import get_base_branch
from ..utils import version, extract_gh_owner_repo
from ..cli_base import app
from ..gh_api import gh_api


@app.command(
    name="deploy",
    help="\bCreate and configure Gito GitHub Actions for current repository.\naliases: init"
)
@app.command(name="init", hidden=True)
def deploy(
    api_type: ApiType = None,
    commit: bool = None,
    rewrite: bool = False,
    to_branch: str = typer.Option(
        default="gito_deploy",
        help="Branch name for new PR containing with Gito workflows commit"
    ),
    token: str = typer.Option(
        "", help="GitHub token (or set GITHUB_TOKEN env var)"
    ),
):
    repo = Repo(".")
    workflow_files = dict(
        code_review=Path(".github/workflows/gito-code-review.yml"),
        react_to_comments=Path(".github/workflows/gito-react-to-comments.yml")
    )
    for file in workflow_files.values():
        if file.exists():
            message = f"Gito workflow already exists at {utils.file_link(file)}."
            if rewrite:
                ui.warning(message)
            else:
                message += "\nUse --rewrite to overwrite it."
                ui.error(message)
                return False

    api_types = [ApiType.ANTHROPIC, ApiType.OPEN_AI, ApiType.GOOGLE_AI_STUDIO]
    default_models = {
        ApiType.ANTHROPIC: "claude-sonnet-4-20250514",
        ApiType.OPEN_AI: "gpt-4.1",
        ApiType.GOOGLE_AI_STUDIO: "gemini-2.5-pro",
    }
    secret_names = {
        ApiType.ANTHROPIC: "ANTHROPIC_API_KEY",
        ApiType.OPEN_AI: "OPENAI_API_KEY",
        ApiType.GOOGLE_AI_STUDIO: "GOOGLE_AI_API_KEY",
    }
    if not api_type:
        api_type = mc.ui.ask_choose(
            "Choose your LLM API type",
            api_types,
        )
    elif api_type not in api_types:
        mc.ui.error(f"Unsupported API type: {api_type}")
        return False
    major, minor, *_ = version().split(".")
    template_vars = dict(
        model=default_models[api_type],
        api_type=api_type,
        secret_name=secret_names[api_type],
        major=major,
        minor=minor,
        ApiType=ApiType,
        remove_indent=True,
    )
    gito_code_review_yml = mc.tpl(
        "github_workflows/gito-code-review.yml.j2",
        **template_vars
    )
    gito_react_to_comments_yml = mc.tpl(
        "github_workflows/gito-react-to-comments.yml.j2",
        **template_vars
    )

    workflow_files["code_review"].parent.mkdir(parents=True, exist_ok=True)
    workflow_files["code_review"].write_text(gito_code_review_yml)
    workflow_files["react_to_comments"].write_text(gito_react_to_comments_yml)
    print(
        mc.ui.green("Gito workflows have been created.\n")
        + f"  - {mc.utils.file_link(workflow_files['code_review'])}\n"
        + f"  - {mc.utils.file_link(workflow_files['react_to_comments'])}\n"
    )
    owner, repo_name = extract_gh_owner_repo(repo)
    if commit is True or commit is None and mc.ui.ask_yn(
        "Do you want to commit and push created GitHub workflows to a new branch?"
    ):
        repo.git.add([str(file) for file in workflow_files.values()])
        if not repo.active_branch.name.startswith(to_branch):
            repo.git.checkout("-b", to_branch)
        try:
            repo.git.commit("-m", "Deploy Gito workflows")
        except GitCommandError as e:
            if "nothing added" in str(e):
                ui.warning("Failed to commit changes: nothing was added")
            else:
                ui.error(f"Failed to commit changes: {e}")
                return False

        repo.git.push("origin", to_branch)
        print(f"Changes pushed to {to_branch} branch.")
        try:
            api = gh_api(repo=repo)
            base = get_base_branch(repo).split('/')[-1]
            logging.info(f"Creating PR {ui.green(to_branch)} -> {ui.yellow(base)}...")
            res = api.pulls.create(
                head=to_branch,
                base=base,
                title="Deploy Gito workflows",
            )
            print(f"Pull request #{res.number} created successfully:\n{res.html_url}")
        except Exception as e:
            mc.ui.error(f"Failed to create pull request automatically: {e}")
            print(
                f"Please create a PR from '{to_branch}' to your main branch and merge it:\n"
                f"https://github.com/{owner}/{repo_name}/compare/{to_branch}?expand=1"
            )
    else:
        print(
            "Now you can commit and push created GitHub workflows to your main repository branch.\n"
        )

    print(
        "(!IMPORTANT):\n"
        f"Add {mc.ui.cyan(secret_names[api_type])} with actual API_KEY "
        "to your repository secrets here:\n"
        f"https://github.com/{owner}/{repo_name}/settings/secrets/actions"
    )
    return True
