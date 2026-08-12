"""Tests for v1.56.0: get_index_overview repo snapshot."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.get_index_overview import get_index_overview
from jdocmunch_mcp.tools.index_local import index_local


def _index_mixed(tmp_path):
    """Index a mix of formats and tag distributions for the snapshot."""
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "auth.md").write_text(textwrap.dedent("""
        # Authentication

        Configure tokens. #api #auth

        Bearer tokens.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "billing.md").write_text(textwrap.dedent("""
        # Billing

        Configure invoices. #api #billing

        Bill cycle.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "guide.md").write_text(textwrap.dedent("""
        # How to install

        Install the package via pip and verify with foo --version.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "notes.txt").write_text("Notes content.\n", encoding="utf-8")
    index_local(
        path=str(repo), name="ov",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestGetIndexOverview:
    def test_unknown_repo(self, tmp_path):
        out = get_index_overview(repo="missing", storage_path=str(tmp_path))
        assert "error" in out

    def test_negative_top_n_rejected(self, tmp_path):
        out = get_index_overview(repo="ov", top_n=-1,
                                 storage_path=str(tmp_path))
        assert "error" in out

    def test_basic_counts(self, tmp_path):
        _index_mixed(tmp_path)
        out = get_index_overview(repo="ov", storage_path=str(tmp_path))
        assert out["doc_count"] >= 4
        assert out["section_count"] > 0
        assert out["total_byte_size"] > 0
        assert out["indexed_at"]

    def test_format_breakdown(self, tmp_path):
        _index_mixed(tmp_path)
        out = get_index_overview(repo="ov", storage_path=str(tmp_path))
        formats = {r["format"] for r in out["format_breakdown"]}
        assert ".md" in formats
        assert ".txt" in formats
        # .md count should be 3 (auth, billing, guide).
        md = next(r for r in out["format_breakdown"] if r["format"] == ".md")
        assert md["doc_count"] == 3

    def test_top_tags_default_5(self, tmp_path):
        _index_mixed(tmp_path)
        out = get_index_overview(repo="ov", storage_path=str(tmp_path))
        tag_names = {r["tag"] for r in out["top_tags"]}
        # api shows up in 2 docs, auth/billing each in 1.
        assert "api" in tag_names
        # Top tag must be the highest-count one.
        assert out["top_tags"][0]["tag"] == "api"
        assert out["top_tags"][0]["section_count"] >= 2

    def test_top_roles_present(self, tmp_path):
        _index_mixed(tmp_path)
        out = get_index_overview(repo="ov", storage_path=str(tmp_path))
        # Some sections should classify (how_to from "How to install" etc).
        assert isinstance(out["top_roles"], list)

    def test_top_n_zero_omits_both(self, tmp_path):
        _index_mixed(tmp_path)
        out = get_index_overview(repo="ov", top_n=0, storage_path=str(tmp_path))
        assert out["top_tags"] == []
        assert out["top_roles"] == []
        assert out["_meta"]["top_n"] == 0

    def test_top_n_caps_lists(self, tmp_path):
        _index_mixed(tmp_path)
        out = get_index_overview(repo="ov", top_n=2, storage_path=str(tmp_path))
        assert len(out["top_tags"]) <= 2
        assert len(out["top_roles"]) <= 2

    def test_format_sorted(self, tmp_path):
        _index_mixed(tmp_path)
        out = get_index_overview(repo="ov", storage_path=str(tmp_path))
        formats = [r["format"] for r in out["format_breakdown"]]
        assert formats == sorted(formats)


class TestSchema:
    def test_get_index_overview_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gio = next(t for t in tools if t.name == "get_index_overview")
        assert gio.inputSchema["required"] == ["repo"]
        assert gio.inputSchema["properties"]["top_n"]["minimum"] == 0
