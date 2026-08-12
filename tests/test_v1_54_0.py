"""Tests for v1.54.0: describe_section consolidated handle bundle."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.describe_section import describe_section
from jdocmunch_mcp.tools.index_local import index_local


def _index_nested(tmp_path):
    body = textwrap.dedent("""
        # Top

        Top body.

        ## Middle

        Middle body.

        ### Deep

        Deep body for the inner-most section we'll target.

        ## Sibling

        Sibling body.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "page.md").write_text(body, encoding="utf-8")
    index_local(
        path=str(repo), name="ds",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    return DocStore(base_path=str(tmp_path)).load_index("local", "ds")


class TestDescribeSection:
    def test_unknown_repo(self, tmp_path):
        out = describe_section(repo="missing", section_id="x",
                               storage_path=str(tmp_path))
        assert "error" in out

    def test_unknown_section(self, tmp_path):
        _index_nested(tmp_path)
        out = describe_section(repo="ds", section_id="bogus",
                               storage_path=str(tmp_path))
        assert "error" in out

    def test_returns_full_bundle(self, tmp_path):
        idx = _index_nested(tmp_path)
        deep = next(s for s in idx.sections if s.get("title") == "Deep")
        out = describe_section(repo="ds", section_id=deep["id"],
                               storage_path=str(tmp_path))
        # Section view present, no content.
        assert "section" in out
        assert "content" not in out["section"]
        assert "byte_length" in out["section"]
        # Path includes ancestors root-first.
        titles = [step["title"] for step in out["path"]]
        assert titles[-1] == "Deep"
        assert "Middle" in titles
        assert "Top" in titles
        # Neighbors block populated.
        nbrs = out["neighbors"]
        assert nbrs["parent"]["title"] == "Middle"
        assert nbrs["prev"] is None or nbrs["prev"]["level"] >= 0
        # No first_child for a leaf.
        assert nbrs["first_child"] is None
        # depth equals path length minus 1.
        assert out["depth"] == len(out["path"]) - 1

    def test_neighbors_for_middle_node(self, tmp_path):
        idx = _index_nested(tmp_path)
        middle = next(s for s in idx.sections if s.get("title") == "Middle")
        out = describe_section(repo="ds", section_id=middle["id"],
                               storage_path=str(tmp_path))
        nbrs = out["neighbors"]
        # Middle has Top as parent, Deep as first_child, and Sibling as
        # next in document order.
        assert nbrs["parent"]["title"] == "Top"
        assert nbrs["first_child"]["title"] == "Deep"
        assert nbrs["next"]["title"] in ("Deep", "Sibling")  # depends on parser
        assert nbrs["child_count"] >= 1

    def test_handle_shape_in_neighbors(self, tmp_path):
        idx = _index_nested(tmp_path)
        middle = next(s for s in idx.sections if s.get("title") == "Middle")
        out = describe_section(repo="ds", section_id=middle["id"],
                               storage_path=str(tmp_path))
        for handle in (out["neighbors"]["parent"], out["neighbors"]["first_child"]):
            if handle is not None:
                assert set(handle.keys()) == {"id", "title", "level", "doc_path"}
                assert "content" not in handle

    def test_path_handle_shape(self, tmp_path):
        idx = _index_nested(tmp_path)
        deep = next(s for s in idx.sections if s.get("title") == "Deep")
        out = describe_section(repo="ds", section_id=deep["id"],
                               storage_path=str(tmp_path))
        for step in out["path"]:
            assert set(step.keys()) == {"id", "title", "level", "doc_path"}

    def test_meta_shape(self, tmp_path):
        idx = _index_nested(tmp_path)
        top = next(s for s in idx.sections if s.get("title") == "Top")
        out = describe_section(repo="ds", section_id=top["id"],
                               storage_path=str(tmp_path))
        assert out["_meta"]["repo"] == "local/ds"
        assert out["_meta"]["indexed_at"]


class TestSchema:
    def test_describe_section_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ds = next(t for t in tools if t.name == "describe_section")
        assert ds.inputSchema["required"] == ["repo", "section_id"]
