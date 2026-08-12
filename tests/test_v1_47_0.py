"""Tests for v1.47.0: get_recent_changes drift surface tool."""

from __future__ import annotations

from pathlib import Path

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_recent_changes import get_recent_changes
from jdocmunch_mcp.tools.index_local import index_local


def _index_one(tmp_path):
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "page.md").write_bytes(b"# Page\n\n## Auth\n\nbearer body\n\n## Logs\n\nlog body\n")
    index_local(
        path=str(repo), name="rec",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    # Return the indexed cache path that FreshnessProbe actually reads.
    cache = DocStore(base_path=str(tmp_path))._content_dir("local", "rec")
    return cache / "page.md"


class TestGetRecentChanges:
    def test_unknown_repo(self, tmp_path):
        out = get_recent_changes(repo="missing", storage_path=str(tmp_path))
        assert "error" in out

    def test_clean_index_empty_changes(self, tmp_path):
        _index_one(tmp_path)
        out = get_recent_changes(repo="rec", storage_path=str(tmp_path))
        assert out["change_count"] == 0
        assert out["changes"] == []
        assert out["by_bucket"]["edited_uncommitted"] == 0
        assert out["by_bucket"]["stale_index"] == 0

    def test_edited_file_surfaces_section(self, tmp_path):
        cached = _index_one(tmp_path)
        # Append a new heading after indexing — file's full hash diverges.
        with cached.open("ab") as f:
            f.write(b"\n## Extra\n\nnew body\n")
        out = get_recent_changes(repo="rec", storage_path=str(tmp_path))
        assert out["change_count"] >= 1
        # All surfaced sections must be in non-fresh buckets.
        for c in out["changes"]:
            assert c["freshness"] in ("edited_uncommitted", "stale_index")

    def test_stale_section_when_byte_range_diverges(self, tmp_path):
        cached = _index_one(tmp_path)
        # Replace the file entirely so byte ranges no longer hash the same.
        cached.write_bytes(b"# Different\n\n## Different Sub\n\nNew text.\n")
        out = get_recent_changes(repo="rec", storage_path=str(tmp_path))
        # At least one section should be flagged stale_index.
        buckets = {c["freshness"] for c in out["changes"]}
        assert "stale_index" in buckets

    def test_include_flags_filter_buckets(self, tmp_path):
        cached = _index_one(tmp_path)
        cached.write_bytes(b"# Different\n\n## Different\n\nnew\n")
        out = get_recent_changes(
            repo="rec", include_stale=False, include_edited=False,
            storage_path=str(tmp_path),
        )
        # With both buckets disabled, nothing surfaces.
        assert out["change_count"] == 0

    def test_synthetic_root_excluded(self, tmp_path):
        cached = _index_one(tmp_path)
        cached.write_bytes(b"# Different\n")
        out = get_recent_changes(repo="rec", storage_path=str(tmp_path))
        for c in out["changes"]:
            assert c["level"] != 0

    def test_handle_shape(self, tmp_path):
        cached = _index_one(tmp_path)
        cached.write_bytes(b"# Different\n\n## Different Sub\n\nNew.\n")
        out = get_recent_changes(repo="rec", storage_path=str(tmp_path))
        if out["changes"]:
            entry = out["changes"][0]
            assert set(entry.keys()) == {"id", "title", "doc_path", "level", "freshness"}
            assert "content" not in entry

    def test_meta_records_flags(self, tmp_path):
        _index_one(tmp_path)
        out = get_recent_changes(
            repo="rec", include_stale=True, include_edited=False,
            storage_path=str(tmp_path),
        )
        assert out["_meta"]["include_stale"] is True
        assert out["_meta"]["include_edited"] is False
        assert out["_meta"]["indexed_at"]


class TestSchema:
    def test_get_recent_changes_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        grc = next(t for t in tools if t.name == "get_recent_changes")
        assert grc.inputSchema["required"] == ["repo"]
        assert grc.inputSchema["properties"]["include_stale"]["default"] is True
        assert grc.inputSchema["properties"]["include_edited"]["default"] is True
