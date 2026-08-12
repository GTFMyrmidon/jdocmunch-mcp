"""Tests for v1.39.0: get_orphan_sections doc-rot finder."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.get_orphan_sections import get_orphan_sections
from jdocmunch_mcp.tools.index_local import index_local


def _index_with_links(tmp_path):
    """Three docs: hub links to linked.md; orphan.md gets no inbound."""
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "hub.md").write_text(
        "# Hub\n\nSee [linked](linked.md) for details.\n",
        encoding="utf-8",
    )
    (repo / "linked.md").write_text(
        "# Linked Page\n\n## Detail\n\nThe linked content.\n",
        encoding="utf-8",
    )
    (repo / "orphan.md").write_text(
        "# Orphan Page\n\n## Lonely Section\n\nNobody links to this.\n",
        encoding="utf-8",
    )
    index_local(
        path=str(repo), name="orph",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestGetOrphanSections:
    def test_unknown_repo(self, tmp_path):
        out = get_orphan_sections(repo="missing", storage_path=str(tmp_path))
        assert "error" in out

    def test_orphan_doc_surfaced(self, tmp_path):
        _index_with_links(tmp_path)
        out = get_orphan_sections(repo="orph", storage_path=str(tmp_path))
        result = out["result"]
        assert result["orphan_count"] >= 1
        orphan_paths = {s["doc_path"] for s in result["orphan_sections"]}
        assert "orphan.md" in orphan_paths

    def test_linked_doc_not_orphan(self, tmp_path):
        _index_with_links(tmp_path)
        out = get_orphan_sections(repo="orph", storage_path=str(tmp_path))
        orphan_paths = {s["doc_path"] for s in out["result"]["orphan_sections"]}
        assert "linked.md" not in orphan_paths

    def test_hub_is_orphan_when_nothing_links_back(self, tmp_path):
        # hub.md links out but receives no links — also an orphan.
        _index_with_links(tmp_path)
        out = get_orphan_sections(repo="orph", storage_path=str(tmp_path))
        orphan_paths = {s["doc_path"] for s in out["result"]["orphan_sections"]}
        assert "hub.md" in orphan_paths

    def test_synthetic_doc_root_excluded(self, tmp_path):
        _index_with_links(tmp_path)
        out = get_orphan_sections(repo="orph", storage_path=str(tmp_path))
        # The parser emits a synthetic level-0 root per doc; tool must
        # filter those (level == 0) so they don't pollute the report.
        for sec in out["result"]["orphan_sections"]:
            assert sec["level"] != 0, sec

    def test_handle_shape(self, tmp_path):
        _index_with_links(tmp_path)
        out = get_orphan_sections(repo="orph", storage_path=str(tmp_path))
        if out["result"]["orphan_sections"]:
            sec = out["result"]["orphan_sections"][0]
            assert set(sec.keys()) == {"id", "title", "doc_path", "level", "summary"}
            # No content field — handle-only.
            assert "content" not in sec

    def test_total_sections_reported(self, tmp_path):
        _index_with_links(tmp_path)
        out = get_orphan_sections(repo="orph", storage_path=str(tmp_path))
        assert out["result"]["total_sections"] > 0

    def test_meta_records_flag(self, tmp_path):
        _index_with_links(tmp_path)
        out = get_orphan_sections(repo="orph", include_same_doc=True,
                                  storage_path=str(tmp_path))
        assert out["_meta"]["include_same_doc"] is True


class TestSchema:
    def test_get_orphan_sections_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        tool = next(t for t in tools if t.name == "get_orphan_sections")
        assert tool.inputSchema["required"] == ["repo"]
        assert "include_same_doc" in tool.inputSchema["properties"]
