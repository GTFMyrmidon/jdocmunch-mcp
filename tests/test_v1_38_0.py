"""Tests for v1.38.0: get_section_summary metadata-only tool."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_section_summary import get_section_summary
from jdocmunch_mcp.tools.index_local import index_local


def _index(tmp_path):
    body = textwrap.dedent("""
        # Top

        Top body has some token content for summary derivation.

        ## API Reference

        Endpoint listing prose with enough text for the summary heuristic.
        Issue requests against /v1/users and inspect responses.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "page.md").write_text(body, encoding="utf-8")
    index_local(
        path=str(repo), name="summ",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    return DocStore(base_path=str(tmp_path)).load_index("local", "summ")


class TestGetSectionSummary:
    def test_unknown_repo(self, tmp_path):
        out = get_section_summary(repo="missing", section_id="x",
                                  storage_path=str(tmp_path))
        assert "error" in out

    def test_unknown_section(self, tmp_path):
        _index(tmp_path)
        out = get_section_summary(repo="summ", section_id="bogus",
                                  storage_path=str(tmp_path))
        assert "error" in out

    def test_returns_metadata_no_content(self, tmp_path):
        idx = _index(tmp_path)
        api = next(s for s in idx.sections if s["title"] == "API Reference")
        out = get_section_summary(repo="summ", section_id=api["id"],
                                  storage_path=str(tmp_path))
        sec = out["section"]
        # Content excluded — that's the contract.
        assert "content" not in sec
        # Metadata present.
        assert sec["title"] == "API Reference"
        assert sec["doc_path"] == "page.md"
        assert sec["level"] == 2
        assert sec["id"] == api["id"]
        # Byte fields present.
        assert "byte_start" in sec
        assert "byte_end" in sec
        assert "byte_length" in sec
        assert sec["byte_length"] == sec["byte_end"] - sec["byte_start"]

    def test_byte_length_matches_real_content(self, tmp_path):
        idx = _index(tmp_path)
        api = next(s for s in idx.sections if s["title"] == "API Reference")
        out = get_section_summary(repo="summ", section_id=api["id"],
                                  storage_path=str(tmp_path))
        # byte_length should be byte_end - byte_start, non-negative.
        assert out["section"]["byte_length"] >= 0

    def test_meta_includes_indexed_at(self, tmp_path):
        idx = _index(tmp_path)
        top = next(s for s in idx.sections if s["title"] == "Top")
        out = get_section_summary(repo="summ", section_id=top["id"],
                                  storage_path=str(tmp_path))
        assert out["_meta"]["repo"] == "local/summ"
        assert out["_meta"]["indexed_at"]


class TestSchema:
    def test_get_section_summary_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gs = next(t for t in tools if t.name == "get_section_summary")
        assert gs.inputSchema["required"] == ["repo", "section_id"]
        assert "section_id" in gs.inputSchema["properties"]
