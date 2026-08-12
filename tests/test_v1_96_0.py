"""v1.96.0 — paid-cloud summarizer auto-billing guard (suite parity with jcm v1.108.128).

A bare cloud API key in the environment (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...)
used to auto-enable AI summarization, silently billing the account on every
index. Auto-detect now refuses to select a paid cloud provider from a bare key
unless the user explicitly opts in (names the provider, or sets
JDOCMUNCH_ALLOW_PAID_SUMMARIES).
"""

import pytest

from jdocmunch_mcp.summarizer import batch_summarize as bs

_CLOUD_KEYS = ["ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in _CLOUD_KEYS + ["JDOCMUNCH_SUMMARIZER_PROVIDER", "JDOCMUNCH_ALLOW_PAID_SUMMARIES"]:
        monkeypatch.delenv(k, raising=False)
    bs._WARNED_SUPPRESSED_PAID.clear()


@pytest.mark.parametrize("env_var", _CLOUD_KEYS)
def test_bare_paid_key_does_not_auto_select(monkeypatch, env_var):
    monkeypatch.setenv(env_var, "sk-would-bill")
    assert bs.get_provider_name() is None


@pytest.mark.parametrize("env_var,expected", list(zip(_CLOUD_KEYS, ["anthropic", "gemini", "openai", "minimax", "glm"])))
def test_env_opt_in_restores_auto_select(monkeypatch, env_var, expected):
    monkeypatch.setenv(env_var, "sk-test")
    monkeypatch.setenv("JDOCMUNCH_ALLOW_PAID_SUMMARIES", "1")
    assert bs.get_provider_name() == expected


def test_explicit_provider_still_honored(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "anthropic")
    assert bs.get_provider_name() == "anthropic"


def test_create_summarizer_none_on_bare_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-would-bill")
    assert bs._create_summarizer() is None
