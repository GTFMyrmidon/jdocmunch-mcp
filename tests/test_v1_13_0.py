"""Tests for v1.13.0: posting-list prune, RRF hybrid fusion, query embedding cache."""

from __future__ import annotations

import time

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.retrieval.prune import (
    MAX_CANDIDATES,
    PostingIndex,
    get_or_build,
    reciprocal_rank_fusion,
)
from jdocmunch_mcp.storage import DocStore


# ---------------------------------------------------------------------------
# PostingIndex
# ---------------------------------------------------------------------------

class TestPostingIndex:
    def test_build_indexes_title_summary_content(self):
        sections = [
            {"id": "a#1", "title": "Authentication", "summary": "tokens", "content": "secrets here"},
            {"id": "b#1", "title": "Misc", "summary": "", "content": "unrelated stuff"},
        ]
        idx = PostingIndex.build(sections)
        # Tokens from title + summary + content all indexed
        assert "a#1" in idx.postings.get("authentication", set())
        assert "a#1" in idx.postings.get("tokens", set())
        assert "a#1" in idx.postings.get("secrets", set())
        assert "b#1" in idx.postings.get("unrelated", set())

    def test_candidates_returns_intersection_of_postings(self):
        sections = [
            {"id": "a#1", "title": "Authentication", "summary": "", "content": "alpha"},
            {"id": "b#1", "title": "Logging", "summary": "", "content": "beta"},
            {"id": "c#1", "title": "Caching", "summary": "", "content": "alpha gamma"},
        ]
        idx = PostingIndex.build(sections)
        cand = idx.candidates("alpha")
        assert cand == {"a#1", "c#1"}

    def test_candidates_returns_none_for_zero_in_vocab(self):
        sections = [{"id": "a#1", "title": "Authentication", "summary": "", "content": ""}]
        idx = PostingIndex.build(sections)
        # Query has only stop-words and a token not in the corpus.
        assert idx.candidates("the the the") is None

    def test_max_candidates_cap_respected(self):
        # Build 500 sections all sharing token 'common'.
        sections = [
            {"id": f"s{i}#1", "title": "Common", "summary": "", "content": ""}
            for i in range(500)
        ]
        idx = PostingIndex.build(sections)
        cand = idx.candidates("common", max_candidates=42)
        assert cand is not None
        assert len(cand) == 42

    def test_uses_content_loader_when_inline_empty(self):
        called = []

        def loader(doc_path, byte_start, byte_end):
            called.append((doc_path, byte_start, byte_end))
            return "loaded body alpha"

        sections = [
            {"id": "a#1", "title": "T", "summary": "", "content": "",
             "doc_path": "f.md", "byte_start": 0, "byte_end": 10},
        ]
        idx = PostingIndex.build(sections, content_loader=loader)
        assert called  # loader invoked
        assert "a#1" in idx.postings.get("alpha", set())

    def test_get_or_build_caches_per_section_list_identity(self):
        from types import SimpleNamespace

        sections = [{"id": "a#1", "title": "Foo", "summary": "", "content": ""}]
        idx = SimpleNamespace(sections=sections)
        first = get_or_build(idx)
        second = get_or_build(idx)
        assert first is second  # cached
        # Replacing the section list invalidates.
        idx.sections = [{"id": "b#1", "title": "Bar", "summary": "", "content": ""}]
        third = get_or_build(idx)
        assert third is not first


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------

class TestRRF:
    def test_basic_fusion(self):
        # Both rankings agree on 'a' first.
        out = reciprocal_rank_fusion([["a", "b"], ["a", "c"]])
        assert out[0][0] == "a"

    def test_rrf_score_formula(self):
        # Single ranking: score = 1/(60+rank).
        out = reciprocal_rank_fusion([["a", "b", "c"]], k=60)
        a_score = next(s for sid, s in out if sid == "a")
        b_score = next(s for sid, s in out if sid == "b")
        assert pytest.approx(a_score) == 1.0 / 61
        assert pytest.approx(b_score) == 1.0 / 62

    def test_weighted_rankings(self):
        # Lex-only weighting elevates 'a'; semantic-only would elevate 'z'.
        out = reciprocal_rank_fusion(
            [["a", "z"], ["z", "a"]],
            weights=[1.0, 0.0],
        )
        assert out[0][0] == "a"

    def test_empty_input(self):
        assert reciprocal_rank_fusion([]) == []

    def test_stable_tie_break(self):
        # Two rankings with identical fused scores — order should be stable
        # (first-seen).
        out = reciprocal_rank_fusion([["x"], ["y"]])
        # x and y have the same score (1/61 each); first ranking's x wins.
        assert out[0][0] == "x"
        assert out[1][0] == "y"

    def test_weights_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            reciprocal_rank_fusion([["a"], ["b"]], weights=[1.0])

    def test_higher_k_flattens_curve(self):
        # Larger k → smaller per-rank score → top-1 advantage shrinks.
        small_k = reciprocal_rank_fusion([["a", "b"]], k=1)
        large_k = reciprocal_rank_fusion([["a", "b"]], k=1000)
        a_small, b_small = small_k[0][1], small_k[1][1]
        a_large, b_large = large_k[0][1], large_k[1][1]
        assert (a_small / b_small) > (a_large / b_large)


# ---------------------------------------------------------------------------
# Two-stage retrieval end-to-end via DocStore.search
# ---------------------------------------------------------------------------

