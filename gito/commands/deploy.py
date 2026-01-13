"""Deploy Gito to repository's CI pipeline for automatic code reviews."""
import logging
from pathlib import Path

import typer
import yaml
import microcore as mc
from microcore import ApiType, ui, utils
from git import Repo, GitCommandError
from rich.panel import Panel
from rich.console import Console

from ..core import get_base_branch
from ..cli_base import app
from ..gh_api import gh_api
from ..identify_git_provider import identify_git_provider, GitProvider
from ..utils.package_metadata import version
from ..utils.github import get_gh_create_pr_link, get_gh_secrets_link
from ..utils.gitlab import get_gitlab_create_mr_link, get_gitlab_secrets_link
from ..utils.git import get_cwd_repo_or_fail


def merge_gitlab_configs(file: Path, vars: dict):
    """Merge GitLab CI configuration files."""
    # Read existing config or start with empty dict
    if file.exists():
        with open(file, 'r') as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    # Ensure 'stages' exists and add 'review' if not present
    if 'stages' not in config:
        config['stages'] = []
    if 'review' not in config['stages']:
        config['stages'].insert(0, 'review')  # Insert at beginning

    # Ensure 'include' exists and add the local include if not present
    if 'include' not in config:
        config['include'] = []

    # Handle case where include might be a single item (not a list)
    if not isinstance(config['include'], list):
        config['include'] = [config['include']]

    # Check if the local include already exists
    new_include = {'local': '.gitlab/ci/gito-code-review.yml'}
    include_exists = any(
        'gito-code-review.yml' in str(item.get('local'))
        if isinstance(item, dict) else 'gito-code-review.yml' in str(item)
        for item in config['include']
    )

    if not include_exists:
        config['include'].append(new_include)

    content = yaml.dump(config, default_flow_style=False, sort_keys=False, indent=2)
    with open(file, 'w') as f:
        f.write(content)


GIT_PROVIDER_WORKFLOWS = {
    GitProvider.GITHUB: dict(
        code_review=dict(
            path=Path(".github/workflows/gito-code-review.yml"),
            template="workflows/github/gito-code-review.yml.j2",
        ),
        react_to_comments=dict(
            path=Path(".github/workflows/gito-react-to-comments.yml"),
            template="workflows/github/gito-react-to-comments.yml.j2",
        ),
    ),
    GitProvider.GITLAB: dict(
        code_review=dict(
            path=Path(".gitlab/ci/gito-code-review.yml"),
            template="workflows/gitlab/gito-code-review.yml.j2",
        ),
        gitlab_ci=dict(
            path=Path(".gitlab-ci.yml"),
            template="workflows/gitlab/.gitlab-ci.yml.j2",
            merge_function=merge_gitlab_configs,
        ),
    )
}


