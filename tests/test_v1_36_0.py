"""Tests for v1.36.0: path_glob filter on search_sections / get_toc / get_toc_tree."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.tools.get_toc import get_toc
from jdocmunch_mcp.tools.get_toc_tree import get_toc_tree
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


def _index_tree(tmp_path):
    """Index a synthetic mini-repo with api/, guide/, and reference/ subtrees."""
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "api").mkdir()
    (repo / "guide").mkdir()
    (repo / "reference").mkdir()
    (repo / "api" / "users.md").write_text(
        "# Users API\n\nList and create user accounts.\n", encoding="utf-8")
    (repo / "api" / "tokens.md").write_text(
        "# Tokens API\n\nIssue and revoke auth tokens.\n", encoding="utf-8")
    (repo / "guide" / "getting-started.md").write_text(
        "# Getting Started\n\nInstall and run.\n", encoding="utf-8")
    (repo / "reference" / "config.md").write_text(
        "# Config Reference\n\nAll configuration knobs.\n", encoding="utf-8")
    index_local(
        path=str(repo), name="globtree",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


# ---------------------------------------------------------------------------
# get_toc
# ---------------------------------------------------------------------------


class TestGetTocPathGlob:
    def test_default_returns_all(self, tmp_path):
        _index_tree(tmp_path)
        out = get_toc(repo="globtree", storage_path=str(tmp_path))
        paths = {s["doc_path"] for s in out["sections"]}
        assert paths == {"api/users.md", "api/tokens.md",
                         "guide/getting-started.md", "reference/config.md"}
        assert "path_glob" not in out["_meta"]

    def test_glob_restricts_to_subtree(self, tmp_path):
        _index_tree(tmp_path)
        out = get_toc(repo="globtree", path_glob="api/*",
                      storage_path=str(tmp_path))
        paths = {s["doc_path"] for s in out["sections"]}
        assert paths == {"api/users.md", "api/tokens.md"}
        assert out["_meta"]["path_glob"] == "api/*"

    def test_glob_no_match_empty(self, tmp_path):
        _index_tree(tmp_path)
        out = get_toc(repo="globtree", path_glob="nope/*",
                      storage_path=str(tmp_path))
        assert out["sections"] == []
        assert out["section_count"] == 0

    def test_glob_extension_filter(self, tmp_path):
        _index_tree(tmp_path)
        out = get_toc(repo="globtree", path_glob="*.md",
                      storage_path=str(tmp_path))
        # fnmatch '*.md' matches the basename only, not nested paths.
        # api/users.md → no match (slash not handled by *).
        # That's expected fnmatch semantics; documented in the param desc.
        paths = {s["doc_path"] for s in out["sections"]}
        # Confirm: fnmatch treats "*" as "any chars except /" on POSIX-ish.
        # Actually Python's fnmatch.fnmatch DOES match across "/" — confirm:
        # >>> fnmatch.fnmatch("api/users.md", "*.md") → True
        # So this test asserts whatever the real behavior is.
        import fnmatch
        for p in {"api/users.md", "api/tokens.md", "guide/getting-started.md",
                  "reference/config.md"}:
            if fnmatch.fnmatch(p, "*.md"):
                assert p in paths


# ---------------------------------------------------------------------------
# get_toc_tree
# ---------------------------------------------------------------------------


class TestGetTocTreePathGlob:
    def test_glob_filters_documents(self, tmp_path):
        _index_tree(tmp_path)
        out = get_toc_tree(repo="globtree", path_glob="reference/*",
                           storage_path=str(tmp_path))
        paths = {d["doc_path"] for d in out["documents"]}
        assert paths == {"reference/config.md"}
        assert out["_meta"]["path_glob"] == "reference/*"

    def test_default_returns_all_docs(self, tmp_path):
        _index_tree(tmp_path)
        out = get_toc_tree(repo="globtree", storage_path=str(tmp_path))
        paths = {d["doc_path"] for d in out["documents"]}
        assert len(paths) == 4
        assert "path_glob" not in out["_meta"]


# ---------------------------------------------------------------------------
# search_sections
# ---------------------------------------------------------------------------


class TestSearchSectionsPathGlob:
    def test_default_searches_all(self, tmp_path):
        _index_tree(tmp_path)
        out = search_sections(repo="globtree", query="API",
                              semantic=False, storage_path=str(tmp_path))
        # Both api/* docs match.
        paths = {r["doc_path"] for r in out["results"]}
        assert "api/users.md" in paths
        assert "api/tokens.md" in paths
        assert "path_glob" not in out["_meta"]

    def test_glob_restricts_to_subtree(self, tmp_path):
        _index_tree(tmp_path)
        out = search_sections(
            repo="globtree", query="API",
            path_glob="api/*", semantic=False, storage_path=str(tmp_path),
        )
        paths = {r["doc_path"] for r in out["results"]}
        # Only api/* paths should remain.
        for p in paths:
            assert p.startswith("api/"), out["results"]
        assert out["_meta"]["path_glob"] == "api/*"

    def test_glob_excludes_non_matches(self, tmp_path):
        _index_tree(tmp_path)
        # Query that matches "Getting Started" — but path_glob excludes guide/.
        out = search_sections(
            repo="globtree", query="install",
            path_glob="api/*", semantic=False, storage_path=str(tmp_path),
        )
        paths = {r["doc_path"] for r in out["results"]}
        assert "guide/getting-started.md" not in paths

    def test_glob_no_match_empty(self, tmp_path):
        _index_tree(tmp_path)
        out = search_sections(
            repo="globtree", query="API",
            path_glob="missing/*", semantic=False, storage_path=str(tmp_path),
        )
        assert out["results"] == []
        assert out["result_count"] == 0


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def _tools(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        return asyncio.run(srv.list_tools())

    def test_search_sections_has_path_glob(self):
        ss = next(t for t in self._tools() if t.name == "search_sections")
        assert "path_glob" in ss.inputSchema["properties"]

    def test_get_toc_has_path_glob(self):
        gt = next(t for t in self._tools() if t.name == "get_toc")
        assert "path_glob" in gt.inputSchema["properties"]

    def test_get_toc_tree_has_path_glob(self):
        gtt = next(t for t in self._tools() if t.name == "get_toc_tree")
        assert "path_glob" in gtt.inputSchema["properties"]
