"""Tests for v1.44.0: min_level / max_level filters on search_sections."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


def _index_levels(tmp_path):
    body = textwrap.dedent("""
        # Top Level Configuration

        Top body about configuration.

        ## Sub Configuration

        Sub body about configuration.

        ### Deep Configuration

        Deep body about configuration.

        #### Deeper Configuration

        Deeper body about configuration.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "page.md").write_text(body, encoding="utf-8")
    index_local(
        path=str(repo), name="lv",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestLevelFilters:
    def test_default_returns_all_levels(self, tmp_path):
        _index_levels(tmp_path)
        out = search_sections(repo="lv", query="configuration",
                              semantic=False, storage_path=str(tmp_path))
        levels = {r["level"] for r in out["results"]}
        # All four levels (1,2,3,4) match the query.
        assert levels >= {1, 2, 3, 4}

    def test_min_level_restricts_to_deeper(self, tmp_path):
        _index_levels(tmp_path)
        out = search_sections(
            repo="lv", query="configuration", min_level=3,
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            assert r["level"] >= 3
        assert out["_meta"]["min_level"] == 3

    def test_max_level_restricts_to_shallower(self, tmp_path):
        _index_levels(tmp_path)
        out = search_sections(
            repo="lv", query="configuration", max_level=2,
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            assert r["level"] <= 2
        assert out["_meta"]["max_level"] == 2

    def test_both_define_inclusive_range(self, tmp_path):
        _index_levels(tmp_path)
        out = search_sections(
            repo="lv", query="configuration",
            min_level=2, max_level=3,
            semantic=False, storage_path=str(tmp_path),
        )
        for r in out["results"]:
            assert 2 <= r["level"] <= 3
        assert out["_meta"]["min_level"] == 2
        assert out["_meta"]["max_level"] == 3

    def test_unsatisfiable_range_returns_empty(self, tmp_path):
        _index_levels(tmp_path)
        out = search_sections(
            repo="lv", query="configuration",
            min_level=10, max_level=20,
            semantic=False, storage_path=str(tmp_path),
        )
        assert out["result_count"] == 0

    def test_meta_omits_when_filter_off(self, tmp_path):
        _index_levels(tmp_path)
        out = search_sections(repo="lv", query="configuration",
                              semantic=False, storage_path=str(tmp_path))
        assert "min_level" not in out["_meta"]
        assert "max_level" not in out["_meta"]


class TestSchema:
    def test_min_max_level_in_schema(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ss = next(t for t in tools if t.name == "search_sections")
        props = ss.inputSchema["properties"]
        assert "min_level" in props
        assert "max_level" in props
        assert props["min_level"]["minimum"] == 0
