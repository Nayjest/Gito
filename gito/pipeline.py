import logging
import warnings
from enum import StrEnum
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from microcore import ui
from microcore.utils import resolve_callable

from .context import Context
from .utils.cli import is_running_in_ci


class PipelineEnv(StrEnum):
    LOCAL = "local"
    CI = "ci"

    @staticmethod
    def all():
        return [PipelineEnv.LOCAL, PipelineEnv.CI]

    @staticmethod
    def current():
        return PipelineEnv.CI if is_running_in_ci() else PipelineEnv.LOCAL

    @classmethod
    def _missing_(cls, value):
        """
        Handle deprecated values:
        PipelineEnv.GH_ACTION -> PipelineEnv.CI
        """
        if value == "gh-action":
            warnings.warn(
                "PipelineEnv 'gh-action' is deprecated; use 'ci' instead",
                DeprecationWarning,
                stacklevel=2,
            )
            return cls.CI
        return None


@dataclass
class PipelineStep:
    call: str
    envs: list[PipelineEnv] = field(default_factory=PipelineEnv.all)
    enabled: bool = field(default=True)
    scope: str = field(default="")
    """
    Repo-relative directory the step applies to (POSIX path, no leading slash,
    no trailing slash). Empty string means repo-wide. Set automatically when a
    step is defined in a sub-directory's .gito/config.toml.
    """

    def get_callable(self):
        """
        Resolve the callable from the string representation.
        """
        return resolve_callable(self.call)

    def run(self, *args, **kwargs):
        return self.get_callable()(*args, **kwargs)

    def matches_path(self, path: str) -> bool:
        """True if `path` falls under this step's scope."""
        if not self.scope:
            return True
        p = PurePosixPath(path)
        scope = PurePosixPath(self.scope)
        try:
            p.relative_to(scope)
            return True
        except ValueError:
            return False


@dataclass
class Pipeline:
    ctx: Context = field()
    steps: dict[str, PipelineStep] = field(default_factory=dict)
    verbose: bool = False

    @property
    def enabled_steps(self):
        return {k: v for k, v in self.steps.items() if v.enabled}

    def _scoped_ctx_kwargs(self, step: PipelineStep) -> dict:
        """
        Build kwargs from ctx, filtering `diff` to files under step.scope.
        Returns the filtered diff list (possibly empty) so callers can decide
        whether to skip the step.
        """
        ctx_kwargs = vars(self.ctx).copy()
        if step.scope and self.ctx.diff is not None:
            ctx_kwargs["diff"] = [
                f for f in self.ctx.diff if step.matches_path(f.path)
            ]
        return ctx_kwargs

    def run(self, *args, **kwargs):
        cur_env = PipelineEnv.current()
        logging.info("Running pipeline... [env: %s]", ui.yellow(cur_env))
        for step_name, step in self.enabled_steps.items():
            if cur_env not in step.envs:
                logging.info(
                    f"Skipping pipeline step: {step_name}"
                    f" [env: {ui.yellow(cur_env)} not in {step.envs}]"
                )
                continue
            ctx_kwargs = self._scoped_ctx_kwargs(step)
            if step.scope and not ctx_kwargs.get("diff"):
                logging.info(
                    f"Skipping pipeline step: {step_name}"
                    f" [scope {ui.yellow(step.scope)} has no matching files in diff]"
                )
                continue
            logging.info(f"Running pipeline step: {step_name}")
            try:
                step_output = step.run(*args, **kwargs, **ctx_kwargs)
                if isinstance(step_output, dict):
                    self.ctx.pipeline_out.update(step_output)
                self.ctx.pipeline_out[step_name] = step_output
                if self.verbose and step_output:
                    logging.info(f"Pipeline step {step_name} output: {repr(step_output)}")
                if not step_output:
                    logging.warning(
                        f'Pipeline step "{step_name}" '
                        f"returned no result ({repr(step_output)})."
                    )
            except Exception as e:
                logging.error(f'Error in pipeline step "{step_name}": {e}')
        return self.ctx.pipeline_out