class TestTwoStage:
    def test_prune_built_lazily_and_cached(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections

        store = DocStore(base_path=str(tmp_path))
        content = (
            "# Doc\n\n"
            "## Authentication\n\nAlpha tokens describe authentication flow.\n\n"
            "## Logging\n\nNothing relevant here.\n"
        )
        sections = parse_file(content, "g.md", "local/r")
        store.save_index(
            owner="local",
            name="r",
            sections=sections,
            raw_files={"g.md": content},
            doc_types={".md": 1},
        )
        # First call builds the posting index.
        out1 = search_sections(repo="local/r", query="authentication", semantic=False, storage_path=str(tmp_path))
        index = store.load_index("local", "r")
        first_posting = getattr(index, "_posting_index", None)
        assert first_posting is not None, "posting index must be built on first search"
        # Second call reuses it.
        out2 = search_sections(repo="local/r", query="authentication", semantic=False, storage_path=str(tmp_path))
        index_again = store.load_index("local", "r")
        assert getattr(index_again, "_posting_index", None) is first_posting

    def test_prune_skips_unrelated_sections(self, tmp_path):
        """A section without any query-token in title/summary/content is
        eliminated by the prune. We probe by checking that scoring isn't
        called for the unrelated section — the BM25 candidate set is
        observable via search results."""
        from jdocmunch_mcp.tools.search_sections import search_sections

        store = DocStore(base_path=str(tmp_path))
        content = (
            "# Top\n\n"
            "## Alpha\n\n"
            "alpha alpha alpha\n\n"
            "## Beta\n\n"
            "totally different words about caching layers\n"
        )
        sections = parse_file(content, "g.md", "local/r")
        store.save_index(
            owner="local",
            name="r",
            sections=sections,
            raw_files={"g.md": content},
            doc_types={".md": 1},
        )
        out = search_sections(repo="local/r", query="alpha", semantic=False, storage_path=str(tmp_path))
        ids = [r["id"] for r in out["results"]]
        # Only the Alpha section (and possibly the Top root) should appear.
        for sid in ids:
            assert "Beta" not in sid


# ---------------------------------------------------------------------------
# Query embedding cache
# ---------------------------------------------------------------------------

class TestQueryEmbeddingCache:
    def test_cache_hits_skip_provider(self, monkeypatch):
        from jdocmunch_mcp.embeddings import provider as p

        calls = {"n": 0}

        class _FakeProvider:
            def __init__(self):
                pass

            def embed_texts(self, texts, task_type="retrieval_document"):
                calls["n"] += 1
                return [[0.1, 0.2, 0.3] for _ in texts]

        p._reset_provider_cache()
        p._reset_query_cache()
        monkeypatch.setitem(p._PROVIDER_FACTORIES, "fake", _FakeProvider)
        monkeypatch.setattr(p, "get_provider_name", lambda: "fake")

        v1 = p.embed_query("hello world")
        v2 = p.embed_query("hello world")
        v3 = p.embed_query("hello world")
        assert v1 == [0.1, 0.2, 0.3]
        assert v2 is v1 or v2 == v1
        assert v3 == v1
        assert calls["n"] == 1, "provider should only be called on the first query"

    def test_distinct_queries_cached_separately(self, monkeypatch):
        from jdocmunch_mcp.embeddings import provider as p

        calls = {"n": 0}

        class _FakeProvider:
            def __init__(self):
                pass

            def embed_texts(self, texts, task_type="retrieval_document"):
                calls["n"] += 1
                return [[float(len(t)), 0.0] for t in texts]

        p._reset_provider_cache()
        p._reset_query_cache()
        monkeypatch.setitem(p._PROVIDER_FACTORIES, "fake", _FakeProvider)
        monkeypatch.setattr(p, "get_provider_name", lambda: "fake")

        a = p.embed_query("foo")
        b = p.embed_query("longer query")
        assert a != b
        assert calls["n"] == 2

    def test_ttl_expiry(self, monkeypatch):
        from jdocmunch_mcp.embeddings import provider as p

        calls = {"n": 0}

        class _FakeProvider:
            def __init__(self):
                pass

            def embed_texts(self, texts, task_type="retrieval_document"):
                calls["n"] += 1
                return [[float(calls["n"])]]

        p._reset_provider_cache()
        p._reset_query_cache()
        monkeypatch.setitem(p._PROVIDER_FACTORIES, "fake", _FakeProvider)
        monkeypatch.setattr(p, "get_provider_name", lambda: "fake")
        # Shrink TTL for this test.
        monkeypatch.setattr(p, "_QUERY_CACHE_TTL_SECONDS", 0.01)

        v1 = p.embed_query("q")
        time.sleep(0.05)
        v2 = p.embed_query("q")
        assert v1 != v2, "stale entries must be refetched"
        assert calls["n"] == 2

    def test_provider_rotation_invalidates(self, monkeypatch):
        from jdocmunch_mcp.embeddings import provider as p

        class _A:
            def __init__(self):
                pass

            def embed_texts(self, texts, task_type="retrieval_document"):
                return [[1.0]]

        class _B:
            def __init__(self):
                pass

            def embed_texts(self, texts, task_type="retrieval_document"):
                return [[2.0]]

        p._reset_provider_cache()
        p._reset_query_cache()
        monkeypatch.setitem(p._PROVIDER_FACTORIES, "fake_a", _A)
        monkeypatch.setitem(p._PROVIDER_FACTORIES, "fake_b", _B)

        monkeypatch.setattr(p, "get_provider_name", lambda: "fake_a")
        v1 = p.embed_query("q")
        monkeypatch.setattr(p, "get_provider_name", lambda: "fake_b")
        v2 = p.embed_query("q")
        assert v1 == [1.0]
        assert v2 == [2.0]
