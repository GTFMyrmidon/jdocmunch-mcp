"""Tests for v1.32.0: citation block + task-aware retrieval profiles."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_section import get_section
from jdocmunch_mcp.tools.get_section_context import get_section_context
from jdocmunch_mcp.tools.get_sections import get_sections
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


# ---------------------------------------------------------------------------
# Citation block on retrieval responses
# ---------------------------------------------------------------------------


def _index_simple(tmp_path) -> DocStore:
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "g.md").write_text(
        "# Top\n\n## Auth\n\nbearer body\n\n## Logs\n\nlog body\n",
        encoding="utf-8",
    )
    index_local(
        path=str(repo), name="cit",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    return DocStore(base_path=str(tmp_path))


class TestCitationBlock:
    def test_get_section_emits_citation(self, tmp_path):
        store = _index_simple(tmp_path)
        idx = store.load_index("local", "cit")
        auth = next(s for s in idx.sections if s["title"] == "Auth")
        out = get_section(repo="cit", section_id=auth["id"], storage_path=str(tmp_path))
        cit = out["_meta"].get("citation")
        assert cit is not None
        assert cit["repo"] == "local/cit"
        assert cit["doc_path"] == "g.md"
        assert cit["section_id"] == auth["id"]
        assert cit["byte_end"] > cit["byte_start"]
        assert cit["content_hash"]
        assert cit["indexed_at"]

    def test_get_sections_emits_citations_per_row(self, tmp_path):
        store = _index_simple(tmp_path)
        idx = store.load_index("local", "cit")
        auth = next(s for s in idx.sections if s["title"] == "Auth")
        logs = next(s for s in idx.sections if s["title"] == "Logs")
        out = get_sections(
            repo="cit", section_ids=[auth["id"], logs["id"]],
            storage_path=str(tmp_path),
        )
        citations = out["_meta"].get("citations") or []
        assert len(citations) == 2
        ids = {c["section_id"] for c in citations}
        assert ids == {auth["id"], logs["id"]}
        for c in citations:
            assert c["repo"] == "local/cit"
            assert c["content_hash"]

    def test_get_section_context_emits_citation(self, tmp_path):
        store = _index_simple(tmp_path)
        idx = store.load_index("local", "cit")
        auth = next(s for s in idx.sections if s["title"] == "Auth")
        out = get_section_context(
            repo="cit", section_id=auth["id"], storage_path=str(tmp_path),
        )
        cit = out["_meta"].get("citation")
        assert cit is not None
        assert cit["section_id"] == auth["id"]
        assert cit["repo"] == "local/cit"


# ---------------------------------------------------------------------------
# Task-aware retrieval profiles
# ---------------------------------------------------------------------------


class TestTaskProfiles:
    def _index_with_roles(self, tmp_path):
        repo = tmp_path / "docs"
        repo.mkdir()
        # Include sections that the heuristic role classifier will tag
        # with each of the roles the four profiles boost.
        (repo / "g.md").write_text(textwrap.dedent("""
            # Title

            ## Troubleshooting connection errors

            Connection refused — check the port.

            ## How to install

            Install the package.

            ## API Reference

            Endpoint listing.

            ## Concept Background

            Some prose explanation about the system.

            ## Examples

            Sample usage code.
        """).lstrip(), encoding="utf-8")
        index_local(
            path=str(repo), name="prof",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

    def test_unknown_profile_errors(self, tmp_path):
        self._index_with_roles(tmp_path)
        out = search_sections(
            repo="prof", query="install", profile="cosmic",
            semantic=False, storage_path=str(tmp_path),
        )
        assert "error" in out

    def test_install_profile_lifts_how_to(self, tmp_path):
        self._index_with_roles(tmp_path)
        # Without a profile, "install" might rank either how-to or
        # examples high — both contain the term. With profile=install,
        # how_to/tutorial/example sections must be at the top.
        out = search_sections(
            repo="prof", query="install", profile="install",
            semantic=False, storage_path=str(tmp_path),
        )
        meta = out["_meta"]
        assert meta["profile"] == "install"
        assert "how_to" in meta["profile_boost_roles"]
        # Top section's role should be in the boost set.
        results = out["results"]
        assert results
        top_role = (results[0].get("metadata") or {}).get("role")
        assert top_role in {"how_to", "tutorial", "example"}, results[0]

    def test_debug_profile_lifts_troubleshooting(self, tmp_path):
        self._index_with_roles(tmp_path)
        out = search_sections(
            repo="prof", query="connection refused", profile="debug",
            semantic=False, storage_path=str(tmp_path),
        )
        results = out["results"]
        assert results
        top_role = (results[0].get("metadata") or {}).get("role")
        assert top_role in {"troubleshooting", "faq", "example"}

    def test_explicit_role_overrides_profile(self, tmp_path):
        self._index_with_roles(tmp_path)
        # Force role=concept; profile would otherwise prefer how_to.
        out = search_sections(
            repo="prof", query="install", role="concept", profile="install",
            semantic=False, storage_path=str(tmp_path),
        )
        # role= is a hard filter; profile is a soft re-rank.
        assert out["_meta"]["role_filter"] == "concept"
        for r in out["results"]:
            assert (r.get("metadata") or {}).get("role") == "concept"

    def test_profile_meta_round_trips(self, tmp_path):
        self._index_with_roles(tmp_path)
        out = search_sections(
            repo="prof", query="endpoint", profile="api",
            semantic=False, storage_path=str(tmp_path),
        )
        meta = out["_meta"]
        assert meta.get("profile") == "api"
        assert "api" in meta["profile_boost_roles"]


# ---------------------------------------------------------------------------
# Schema additions
# ---------------------------------------------------------------------------


class TestSchema:
    def test_search_sections_schema_has_profile(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ss = next(t for t in tools if t.name == "search_sections")
        assert "profile" in ss.inputSchema["properties"]
        assert set(ss.inputSchema["properties"]["profile"]["enum"]) == {
            "install", "debug", "explain", "api"
        }
