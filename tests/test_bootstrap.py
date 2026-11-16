import logging

import microcore as mc
import pytest

from gito.bootstrap import configure_tiktoken_encoding


@pytest.fixture
def config(monkeypatch):
    """Isolate the shared microcore config between tests."""
    cfg = mc.config()
    monkeypatch.setattr(cfg, "TIKTOKEN_ENCODING", None)
    monkeypatch.setattr(cfg, "LLM_DEFAULT_ARGS", {})
    monkeypatch.setattr(cfg, "MODEL", None)
    return cfg


def test_sets_default_encoding_for_unsupported_model(config, monkeypatch):
    monkeypatch.setattr(config, "MODEL", "claude-opus-4-5")
    configure_tiktoken_encoding()
    assert config.TIKTOKEN_ENCODING == "cl100k_base"


def test_keeps_dedicated_encoding_for_supported_model(config, monkeypatch):
    monkeypatch.setattr(config, "MODEL", "gpt-4o")
    configure_tiktoken_encoding()
    assert config.TIKTOKEN_ENCODING is None


def test_respects_explicit_encoding(config, monkeypatch):
    monkeypatch.setattr(config, "TIKTOKEN_ENCODING", "o200k_base")
    monkeypatch.setattr(config, "MODEL", "claude-opus-4-5")
    configure_tiktoken_encoding()
    assert config.TIKTOKEN_ENCODING == "o200k_base"


def test_reads_model_from_llm_default_args(config, monkeypatch):
    monkeypatch.setattr(config, "LLM_DEFAULT_ARGS", {"model": "claude-opus-4-5"})
    configure_tiktoken_encoding()
    assert config.TIKTOKEN_ENCODING == "cl100k_base"


def test_does_nothing_without_model(config):
    configure_tiktoken_encoding()
    assert config.TIKTOKEN_ENCODING is None


def test_no_tiktoken_warning_for_unsupported_model(config, monkeypatch, caplog):
    monkeypatch.setattr(config, "MODEL", "claude-opus-4-5")
    configure_tiktoken_encoding()
    with caplog.at_level(logging.WARNING):
        mc.tokenizing.num_tokens_from_string("hello world")
    assert "Can't resolve tiktoken encoding" not in caplog.text
