"""Tests for the v1.12.0 retrieval engine: tokenizer, BM25-Okapi, heading-path boost."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.retrieval.bm25 import (
    FIELD_WEIGHTS,
    HEADING_PATH_WEIGHT,
    _ancestor_titles_from_id,
    _bm25_field,
    _idf,
    compute_corpus_stats,
    score_section,
)
from jdocmunch_mcp.retrieval.tokenize import (
    STOP_WORDS,
    term_frequencies,
    tokenize,
    tokenize_unique,
)
from jdocmunch_mcp.storage import DocStore


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

class TestTokenizer:
    def test_basic_lowercase_split(self):
        assert tokenize("Hello World") == ["hello", "world"]

    def test_drops_short_tokens(self):
        # 'a' is stop-word AND <2 chars
        assert "a" not in tokenize("a big dog")

    def test_drops_stop_words(self):
        toks = tokenize("the quick brown fox")
        assert "the" not in toks
        assert "quick" in toks
        assert "brown" in toks
        assert "fox" in toks

    def test_camel_case_split(self):
        assert tokenize("DocStore") == ["doc", "store"]
        assert tokenize("HTTPSConnection") == ["https", "connection"]

    def test_snake_kebab_split(self):
        assert tokenize("embed_query") == ["embed", "query"]
        assert tokenize("foo-bar-baz") == ["foo", "bar", "baz"]

    def test_url_expansion(self):
        toks = tokenize("See https://api.example.com/v2/users for details")
        assert "api" in toks
        assert "example" in toks
        assert "users" in toks
        assert "https" not in toks  # scheme stripped

    def test_inline_code_kept_but_stripped_of_backticks(self):
        toks = tokenize("Call `embed_query` here")
        assert "embed" in toks
        assert "query" in toks

    def test_fenced_code_stripped(self):
        text = "Before\n```\nfoo bar baz\n```\nAfter"
        toks = tokenize(text)
        assert "before" in toks
        assert "after" in toks
        assert "foo" not in toks
        assert "bar" not in toks

    def test_markdown_link_keeps_text_drops_url(self):
        toks = tokenize("[Click here](https://x.com/path)")
        assert "click" in toks
        # URL host tokens should NOT appear because the link rewrite drops the URL
        assert "x" not in toks  # 'x' is also <2 chars but be defensive

    def test_empty_input(self):
        assert tokenize("") == []
        assert tokenize_unique("") == set()

    def test_tokenize_unique_dedupes(self):
        out = tokenize_unique("foo foo foo bar")
        assert out == {"foo", "bar"}

    def test_term_frequencies(self):
        assert term_frequencies(["foo", "foo", "bar"]) == {"foo": 2, "bar": 1}

    def test_stop_words_set_is_frozen(self):
        with pytest.raises(AttributeError):
            STOP_WORDS.add("notallowed")  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# IDF math
# ---------------------------------------------------------------------------

class TestIDF:
    def test_idf_zero_corpus(self):
        assert _idf(df=1, N=0) == 0.0

    def test_idf_ubiquitous_term(self):
        # Term in every document — IDF should be near zero (Robertson formula
        # adds +1 inside the log so it's never negative).
        score = _idf(df=100, N=100)
        assert score >= 0.0
        assert score < 1.0

    def test_idf_rare_term(self):
        # Rare term in a large corpus — IDF should be high.
        rare = _idf(df=1, N=10000)
        common = _idf(df=5000, N=10000)
        assert rare > common

    def test_idf_monotone_in_df(self):
        scores = [_idf(df=k, N=100) for k in (1, 10, 50, 90)]
        # Monotone non-increasing in df.
        for a, b in zip(scores, scores[1:]):
            assert a >= b


# ---------------------------------------------------------------------------
# Field-level BM25 saturation + length normalization
# ---------------------------------------------------------------------------

class TestBM25Field:
    def test_no_match_returns_zero(self):
        score = _bm25_field(["alpha"], "beta gamma delta", 3.0, df={}, N=10)
        assert score == 0.0

    def test_empty_field_returns_zero(self):
        score = _bm25_field(["alpha"], "", 3.0, df={"alpha": 1}, N=10)
        assert score == 0.0

    def test_term_frequency_saturation(self):
        # BM25 saturates: doubling TF should NOT double the score.
        df = {"alpha": 1}
        text_1 = "alpha"
        text_5 = "alpha alpha alpha alpha alpha"
        s1 = _bm25_field(["alpha"], text_1, 1.0, df, N=10)
        s5 = _bm25_field(["alpha"], text_5, 1.0, df, N=10)
        assert s5 > s1
        assert s5 < 5 * s1  # saturation in action

    def test_length_normalization_penalizes_long_docs(self):
        df = {"alpha": 1}
        short = "alpha"
        # Long doc with same TF — should score less under b=0.75.
        long_text = "alpha " + "filler " * 50
        s_short = _bm25_field(["alpha"], short, 1.0, df, N=10)
        s_long = _bm25_field(["alpha"], long_text, 1.0, df, N=10)
        assert s_short > s_long


# ---------------------------------------------------------------------------
# compute_corpus_stats
# ---------------------------------------------------------------------------

class TestCorpusStats:
    def test_handles_section_objects(self):
        sections = parse_file("# Title\n\nbody text", "f.md", "local/r")
        stats = compute_corpus_stats(sections)
        assert stats["N"] == len(sections)
        assert "title" in stats["avgdl"]
        assert "summary" in stats["avgdl"]
        assert "content" in stats["avgdl"]
        assert isinstance(stats["df"], dict)

    def test_handles_section_dicts(self):
        sections = [
            {"title": "Foo", "summary": "bar baz", "content": "alpha beta gamma", "id": "x::y::s#1"}
        ]
        stats = compute_corpus_stats(sections)
        assert stats["N"] == 1
        assert "alpha" in stats["df"]
        assert "foo" in stats["df"]

    def test_df_capped_at_5000(self):
        # Synthesize > 5000 distinct terms across one big content body and assert cap.
        big_terms = " ".join(f"tok{i:05d}" for i in range(6000))
        sections = [{"title": "T", "summary": "", "content": big_terms, "id": "x::y::s#1"}]
        stats = compute_corpus_stats(sections)
        assert len(stats["df"]) <= 5000

    def test_loader_used_when_content_empty(self):
        called = {}

        def loader(doc_path, byte_start, byte_end):
            called["yes"] = (doc_path, byte_start, byte_end)
            return "loaded body"

        sections = [
            {"title": "T", "summary": "", "content": "", "doc_path": "x.md",
             "byte_start": 0, "byte_end": 11, "id": "x::y::s#1"}
        ]
        stats = compute_corpus_stats(sections, content_loader=loader)
        assert called.get("yes") == ("x.md", 0, 11)
        assert "loaded" in stats["df"]


# ---------------------------------------------------------------------------
# Heading-path recovery
# ---------------------------------------------------------------------------

class TestAncestorRecovery:
    def test_no_ancestors_returns_empty(self):
        assert _ancestor_titles_from_id("local/r::doc.md::leaf#3") == []

    def test_single_ancestor(self):
        out = _ancestor_titles_from_id("local/r::doc.md::root/leaf#3")
        assert out == ["root"]

    def test_deep_chain(self):
        out = _ancestor_titles_from_id("local/r::d.md::a/b/c/d#5")
        assert out == ["a", "b", "c"]

    def test_hyphens_become_spaces(self):
        # Slug 'security-audit' becomes 'security audit' — so query "security"
        # tokenizes into the heading-path channel.
        out = _ancestor_titles_from_id("r::d.md::security-audit/leaf#3")
        assert out == ["security audit"]

    def test_malformed_id_safe(self):
        assert _ancestor_titles_from_id("") == []
        assert _ancestor_titles_from_id("nope") == []


# ---------------------------------------------------------------------------
# score_section integration
# ---------------------------------------------------------------------------

class TestScoreSection:
    def _stats(self):
        return {
            "N": 4,
            "avgdl": {"title": 2.0, "summary": 6.0, "content": 50.0},
            "df": {"alpha": 1, "beta": 2, "shared": 4},
        }

    def test_field_weights_title_dominates(self):
        sec_title = {"id": "r::d::s#1", "title": "alpha", "summary": "", "content": ""}
        sec_content = {"id": "r::d::s#1", "title": "x", "summary": "", "content": "alpha"}
        s_t = score_section(sec_title, "alpha", stats=self._stats())
        s_c = score_section(sec_content, "alpha", stats=self._stats())
        assert s_t > s_c, f"title-only ({s_t}) must outscore content-only ({s_c}) at FW {FIELD_WEIGHTS}"

    def test_heading_path_boost(self):
        sec_with_path = {"id": "r::d::auth/tokens#3", "title": "Tokens", "summary": "", "content": ""}
        sec_without = {"id": "r::d::tokens#3", "title": "Tokens", "summary": "", "content": ""}
        # Query 'auth' should boost the section whose ancestors include 'auth'.
        stats = {
            "N": 4,
            "avgdl": {"title": 2.0, "summary": 6.0, "content": 50.0},
            "df": {"auth": 1, "tokens": 1},
        }
        s_with = score_section(sec_with_path, "auth", stats=stats)
        s_without = score_section(sec_without, "auth", stats=stats)
        assert s_with > s_without

    def test_lazy_content_loader(self):
        called = {}

        def loader(doc_path, byte_start, byte_end):
            called["yes"] = True
            return "alpha beta"

        sec = {
            "id": "r::d::s#1", "title": "x", "summary": "", "content": "",
            "doc_path": "f.md", "byte_start": 0, "byte_end": 10,
        }
        score = score_section(sec, "alpha", stats=self._stats(), content_loader=loader)
        assert called.get("yes") is True
        assert score > 0

    def test_empty_query_returns_zero(self):
        sec = {"id": "r::d::s#1", "title": "alpha", "summary": "", "content": ""}
        assert score_section(sec, "", stats=self._stats()) == 0.0

    def test_missing_stats_degrades_gracefully(self):
        sec = {"id": "r::d::s#1", "title": "alpha beta", "summary": "", "content": ""}
        # No stats — should still produce some score (degraded IDF).
        score = score_section(sec, "alpha", stats=None)
        assert score >= 0.0


# ---------------------------------------------------------------------------
# End-to-end: BM25 wired through DocStore.search
# ---------------------------------------------------------------------------

class TestBM25EndToEnd:
    def test_bm25_default_outranks_short_unrelated_section(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections

        store = DocStore(base_path=str(tmp_path))
        content = (
            "# Doc\n\n"
            "## Authentication\n\n"
            "This section explains authentication tokens and secret rotation in detail.\n\n"
            "## Misc\n\n"
            "Unrelated content here.\n"
        )
        sections = parse_file(content, "g.md", "local/repo")
        store.save_index(
            owner="local",
            name="repo",
            sections=sections,
            raw_files={"g.md": content},
            doc_types={".md": 1},
        )
        out = search_sections(
            repo="local/repo",
            query="authentication tokens",
            storage_path=str(tmp_path),
            semantic=False,
        )
        # Default lexical_engine is bm25.
        meta = out["_meta"]
        assert meta["lexical_engine"] == "bm25"
        ids = [r["id"] for r in out["results"]]
        assert ids, "expected at least one result"
        assert "Authentication" in out["results"][0]["title"]

    def test_legacy_engine_still_available(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections

        store = DocStore(base_path=str(tmp_path))
        content = "# Top\n\n## Sub\n\nbody xyzzy frobnicate.\n"
        sections = parse_file(content, "g.md", "local/repo")
        store.save_index(
            owner="local",
            name="repo",
            sections=sections,
            raw_files={"g.md": content},
            doc_types={".md": 1},
        )
        # v2.0.0 dropped the legacy engine — request must raise loudly.
        out = search_sections(
            repo="local/repo",
            query="xyzzyfrobnicate",
            storage_path=str(tmp_path),
            semantic=False,
            lexical_engine="legacy",
        )
        assert "error" in out
        assert "legacy" in out["error"].lower() or "bm25" in out["error"].lower()

    def test_bm25_stats_persisted_in_index(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        content = "# Foo\n\nalpha beta gamma\n\n## Bar\n\ndelta epsilon\n"
        sections = parse_file(content, "g.md", "local/r")
        store.save_index(
            owner="local",
            name="r",
            sections=sections,
            raw_files={"g.md": content},
            doc_types={".md": 1},
        )
        index = store.load_index("local", "r")
        assert index is not None
        assert index.bm25_stats, "save_index must persist bm25_stats"
        assert "N" in index.bm25_stats
        assert "avgdl" in index.bm25_stats
        assert "df" in index.bm25_stats


# ---------------------------------------------------------------------------
# Tunables via env vars
# ---------------------------------------------------------------------------

class TestEnvTunables:
    def test_k1_env_override(self, monkeypatch):
        from jdocmunch_mcp.retrieval.bm25 import _k1

        monkeypatch.setenv("JDOCMUNCH_BM25_K1", "2.5")
        assert _k1() == 2.5

    def test_b_env_override(self, monkeypatch):
        from jdocmunch_mcp.retrieval.bm25 import _b

        monkeypatch.setenv("JDOCMUNCH_BM25_B", "0.4")
        assert _b() == 0.4

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        from jdocmunch_mcp.retrieval.bm25 import _b, _k1

        monkeypatch.setenv("JDOCMUNCH_BM25_K1", "not-a-number")
        monkeypatch.setenv("JDOCMUNCH_BM25_B", "also-not")
        assert _k1() == 1.2
        assert _b() == 0.75
