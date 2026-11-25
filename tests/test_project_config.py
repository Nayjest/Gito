import logging
import textwrap
import tomllib
from pathlib import Path

import pytest
from git import Repo

from gito.env import Env
from gito.project_config import ProjectConfig
from gito.pipeline import PipelineStep


def test_load_defaults(monkeypatch):
    cfg = ProjectConfig.load()
    assert isinstance(cfg.prompt, str)
    assert isinstance(cfg.summary_prompt, str)
    assert isinstance(cfg.retries, int)
    assert isinstance(cfg.max_code_tokens, int)
    assert "self_id" in cfg.prompt_vars


def test_prompt_vars_merging(tmp_path):
    sample = textwrap.dedent("""
    retries = 7
    [prompt_vars]
    foo = "bar"
    """)
    toml_path = tmp_path / ".ai-code-review.toml"
    toml_path.write_text(sample)
    logging.info(f"Writing to {toml_path}")
    cfg = ProjectConfig.load(config_path=toml_path)
    assert "foo" in cfg.prompt_vars
    assert "self_id" in cfg.prompt_vars
    assert cfg.prompt_vars["foo"] == "bar"
    assert cfg.retries == 7


def test_prompt_vars_field_collision_warns(tmp_path, caplog):
    sample = textwrap.dedent("""
    [prompt_vars]
    foo = "bar"
    post_process = "fn:my_module:my_hook"
    """)
    toml_path = tmp_path / "config.toml"
    toml_path.write_text(sample)
    with caplog.at_level(logging.WARNING):
        cfg = ProjectConfig.load(config_path=toml_path)
    assert cfg.post_process != "fn:my_module:my_hook"
    assert cfg.prompt_vars["post_process"] == "fn:my_module:my_hook"
    assert any(
        "post_process" in record.message and "prompt_vars" in record.message
        for record in caplog.records
    )


def test_load_config_with_utf8_bom(tmp_path):
    sample = textwrap.dedent("""
    retries = 7
    [prompt_vars]
    foo = "bar"
    """)
    toml_path = tmp_path / "config.toml"
    toml_path.write_bytes(b"\xef\xbb\xbf" + sample.encode("utf-8"))
    cfg = ProjectConfig.load(config_path=toml_path)
    assert cfg.retries == 7
    assert cfg.prompt_vars["foo"] == "bar"


def test_env_project_config_path_overrides(tmp_path, monkeypatch):
    override = tmp_path / "global-config.toml"
    override.write_text("retries = 42\n")
    project = tmp_path / "config.toml"
    project.write_text("retries = 7\n")
    monkeypatch.setattr(Env, "project_config_path", override)
    assert ProjectConfig.load().retries == 42
    # explicit per-repo path is superseded by the CLI-provided one
    assert ProjectConfig.load(config_path=project).retries == 42


def test_merge_pipeline_steps():
    file = Path(__file__).parent / "fixtures" / "config-disable-jira.toml"
    cfg = ProjectConfig.load(config_path=file)
    assert "linear" in cfg.pipeline_steps
    assert "jira" in cfg.pipeline_steps
    assert cfg.pipeline_steps["linear"].enabled
    assert not cfg.pipeline_steps["jira"].enabled


def test_post_init_coerces_dict_steps_to_pipeline_step():
    existing = PipelineStep(call="already")
    cfg = ProjectConfig(
        pipeline_steps={
            "from_dict": {"call": "x"},
            "kept": existing,
        }
    )
    assert isinstance(cfg.pipeline_steps["from_dict"], PipelineStep)
    assert cfg.pipeline_steps["from_dict"].call == "x"
    assert cfg.pipeline_steps["kept"] is existing


def test_global_config_without_project_file(tmp_path, global_project_config_path):
    global_project_config_path.write_text(
        'retries = 7\n[prompt_vars]\nlanguage = "日本語"\n', encoding="utf-8-sig"
    )

    cfg = ProjectConfig.load(tmp_path / "missing.toml")

    assert cfg.retries == 7
    assert cfg.prompt_vars["language"] == "日本語"
    assert "self_id" in cfg.prompt_vars
    assert cfg.prompt
    assert cfg.pipeline_steps["jira"].call


