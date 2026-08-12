"""Tests for v1.51.0: exclude_tags filter on search_sections."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


def _index_tagged(tmp_path):
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
        path=str(repo), name="ex",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestExcludeTags:
    def test_default_returns_all(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(repo="ex", query="configure",
                              semantic=False, storage_path=str(tmp_path))
        paths = {r["doc_path"] for r in out["results"]}
        assert {"auth.md", "billing.md", "internal.md"} <= paths
        assert "exclude_tags_filter" not in out["_meta"]

    def test_single_exclude_drops_matching(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="ex", query="configure", exclude_tags=["internal"],
            semantic=False, storage_path=str(tmp_path),
        )
        paths = {r["doc_path"] for r in out["results"]}
        assert "internal.md" not in paths
        assert "auth.md" in paths
        assert "billing.md" in paths
        assert out["_meta"]["exclude_tags_filter"] == ["internal"]

    def test_two_excludes_any_match(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="ex", query="configure", exclude_tags=["auth", "internal"],
            semantic=False, storage_path=str(tmp_path),
        )
        paths = {r["doc_path"] for r in out["results"]}
        # Drop sections containing #auth OR #internal — only billing.md remains.
        assert paths == {"billing.md"}

    def test_excludes_stacks_with_includes(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="ex", query="configure",
            tags=["api"], exclude_tags=["billing"],
            semantic=False, storage_path=str(tmp_path),
        )
        paths = {r["doc_path"] for r in out["results"]}
        # Must have #api AND must NOT have #billing → only auth.md.
        assert paths == {"auth.md"}

    def test_unknown_exclude_is_noop(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="ex", query="configure", exclude_tags=["nonexistent"],
            semantic=False, storage_path=str(tmp_path),
        )
        # Nothing has #nonexistent, so all three remain.
        paths = {r["doc_path"] for r in out["results"]}
        assert {"auth.md", "billing.md", "internal.md"} <= paths

    def test_case_insensitive(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="ex", query="configure", exclude_tags=["INTERNAL"],
            semantic=False, storage_path=str(tmp_path),
        )
        paths = {r["doc_path"] for r in out["results"]}
        assert "internal.md" not in paths

    def test_empty_list_treated_as_off(self, tmp_path):
        _index_tagged(tmp_path)
        out = search_sections(
            repo="ex", query="configure", exclude_tags=[],
            semantic=False, storage_path=str(tmp_path),
        )
        assert "exclude_tags_filter" not in out["_meta"]


class TestSchema:
    def test_exclude_tags_in_schema(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ss = next(t for t in tools if t.name == "search_sections")
        props = ss.inputSchema["properties"]
        assert "exclude_tags" in props
        assert props["exclude_tags"]["type"] == "array"
        assert props["exclude_tags"]["items"]["type"] == "string"
