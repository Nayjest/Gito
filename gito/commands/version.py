from ..cli_base import app
from ..env import Env


@app.command(name='version', help='Show the version of gito.bot')
def version():
    print(Env.gito_version)
    return Env.gito_version
