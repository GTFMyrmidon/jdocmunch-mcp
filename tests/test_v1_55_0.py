"""Tests for v1.55.0: list_docs flat per-doc inventory."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.list_docs import list_docs


def _index_multi(tmp_path):
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "guide.md").write_text(textwrap.dedent("""
        # Guide

        Body.

        ## Section A

        a.

        ## Section B

        b.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "reference.md").write_text("# Reference\n\nrest.\n", encoding="utf-8")
    # Plain text (alt format)
    (repo / "notes.txt").write_text("Notes content here.\n", encoding="utf-8")
    index_local(
        path=str(repo), name="ld",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestListDocs:
    def test_unknown_repo(self, tmp_path):
        out = list_docs(repo="missing", storage_path=str(tmp_path))
        assert "error" in out

    def test_returns_all_docs(self, tmp_path):
        _index_multi(tmp_path)
        out = list_docs(repo="ld", storage_path=str(tmp_path))
        paths = {d["doc_path"] for d in out["docs"]}
        assert {"guide.md", "reference.md", "notes.txt"} <= paths
        assert out["doc_count"] >= 3

    def test_section_count_per_doc(self, tmp_path):
        _index_multi(tmp_path)
        out = list_docs(repo="ld", storage_path=str(tmp_path))
        by_path = {d["doc_path"]: d["section_count"] for d in out["docs"]}
        # guide.md has multiple sections (synthetic root + Guide + A + B).
        assert by_path["guide.md"] >= 3
        # reference.md has at least 1.
        assert by_path["reference.md"] >= 1

    def test_format_extension_lowercase(self, tmp_path):
        _index_multi(tmp_path)
        out = list_docs(repo="ld", storage_path=str(tmp_path))
        formats = {d["format"] for d in out["docs"]}
        assert ".md" in formats
        assert ".txt" in formats

    def test_byte_size_reflects_disk(self, tmp_path):
        _index_multi(tmp_path)
        out = list_docs(repo="ld", storage_path=str(tmp_path))
        # All cached files exist post-index → all byte_size > 0.
        for d in out["docs"]:
            assert d["byte_size"] > 0
        # total_byte_size matches sum.
        assert out["total_byte_size"] == sum(d["byte_size"] for d in out["docs"])

    def test_sorted_by_doc_path(self, tmp_path):
        _index_multi(tmp_path)
        out = list_docs(repo="ld", storage_path=str(tmp_path))
        paths = [d["doc_path"] for d in out["docs"]]
        assert paths == sorted(paths)

    def test_total_section_count_matches_index(self, tmp_path):
        _index_multi(tmp_path)
        out = list_docs(repo="ld", storage_path=str(tmp_path))
        # total_section_count must equal sum of per-doc counts (no
        # sections without doc_path).
        assert out["total_section_count"] == sum(
            d["section_count"] for d in out["docs"]
        )

    def test_meta_includes_indexed_at(self, tmp_path):
        _index_multi(tmp_path)
        out = list_docs(repo="ld", storage_path=str(tmp_path))
        assert out["_meta"]["indexed_at"]


class TestSchema:
    def test_list_docs_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ld = next(t for t in tools if t.name == "list_docs")
        assert ld.inputSchema["required"] == ["repo"]
