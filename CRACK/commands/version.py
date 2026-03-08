"""Show CRACK version command."""

from ..cli_base import app
from ..env import Env


@app.command(name="version", help="Show CRACK version.")
def version():
    print(Env.CRACK_version)
    return Env.CRACK_version
