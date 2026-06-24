"""Show Gito version command."""

from ..cli_base import app, runs_without_llm
from ..env import Env


@app.command(name="version", help="Show Gito version.")
@runs_without_llm
def version():
    print(Env.gito_version)
    return Env.gito_version
