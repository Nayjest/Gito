import logging
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path, PurePosixPath

import microcore as mc
from microcore import ui
from git import Repo

from .constants import (
    PROJECT_CONFIG_BUNDLED_DEFAULTS_FILE,
    PROJECT_CONFIG_FILE_NAME,
    PROJECT_CONFIG_FILE_PATH,
    PROJECT_GITO_FOLDER,
)
from .pipeline import PipelineStep
from .utils.git_platform.github import detect_github_env


# Fields that should be concatenated (cumulative) across hierarchy levels,
# de-duplicated while preserving root-first order.
_LIST_MERGE_FIELDS = frozenset({"aux_files", "exclude_files", "mention_triggers"})

# Directories to skip when walking the repo for nested .gito/config.toml files.
_DISCOVERY_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn",
    "node_modules", ".venv", "venv", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", ".idea", ".vscode",
})


@dataclass
class ProjectConfig:
    prompt: str = ""
    summary_prompt: str = ""
    answer_prompt: str = ""
    report_template_md: str = ""
    """Markdown report template"""
    report_template_cli: str = ""
    """Report template for CLI output"""
    report_template_gitlab_code_quality: str = (
        "fn:gito.gitlab:convert_to_gitlab_code_quality_report"
    )
    post_process: str = ""
    retries: int = 3
    """LLM retries for one request"""
    max_code_tokens: int = 32000
    prompt_vars: dict = field(default_factory=dict)
    mention_triggers: list[str] = field(default_factory=list)
    answer_github_comments: bool = field(default=True)
    """
    Defines the keyword or mention tag that triggers bot actions
    when referenced in code review comments.
    """
    aux_files: list[str] = field(default_factory=list)
    exclude_files: list[str] = field(default_factory=list)
    """
    List of file patterns to exclude from analysis.
    """
    pipeline_steps: dict[str, dict | PipelineStep] = field(default_factory=dict)
    collapse_previous_code_review_comments: bool = field(default=True)
    """
    If True, previously added code review comments in the pull request
    will be collapsed automatically when a new comment is added.
    """

    def __post_init__(self):
        self.pipeline_steps = {
            k: PipelineStep(**v) if isinstance(v, dict) else v
            for k, v in self.pipeline_steps.items()
        }

    @staticmethod
    def _read_bundled_defaults() -> dict:
        """
        Read the bundled default project configuration,
        typically located at <root>/gito/config.toml in the gito.bot distribution.
        Returns:
            dict: The default project configuration.
        """
        with open(PROJECT_CONFIG_BUNDLED_DEFAULTS_FILE, "rb") as f:
            config = tomllib.load(f)
        return config

    @staticmethod
    def _read_toml(path: Path) -> dict:
        with open(path, "rb") as f:
            return tomllib.load(f)

    @staticmethod
    def _discover_subdir_configs(repo_root: Path) -> list[tuple[str, dict]]:
        """
        Find every <subdir>/.gito/config.toml under `repo_root`, excluding the
        root-level .gito/config.toml itself.

        Returns:
            List of (scope, parsed_config_dict) where `scope` is the
            POSIX-style relative directory of the config file (i.e. the
            directory containing .gito/), e.g. "frontend" or "src/foo".
            Sorted by scope depth ascending, then lexicographically — so the
            shallowest config is applied first and deeper configs override it,
            with the root config (applied last by the caller) winning overall.
        """
        results: list[tuple[str, dict]] = []
        repo_root = repo_root.resolve()
        for dirpath, dirnames, filenames in os.walk(repo_root):
            # Prune ignored dirs in place so os.walk doesn't descend into them.
            dirnames[:] = [d for d in dirnames if d not in _DISCOVERY_SKIP_DIRS]
            if PROJECT_GITO_FOLDER not in dirnames:
                continue
            cfg_path = Path(dirpath) / PROJECT_GITO_FOLDER / PROJECT_CONFIG_FILE_NAME
            if not cfg_path.is_file():
                continue
            scope_dir = Path(dirpath).relative_to(repo_root)
            scope = PurePosixPath(*scope_dir.parts).as_posix() if scope_dir.parts else ""
            if scope == "":
                # Root-level .gito/config.toml is handled by the caller.
                continue
            try:
                results.append((scope, ProjectConfig._read_toml(cfg_path)))
            except Exception as e:
                logging.warning(
                    f"Failed to parse nested config {mc.utils.file_link(cfg_path)}: {e}"
                )
        results.sort(key=lambda item: (item[0].count("/"), item[0]))
        return results

    @staticmethod
    def _merge_level(base: dict, overlay: dict, *, scope: str = "") -> dict:
        """
        Merge `overlay` into `base` in place; overlay wins on every conflict.
        Apply levels in order `defaults -> subdirs -> root` to get "root
        supersedes on conflicts".

        - List fields in `_LIST_MERGE_FIELDS` concatenate (dedupe, base-first).
        - `prompt_vars`: union of keys, overlay wins on conflict.
        - `pipeline_steps`: union of step names; per-step dicts are field-merged
          (overlay fields win, base fields fill gaps), preserving the original
          single-file behavior where `enabled=false` in a project config only
          flips that field and inherits `call` etc. from defaults.
        - Pipeline step entries coming in via `overlay` get their `scope`
          field set to the provided `scope` unless explicitly set in TOML.
        - All other (scalar) fields: overlay overwrites.
        """
        for key, value in overlay.items():
            if key in _LIST_MERGE_FIELDS:
                merged = list(base.get(key, []))
                for item in value or []:
                    if item not in merged:
                        merged.append(item)
                base[key] = merged
                continue

            if key == "prompt_vars":
                merged_vars = dict(base.get("prompt_vars", {}))
                merged_vars.update(value or {})
                base["prompt_vars"] = merged_vars
                continue

            if key == "pipeline_steps":
                merged_steps = dict(base.get("pipeline_steps", {}))
                for step_name, step_def in (value or {}).items():
                    if isinstance(step_def, dict):
                        step_def = dict(step_def)
                        step_def.setdefault("scope", scope)
                    existing = merged_steps.get(step_name)
                    if isinstance(existing, dict) and isinstance(step_def, dict):
                        merged_steps[step_name] = {**existing, **step_def}
                    else:
                        merged_steps[step_name] = step_def
                base["pipeline_steps"] = merged_steps
                continue

            base[key] = value
        return base

    @staticmethod
    def load_for_repo(repo: Repo) -> "ProjectConfig":
        if repo.working_tree_dir is not None:
            repo_root = Path(repo.working_tree_dir)
            config_path = repo_root / PROJECT_CONFIG_FILE_PATH
            return ProjectConfig.load(config_path, repo_root=repo_root)
        return ProjectConfig.load(None)

    @staticmethod
    def load(
        config_path: str | Path | None = None,
        repo_root: str | Path | None = None,
    ) -> "ProjectConfig":
        """
        Load the project configuration.

        Merge order (each level only contributes keys it explicitly wrote):
            bundled_defaults  -> nested <subdir>/.gito/config.toml files
                              -> root .gito/config.toml (wins on conflict)

        Cumulative rules:
        - `aux_files`, `exclude_files`, `mention_triggers`: concatenated +
          deduped across all levels.
        - `prompt_vars`: union of keys; root wins on key conflict.
        - `pipeline_steps`: union of step names; root wins on name conflict.
          Steps defined in a sub-directory get `scope` set to that directory,
          so they only apply to files under it.
        - All other scalar fields: root overrides; sub-directory configs only
          fill values the root did not explicitly set.

        If `repo_root` is provided, sub-directory configs are discovered under
        it. Otherwise discovery is skipped (back-compat with the single-file
        load() API used in tests).
        """
        merged: dict = ProjectConfig._read_bundled_defaults()
        github_env = detect_github_env()
        merged["prompt_vars"] |= github_env | dict(github_env=github_env)

        config_path = Path(config_path or PROJECT_CONFIG_FILE_PATH)
        if repo_root is not None:
            repo_root = Path(repo_root)
            for scope, sub_cfg in ProjectConfig._discover_subdir_configs(repo_root):
                logging.info(
                    f"Loading sub-directory config "
                    f"[{ui.cyan(scope)}/{PROJECT_GITO_FOLDER}/{PROJECT_CONFIG_FILE_NAME}]"
                )
                ProjectConfig._merge_level(merged, sub_cfg, scope=scope)

        if config_path.exists():
            logging.info(
                f"Loading project-specific configuration from {mc.utils.file_link(config_path)}..."
            )
            root_cfg = ProjectConfig._read_toml(config_path)
            ProjectConfig._merge_level(merged, root_cfg, scope="")
        else:
            logging.info(f"No project config found at {ui.blue(config_path)}, using defaults")

        # Drop any keys that aren't dataclass fields (forward-compat with TOML
        # users who add unknown options).
        known = {f.name for f in fields(ProjectConfig)}
        merged = {k: v for k, v in merged.items() if k in known}
        return ProjectConfig(**merged)
