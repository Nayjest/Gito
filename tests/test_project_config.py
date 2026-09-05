import logging
import textwrap
from pathlib import Path

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


def test_prompt_vars_field_collision_warns(tmp_path, caplog):
    sample = textwrap.dedent(
        """
    [prompt_vars]
    foo = "bar"
    post_process = "fn:my_module:my_hook"
    """
    )
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
    sample = textwrap.dedent(
        """
    retries = 7
    [prompt_vars]
    foo = "bar"
    """
    )
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
