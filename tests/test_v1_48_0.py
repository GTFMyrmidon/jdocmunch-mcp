"""Tests for v1.48.0: get_section_summaries batch metadata tool."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_section_summaries import get_section_summaries
from jdocmunch_mcp.tools.index_local import index_local


def _index(tmp_path):
    body = textwrap.dedent("""
        # Top

        Top body has some token content for summary derivation.

        ## Auth

        Auth body for token configuration.

        ## Logs

        Logs body for log retention.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "page.md").write_text(body, encoding="utf-8")
    index_local(
        path=str(repo), name="bsm",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    return DocStore(base_path=str(tmp_path)).load_index("local", "bsm")


class TestGetSectionSummaries:
    def test_unknown_repo(self, tmp_path):
        out = get_section_summaries(repo="missing", section_ids=["x"],
                                    storage_path=str(tmp_path))
        assert "error" in out

    def test_non_list_section_ids(self, tmp_path):
        _index(tmp_path)
        out = get_section_summaries(repo="bsm", section_ids="not-a-list",
                                    storage_path=str(tmp_path))
        assert "error" in out

    def test_empty_list_returns_empty(self, tmp_path):
        _index(tmp_path)
        out = get_section_summaries(repo="bsm", section_ids=[],
                                    storage_path=str(tmp_path))
        assert out["section_count"] == 0
        assert out["found_count"] == 0
        assert out["sections"] == []

    def test_all_found(self, tmp_path):
        idx = _index(tmp_path)
        ids = [s["id"] for s in idx.sections if s.get("title") in ("Auth", "Logs")]
        out = get_section_summaries(repo="bsm", section_ids=ids,
                                    storage_path=str(tmp_path))
        assert out["found_count"] == len(ids)
        assert out["missing_count"] == 0
        for entry in out["sections"]:
            assert "section" in entry
            assert "error" not in entry
            assert entry["requested_id"] in ids
            assert "content" not in entry["section"]
            assert "byte_length" in entry["section"]

    def test_partial_missing(self, tmp_path):
        idx = _index(tmp_path)
        auth = next(s for s in idx.sections if s.get("title") == "Auth")
        out = get_section_summaries(
            repo="bsm",
            section_ids=[auth["id"], "bogus-id", auth["id"]],
            storage_path=str(tmp_path),
        )
        assert out["found_count"] == 2
        assert out["missing_count"] == 1
        # Order preserved.
        assert out["sections"][0]["requested_id"] == auth["id"]
        assert "section" in out["sections"][0]
        assert out["sections"][1]["requested_id"] == "bogus-id"
        assert "error" in out["sections"][1]
        assert out["sections"][2]["requested_id"] == auth["id"]
        assert "section" in out["sections"][2]

    def test_non_string_id_handled(self, tmp_path):
        _index(tmp_path)
        out = get_section_summaries(repo="bsm", section_ids=[123, None],
                                    storage_path=str(tmp_path))
        assert out["missing_count"] == 2
        for entry in out["sections"]:
            assert "error" in entry

    def test_meta_includes_indexed_at(self, tmp_path):
        _index(tmp_path)
        out = get_section_summaries(repo="bsm", section_ids=[],
                                    storage_path=str(tmp_path))
        assert out["_meta"]["indexed_at"]


class TestSchema:
    def test_get_section_summaries_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gss = next(t for t in tools if t.name == "get_section_summaries")
        assert gss.inputSchema["required"] == ["repo", "section_ids"]
        assert gss.inputSchema["properties"]["section_ids"]["type"] == "array"
