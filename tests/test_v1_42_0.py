"""Tests for v1.42.0: min_answerability + min_quotability filters on search_sections."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


def _index_mixed(tmp_path):
    """Two sections sharing a query token, one rich (answerable), one thin."""
    repo = tmp_path / "docs"
    repo.mkdir()
    # Rich section: imperative verbs, code fence, definition syntax —
    # high answerability + quotability.
    (repo / "rich.md").write_text(textwrap.dedent("""
        # Configure the Loader

        The loader **searches** the local config file then the user
        config file. Default values apply when a field is omitted.

        ```toml
        timeout = 30
        retry = 3
        ```

        Install the package, set your API key, and run the loader.
    """).lstrip("\n"), encoding="utf-8")
    # Thin section: bare keyword match, no imperatives, no code, no
    # definitions — low scores.
    (repo / "thin.md").write_text(textwrap.dedent("""
        # Loader Notes

        Loader.
    """).lstrip("\n"), encoding="utf-8")
    index_local(
        path=str(repo), name="qf",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestQualityFilters:
    def test_default_returns_both(self, tmp_path):
        _index_mixed(tmp_path)
        out = search_sections(repo="qf", query="loader",
                              semantic=False, storage_path=str(tmp_path))
        assert out["result_count"] >= 2

    def test_min_answerability_drops_thin(self, tmp_path):
        _index_mixed(tmp_path)
        # rich.md scores ~0.39 answerability; thin.md scores 0.0.
        # 0.2 threshold keeps rich, drops thin.
        out = search_sections(
            repo="qf", query="loader",
            min_answerability=0.2,
            semantic=False, storage_path=str(tmp_path),
        )
        paths = {r["doc_path"] for r in out["results"]}
        assert "rich.md" in paths
        assert "thin.md" not in paths
        assert out["_meta"]["min_answerability"] == 0.2
        assert out["_meta"]["quality_filtered"] >= 1

    def test_min_quotability_threshold_recorded(self, tmp_path):
        _index_mixed(tmp_path)
        out = search_sections(
            repo="qf", query="loader",
            min_quotability=0.99,
            semantic=False, storage_path=str(tmp_path),
        )
        # 0.99 is very strict — both sections likely dropped.
        assert out["_meta"]["min_quotability"] == 0.99
        # All retained results must meet the threshold.
        for r in out["results"]:
            assert r.get("_quotability", 0) >= 0.99

    def test_threshold_one_drops_everything(self, tmp_path):
        _index_mixed(tmp_path)
        out = search_sections(
            repo="qf", query="loader",
            min_answerability=1.01,
            semantic=False, storage_path=str(tmp_path),
        )
        assert out["result_count"] == 0
        assert out["_meta"]["quality_filtered"] >= 2

    def test_threshold_zero_keeps_everything(self, tmp_path):
        _index_mixed(tmp_path)
        out = search_sections(
            repo="qf", query="loader",
            min_answerability=0.0,
            semantic=False, storage_path=str(tmp_path),
        )
        # min=0 keeps everything; quality_filtered may still be 0.
        assert out["result_count"] >= 2

    def test_meta_omits_when_filter_off(self, tmp_path):
        _index_mixed(tmp_path)
        out = search_sections(repo="qf", query="loader",
                              semantic=False, storage_path=str(tmp_path))
        assert "min_answerability" not in out["_meta"]
        assert "min_quotability" not in out["_meta"]
        assert "quality_filtered" not in out["_meta"]


class TestSchema:
    def test_min_answerability_in_schema(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ss = next(t for t in tools if t.name == "search_sections")
        props = ss.inputSchema["properties"]
        assert "min_answerability" in props
        assert "min_quotability" in props
        assert props["min_answerability"]["type"] == "number"