@app.command(
    name="deploy",
    help="\bCreate and deploy Gito workflows to your CI pipeline for automatic code reviews."
         "\nRun this command from your repository root."
         "\naliases: init, connect, ci"
)
@app.command(name="init", hidden=True)
@app.command(name="connect", hidden=True)
@app.command(name="ci", hidden=True)
def deploy(
    api_type: ApiType = typer.Option(None, help="LLM API type (interactive if omitted)"),
    commit: bool = typer.Option(None, help="Commit and push changes"),
    rewrite: bool = typer.Option(False, help="Overwrite existing configuration"),
    to_branch: str = typer.Option(
        default="gito-ci",
        help="Branch name for PR containing Gito CI workflows"
    ),
    token: str = typer.Option(
        "", help="GitHub token (or set GITHUB_TOKEN env var)"
    ),
) -> bool:
    """Deploy Gito to repository's CI pipeline for automatic code reviews."""
    repo: Repo = get_cwd_repo_or_fail()
    console = Console()

    provider: GitProvider | None = identify_git_provider(repo)
    if not provider:
        ui.error("No supported Git provider detected.")
        if ui.ask_yn("Choose Git provider manually?"):
            provider = ui.ask_choose("Choose your Git provider", list(GitProvider))
        else:
            return False
    if provider not in GIT_PROVIDER_WORKFLOWS:
        ui.error(
            f"Git provider {ui.bright(provider)} is not supported for automatic deployment yet.\n"
            f"Please create CI workflows manually."
        )
        return False
    workflow_files = GIT_PROVIDER_WORKFLOWS[provider]
    for file_config in workflow_files.values():
        file = file_config["path"]
        if file.exists():
            message = f"Gito CI workflow already exists at {utils.file_link(file)}."
            if rewrite:
                ui.warning(message)
            else:
                message += "\nUse --rewrite to replace existing files."
                ui.error(message)
                return False

    # configure LLM
    api_type, secret_name, model = _configure_llm(api_type)

    # generate workflow files from templates
    major, minor, *_ = version().split(".")
    template_vars = dict(
        model=model,
        api_type=api_type,
        secret_name=secret_name,
        major=major,
        minor=minor,
        ApiType=ApiType,
        remove_indent=True,
    )
    created_files = []
    for key, file_config in workflow_files.items():
        file: Path = file_config["path"]
        file.parent.mkdir(parents=True, exist_ok=True)
        if file.exists() and "merge_function" in file_config:
            ui.warning("Merging Gito CI workflow into existing file:", utils.file_link(file))
            merge_function = file_config["merge_function"]
            merge_function(file, vars=template_vars)
        else:
            template: str = file_config["template"]
            content = mc.tpl(template, **template_vars)
            if not content.endswith("\n"):
                content = content.rstrip() + "\n"
            file.write_text(content)
        created_files.append(file)
    print(
        mc.ui.green("Gito CI workflows have been created.\n"),
        *[f"  - {mc.utils.file_link(file)}\n" for file in created_files]
    )

    # commit and push
    ui.warning('[!] Please review created files before proceeding.')
    if commit is True or commit is None and mc.ui.ask_yn(
        f"Commit & push CI workflows to a {mc.ui.green(to_branch)} branch?"
    ):
        repo.git.add([str(file) for file in created_files])
        if not repo.active_branch.name.startswith(to_branch):
            repo.git.checkout("-b", to_branch)
        try:
            repo.git.commit("-m", "Add Gito CI workflows")
        except GitCommandError as e:
            if "nothing added" in str(e):
                ui.warning("Failed to commit changes: nothing was added")
            else:
                ui.error(f"Failed to commit changes: {e}")
                return False

        repo.git.push("origin", to_branch)
        print(f"Changes pushed to {ui.green(to_branch)} branch.")
        if provider == GitProvider.GITHUB:
            try:
                api = gh_api(repo=repo, token=token)
                base = get_base_branch(repo).split('/')[-1]
                logging.info(f"Creating PR {ui.green(to_branch)} -> {ui.yellow(base)}...")
                res = api.pulls.create(
                    head=to_branch,
                    base=base,
                    title="Add Gito CI workflows",
                )
                print(f"Pull request #{res.number} created successfully:\n{res.html_url}")
            except Exception as e:
                mc.ui.error(f"Failed to create pull request automatically: {e}")
                create_pr_link = get_gh_create_pr_link(repo, to_branch)
                if create_pr_link:
                    details = f":\n[link]{create_pr_link}[/link]"
                else:
                    details = "."
                console.print(Panel(
                    title="Next step",
                    renderable=(
                        f"Please create a PR from '{to_branch}' "
                        f"to your main branch and merge it{details}"
                    ),
                    border_style="yellow",
                    expand=False,
                ))
        elif provider == GitProvider.GITLAB:
            create_pr_link = get_gitlab_create_mr_link(repo, to_branch)
            if create_pr_link:
                details = f":\n[link]{create_pr_link}[/link]"
            else:
                details = "."
            console.print(Panel(
                title="Next step",
                renderable=(
                    f"Please create a Merge Request from branch '{to_branch}' "
                    f"to your main branch and merge it{details}"
                ),
                border_style="yellow",
                expand=False,
            ))
        else:
            console.print(Panel(
                title="Next step",
                renderable=f"Please merge branch named '{to_branch}' to your main branch.",
                border_style="yellow",
                expand=False,
            ))
    else:
        console.print(Panel(
            title="Next step: Deliver CI workflows to the repository",
            renderable=(
                "Commit and push created CI workflow files to your main repository branch "
                "to activate Gito."
            ),
            border_style="yellow",
            expand=False,
        ))

    details = ""
    if provider == GitProvider.GITHUB:
        if secrets_url := get_gh_secrets_link(repo):
            details = f"\n\nAdd it here:  [link]{secrets_url}[/link]"
    elif provider == GitProvider.GITLAB:
        details = (
            "\n\nAdd it in your GitLab project settings under"
            "\nSettings -> CI/CD -> Variables."
        )
        if secrets_url := get_gitlab_secrets_link(repo):
            details += f"\n[link]{secrets_url}[/link]"
    console.print(Panel(
        title="Final step: Add your LLM API key to repository secrets",
        renderable=(
            f"[bold yellow]Required[/bold yellow]\n"
            f"  {secret_name}\n"
            f"\n"
            f"[bold dim]Optional — Issue trackers[/bold dim]\n"
            f"  LINEAR_API_KEY, JIRA_URL, JIRA_USER, JIRA_TOKEN{details}"
        ),
        border_style="green",
        expand=False,
    ))
    return True


