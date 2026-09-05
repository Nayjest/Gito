import os

import microcore as mc
import pytest

os.environ["LLM_API_TYPE"] = str(mc.ApiType.NONE)


@pytest.fixture(autouse=True)
def global_project_config_path(tmp_path, monkeypatch):
    """Keep the user's global review settings out of every test."""
    config_path = tmp_path / "home" / ".gito" / "config.toml"
    config_path.parent.mkdir(parents=True)
    monkeypatch.setattr("gito.project_config.GLOBAL_PROJECT_CONFIG_FILE_PATH", config_path)
    return config_path