def test_all_config_layers_merge(tmp_path, monkeypatch, global_project_config_path):
    global_project_config_path.write_text(textwrap.dedent("""
        retries = 7
        max_code_tokens = 1234
        exclude_files = ["global.py"]
        [prompt_vars]
        global_only = "inherited"
        winner = "global"
        [pipeline_steps.jira]
        enabled = false
        [pipeline_steps.custom]
        call = "example.custom"
        enabled = true
    """))
    project = tmp_path / "project.toml"
    project.write_text(textwrap.dedent("""
        retries = 8
        exclude_files = ["project.py"]
        [prompt_vars]
        project_only = "inherited"
        winner = "project"
        [pipeline_steps.custom]
        enabled = false
    """))
    override = tmp_path / "override.toml"
    override.write_text(textwrap.dedent("""
        retries = 9
        exclude_files = []
        [prompt_vars]
        winner = "override"
        [pipeline_steps.jira]
        enabled = true
    """))

    project_cfg = ProjectConfig.load(project)
    assert project_cfg.retries == 8
    assert project_cfg.exclude_files == ["project.py"]
    assert project_cfg.prompt_vars["winner"] == "project"
    assert not project_cfg.pipeline_steps["jira"].enabled

    monkeypatch.setattr(Env, "project_config_path", override)
    cfg = ProjectConfig.load(project)
    assert cfg.retries == 9
    assert cfg.max_code_tokens == 1234
    assert cfg.exclude_files == []
    assert cfg.prompt_vars["winner"] == "override"
    assert cfg.prompt_vars["global_only"] == "inherited"
    assert cfg.prompt_vars["project_only"] == "inherited"
    assert "self_id" in cfg.prompt_vars
    assert cfg.pipeline_steps["jira"].enabled
    assert cfg.pipeline_steps["jira"].call == project_cfg.pipeline_steps["jira"].call
    assert cfg.pipeline_steps["custom"].call == "example.custom"
    assert not cfg.pipeline_steps["custom"].enabled
    assert "linear" in cfg.pipeline_steps
    # Loading the override must not mutate previously returned configurations.
    assert not project_cfg.pipeline_steps["jira"].enabled


def test_load_for_repo_uses_repo_root(tmp_path, monkeypatch, global_project_config_path):
    global_project_config_path.write_text("retries = 7\nmax_code_tokens = 1234\n")
    repo_path = tmp_path / "repo"
    with Repo.init(repo_path) as repo:
        project = repo_path / ".gito" / "config.toml"
        project.parent.mkdir()
        project.write_text("retries = 8\n")
        monkeypatch.chdir(tmp_path)

        cfg = ProjectConfig.load_for_repo(repo)

    assert cfg.retries == 8
    assert cfg.max_code_tokens == 1234


def test_load_uses_current_directory_config(tmp_path, monkeypatch, global_project_config_path):
    global_project_config_path.write_text("retries = 7\nmax_code_tokens = 1234\n")
    project = tmp_path / ".gito" / "config.toml"
    project.parent.mkdir()
    project.write_text("retries = 8\n")
    monkeypatch.chdir(tmp_path)

    cfg = ProjectConfig.load()

    assert cfg.retries == 8
    assert cfg.max_code_tokens == 1234


def test_invalid_global_config_is_not_silently_ignored(tmp_path, global_project_config_path):
    global_project_config_path.write_text("invalid = [\n")

    with pytest.raises(tomllib.TOMLDecodeError):
        ProjectConfig.load(tmp_path / "missing.toml")


def test_global_prompt_vars_field_collision_warns(tmp_path, global_project_config_path, caplog):
    global_project_config_path.write_text('[prompt_vars]\nretries = "custom variable"\n')

    with caplog.at_level(logging.WARNING):
        cfg = ProjectConfig.load(tmp_path / "missing.toml")

    assert isinstance(cfg.retries, int)
    assert cfg.prompt_vars["retries"] == "custom variable"
    assert any(
        "retries" in record.message and "prompt_vars" in record.message for record in caplog.records
    )
