"""Tests for v1.40.0: get_section_path breadcrumb + get_doc_health orphan integration."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_doc_health import get_doc_health
from jdocmunch_mcp.tools.get_section_path import get_section_path
from jdocmunch_mcp.tools.index_local import index_local


def _index_nested(tmp_path):
    body = textwrap.dedent("""
        # Top

        Top body.

        ## Middle

        Middle body.

        ### Deep

        Deep body.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "page.md").write_text(body, encoding="utf-8")
    index_local(
        path=str(repo), name="path",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    return DocStore(base_path=str(tmp_path)).load_index("local", "path")


class TestGetSectionPath:
    def test_unknown_repo(self, tmp_path):
        out = get_section_path(repo="missing", section_id="x",
                               storage_path=str(tmp_path))
        assert "error" in out

    def test_unknown_section(self, tmp_path):
        _index_nested(tmp_path)
        out = get_section_path(repo="path", section_id="bogus",
                               storage_path=str(tmp_path))
        assert "error" in out

    def test_root_section_has_no_parents(self, tmp_path):
        idx = _index_nested(tmp_path)
        top = next(s for s in idx.sections
                   if s["doc_path"] == "page.md" and s["title"] == "Top")
        out = get_section_path(repo="path", section_id=top["id"],
                               storage_path=str(tmp_path))
        # Top is root of authored sections; depth depends on whether
        # the parser emitted a synthetic doc-root above it.
        titles = [step["title"] for step in out["path"]]
        assert "Top" in titles
        assert titles[-1] == "Top"  # target is always last.

    def test_deep_section_has_full_chain(self, tmp_path):
        idx = _index_nested(tmp_path)
        deep = next(s for s in idx.sections
                    if s["doc_path"] == "page.md" and s["title"] == "Deep")
        out = get_section_path(repo="path", section_id=deep["id"],
                               storage_path=str(tmp_path))
        titles = [step["title"] for step in out["path"]]
        # Path is root-first; target is last.
        assert titles[-1] == "Deep"
        # Middle and Top must appear before Deep.
        assert "Middle" in titles
        assert "Top" in titles
        assert titles.index("Top") < titles.index("Middle") < titles.index("Deep")

    def test_depth_matches_path_length(self, tmp_path):
        idx = _index_nested(tmp_path)
        deep = next(s for s in idx.sections
                    if s["doc_path"] == "page.md" and s["title"] == "Deep")
        out = get_section_path(repo="path", section_id=deep["id"],
                               storage_path=str(tmp_path))
        assert out["depth"] == len(out["path"]) - 1

    def test_handle_shape(self, tmp_path):
        idx = _index_nested(tmp_path)
        middle = next(s for s in idx.sections
                      if s["doc_path"] == "page.md" and s["title"] == "Middle")
        out = get_section_path(repo="path", section_id=middle["id"],
                               storage_path=str(tmp_path))
        for step in out["path"]:
            assert set(step.keys()) == {"id", "title", "level", "doc_path"}
            assert "content" not in step


class TestDocHealthOrphanIntegration:
    def test_orphan_count_field_present(self, tmp_path):
        # Two-doc repo with no inter-doc links → both docs are orphans.
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "a.md").write_text("# A\n\nAlpha.\n", encoding="utf-8")
        (repo / "b.md").write_text("# B\n\nBeta.\n", encoding="utf-8")
        index_local(
            path=str(repo), name="dh",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        out = get_doc_health(repo="dh", storage_path=str(tmp_path))
        assert "orphan_section_count" in out
        # Both authored sections should be orphans (nothing links anywhere).
        assert out["orphan_section_count"] >= 2


class TestSchema:
    def test_get_section_path_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gsp = next(t for t in tools if t.name == "get_section_path")
        assert gsp.inputSchema["required"] == ["repo", "section_id"]
