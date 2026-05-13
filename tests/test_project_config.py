import logging
import textwrap
from pathlib import Path

from gito.project_config import ProjectConfig
from gito.constants import PROJECT_CONFIG_FILE_PATH


def test_load_defaults(monkeypatch):
    cfg = ProjectConfig.load()
    assert isinstance(cfg.prompt, str)
    assert isinstance(cfg.summary_prompt, str)
    assert isinstance(cfg.retries, int)
    assert isinstance(cfg.max_code_tokens, int)
    assert "self_id" in cfg.prompt_vars


def test_prompt_vars_merging(tmp_path):
    sample = textwrap.dedent(
        """
    retries = 7
    [prompt_vars]
    foo = "bar"
    """
    )
    toml_path = tmp_path / ".ai-code-review.toml"
    toml_path.write_text(sample)
    logging.info(f"Writing to {toml_path}")
    cfg = ProjectConfig.load(config_path=toml_path)
    assert "foo" in cfg.prompt_vars
    assert "self_id" in cfg.prompt_vars
    assert cfg.prompt_vars["foo"] == "bar"
    assert cfg.retries == 7


def test_merge_pipeline_steps():
    file = Path(__file__).parent / "fixtures" / "config-disable-jira.toml"
    cfg = ProjectConfig.load(config_path=file)
    assert "linear" in cfg.pipeline_steps
    assert "jira" in cfg.pipeline_steps
    assert cfg.pipeline_steps["linear"].enabled
    assert not cfg.pipeline_steps["jira"].enabled


# --- Hierarchical (sub-directory) config tests --------------------------------


def _write_config(dir_path: Path, body: str) -> Path:
    gito_dir = dir_path / ".gito"
    gito_dir.mkdir(parents=True, exist_ok=True)
    path = gito_dir / "config.toml"
    path.write_text(textwrap.dedent(body))
    return path


def test_hierarchical_lists_are_cumulative(tmp_path):
    _write_config(tmp_path, """
        exclude_files = ["root_only.txt"]
        aux_files = ["root.md"]
        mention_triggers = ["root-mention"]
    """)
    _write_config(tmp_path / "frontend", """
        exclude_files = ["frontend_only.js"]
        aux_files = ["frontend.md"]
        mention_triggers = ["frontend-mention"]
    """)
    _write_config(tmp_path / "backend" / "svc", """
        exclude_files = ["svc_only.go"]
    """)

    cfg = ProjectConfig.load(
        config_path=tmp_path / PROJECT_CONFIG_FILE_PATH,
        repo_root=tmp_path,
    )
    # Cumulative: every level contributes to the merged list.
    for entry in ("root_only.txt", "frontend_only.js", "svc_only.go"):
        assert entry in cfg.exclude_files, entry
    assert "root.md" in cfg.aux_files and "frontend.md" in cfg.aux_files
    assert "root-mention" in cfg.mention_triggers
    assert "frontend-mention" in cfg.mention_triggers


def test_hierarchical_scalar_root_supersedes(tmp_path):
    _write_config(tmp_path, "retries = 9\n")
    _write_config(tmp_path / "frontend", "retries = 99\nmax_code_tokens = 12345\n")

    cfg = ProjectConfig.load(
        config_path=tmp_path / PROJECT_CONFIG_FILE_PATH,
        repo_root=tmp_path,
    )
    # Root wins on conflict; subdir-only scalar (max_code_tokens) still applies.
    assert cfg.retries == 9
    assert cfg.max_code_tokens == 12345


def test_hierarchical_prompt_vars_root_wins_on_conflict(tmp_path):
    _write_config(tmp_path, """
        [prompt_vars]
        shared = "from-root"
        only_root = "yes"
    """)
    _write_config(tmp_path / "lib", """
        [prompt_vars]
        shared = "from-subdir"
        only_subdir = "yep"
    """)

    cfg = ProjectConfig.load(
        config_path=tmp_path / PROJECT_CONFIG_FILE_PATH,
        repo_root=tmp_path,
    )
    assert cfg.prompt_vars["shared"] == "from-root"
    assert cfg.prompt_vars["only_root"] == "yes"
    assert cfg.prompt_vars["only_subdir"] == "yep"


def test_hierarchical_pipeline_step_scope_and_root_wins(tmp_path):
    _write_config(tmp_path, """
        [pipeline_steps.jira]
        enabled = false
    """)
    _write_config(tmp_path / "ui", """
        [pipeline_steps.jira]
        enabled = true
        [pipeline_steps.ui_only_step]
        call = "gito.pipeline_steps.linear.fetch_associated_issue"
    """)

    cfg = ProjectConfig.load(
        config_path=tmp_path / PROJECT_CONFIG_FILE_PATH,
        repo_root=tmp_path,
    )
    # Root's `enabled=false` for jira wins over subdir's `enabled=true`,
    # but the `call` field is still inherited from the bundled defaults.
    assert cfg.pipeline_steps["jira"].enabled is False
    assert cfg.pipeline_steps["jira"].call
    # Subdir-only step is registered and carries the subdir scope.
    assert cfg.pipeline_steps["ui_only_step"].scope == "ui"
    # Root-defined step has empty scope (repo-wide).
    assert cfg.pipeline_steps["jira"].scope == ""


def test_hierarchical_skips_ignored_dirs(tmp_path):
    _write_config(tmp_path, "retries = 5\n")
    # A config inside .venv should be ignored entirely.
    _write_config(tmp_path / ".venv" / "pkg", "retries = 7777\n")

    cfg = ProjectConfig.load(
        config_path=tmp_path / PROJECT_CONFIG_FILE_PATH,
        repo_root=tmp_path,
    )
    assert cfg.retries == 5
