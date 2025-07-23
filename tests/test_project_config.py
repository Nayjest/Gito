import logging
import textwrap
from pathlib import Path

from gito.project_config import ProjectConfig


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
