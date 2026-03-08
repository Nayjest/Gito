import subprocess
from CRACK.env import Env


def test_version_command_shell():
    result = subprocess.run(
        ["python", "-m", "CRACK", "-v0", "version"], capture_output=True, text=True
    )

    assert result.returncode == 0
    assert result.stdout.strip() == Env.CRACK_version
    assert Env.CRACK_version and "." in Env.CRACK_version
    assert result.stderr == ""


def test_version_return_val():
    from CRACK.commands.version import version

    assert version() == Env.CRACK_version
