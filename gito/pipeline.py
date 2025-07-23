import logging
from enum import StrEnum
from dataclasses import dataclass, field

from microcore import ui
from microcore.utils import resolve_callable

from .context import Context
from .utils import is_running_in_github_action


class PipelineEnv(StrEnum):
    LOCAL = "local"
    GH_ACTION = "gh-action"

    @staticmethod
    def all():
        return [PipelineEnv.LOCAL, PipelineEnv.GH_ACTION]

    @staticmethod
    def current():
        return (
            PipelineEnv.GH_ACTION
            if is_running_in_github_action()
            else PipelineEnv.LOCAL
        )


@dataclass
class PipelineStep:
    call: str
    envs: list[PipelineEnv] = field(default_factory=PipelineEnv.all)
    enabled: bool = field(default=True)

    def get_callable(self):
        """
        Resolve the callable from the string representation.
        """
        return resolve_callable(self.call)

    def run(self, *args, **kwargs):
        return self.get_callable()(*args, **kwargs)


@dataclass
class Pipeline:
    ctx: Context = field()
    steps: dict[str, PipelineStep] = field(default_factory=dict)
    verbose: bool = False

    @property
    def enabled_steps(self):
        return {
            k: v for k, v in self.steps.items() if v.enabled
        }

    def run(self, *args, **kwargs):
        cur_env = PipelineEnv.current()
        logging.info("Running pipeline... [env: %s]", ui.yellow(cur_env))
        for step_name, step in self.enabled_steps.items():
            if cur_env in step.envs:
                logging.info(f"Running pipeline step: {step_name}")
                try:
                    step_output = step.run(*args, **kwargs, **vars(self.ctx))
                    if isinstance(step_output, dict):
                        self.ctx.pipeline_out.update(step_output)
                    self.ctx.pipeline_out[step_name] = step_output
                    if self.verbose and step_output:
                        logging.info(
                            f"Pipeline step {step_name} output: {repr(step_output)}"
                        )
                    if not step_output:
                        logging.warning(
                            f'Pipeline step "{step_name}" returned {repr(step_output)}.'
                        )
                except Exception as e:
                    logging.error(f'Error in pipeline step "{step_name}": {e}')
            else:
                logging.info(
                    f"Skipping pipeline step: {step_name}"
                    f" [env: {ui.yellow(cur_env)} not in {step.envs}]"
                )
        return self.ctx.pipeline_out
