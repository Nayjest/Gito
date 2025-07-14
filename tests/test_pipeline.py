import pytest
from unittest.mock import patch, MagicMock
import microcore as mc
from gito.pipeline import Pipeline, PipelineStep, PipelineEnv
from gito.context import Context
from gito.project_config import ProjectConfig
from gito.report_struct import Report


# --- Fixtures and helpers ---


@pytest.fixture
def dummy_callable():
    def _callable(*args, **kwargs):
        return {"result": "ok"}

    return _callable


@pytest.fixture
def patch_resolve_callable(dummy_callable):
    with patch("gito.pipeline.resolve_callable", return_value=dummy_callable):
        yield


@pytest.fixture
def patch_github_action_env(monkeypatch):
    # Monkeypatch is_running_in_github_action to return True (GH_ACTION) or False (LOCAL)
    def _patch(is_gh_action):
        monkeypatch.setattr(
            "gito.pipeline.is_running_in_github_action", lambda: is_gh_action
        )

    return _patch


# --- Tests ---


def test_pipelineenv_current_local(patch_github_action_env):
    patch_github_action_env(False)
    assert PipelineEnv.current() == PipelineEnv.LOCAL


def test_pipelineenv_current_gh_action(patch_github_action_env):
    patch_github_action_env(True)
    assert PipelineEnv.current() == PipelineEnv.GH_ACTION


def test_pipeline_step_run_calls_resolve_callable(patch_resolve_callable):
    step = PipelineStep(call="myfunc")
    # Should call the resolved dummy_callable and not fail
    step.run(foo="bar")  # should not raise


def test_pipeline_run_skips_steps_for_other_env(
    monkeypatch, patch_resolve_callable, patch_github_action_env
):
    patch_github_action_env(False)  # LOCAL

    dummy_step = PipelineStep(call="myfunc", envs=[PipelineEnv.GH_ACTION])
    dummy_step.run = MagicMock()
    steps = {"step1": dummy_step}
    mc.configure(
        LLM_API_TYPE=mc.ApiType.NONE,
    )
    ctx = Context(
        report=Report(),  # Mock or set up a repo if needed
        config=ProjectConfig.load(),
        diff=[],
        repo=None,
    )
    pipeline = Pipeline(ctx, steps=steps)

    pipeline.run()
    dummy_step.run.assert_not_called()


def test_pipeline_step_envs_default(patch_resolve_callable):
    step = PipelineStep(call="myfunc")
    assert set(step.envs) == set(PipelineEnv.all())


# --- Optional: test multiple steps and context updates ---


def test_pipeline_multiple_steps(monkeypatch, patch_github_action_env):
    mc.configure(
        LLM_API_TYPE=mc.ApiType.NONE,
    )
    patch_github_action_env(False)  # LOCAL

    step1 = PipelineStep(call="func1", envs=[PipelineEnv.LOCAL])
    step2 = PipelineStep(call="func2", envs=[PipelineEnv.LOCAL])
    # Fake run: each step updates ctx
    step1.run = lambda *a, **k: {"a": 1}
    step2.run = lambda *a, **k: {"b": 2}

    ctx = Context(
        report=Report(),  # Mock or set up a repo if needed
        config=ProjectConfig.load(),
        diff=[],
        repo=None,
    )
    pipeline = Pipeline(ctx, steps={"step1": step1, "step2": step2})

    result = pipeline.run()
    assert result["a"] == 1
    assert result["b"] == 2


def test_get_callable():
    callable_fn = PipelineStep(
        call="gito.pipeline_steps.jira.fetch_associated_issue"
    ).get_callable()
    assert callable(callable_fn), "Expected a callable function"