def _configure_llm(api_type: str | ApiType | None) -> tuple[ApiType, str, str]:
    """
    Configure LLM.
    Args:
        api_type (str | ApiType | None): API type as string.
    Returns:
        tuple[ApiType, str, str]: (api_type, secret_name, default_model
    """
    api_types = {
        ApiType.ANTHROPIC: "Anthropic",
        ApiType.OPEN_AI: "OpenAI",
        ApiType.GOOGLE_AI_STUDIO: "Google",
    }
    model_proposals = {
        ApiType.ANTHROPIC: {
            "claude-opus-4-5": f"Claude Opus 4.5 {ui.dim}(most capable)",
            "claude-sonnet-4-5": f"Claude Sonnet 4.5 {ui.dim}(balanced)",
            "claude-haiku-4-5": f"Claude Haiku 4.5 {ui.dim}(cheapest but less capable)",
        },
        ApiType.OPEN_AI: {
            "gpt-5.2": f"GPT-5.2 {ui.dim} (recommended)",
            "gpt-5.1": "GPT-5.1",
            "gpt-5": "GPT-5",
            "gpt-5-mini": "GPT-5 Mini",
        },
        ApiType.GOOGLE_AI_STUDIO: {
            "gemini-2.5-pro": "Gemini 2.5 Pro",
            "gemini-2.5-flash": "Gemini 2.5 Flash",
            "gemini-3-pro-preview": f"Gemini 3 Pro Preview {ui.dim}(rate limited)",
            "gemini-3-flash-preview": f"Gemini 3 Flash Preview {ui.dim}(rate limited)",
        }
    }
    if not api_type:
        api_type = mc.ui.ask_choose(
            "Which language model API should Gito use?",
            api_types,
        )
    elif api_type not in api_types:
        ui.error(f"Unsupported API type: {api_type}")
        raise typer.Exit(2)
    secret_names = {
        ApiType.ANTHROPIC: "ANTHROPIC_API_KEY",
        ApiType.OPEN_AI: "OPENAI_API_KEY",
        ApiType.GOOGLE_AI_STUDIO: "GOOGLE_API_KEY",
    }
    default_models = {
        ApiType.ANTHROPIC: "claude-sonnet-4-5",
        ApiType.OPEN_AI: "gpt-5.2",
        ApiType.GOOGLE_AI_STUDIO: "gemini-2.5-pro",
    }
    model = default_models[api_type]
    if api_type in model_proposals:
        model = mc.ui.ask_choose(
            "Select a model",
            model_proposals[api_type],
            default=default_models[api_type],
        )

    return api_type, secret_names[api_type], model
