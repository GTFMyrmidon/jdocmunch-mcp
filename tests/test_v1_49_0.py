"""Tests for v1.49.0: get_section_excerpts batch preview tool."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_section_excerpts import get_section_excerpts
from jdocmunch_mcp.tools.index_local import index_local


def _index_long(tmp_path):
    body = textwrap.dedent("""
        # Page A

        First paragraph A with enough text to fill several hundred bytes.
        Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do
        eiusmod tempor incididunt ut labore et dolore magna aliqua.

        Second paragraph A with substantial text. Excepteur sint occaecat
        cupidatat non proident.
    """).lstrip("\n")
    body2 = textwrap.dedent("""
        # Page B

        First paragraph B with enough text to fill several hundred bytes.
        Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do
        eiusmod tempor incididunt ut labore et dolore magna aliqua.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "a.md").write_text(body, encoding="utf-8")
    (repo / "b.md").write_text(body2, encoding="utf-8")
    index_local(
        path=str(repo), name="bex",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    return DocStore(base_path=str(tmp_path)).load_index("local", "bex")


class TestGetSectionExcerpts:
    def test_unknown_repo(self, tmp_path):
        out = get_section_excerpts(repo="missing", section_ids=["x"],
                                   storage_path=str(tmp_path))
        assert "error" in out

    def test_invalid_max_bytes(self, tmp_path):
        out = get_section_excerpts(repo="bex", section_ids=["x"], max_bytes=0,
                                   storage_path=str(tmp_path))
        assert "error" in out

    def test_non_list_section_ids(self, tmp_path):
        _index_long(tmp_path)
        out = get_section_excerpts(repo="bex", section_ids="not-a-list",
                                   storage_path=str(tmp_path))
        assert "error" in out

    def test_empty_list_returns_empty(self, tmp_path):
        _index_long(tmp_path)
        out = get_section_excerpts(repo="bex", section_ids=[],
                                   storage_path=str(tmp_path))
        assert out["section_count"] == 0
        assert out["found_count"] == 0

    def test_all_found_with_truncation(self, tmp_path):
        idx = _index_long(tmp_path)
        ids = [s["id"] for s in idx.sections if s.get("title") in ("Page A", "Page B")]
        out = get_section_excerpts(repo="bex", section_ids=ids, max_bytes=150,
                                   storage_path=str(tmp_path))
        assert out["found_count"] == len(ids)
        assert out["missing_count"] == 0
        for entry in out["sections"]:
            assert "section" in entry
            assert "excerpt" in entry
            assert "truncated" in entry
            assert "full_byte_length" in entry
            assert "excerpt_byte_length" in entry
            assert entry["excerpt_byte_length"] <= entry["full_byte_length"]

    def test_partial_missing(self, tmp_path):
        idx = _index_long(tmp_path)
        page_a = next(s for s in idx.sections if s.get("title") == "Page A")
        out = get_section_excerpts(
            repo="bex",
            section_ids=[page_a["id"], "bogus", page_a["id"]],
            storage_path=str(tmp_path),
        )
        assert out["found_count"] == 2
        assert out["missing_count"] == 1
        assert out["sections"][0]["requested_id"] == page_a["id"]
        assert "section" in out["sections"][0]
        assert out["sections"][1]["requested_id"] == "bogus"
        assert "error" in out["sections"][1]

    def test_meta_aggregates_savings(self, tmp_path):
        idx = _index_long(tmp_path)
        ids = [s["id"] for s in idx.sections if s.get("title") in ("Page A", "Page B")]
        out = get_section_excerpts(repo="bex", section_ids=ids, max_bytes=80,
                                   storage_path=str(tmp_path))
        assert out["_meta"]["max_bytes"] == 80
        assert out["_meta"]["tokens_saved"] >= 0


class TestSchema:
    def test_get_section_excerpts_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gex = next(t for t in tools if t.name == "get_section_excerpts")
        assert gex.inputSchema["required"] == ["repo", "section_ids"]
        assert gex.inputSchema["properties"]["max_bytes"]["default"] == 500
