"""Tests for v1.46.0: get_all_tags discovery tool."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.get_all_tags import get_all_tags
from jdocmunch_mcp.tools.index_local import index_local


def _index_tagged(tmp_path):
    """Sections with overlapping #hashtag distributions for count tests."""
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "a.md").write_text(textwrap.dedent("""
        # Auth API

        Token configuration for the auth flow. #api #auth

        Bearer tokens and API keys.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "b.md").write_text(textwrap.dedent("""
        # Billing API

        Invoices and payment methods. #api #billing

        Bill cycle ends monthly.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "c.md").write_text(textwrap.dedent("""
        # Public API

        Public endpoints. #api #public

        Rate-limited.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "d.md").write_text(textwrap.dedent("""
        # Internals

        Build pipeline. #internal

        Build configuration for the team.
    """).lstrip("\n"), encoding="utf-8")
    index_local(
        path=str(repo), name="atg",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestGetAllTags:
    def test_unknown_repo(self, tmp_path):
        out = get_all_tags(repo="missing", storage_path=str(tmp_path))
        assert "error" in out

    def test_invalid_min_count_rejected(self, tmp_path):
        out = get_all_tags(repo="atg", min_section_count=0,
                           storage_path=str(tmp_path))
        assert "error" in out

    def test_returns_unique_tags(self, tmp_path):
        _index_tagged(tmp_path)
        out = get_all_tags(repo="atg", storage_path=str(tmp_path))
        names = {row["tag"] for row in out["tags"]}
        # All five distinct tags must appear.
        assert {"api", "auth", "billing", "public", "internal"} <= names

    def test_counts_are_correct(self, tmp_path):
        _index_tagged(tmp_path)
        out = get_all_tags(repo="atg", storage_path=str(tmp_path))
        by_tag = {row["tag"]: row["section_count"] for row in out["tags"]}
        # #api appears in three docs (a, b, c).
        assert by_tag["api"] == 3
        # #auth, #billing, #public, #internal each appear in one.
        assert by_tag["auth"] == 1
        assert by_tag["billing"] == 1
        assert by_tag["public"] == 1
        assert by_tag["internal"] == 1

    def test_sort_order_count_desc_then_tag_asc(self, tmp_path):
        _index_tagged(tmp_path)
        out = get_all_tags(repo="atg", storage_path=str(tmp_path))
        # First entry must be the highest-count tag.
        assert out["tags"][0]["tag"] == "api"
        # Among the four 1-count tags, lex-asc: auth, billing, internal, public.
        ones = [r["tag"] for r in out["tags"] if r["section_count"] == 1]
        assert ones == sorted(ones)

    def test_min_section_count_drops_singletons(self, tmp_path):
        _index_tagged(tmp_path)
        out = get_all_tags(repo="atg", min_section_count=2,
                           storage_path=str(tmp_path))
        names = {row["tag"] for row in out["tags"]}
        # Only #api (3 sections) survives a >=2 threshold.
        assert names == {"api"}
        assert out["total_unique"] == 1

    def test_total_sections_reported(self, tmp_path):
        _index_tagged(tmp_path)
        out = get_all_tags(repo="atg", storage_path=str(tmp_path))
        assert out["total_sections"] > 0
        assert out["total_sections_tagged"] >= 4  # 4 tagged docs.

    def test_repo_with_no_tags_returns_empty(self, tmp_path):
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "plain.md").write_text("# Plain\n\nNo hashtags here.\n",
                                       encoding="utf-8")
        index_local(
            path=str(repo), name="ntg",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        out = get_all_tags(repo="ntg", storage_path=str(tmp_path))
        assert out["tags"] == []
        assert out["total_unique"] == 0


class TestSchema:
    def test_get_all_tags_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gat = next(t for t in tools if t.name == "get_all_tags")
        assert gat.inputSchema["required"] == ["repo"]
        assert gat.inputSchema["properties"]["min_section_count"]["minimum"] == 1
