import pytest
import typer

from gito.cli_base import args_to_target, resolve_project_config_path
from gito.constants import REFS_VALUE_ALL


def test_resolve_project_config_path(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    cfg.write_text("retries = 1\n")
    assert resolve_project_config_path(str(cfg)) == cfg
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert resolve_project_config_path("~/config.toml") == cfg
    with pytest.raises(typer.BadParameter):
        resolve_project_config_path(str(tmp_path / "missing.toml"))


def test_args_to_target_all():
    assert args_to_target(REFS_VALUE_ALL, None, None) == (REFS_VALUE_ALL, None)


def test_args_to_target_refs_pair():
    assert args_to_target("main..feature", None, None) == ("main", "feature")


def test_args_to_target_options_only():
    assert args_to_target("", "w", "a") == ("w", "a")


@pytest.mark.parametrize(
    ("refs", "what", "against"),
    [
        ("main..feature", "w", None),
        ("main..feature", None, "a"),
    ],
)
def test_args_to_target_conflict(refs, what, against):
    with pytest.raises(typer.BadParameter):
        args_to_target(refs, what, against)
