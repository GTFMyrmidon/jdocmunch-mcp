"""Tests for v1.15.0: embedding cache + drift canary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jdocmunch_mcp.embeddings import cache as emb_cache
from jdocmunch_mcp.embeddings import provider as emb_provider
from jdocmunch_mcp.embeddings.embed_drift import (
    CANARY_STRINGS,
    CANARY_VERSION,
    _cosine,
    capture_canary,
    check_drift,
)


# ---------------------------------------------------------------------------
# Cache primitives
# ---------------------------------------------------------------------------

class TestEmbeddingCache:
    def test_load_missing_returns_empty(self, tmp_path):
        out = emb_cache.load(str(tmp_path), "owner", "name", provider="p", model="m", dim=4)
        assert out == {}

    def test_round_trip(self, tmp_path):
        entries = [("h1", [0.1, 0.2, 0.3, 0.4]), ("h2", [1.0, 0.0, 0.0, 0.0])]
        emb_cache.write(str(tmp_path), "o", "n", provider="p", model="m", dim=4, entries=entries)
        out = emb_cache.load(str(tmp_path), "o", "n", provider="p", model="m", dim=4)
        assert out == {"h1": [0.1, 0.2, 0.3, 0.4], "h2": [1.0, 0.0, 0.0, 0.0]}

    def test_identity_mismatch_purges_on_load(self, tmp_path):
        emb_cache.write(
            str(tmp_path), "o", "n",
            provider="gemini", model="text-embedding-004", dim=768,
            entries=[("h1", [0.0] * 768)],
        )
        # Different provider — load returns empty (caller will rewrite).
        out = emb_cache.load(
            str(tmp_path), "o", "n",
            provider="openai", model="text-embedding-3-small", dim=1536,
        )
        assert out == {}

    def test_dim_wildcard_when_dim_none(self, tmp_path):
        emb_cache.write(
            str(tmp_path), "o", "n",
            provider="sbert", model="all-MiniLM-L6-v2", dim=None,
            entries=[("h1", [0.0] * 384)],
        )
        out = emb_cache.load(
            str(tmp_path), "o", "n",
            provider="sbert", model="all-MiniLM-L6-v2", dim=None,
        )
        assert out == {"h1": [0.0] * 384}

    def test_atomic_rewrite_does_not_lose_old_on_crash(self, tmp_path, monkeypatch):
        # Seed the cache.
        emb_cache.write(
            str(tmp_path), "o", "n",
            provider="p", model="m", dim=4,
            entries=[("h1", [1.0, 0.0, 0.0, 0.0])],
        )
        original = emb_cache.load(str(tmp_path), "o", "n", provider="p", model="m", dim=4)
        assert "h1" in original

        # Simulate a crash: monkeypatch tmp.replace to raise.
        path = emb_cache._cache_path(str(tmp_path), "o", "n")
        original_replace = Path.replace

        def _failing_replace(self, target):
            raise OSError("simulated crash")

        monkeypatch.setattr(Path, "replace", _failing_replace)
        with pytest.raises(OSError):
            emb_cache.write(
                str(tmp_path), "o", "n",
                provider="p", model="m", dim=4,
                entries=[("h2", [0.0, 1.0, 0.0, 0.0])],
            )
        monkeypatch.setattr(Path, "replace", original_replace)

        # Original cache should still load — tmp file never replaced the real one.
        recovered = emb_cache.load(str(tmp_path), "o", "n", provider="p", model="m", dim=4)
        assert "h1" in recovered

    def test_purge_deletes_file(self, tmp_path):
        emb_cache.write(
            str(tmp_path), "o", "n",
            provider="p", model="m", dim=2,
            entries=[("h", [1.0, 0.0])],
        )
        path = emb_cache._cache_path(str(tmp_path), "o", "n")
        assert path.exists()
        assert emb_cache.purge(str(tmp_path), "o", "n") is True
        assert not path.exists()

    def test_purge_missing_returns_false(self, tmp_path):
        assert emb_cache.purge(str(tmp_path), "o", "n") is False


# ---------------------------------------------------------------------------
# embed_sections cache integration
# ---------------------------------------------------------------------------

class _FakeProvider:
    """Deterministic embeddings for tests — keyed by string length."""

    def __init__(self):
        self.calls = 0

    def embed_texts(self, texts, task_type="retrieval_document"):
        self.calls += 1
        return [[float(len(t)), 0.0, 0.0, 0.0] for t in texts]


class _FakeSection:
    def __init__(self, content_hash: str, content: str = "", title: str = "T", summary: str = ""):
        self.content_hash = content_hash
        self.content = content
        self.title = title
        self.summary = summary
        self.embedding = []


class TestEmbedSectionsCache:
    def setup_method(self):
        emb_provider._reset_provider_cache()

    def teardown_method(self):
        emb_provider._reset_provider_cache()

    def test_cache_hits_skip_provider(self, tmp_path, monkeypatch):
        fake = _FakeProvider()
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "fake", lambda: fake)
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake")
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: ("fake-model", 4))

        s1 = _FakeSection("hash-a", content="alpha alpha alpha")
        s2 = _FakeSection("hash-b", content="beta beta")

        # First pass: both miss.
        emb_provider.embed_sections([s1, s2], owner="o", name="n", storage_path=str(tmp_path))
        assert fake.calls == 1
        assert s1.embedding
        assert s2.embedding

        # Second pass: both hit cache — no new provider calls.
        s1b = _FakeSection("hash-a", content="alpha alpha alpha")
        s2b = _FakeSection("hash-b", content="beta beta")
        emb_provider.embed_sections([s1b, s2b], owner="o", name="n", storage_path=str(tmp_path))
        assert fake.calls == 1, "no provider calls on full cache hit"
        assert s1b.embedding == s1.embedding
        assert s2b.embedding == s2.embedding

    def test_partial_cache_hit_only_misses_call_provider(self, tmp_path, monkeypatch):
        fake = _FakeProvider()
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "fake", lambda: fake)
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake")
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: ("fake-model", 4))

        s_old = _FakeSection("hash-cached", content="kept content")
        emb_provider.embed_sections([s_old], owner="o", name="n", storage_path=str(tmp_path))
        assert fake.calls == 1

        # New mix: one cached, one new.
        s_old2 = _FakeSection("hash-cached", content="kept content")
        s_new = _FakeSection("hash-fresh", content="brand new content")
        emb_provider.embed_sections([s_old2, s_new], owner="o", name="n", storage_path=str(tmp_path))
        # Provider invoked once more, batch size 1 (only the miss).
        assert fake.calls == 2
        assert s_old2.embedding == s_old.embedding
        assert s_new.embedding

    def test_provider_change_purges_cache(self, tmp_path, monkeypatch):
        fake_a = _FakeProvider()
        fake_b = _FakeProvider()
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "fake_a", lambda: fake_a)
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "fake_b", lambda: fake_b)
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: (f"{name}-model", 4))

        # Embed under provider A.
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake_a")
        s = _FakeSection("h", content="text")
        emb_provider.embed_sections([s], owner="o", name="n", storage_path=str(tmp_path))
        assert fake_a.calls == 1

        # Switch to provider B — identity mismatch must force re-embed.
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake_b")
        s2 = _FakeSection("h", content="text")
        emb_provider.embed_sections([s2], owner="o", name="n", storage_path=str(tmp_path))
        assert fake_b.calls == 1, "identity mismatch must invalidate"

    def test_no_cache_when_owner_name_omitted(self, tmp_path, monkeypatch):
        fake = _FakeProvider()
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "fake", lambda: fake)
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake")
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: ("fake-model", 4))

        s = _FakeSection("h", content="text")
        emb_provider.embed_sections([s])  # no owner/name → no caching
        emb_provider.embed_sections([s])
        assert fake.calls == 2, "without cache, every call hits provider"


# ---------------------------------------------------------------------------
# Drift canary
# ---------------------------------------------------------------------------

class TestCanaryContract:
    def test_canary_strings_nonempty(self):
        assert len(CANARY_STRINGS) >= 16
        assert all(isinstance(s, str) and s for s in CANARY_STRINGS)

    def test_canary_contains_markers(self):
        joined = "\n".join(CANARY_STRINGS).lower()
        # Sanity: canary must cover the genres claimed in the docstring.
        assert "search_sections" in joined
        assert "https://" in joined
        assert "todo" in joined  # technical-marker style
        assert "fox" in joined  # natural language

    def test_canary_version_int(self):
        assert isinstance(CANARY_VERSION, int) and CANARY_VERSION >= 1


class TestCosine:
    def test_identical_vectors_one(self):
        assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_zero(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_opposite_minus_one(self):
        assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_length_mismatch_returns_zero(self):
        assert _cosine([1.0], [1.0, 0.0]) == 0.0


class TestCanaryCaptureAndCheck:
    def setup_method(self):
        emb_provider._reset_provider_cache()

    def teardown_method(self):
        emb_provider._reset_provider_cache()

    def test_capture_no_provider_returns_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: None)
        out = capture_canary(base_path=str(tmp_path))
        assert out["status"] == "no_provider"

    def test_check_drift_no_canary_returns_hint(self, tmp_path):
        out = check_drift(base_path=str(tmp_path))
        assert out["has_canary"] is False
        assert out["alarm"] is False
        assert "hint" in out

    def test_capture_then_check_no_drift(self, tmp_path, monkeypatch):
        # Stub a deterministic embedder.
        class _StubProvider:
            def embed_texts(self, texts, task_type="retrieval_document"):
                # Identity-like vector so drift is exactly zero on re-check.
                return [[float(len(t)), 0.5, 0.25, 0.125] for t in texts]

        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "stub", _StubProvider)
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "stub")
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: ("stub-model", 4))

        captured = capture_canary(base_path=str(tmp_path))
        assert captured["status"] == "captured"
        assert captured["n_canaries"] == len(CANARY_STRINGS)

        out = check_drift(base_path=str(tmp_path))
        assert out["has_canary"] is True
        assert out["alarm"] is False
        assert out["max_drift"] == 0.0
        assert out["mean_drift"] == 0.0

    def test_capture_idempotent_unless_forced(self, tmp_path, monkeypatch):
        class _StubProvider:
            def embed_texts(self, texts, task_type="retrieval_document"):
                return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "stub", _StubProvider)
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "stub")
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: ("stub-model", 4))

        first = capture_canary(base_path=str(tmp_path))
        assert first["status"] == "captured"
        second = capture_canary(base_path=str(tmp_path))
        assert second["status"] == "canary_already_exists"
        third = capture_canary(force=True, base_path=str(tmp_path))
        assert third["status"] == "captured"

    def test_drift_alarm_on_provider_swap(self, tmp_path, monkeypatch):
        # Capture under provider A.
        class _ProviderA:
            def embed_texts(self, texts, task_type="retrieval_document"):
                return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        class _ProviderB:
            # Orthogonal vectors → cosine 0 → drift 1.0.
            def embed_texts(self, texts, task_type="retrieval_document"):
                return [[0.0, 1.0, 0.0, 0.0] for _ in texts]

        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "a", _ProviderA)
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "b", _ProviderB)
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: (f"{name}-model", 4))

        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "a")
        capture_canary(base_path=str(tmp_path))

        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "b")
        out = check_drift(threshold=0.05, base_path=str(tmp_path))
        assert out["alarm"] is True
        assert out["max_drift"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# MCP tool wrapper
# ---------------------------------------------------------------------------

class TestCheckEmbeddingDriftTool:
    def setup_method(self):
        emb_provider._reset_provider_cache()

    def test_tool_capture_then_check_flow(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.check_embedding_drift import check_embedding_drift

        class _StubProvider:
            def embed_texts(self, texts, task_type="retrieval_document"):
                return [[1.0, 0.0] for _ in texts]

        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "stub", _StubProvider)
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "stub")
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: ("stub-model", 2))

        cap = check_embedding_drift(capture=True, storage_path=str(tmp_path))
        assert cap["status"] == "captured"

        check = check_embedding_drift(storage_path=str(tmp_path))
        assert check["has_canary"] is True
        assert check["alarm"] is False


# ---------------------------------------------------------------------------
# Server registration
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_check_embedding_drift_in_tool_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv

        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        assert "check_embedding_drift" in names
