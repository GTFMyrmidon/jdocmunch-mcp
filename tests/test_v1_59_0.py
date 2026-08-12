"""Tests for v1.59.0: count_sections filter-only count tool."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.count_sections import count_sections
from jdocmunch_mcp.tools.index_local import index_local


def _index_mixed(tmp_path):
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "api").mkdir()
    (repo / "api" / "auth.md").write_text(textwrap.dedent("""
        # Authentication

        Configure tokens. #api #auth

        ## How to install

        Install the package via pip and verify with foo --version.
    """).lstrip("\n"), encoding="utf-8")
    (repo / "api" / "billing.md").write_text(textwrap.dedent("""
        # Billing

        Configure invoices. #api #billing
    """).lstrip("\n"), encoding="utf-8")
    (repo / "internal.md").write_text(textwrap.dedent("""
        # Internals

        Build pipeline. #internal

        ## Troubleshooting

        Connection refused — check the port.
    """).lstrip("\n"), encoding="utf-8")
    index_local(
        path=str(repo), name="cs",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestCountSections:
    def test_unknown_repo(self, tmp_path):
        out = count_sections(repo="missing", storage_path=str(tmp_path))
        assert "error" in out

    def test_no_filters_returns_total(self, tmp_path):
        _index_mixed(tmp_path)
        out = count_sections(repo="cs", storage_path=str(tmp_path))
        assert out["count"] == out["total_sections"]
        assert out["count"] > 0

    def test_path_glob(self, tmp_path):
        _index_mixed(tmp_path)
        out = count_sections(repo="cs", path_glob="api/*",
                             storage_path=str(tmp_path))
        assert out["count"] >= 2  # at least Authentication + Billing
        # Cross-check by querying without filter — must be larger.
        full = count_sections(repo="cs", storage_path=str(tmp_path))
        assert out["count"] < full["count"]

    def test_doc_path_exact(self, tmp_path):
        _index_mixed(tmp_path)
        out = count_sections(repo="cs", doc_path="api/auth.md",
                             storage_path=str(tmp_path))
        # Just the auth doc's sections (synthetic root + Authentication + How to install).
        assert out["count"] >= 2

    def test_tag_include_AND(self, tmp_path):
        _index_mixed(tmp_path)
        out_any = count_sections(repo="cs", tags=["api"],
                                 storage_path=str(tmp_path))
        out_both = count_sections(repo="cs", tags=["api", "auth"],
                                  storage_path=str(tmp_path))
        # AND-include: api+auth subset of api alone.
        assert out_both["count"] <= out_any["count"]

    def test_tag_exclude(self, tmp_path):
        _index_mixed(tmp_path)
        full = count_sections(repo="cs", storage_path=str(tmp_path))
        out = count_sections(repo="cs", exclude_tags=["internal"],
                             storage_path=str(tmp_path))
        # Excluding #internal removes at least one section.
        assert out["count"] < full["count"]

    def test_role_filter(self, tmp_path):
        _index_mixed(tmp_path)
        out = count_sections(repo="cs", role="how_to",
                             storage_path=str(tmp_path))
        assert out["count"] >= 1

    def test_level_range(self, tmp_path):
        _index_mixed(tmp_path)
        out = count_sections(repo="cs", min_level=2, max_level=2,
                             storage_path=str(tmp_path))
        # Level-2 sections only.
        assert out["count"] >= 1

    def test_byte_length_range(self, tmp_path):
        _index_mixed(tmp_path)
        out = count_sections(repo="cs", min_byte_length=1_000_000,
                             storage_path=str(tmp_path))
        # No section is a megabyte.
        assert out["count"] == 0

    def test_filters_compose_AND(self, tmp_path):
        _index_mixed(tmp_path)
        # Sections that match both: path under api/ AND tag #auth.
        out = count_sections(repo="cs", path_glob="api/*", tags=["auth"],
                             storage_path=str(tmp_path))
        # auth.md sections with #auth tag.
        assert out["count"] >= 1


class TestSchema:
    def test_count_sections_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        cs = next(t for t in tools if t.name == "count_sections")
        assert cs.inputSchema["required"] == ["repo"]
        # All filter axes present.
        props = cs.inputSchema["properties"]
        for k in ("path_glob", "role", "roles", "exclude_roles",
                  "tags", "exclude_tags", "min_level", "max_level",
                  "min_byte_length", "max_byte_length"):
            assert k in props, k
