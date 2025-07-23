from ..cli_base import app
from ..env import Env


@app.command(name='version', help='Show Gito version.')
def version():
    print(Env.gito_version)
    return Env.gito_version
