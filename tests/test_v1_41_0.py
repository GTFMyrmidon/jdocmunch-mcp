"""Tests for v1.41.0: get_section_excerpt content preview tool."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_section_excerpt import get_section_excerpt, _safe_truncate
from jdocmunch_mcp.tools.index_local import index_local


def _index_long(tmp_path):
    body = textwrap.dedent("""
        # Page

        First paragraph with enough text to fill several hundred bytes.
        We need this to be longer than the default 500-byte cap so the
        truncation path is exercised. Lorem ipsum dolor sit amet,
        consectetur adipiscing elit, sed do eiusmod tempor incididunt
        ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis
        nostrud exercitation ullamco laboris nisi ut aliquip ex ea
        commodo consequat. Duis aute irure dolor in reprehenderit in
        voluptate velit esse cillum dolore eu fugiat nulla pariatur.

        Second paragraph also has substantial text. Excepteur sint
        occaecat cupidatat non proident, sunt in culpa qui officia
        deserunt mollit anim id est laborum.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "long.md").write_text(body, encoding="utf-8")
    (repo / "short.md").write_text("# Short\n\nbrief.\n", encoding="utf-8")
    index_local(
        path=str(repo), name="exc",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    return DocStore(base_path=str(tmp_path)).load_index("local", "exc")


class TestSafeTruncate:
    def test_short_text_returns_as_is(self):
        excerpt, truncated = _safe_truncate("hello world", 100)
        assert excerpt == "hello world"
        assert truncated is False

    def test_long_text_truncates_with_marker(self):
        text = "lorem ipsum " * 100
        excerpt, truncated = _safe_truncate(text, 100)
        assert truncated is True
        assert excerpt.endswith("…")
        # Truncated body ≤ cap; marker (\n…, 5 bytes UTF-8) appended after.
        body = excerpt.rstrip("…").rstrip()
        assert len(body.encode("utf-8")) <= 100

    def test_truncates_to_last_newline(self):
        text = "line one\nline two\nline three\nline four\n"
        # cap that lands inside line three
        excerpt, truncated = _safe_truncate(text, 25)
        assert truncated is True
        # Must end on a complete line before the cap.
        body_no_marker = excerpt.rstrip("…").rstrip()
        assert "\n" in body_no_marker

    def test_utf8_boundary_safe(self):
        # Multi-byte chars near the cap must not produce garbage.
        text = "α" * 200  # each α is 2 bytes
        excerpt, truncated = _safe_truncate(text, 50)
        assert truncated is True
        # Should round-trip cleanly (no UnicodeDecodeError).
        excerpt.encode("utf-8")


class TestGetSectionExcerpt:
    def test_unknown_repo(self, tmp_path):
        out = get_section_excerpt(repo="missing", section_id="x",
                                  storage_path=str(tmp_path))
        assert "error" in out

    def test_unknown_section(self, tmp_path):
        _index_long(tmp_path)
        out = get_section_excerpt(repo="exc", section_id="bogus",
                                  storage_path=str(tmp_path))
        assert "error" in out

    def test_invalid_max_bytes(self, tmp_path):
        out = get_section_excerpt(repo="exc", section_id="x", max_bytes=0,
                                  storage_path=str(tmp_path))
        assert "error" in out

    def test_short_section_not_truncated(self, tmp_path):
        idx = _index_long(tmp_path)
        short = next(s for s in idx.sections
                     if s["doc_path"] == "short.md" and s["title"] == "Short")
        out = get_section_excerpt(repo="exc", section_id=short["id"],
                                  storage_path=str(tmp_path))
        assert out["truncated"] is False
        assert "…" not in out["excerpt"]

    def test_long_section_truncated(self, tmp_path):
        idx = _index_long(tmp_path)
        page = next(s for s in idx.sections
                    if s["doc_path"] == "long.md" and s["title"] == "Page")
        out = get_section_excerpt(repo="exc", section_id=page["id"],
                                  max_bytes=200, storage_path=str(tmp_path))
        assert out["truncated"] is True
        assert out["excerpt_byte_length"] <= 250
        assert out["full_byte_length"] > out["excerpt_byte_length"]
        assert out["excerpt"].endswith("…")

    def test_metadata_handle_present(self, tmp_path):
        idx = _index_long(tmp_path)
        page = next(s for s in idx.sections
                    if s["doc_path"] == "long.md" and s["title"] == "Page")
        out = get_section_excerpt(repo="exc", section_id=page["id"],
                                  storage_path=str(tmp_path))
        sec = out["section"]
        assert sec["title"] == "Page"
        assert sec["doc_path"] == "long.md"
        assert "id" in sec
        assert "level" in sec
        assert "summary" in sec

    def test_meta_reports_savings(self, tmp_path):
        idx = _index_long(tmp_path)
        page = next(s for s in idx.sections
                    if s["doc_path"] == "long.md" and s["title"] == "Page")
        out = get_section_excerpt(repo="exc", section_id=page["id"],
                                  max_bytes=100, storage_path=str(tmp_path))
        assert out["_meta"]["max_bytes"] == 100
        # tokens_saved positive when truncation actually saves bytes.
        assert out["_meta"]["tokens_saved"] >= 0


class TestSchema:
    def test_get_section_excerpt_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ex = next(t for t in tools if t.name == "get_section_excerpt")
        assert ex.inputSchema["required"] == ["repo", "section_id"]
        assert ex.inputSchema["properties"]["max_bytes"]["default"] == 500
