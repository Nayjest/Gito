import logging
import textwrap
from pathlib import Path

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


def test_extra_project_config_merges_after_repo_config(tmp_path, monkeypatch):
    repo_config = tmp_path / "repo.toml"
    repo_config.write_text(
        textwrap.dedent(
            """
            retries = 5
            [prompt_vars]
            repo_rule = "present"
            requirements = "repo"
            [pipeline_steps.jira]
            enabled = true
            """
        )
    )
    extra_config = tmp_path / "extra.toml"
    extra_config.write_text(
        textwrap.dedent(
            """
            [prompt_vars]
            requirements = "extra"
            mentor_rule = "present"
            [pipeline_steps.jira]
            enabled = false
            """
        )
    )

    monkeypatch.setenv("GITO_EXTRA_PROJECT_CONFIG", str(extra_config))

    cfg = ProjectConfig.load(config_path=repo_config)

    assert cfg.retries == 5
    assert cfg.prompt_vars["repo_rule"] == "present"
    assert cfg.prompt_vars["mentor_rule"] == "present"
    assert cfg.prompt_vars["requirements"] == "extra"
    assert "linear" in cfg.pipeline_steps
    assert not cfg.pipeline_steps["jira"].enabled


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
