"""Tests for v1.45.0: tags filter on search_sections."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


def _index_tagged(tmp_path):
    """Three sections with different #hashtag tags."""
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "auth.md").write_text(textwrap.dedent("""
        # Authentication

        Configure tokens for the auth flow. #api #auth

        Use bearer tokens or API keys.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "billing.md").write_text(textwrap.dedent("""
        # Billing

        Configure invoices and payment methods. #api #billing

        Bill cycle ends monthly.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "internal.md").write_text(textwrap.dedent("""
        # Internals

        Configure the build pipeline. #internal

        Build configuration for the team.
    """).lstrip("\n"), encoding="utf-8")
    index_local(
        path=str(repo), name="tg",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestTagsFilter:
    def test_default_returns_all(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(repo="tg", query="configure",
                              semantic=False, storage_path=str(tmp_path))
        # All three sections match the query.
        paths = {r["doc_path"] for r in out["results"]}
        assert {"auth.md", "billing.md", "internal.md"} <= paths
        assert "tags_filter" not in out["_meta"]

    def test_single_tag_restricts(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="tg", query="configure", tags=["api"],
            semantic=False, storage_path=str(tmp_path),
        )
        paths = {r["doc_path"] for r in out["results"]}
        # auth.md and billing.md both have #api; internal.md does not.
        assert "auth.md" in paths
        assert "billing.md" in paths
        assert "internal.md" not in paths
        assert out["_meta"]["tags_filter"] == ["api"]

    def test_two_tags_AND_semantics(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="tg", query="configure", tags=["api", "auth"],
            semantic=False, storage_path=str(tmp_path),
        )
        paths = {r["doc_path"] for r in out["results"]}
        # Only auth.md has BOTH #api and #auth.
        assert paths == {"auth.md"}

    def test_unknown_tag_returns_empty(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="tg", query="configure", tags=["nonexistent"],
            semantic=False, storage_path=str(tmp_path),
        )
        assert out["result_count"] == 0

    def test_case_insensitive(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="tg", query="configure", tags=["API"],
            semantic=False, storage_path=str(tmp_path),
        )
        paths = {r["doc_path"] for r in out["results"]}
        assert "auth.md" in paths
        assert "billing.md" in paths

    def test_empty_tags_list_treated_as_off(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="tg", query="configure", tags=[],
            semantic=False, storage_path=str(tmp_path),
        )
        # Empty list should be treated as no filter.
        paths = {r["doc_path"] for r in out["results"]}
        assert {"auth.md", "billing.md", "internal.md"} <= paths


class TestSchema:
    def test_tags_in_schema(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ss = next(t for t in tools if t.name == "search_sections")
        props = ss.inputSchema["properties"]
        assert "tags" in props
        assert props["tags"]["type"] == "array"
        assert props["tags"]["items"]["type"] == "string"
