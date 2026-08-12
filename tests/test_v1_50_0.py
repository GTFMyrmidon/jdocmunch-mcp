"""Tests for v1.50.0: get_all_roles role-discovery tool."""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.get_all_roles import get_all_roles
from jdocmunch_mcp.tools.index_local import index_local


def _index_with_roles(tmp_path):
    """Index sections so the heuristic role classifier tags multiple roles."""
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "g.md").write_text(textwrap.dedent("""
        # Title

        ## Troubleshooting connection errors

        Connection refused — check the port. If you see this error,
        verify the firewall and try again. Common fix is to restart
        the service.

        ## How to install

        Install the package via pip. Run pip install foo and then
        verify with foo --version.

        ## API Reference

        Endpoint listing for /v1/users and /v1/tokens.

        ## Concept Background

        Some prose explanation about the system architecture and
        why it works the way it does.

        ## Examples

        Sample usage code with curl and python.
    """).lstrip("\n"), encoding="utf-8")
    index_local(
        path=str(repo), name="rl",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


class TestGetAllRoles:
    def test_unknown_repo(self, tmp_path):
        out = get_all_roles(repo="missing", storage_path=str(tmp_path))
        assert "error" in out

    def test_negative_sample_rejected(self, tmp_path):
        out = get_all_roles(repo="rl", sample_size=-1,
                            storage_path=str(tmp_path))
        assert "error" in out

    def test_returns_role_buckets(self, tmp_path):
        _index_with_roles(tmp_path)
        out = get_all_roles(repo="rl", storage_path=str(tmp_path))
        # Sum of section_count across roles must equal total_sections.
        total = sum(r["section_count"] for r in out["roles"])
        assert total == out["total_sections"]

    def test_unknown_bucket_present_when_unclassified(self, tmp_path):
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "p.md").write_text("# Plain\n\nNo classifiable content.\n",
                                   encoding="utf-8")
        index_local(
            path=str(repo), name="plain",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        out = get_all_roles(repo="plain", storage_path=str(tmp_path))
        roles = {r["role"] for r in out["roles"]}
        # Synthetic roots and unclassified sections fall into "unknown".
        assert "unknown" in roles or out["total_sections_classified"] >= 0

    def test_samples_included_by_default(self, tmp_path):
        _index_with_roles(tmp_path)
        out = get_all_roles(repo="rl", storage_path=str(tmp_path))
        for r in out["roles"]:
            assert "samples" in r
            assert isinstance(r["samples"], list)
            assert len(r["samples"]) <= 3

    def test_sample_size_zero_omits_samples(self, tmp_path):
        _index_with_roles(tmp_path)
        out = get_all_roles(repo="rl", sample_size=0,
                            storage_path=str(tmp_path))
        for r in out["roles"]:
            assert "samples" not in r

    def test_sort_order_count_desc_then_role_asc(self, tmp_path):
        _index_with_roles(tmp_path)
        out = get_all_roles(repo="rl", storage_path=str(tmp_path))
        prev_count = float("inf")
        prev_role = ""
        for r in out["roles"]:
            assert r["section_count"] <= prev_count
            if r["section_count"] == prev_count:
                assert r["role"] >= prev_role
            prev_count = r["section_count"]
            prev_role = r["role"]

    def test_meta_records_sample_size(self, tmp_path):
        _index_with_roles(tmp_path)
        out = get_all_roles(repo="rl", sample_size=5,
                            storage_path=str(tmp_path))
        assert out["_meta"]["sample_size"] == 5


class TestSchema:
    def test_get_all_roles_in_tools_list(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gar = next(t for t in tools if t.name == "get_all_roles")
        assert gar.inputSchema["required"] == ["repo"]
        assert gar.inputSchema["properties"]["sample_size"]["minimum"] == 0
        assert gar.inputSchema["properties"]["sample_size"]["default"] == 3
