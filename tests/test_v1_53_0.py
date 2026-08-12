"""Tests for v1.53.0: min_byte_length / max_byte_length filters."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


def _index_sized(tmp_path):
    """Three sections of distinct sizes."""
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "stub.md").write_text(textwrap.dedent("""
        # Stub

        x.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "medium.md").write_text(textwrap.dedent("""
        # Medium

        Configuration of moderate length. Loader looks at typical paths.
        It contains enough text for a useful match without being huge.
    """).lstrip("\n"), encoding="utf-8")
    long_body = "Configuration loader\n" + ("Lorem ipsum dolor sit amet. " * 80)
    (repo / "long.md").write_text(f"# Long\n\n{long_body}\n", encoding="utf-8")
    index_local(
        path=str(repo), name="bl",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestByteLengthFilters:
    def test_default_returns_all(self, tmp_path):
        _index_sized(tmp_path)
        out = search_sections(repo="bl", query="Configuration loader",
                              semantic=False, storage_path=str(tmp_path))
        assert "min_byte_length" not in out["_meta"]
        assert "max_byte_length" not in out["_meta"]

    def test_min_drops_stubs(self, tmp_path):
        _index_sized(tmp_path)
        out = search_sections(
            repo="bl", query="Configuration loader",
            min_byte_length=200,
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            length = int(r["byte_end"]) - int(r["byte_start"])
            assert length >= 200
        assert out["_meta"]["min_byte_length"] == 200

    def test_max_drops_long(self, tmp_path):
        _index_sized(tmp_path)
        out = search_sections(
            repo="bl", query="Configuration loader",
            max_byte_length=400,
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            length = int(r["byte_end"]) - int(r["byte_start"])
            assert length <= 400
        assert out["_meta"]["max_byte_length"] == 400

    def test_both_define_inclusive_range(self, tmp_path):
        _index_sized(tmp_path)
        out = search_sections(
            repo="bl", query="Configuration loader",
            min_byte_length=50, max_byte_length=400,
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            length = int(r["byte_end"]) - int(r["byte_start"])
            assert 50 <= length <= 400

    def test_unsatisfiable_range_returns_empty(self, tmp_path):
        _index_sized(tmp_path)
        out = search_sections(
            repo="bl", query="Configuration loader",
            min_byte_length=10_000_000,
            semantic=False, storage_path=str(tmp_path),
        )
        assert out["result_count"] == 0


class TestSchema:
    def test_byte_length_filters_in_schema(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ss = next(t for t in tools if t.name == "search_sections")
        props = ss.inputSchema["properties"]
        assert "min_byte_length" in props
        assert "max_byte_length" in props
        assert props["min_byte_length"]["minimum"] == 0
        assert props["max_byte_length"]["minimum"] == 0
