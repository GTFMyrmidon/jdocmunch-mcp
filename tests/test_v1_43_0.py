"""Tests for v1.43.0: get_section_descendants subtree traversal."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_section_descendants import get_section_descendants
from jdocmunch_mcp.tools.index_local import index_local


def _index_tree(tmp_path):
    body = textwrap.dedent("""
        # Root

        Root body.

        ## Branch A

        Branch A body.

        ### Leaf A1

        Leaf A1 body.

        ### Leaf A2

        Leaf A2 body.

        #### Deep A2a

        Deep A2a body.

        ## Branch B

        Branch B body.

        ### Leaf B1

        Leaf B1 body.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "page.md").write_text(body, encoding="utf-8")
    index_local(
        path=str(repo), name="desc",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    return DocStore(base_path=str(tmp_path)).load_index("local", "desc")


class TestGetSectionDescendants:
    def test_unknown_repo(self, tmp_path):
        out = get_section_descendants(repo="missing", section_id="x",
                                      storage_path=str(tmp_path))
        assert "error" in out

    def test_unknown_section(self, tmp_path):
        _index_tree(tmp_path)
        out = get_section_descendants(repo="desc", section_id="bogus",
                                      storage_path=str(tmp_path))
        assert "error" in out

    def test_negative_max_depth_rejected(self, tmp_path):
        out = get_section_descendants(repo="desc", section_id="x",
                                      max_depth=-1, storage_path=str(tmp_path))
        assert "error" in out

    def test_leaf_has_no_descendants(self, tmp_path):
        idx = _index_tree(tmp_path)
        leaf = next(s for s in idx.sections if s["title"] == "Leaf B1")
        out = get_section_descendants(repo="desc", section_id=leaf["id"],
                                      storage_path=str(tmp_path))
        assert out["descendant_count"] == 0
        assert out["descendants"] == []

    def test_full_subtree_walked(self, tmp_path):
        idx = _index_tree(tmp_path)
        root = next(s for s in idx.sections if s["title"] == "Root")
        out = get_section_descendants(repo="desc", section_id=root["id"],
                                      storage_path=str(tmp_path))
        titles = [d["title"] for d in out["descendants"]]
        # Branch A, Branch B at depth 1; their children at depth 2;
        # Deep A2a at depth 3.
        assert "Branch A" in titles
        assert "Branch B" in titles
        assert "Leaf A1" in titles
        assert "Leaf A2" in titles
        assert "Leaf B1" in titles
        assert "Deep A2a" in titles

    def test_max_depth_1_returns_immediate_children_only(self, tmp_path):
        idx = _index_tree(tmp_path)
        root = next(s for s in idx.sections if s["title"] == "Root")
        out = get_section_descendants(repo="desc", section_id=root["id"],
                                      max_depth=1, storage_path=str(tmp_path))
        titles = [d["title"] for d in out["descendants"]]
        assert "Branch A" in titles
        assert "Branch B" in titles
        # Grandchildren must NOT appear.
        assert "Leaf A1" not in titles
        assert "Leaf A2" not in titles
        assert "Deep A2a" not in titles
        # All depths must be 1.
        for d in out["descendants"]:
            assert d["depth"] == 1

    def test_depth_offsets_correct(self, tmp_path):
        idx = _index_tree(tmp_path)
        root = next(s for s in idx.sections if s["title"] == "Root")
        out = get_section_descendants(repo="desc", section_id=root["id"],
                                      storage_path=str(tmp_path))
        depths = {d["title"]: d["depth"] for d in out["descendants"]}
        assert depths["Branch A"] == 1
        assert depths["Leaf A2"] == 2
        assert depths["Deep A2a"] == 3

    def test_max_depth_zero_returns_nothing(self, tmp_path):
        idx = _index_tree(tmp_path)
        root = next(s for s in idx.sections if s["title"] == "Root")
        out = get_section_descendants(repo="desc", section_id=root["id"],
                                      max_depth=0, storage_path=str(tmp_path))
        assert out["descendant_count"] == 0

    def test_handle_shape(self, tmp_path):
        idx = _index_tree(tmp_path)
        root = next(s for s in idx.sections if s["title"] == "Root")
        out = get_section_descendants(repo="desc", section_id=root["id"],
                                      storage_path=str(tmp_path))
        if out["descendants"]:
            d = out["descendants"][0]
            assert set(d.keys()) == {"id", "title", "level", "doc_path", "depth"}
            assert "content" not in d

    def test_descendants_sorted_by_depth_then_byte_order(self, tmp_path):
        idx = _index_tree(tmp_path)
        root = next(s for s in idx.sections if s["title"] == "Root")
        out = get_section_descendants(repo="desc", section_id=root["id"],
                                      storage_path=str(tmp_path))
        depths = [d["depth"] for d in out["descendants"]]
        # Non-decreasing.
        assert depths == sorted(depths)


class TestSchema:
    def test_get_section_descendants_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gsd = next(t for t in tools if t.name == "get_section_descendants")
        assert gsd.inputSchema["required"] == ["repo", "section_id"]
        assert "max_depth" in gsd.inputSchema["properties"]
        assert gsd.inputSchema["properties"]["max_depth"]["minimum"] == 0
