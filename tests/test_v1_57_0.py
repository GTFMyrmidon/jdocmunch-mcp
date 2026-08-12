"""Tests for v1.57.0: search_titles fast title-only navigation tool."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_titles import search_titles


def _index_titled(tmp_path):
    body = textwrap.dedent("""
        # Authentication

        Some prose about authentication.

        ## Auth Tokens

        Token configuration.

        ## Bearer Tokens

        Bearer-specific notes.

        # Billing

        Billing information.

        ## Billing Configuration

        Configure invoices.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "page.md").write_text(body, encoding="utf-8")
    index_local(
        path=str(repo), name="ti",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestSearchTitles:
    def test_unknown_repo(self, tmp_path):
        out = search_titles(repo="missing", query="auth",
                            storage_path=str(tmp_path))
        assert "error" in out

    def test_empty_query_rejected(self, tmp_path):
        out = search_titles(repo="ti", query="", storage_path=str(tmp_path))
        assert "error" in out

    def test_exact_match_ranks_first(self, tmp_path):
        _index_titled(tmp_path)
        out = search_titles(repo="ti", query="Authentication",
                            storage_path=str(tmp_path))
        assert out["results"]
        # Exact equality wins.
        assert out["results"][0]["title"] == "Authentication"

    def test_substring_match(self, tmp_path):
        _index_titled(tmp_path)
        out = search_titles(repo="ti", query="auth",
                            storage_path=str(tmp_path))
        titles = [r["title"] for r in out["results"]]
        assert "Authentication" in titles
        assert "Auth Tokens" in titles

    def test_token_overlap_match(self, tmp_path):
        _index_titled(tmp_path)
        # "Bearer" only appears in one title.
        out = search_titles(repo="ti", query="Bearer",
                            storage_path=str(tmp_path))
        titles = [r["title"] for r in out["results"]]
        assert "Bearer Tokens" in titles

    def test_no_match_returns_empty(self, tmp_path):
        _index_titled(tmp_path)
        out = search_titles(repo="ti", query="xyzzynothing",
                            storage_path=str(tmp_path))
        assert out["result_count"] == 0

    def test_max_results_caps(self, tmp_path):
        _index_titled(tmp_path)
        out = search_titles(repo="ti", query="tokens billing auth",
                            max_results=2, storage_path=str(tmp_path))
        assert out["result_count"] <= 2

    def test_handle_only_no_content(self, tmp_path):
        _index_titled(tmp_path)
        out = search_titles(repo="ti", query="auth",
                            storage_path=str(tmp_path))
        for r in out["results"]:
            assert "content" not in r
            assert "summary" not in r
            assert set(r.keys()) >= {"id", "title", "level", "doc_path", "_score"}

    def test_score_is_positive(self, tmp_path):
        _index_titled(tmp_path)
        out = search_titles(repo="ti", query="auth",
                            storage_path=str(tmp_path))
        for r in out["results"]:
            assert r["_score"] > 0

    def test_deterministic_ordering(self, tmp_path):
        _index_titled(tmp_path)
        # Run twice; results must be identical.
        out_a = search_titles(repo="ti", query="tokens",
                              storage_path=str(tmp_path))
        out_b = search_titles(repo="ti", query="tokens",
                              storage_path=str(tmp_path))
        ids_a = [r["id"] for r in out_a["results"]]
        ids_b = [r["id"] for r in out_b["results"]]
        assert ids_a == ids_b


class TestSchema:
    def test_search_titles_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        st = next(t for t in tools if t.name == "search_titles")
        assert st.inputSchema["required"] == ["repo", "query"]
        assert st.inputSchema["properties"]["max_results"]["minimum"] == 1
