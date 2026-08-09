"""jdoc#112 — a private corpus can have AI summaries without a cloud provider.

Reported by @pnm-jgb. Every valid `JDOCMUNCH_SUMMARIZER_PROVIDER` was remote
cloud, so for documentation containing internal architecture, credential
locations or security findings the only choices were "no AI summaries" or
"send every section to a third party".

⚠ This is not cosmetic. `embeddings/provider.py` puts `section.summary` into
the embedded text, and the content itself is capped (jdoc#111), so the summary
is the only channel through which anything past that cap can influence a
section's vector. Retrieval quality was materially gated behind exporting the
corpus.

The client machinery already existed — `_make_openai_compat` serves openai,
minimax and glm — and only a configurable endpoint was missing. This mirrors
the embedding side's `openai-compatible` precedent, including its reasoning for
staying OUT of the paid set: an explicit URL + model cannot be reached by an
ambient stray key, so configuring it IS the opt-in.
"""

import pytest

from jdocmunch_mcp.summarizer import batch_summarize as bs


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "JDOCMUNCH_SUMMARIZER_PROVIDER", "JDOCMUNCH_SUMMARIZER_URL",
        "JDOCMUNCH_SUMMARIZER_MODEL", "JDOCMUNCH_SUMMARIZER_API_KEY",
        "JDOCMUNCH_ALLOW_PAID_SUMMARIES", "ANTHROPIC_API_KEY", "GOOGLE_API_KEY",
        "OPENAI_API_KEY", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    bs._WARNED_SUPPRESSED_PAID.clear()


def _configure_local(monkeypatch, url="http://localhost:11434/v1", model="qwen3:8b"):
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_URL", url)
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_MODEL", model)


# --- selection -------------------------------------------------------------

def test_openai_compatible_is_a_valid_provider():
    assert "openai-compatible" in bs._VALID_PROVIDERS


def test_it_is_not_treated_as_paid_cloud():
    """⚠ Same reasoning as the embedding side: an explicit URL + model is the
    opt-in, and the usual target is a local runtime."""
    assert "openai-compatible" not in bs._PAID_CLOUD_PROVIDERS


def test_configuring_url_and_model_selects_it(monkeypatch):
    _configure_local(monkeypatch)
    assert bs.get_provider_name() == "openai-compatible"


@pytest.mark.parametrize("url,model", [
    ("http://localhost:11434/v1", ""),
    ("", "qwen3:8b"),
    ("", ""),
    ("   ", "   "),
])
def test_half_configured_does_not_auto_select(monkeypatch, url, model):
    """Both halves or nothing — a stray URL must not select a dead endpoint."""
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_URL", url)
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_MODEL", model)
    assert bs.get_provider_name() is None


def test_a_local_target_outranks_an_ambient_cloud_key(monkeypatch):
    """⚠⚠ The whole point: free and local beats billed and remote.

    Falling through to Anthropic here would send the corpus off the machine
    while a perfectly good local model sat configured.
    """
    _configure_local(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-whatever")
    assert bs.get_provider_name() == "openai-compatible"


def test_it_wins_even_when_paid_summaries_are_allowed(monkeypatch):
    _configure_local(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    monkeypatch.setenv("JDOCMUNCH_ALLOW_PAID_SUMMARIES", "1")
    assert bs.get_provider_name() == "openai-compatible"


def test_naming_it_explicitly_is_honored(monkeypatch):
    _configure_local(monkeypatch)
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "openai-compatible")
    assert bs.get_provider_name() == "openai-compatible"


def test_none_still_wins_over_a_configured_local_target(monkeypatch):
    """⚠ An explicit 'none' is a decision and must not be overridden."""
    _configure_local(monkeypatch)
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "none")
    assert bs.get_provider_name() is None


def test_an_explicit_cloud_provider_still_wins(monkeypatch):
    _configure_local(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "anthropic")
    assert bs.get_provider_name() == "anthropic"


def test_nothing_configured_still_means_no_summarizer(monkeypatch):
    assert bs.get_provider_name() is None


# --- construction ----------------------------------------------------------

def test_the_summarizer_is_built_from_the_configured_endpoint(monkeypatch):
    # ⚠ `openai` is an optional extra and is NOT installed on CI. Constructing
    # the summarizer imports it, so these two must skip rather than fail —
    # a dev box that happens to have it is not a fixture.
    pytest.importorskip("openai")
    _configure_local(monkeypatch, "http://127.0.0.1:8000/v1", "llama-3.3-70b")
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_API_KEY", "sekrit")
    built = bs._PROVIDERS["openai-compatible"]()
    # ⚠ The class keeps only `model`; url and key are handed to the OpenAI
    # client, so read them back from there rather than adding attributes that
    # exist only to be asserted.
    assert str(built._client.base_url).rstrip("/") == "http://127.0.0.1:8000/v1"
    assert built.model == "llama-3.3-70b"
    assert built._client.api_key == "sekrit"


def test_a_missing_key_falls_back_to_a_placeholder(monkeypatch):
    """Local runtimes ignore the key, but the client requires one."""
    pytest.importorskip("openai")
    _configure_local(monkeypatch)
    assert bs._PROVIDERS["openai-compatible"]()._client.api_key == "local"


@pytest.mark.parametrize("url,model,expect", [
    ("", "qwen3:8b", "JDOCMUNCH_SUMMARIZER_URL"),
    ("http://localhost:11434/v1", "", "JDOCMUNCH_SUMMARIZER_MODEL"),
])
def test_naming_it_while_half_configured_says_what_is_missing(monkeypatch, url, model, expect):
    """⚠ Naming the provider bypasses the configured-check, so this is where a
    half-configured setup has to surface — not later as an opaque connection
    error in the middle of an index."""
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_URL", url)
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_MODEL", model)
    with pytest.raises(ValueError, match=expect):
        bs._PROVIDERS["openai-compatible"]()


def test_a_half_configured_run_degrades_instead_of_dying(monkeypatch):
    """`_create_summarizer` swallows it and indexing continues heuristically."""
    monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "openai-compatible")
    assert bs._create_summarizer() is None


def test_summarize_sections_still_produces_summaries_without_a_provider(monkeypatch):
    """End to end: no summarizer configured is not an error, just tier 1/3."""
    class _S:
        def __init__(self):
            self.title = "Network resilience"
            self.summary = ""
            self.content = "When the uplink drops, queued work is retained."

    out = bs.summarize_sections([_S()], use_ai=True)
    assert out[0].summary
