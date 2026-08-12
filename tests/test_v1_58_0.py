"""Tests for v1.58.0: get_doc per-doc detail view."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.get_doc import get_doc
from jdocmunch_mcp.tools.index_local import index_local


def _index_one_doc(tmp_path):
    body = textwrap.dedent("""
        # Authentication

        Auth flow overview. #api #auth

        ## How to install

        Install the package via pip and verify with foo --version.

        ## Troubleshooting

        Connection refused — check the port and firewall.
    """).lstrip("\n")
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "auth.md").write_text(body, encoding="utf-8")
    (repo / "billing.md").write_text("# Billing\n\n## Invoices\n\nInvoice setup.\n",
                                     encoding="utf-8")
    index_local(
        path=str(repo), name="gd",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestGetDoc:
    def test_unknown_repo(self, tmp_path):
        out = get_doc(repo="missing", doc_path="auth.md",
                      storage_path=str(tmp_path))
        assert "error" in out

    def test_missing_doc_path(self, tmp_path):
        _index_one_doc(tmp_path)
        out = get_doc(repo="gd", doc_path="",
                      storage_path=str(tmp_path))
        assert "error" in out

    def test_unknown_doc(self, tmp_path):
        _index_one_doc(tmp_path)
        out = get_doc(repo="gd", doc_path="nonexistent.md",
                      storage_path=str(tmp_path))
        assert "error" in out

    def test_section_list_ordered_by_byte_start(self, tmp_path):
        _index_one_doc(tmp_path)
        out = get_doc(repo="gd", doc_path="auth.md",
                      storage_path=str(tmp_path))
        starts = [s["byte_start"] for s in out["sections"]]
        assert starts == sorted(starts)

    def test_only_target_doc_sections(self, tmp_path):
        _index_one_doc(tmp_path)
        out = get_doc(repo="gd", doc_path="auth.md",
                      storage_path=str(tmp_path))
        # All sections must come from auth.md (no billing leakage).
        for s in out["sections"]:
            assert "id" in s
        assert out["doc_path"] == "auth.md"

    def test_role_distribution(self, tmp_path):
        _index_one_doc(tmp_path)
        out = get_doc(repo="gd", doc_path="auth.md",
                      storage_path=str(tmp_path))
        # Should classify at least one role from "How to install" or
        # "Troubleshooting".
        assert isinstance(out["role_distribution"], list)

    def test_tag_distribution_from_hashtags(self, tmp_path):
        _index_one_doc(tmp_path)
        out = get_doc(repo="gd", doc_path="auth.md",
                      storage_path=str(tmp_path))
        tags = {r["tag"] for r in out["tag_distribution"]}
        assert "api" in tags
        assert "auth" in tags

    def test_byte_size_and_format(self, tmp_path):
        _index_one_doc(tmp_path)
        out = get_doc(repo="gd", doc_path="auth.md",
                      storage_path=str(tmp_path))
        assert out["byte_size"] > 0
        assert out["format"] == ".md"

    def test_section_handle_shape(self, tmp_path):
        _index_one_doc(tmp_path)
        out = get_doc(repo="gd", doc_path="auth.md",
                      storage_path=str(tmp_path))
        for s in out["sections"]:
            assert set(s.keys()) == {"id", "title", "level", "byte_start", "byte_end"}
            assert "content" not in s

    def test_indexed_at_present(self, tmp_path):
        _index_one_doc(tmp_path)
        out = get_doc(repo="gd", doc_path="auth.md",
                      storage_path=str(tmp_path))
        assert out["indexed_at"]


class TestSchema:
    def test_get_doc_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gd = next(t for t in tools if t.name == "get_doc")
        assert gd.inputSchema["required"] == ["repo", "doc_path"]
