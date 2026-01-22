"""
Gito CI Deployment

Generates and deploys CI workflow files for automatic AI-powered code reviews.

Supported platforms:
  - GitHub Actions (.github/workflows/)
  - GitLab CI (.gitlab/ci/)

The command:
  1. Detects your Git provider (GitHub/GitLab)
  2. Prompts for LLM configuration (provider, model)
  3. Creates workflow files from templates
  4. Optionally commits and pushes to a dedicated branch
  5. Provides instructions for secrets setup

Usage:
  gito deploy [--api-type TYPE] [--commit] [--rewrite] [--to-branch NAME]

Aliases: init, connect, ci
"""
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
from ..utils.package_metadata import version
from ..utils.git_platform.platform_types import PlatformType, identify_git_platform
from ..utils.git_platform.github import get_gh_create_pr_link, get_gh_secrets_link
from ..utils.git_platform.gitlab import (
    get_gitlab_access_tokens_link,
    get_gitlab_create_mr_link,
    get_gitlab_secrets_link,
)
from ..utils.git import get_cwd_repo_or_fail
from ..utils.cli import logo


def merge_gitlab_configs(
    file: Path,
    vars: dict  # vars reserved for future use / other merges
) -> str:
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
        'gito-code-review.yml' in str(item.get('local', ''))
        if isinstance(item, dict) else 'gito-code-review.yml' in str(item or '')
        for item in config['include']
    )

    if not include_exists:
        config['include'].append(new_include)

    return yaml.dump(config, default_flow_style=False, sort_keys=False, indent=2)


GIT_PROVIDER_WORKFLOWS = {
    PlatformType.GITHUB: dict(
        code_review=dict(
            path=Path(".github/workflows/gito-code-review.yml"),
            template="workflows/github/gito-code-review.yml.j2",
        ),
        react_to_comments=dict(
            path=Path(".github/workflows/gito-react-to-comments.yml"),
            template="workflows/github/gito-react-to-comments.yml.j2",
        ),
    ),
    PlatformType.GITLAB: dict(
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


def _show_intro(console: Console):
    """Show introduction message for deploy command."""
    def num(n):
        return f"[green][dim][[/dim][{n:02d}][dim]][/dim][/green]"
    console.print(Panel(
        title="CI Setup",
        renderable=(
            " [bold]Wiring myself into pipelines...[/bold]\n"
            f" [green][dim]⟩[/dim]⟩⟩ INTEGRATION SEQUENCE ⟩⟩[dim]⟩[/dim] [/green] \n"
            f" {num(1)} [bold]C[/bold]onfigure language model\n"
            f" {num(2)} [bold]W[/bold]rite workflow files \n"
            f" {num(3)} [bold]C[/bold]ommit [dim]&[/dim] push to dedicated branch "
            f"[dim][[/dim]optional[dim]][/dim]       \n"  # trailing spaces to align with Logo
            f" {num(4)} [bold]G[/bold]uide you through secrets configuration"
        ),
        border_style="green",
        expand=False,
    ))


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
        help="Branch name for new PR containing Gito CI workflows"
    ),
    token: str = typer.Option(
        "", help="GitHub token (or set GITHUB_TOKEN env var)"
    ),
    model: str = typer.Option(
        None,
        help=(
            "Language model to use "
            "(interactive if omitted; \"default\" selects the recommended model)"
        ),
    )
) -> bool:
    """Deploy Gito to repository's CI pipeline for automatic code reviews."""
    print(logo())
    repo: Repo = get_cwd_repo_or_fail()
    console = Console()
    _show_intro(console)

    git_platform_type: PlatformType | None = identify_git_platform(repo)
    if not git_platform_type:
        ui.error("No supported Git provider detected.")
        if ui.ask_yn("Choose Git provider manually?"):
            git_platform_type = ui.ask_choose("Choose your Git provider", list(PlatformType))
        else:
            return False
    if git_platform_type not in GIT_PROVIDER_WORKFLOWS:
        ui.error(
            f"Git provider {ui.bright(git_platform_type)} is not supported "
            "for automatic deployment yet.\n"
            "Please create CI workflows manually."
        )
        return False
    workflow_files = GIT_PROVIDER_WORKFLOWS[git_platform_type]
    for file_config in workflow_files.values():
        file = file_config["path"]
        if file.exists():
            message = f"Gito CI workflow already exists at {utils.file_link(file)}"
            if rewrite:
                ui.warning(message)
            else:
                message += "\nUse --rewrite to replace existing files."
                ui.error(message)
                return False

    # configure LLM
    api_type, secret_name, model = _configure_llm(api_type, model)

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
            content = merge_function(file, vars=template_vars)
        else:
            template: str = file_config["template"]
            content = mc.tpl(template, **template_vars)
        if not content.endswith("\n"):
            content = content.rstrip() + "\n"
        file.write_text(content, encoding='utf-8')
        created_files.append(file)
    print(
        mc.ui.green("Gito CI workflows have been created.\n"),
        *[f"  - {mc.utils.file_link(file)}\n" for file in created_files]
    )

    # commit and push
    ui.warning('[!] Please review created files before proceeding.')

    need_to_commit = commit is True or commit is None and mc.ui.ask_yn(
        f"Commit & push CI workflows to a {mc.ui.green(to_branch)} branch?"
    )
    is_committed = False
    is_pushed = False

    if need_to_commit:
        try:
            active_branch_name = repo.active_branch.name
        except TypeError:
            active_branch_name = ""
        if active_branch_name != to_branch:
            repo.git.checkout("-b", to_branch)
        repo.git.add([str(file) for file in created_files])
        is_committed = _try_commit_workflow_changes(repo)
        if is_committed:
            is_pushed = _try_push_branch(repo, to_branch)
            if is_pushed:
                if git_platform_type == PlatformType.GITHUB:
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
                elif git_platform_type == PlatformType.GITLAB:
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
    if not need_to_commit or not is_committed:
        console.print(Panel(
            title="Next step: Deliver CI workflows to the repository",
            renderable=(
                "Commit and push created CI workflow files to your main repository branch "
                "to activate Gito."
            ),
            border_style="yellow",
            expand=False,
        ))

    _show_create_secrets_instructions(console, git_platform_type, repo, secret_name)
    return True


