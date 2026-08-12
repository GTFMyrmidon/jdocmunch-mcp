"""Tests for v1.37.0: section_neighbors navigation tool."""

from __future__ import annotations

import textwrap

import pytest

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.section_neighbors import section_neighbors


def _index_nested(tmp_path):
    body = textwrap.dedent("""
        # Top

        Top body.

        ## Alpha

        Alpha body.

        ### Alpha One

        First sub of alpha.

        ### Alpha Two

        Second sub of alpha.

        ## Beta

        Beta body.

        ## Gamma

        Gamma body.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "page.md").write_text(body, encoding="utf-8")
    # second doc to verify cross-doc isolation
    (repo / "other.md").write_text("# Other Top\n\n## Other Alpha\n\nOther.\n", encoding="utf-8")
    index_local(
        path=str(repo), name="nbr",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    return DocStore(base_path=str(tmp_path)).load_index("local", "nbr")


class TestSectionNeighbors:
    def test_unknown_repo(self, tmp_path):
        out = section_neighbors(repo="missing", section_id="x",
                                storage_path=str(tmp_path))
        assert "error" in out

    def test_unknown_section(self, tmp_path):
        _index_nested(tmp_path)
        out = section_neighbors(repo="nbr", section_id="bogus",
                                storage_path=str(tmp_path))
        assert "error" in out

    def test_first_authored_section_neighbors(self, tmp_path):
        idx = _index_nested(tmp_path)
        # The parser emits a synthetic level-0 root section per document.
        # The first authored heading ("Top", level 1) has the synthetic
        # root as its prev in document order.
        top = next(s for s in idx.sections
                   if s["doc_path"] == "page.md" and s["title"] == "Top")
        out = section_neighbors(repo="nbr", section_id=top["id"],
                                storage_path=str(tmp_path))
        assert out["next"] is not None
        assert out["next"]["title"] == "Alpha"
        # prev is the synthetic doc-root (level 0).
        if out["prev"] is not None:
            assert out["prev"]["level"] == 0

    def test_middle_has_prev_and_next(self, tmp_path):
        idx = _index_nested(tmp_path)
        beta = next(s for s in idx.sections
                    if s["doc_path"] == "page.md" and s["title"] == "Beta")
        out = section_neighbors(repo="nbr", section_id=beta["id"],
                                storage_path=str(tmp_path))
        assert out["prev"]["title"] == "Alpha Two"
        assert out["next"]["title"] == "Gamma"

    def test_parent_resolved(self, tmp_path):
        idx = _index_nested(tmp_path)
        alpha_one = next(s for s in idx.sections
                         if s["doc_path"] == "page.md" and s["title"] == "Alpha One")
        out = section_neighbors(repo="nbr", section_id=alpha_one["id"],
                                storage_path=str(tmp_path))
        assert out["parent"]["title"] == "Alpha"

    def test_first_child_returned(self, tmp_path):
        idx = _index_nested(tmp_path)
        alpha = next(s for s in idx.sections
                     if s["doc_path"] == "page.md" and s["title"] == "Alpha")
        out = section_neighbors(repo="nbr", section_id=alpha["id"],
                                storage_path=str(tmp_path))
        assert out["first_child"]["title"] == "Alpha One"
        assert out["child_count"] == 2

    def test_no_cross_doc_neighbors(self, tmp_path):
        idx = _index_nested(tmp_path)
        # Last section in page.md (Gamma) — next must be None, not the
        # Other Top in a different doc.
        gamma = next(s for s in idx.sections
                     if s["doc_path"] == "page.md" and s["title"] == "Gamma")
        out = section_neighbors(repo="nbr", section_id=gamma["id"],
                                storage_path=str(tmp_path))
        assert out["next"] is None
        assert out["prev"]["title"] == "Beta"

    def test_handle_shape(self, tmp_path):
        idx = _index_nested(tmp_path)
        beta = next(s for s in idx.sections
                    if s["doc_path"] == "page.md" and s["title"] == "Beta")
        out = section_neighbors(repo="nbr", section_id=beta["id"],
                                storage_path=str(tmp_path))
        assert set(out["prev"].keys()) == {"id", "title", "level", "doc_path"}
        # No content field — that's the whole point of this tool.
        assert "content" not in out["prev"]


class TestSchema:
    def test_section_neighbors_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        sn = next(t for t in tools if t.name == "section_neighbors")
        assert "section_id" in sn.inputSchema["properties"]
        assert "repo" in sn.inputSchema["properties"]
        assert sn.inputSchema["required"] == ["repo", "section_id"]
