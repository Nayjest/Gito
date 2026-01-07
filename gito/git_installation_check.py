"""
Checks if Git is installed; if not, provides installation instructions.
"""
import platform
import sys

from microcore import ui


def ensure_git_installed() -> None:
    """Ensures that Git is installed on the system."""
    try:
        from git import Repo  # noqa: F401
    except ImportError:
        _exit_with_git_instructions()


def _exit_with_git_instructions() -> None:
    """Exits the program with instructions to install Git."""
    cmd = _get_git_install_command()
    app_name = f"{ui.bright}Gito AI Code Reviewer{ui.reset}{ui.red}"
    msg_begin = (
        f"{ui.red}Error: {app_name} couldn't find "
        f"{ui.bright}Git{ui.reset}{ui.red} on the system."
        f"\n{ui.red}"
    )
    if cmd:
        details = f"To install {ui.bright}Git{ui.reset}{ui.red}, run: {ui.blue}{cmd}{ui.reset}"
    else:
        details = "Please install it from: https://git-scm.com"
    sys.exit(msg_begin + details)


def _get_git_install_command() -> str | None:
    """Returns the command to install Git based on the operating system."""
    system = platform.system().lower()

    if system == "windows":
        return "winget install Git.Git"
    elif system == "darwin":
        return "brew install git"
    elif system == "linux":
        try:
            with open("/etc/os-release") as f:
                os_release = f.read().lower()
        except FileNotFoundError:
            return None

        if "alpine" in os_release:
            return "apk add git"
        if "debian" in os_release or "ubuntu" in os_release:
            return "sudo apt install git"
        if "fedora" in os_release:
            return "sudo dnf install git"
        if "arch" in os_release:
            return "sudo pacman -S git"
    return None