def _try_commit_workflow_changes(repo: Repo) -> bool:
    """
    Try to commit workflow changes.
    Prints success or error message.
    Args:
        repo (Repo): Git repository.
    Returns:
        bool: True if commit was successful, False otherwise.
    """
    try:
        repo.git.commit("-m", "Add Gito CI workflows")
        print(ui.green("Changes committed."))
        return True
    except GitCommandError as e:
        if "nothing added" in str(e):
            ui.error("Failed to commit changes: nothing was added")
        else:
            ui.error(f"Failed to commit changes: {e}")
        return False


def _try_push_branch(repo: Repo, branch: str) -> bool:
    """
    Try to push branch to origin.
    Prints success or error message.
    Args:
        repo (Repo): Git repository.
        branch (str): Branch name.
    Returns:
        bool: True if push was successful, False otherwise.
    """
    try:
        repo.git.push("origin", branch)
        print(ui.green(
            f"Changes pushed to {ui.bright}{ui.white}{branch}{ui.reset}{ui.green} branch."
        ))
        return True
    except GitCommandError as e:
        ui.error(f"Failed to push changes: {e}")
        return False


def _configure_llm(
    api_type: str | ApiType | None,
    model: str | None = None,
) -> tuple[ApiType, str, str]:
    """
    Configure LLM.
    Args:
        api_type (str | ApiType | None): API type as string.
        model (str | None): Model name.
    Returns:
        tuple[ApiType, str, str]: (api_type, secret_name, default_model
    """
    api_types = {
        ApiType.ANTHROPIC: "Anthropic",
        ApiType.OPENAI: "OpenAI",
        ApiType.GOOGLE: "Google",
    }
    model_proposals = {
        ApiType.ANTHROPIC: {
            "claude-opus-4-5": f"Claude Opus 4.5 {ui.dim}(most capable)",
            "claude-sonnet-4-5": f"Claude Sonnet 4.5 {ui.dim}(balanced)",
            "claude-haiku-4-5": f"Claude Haiku 4.5 {ui.dim}(cheapest but less capable)",
        },
        ApiType.OPENAI: {
            "gpt-5.2": f"GPT-5.2 {ui.dim} (recommended)",
            "gpt-5.1": "GPT-5.1",
            "gpt-5": "GPT-5",
            "gpt-5-mini": "GPT-5 Mini",
        },
        ApiType.GOOGLE: {
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

    if api_type not in api_types:
        orig_value = api_type
        api_type = str(api_type).lower()
        if api_type == "openai":
            api_type = ApiType.OPENAI
        if api_type not in api_types:
            ui.error(f"Unsupported API type: {orig_value}")
            raise typer.Exit(2)
    secret_names = {
        ApiType.ANTHROPIC: "ANTHROPIC_API_KEY",
        ApiType.OPENAI: "OPENAI_API_KEY",
        ApiType.GOOGLE: "GOOGLE_API_KEY",
    }
    default_models = {
        ApiType.ANTHROPIC: "claude-sonnet-4-5",
        ApiType.OPENAI: "gpt-5.2",
        ApiType.GOOGLE: "gemini-2.5-pro",
    }
    use_default_model = model == "default"
    if not model or use_default_model:
        model = default_models.get(api_type, "")
        if use_default_model and not model:
            ui.error(f"No default model for API type: {api_type}")
        if not use_default_model or not model:
            if api_type in model_proposals:
                model = ui.ask_choose(
                    "Select a model",
                    model_proposals[api_type],
                    default=default_models[api_type],
                )
            else:
                model = ui.ask_non_empty("Enter the model name")

    return api_type, secret_names[api_type], model


def _show_create_secrets_instructions(
    console: Console,
    git_platform: PlatformType,
    repo: Repo,
    secret_name: str
):
    """Show instructions to create secrets in the repository."""
    details = ""
    secrets = f"  {secret_name}\n"
    title = "Add your LLM API key to repository secrets"
    if git_platform == PlatformType.GITHUB:
        if secrets_url := get_gh_secrets_link(repo):
            details = f"\n\nAdd it here:  [link]{secrets_url}[/link]"
    elif git_platform == PlatformType.GITLAB:
        title = "Add LLM API key and GitLab access token to CI/CD variables"
        details = (
            "\n\nAdd it in your GitLab project settings under "
            "Settings → CI/CD → Variables."
        )
        if secrets_url := get_gitlab_secrets_link(repo):
            details += f"\n[link]{secrets_url}[/link]"
        secrets += (
            "  GITLAB_ACCESS_TOKEN [dim]— Project Access Token "
            "with 'api' scope and 'Reporter' role\n"
            "    Create: Settings → Access Tokens[/dim]\n"
        )
        if manage_tokens_url := get_gitlab_access_tokens_link(repo):
            secrets += f"    [link]{manage_tokens_url}[/link]\n"

    console.print(Panel(
        title=f"Final step: {title}",
        renderable=(
            f"[bold yellow]Required[/bold yellow]\n"
            f"{secrets}"
            f"\n"
            f"[bold dim]Optional — Issue trackers[/bold dim]\n"
            f"  LINEAR_API_KEY, JIRA_URL, JIRA_USER, JIRA_TOKEN{details}"
        ),
        border_style="green",
        expand=False,
    ))
    if git_platform == PlatformType.GITLAB:
        console.print(Panel(
            title="Variable Settings",
            renderable=(
                "☑ Mask variable   ☐ Protect variable (uncheck, or MRs won't have access)\n"
                "\n"
                "Public repos: enable \"Require approval\" for fork pipelines"
                " in CI/CD settings to prevent secret leaks."
                "\n"
                "Docs: [link]https://docs.gitlab.com/ci/variables/[/link]"
            ),
            border_style="yellow",
            expand=False,
        ))
