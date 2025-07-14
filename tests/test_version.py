import subprocess
from gito.env import Env


def test_version_command_shell():
    result = subprocess.run(
        ['python', '-m', 'gito', '-v0', 'version'],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert result.stdout.strip() == Env.gito_version
    assert Env.gito_version and '.' in Env.gito_version
    assert result.stderr == ""


def test_version_return_val():
    from gito.commands.version import version
    assert version() == Env.gito_version
