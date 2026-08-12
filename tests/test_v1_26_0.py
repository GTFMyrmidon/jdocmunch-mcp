"""Tests for v1.26.0: cross-repo concept graph (groups + RRF fan-out)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.storage import repo_groups
from jdocmunch_mcp.tools.index_local import index_local


# ---------------------------------------------------------------------------
# repo_groups storage layer
# ---------------------------------------------------------------------------

class TestRepoGroupsStorage:
    def test_load_missing_returns_empty(self, tmp_path):
        out = repo_groups.load(str(tmp_path))
        assert out == {"groups": {}}

    def test_define_then_load(self, tmp_path):
        repo_groups.define("docs-everywhere", ["a", "b", "c"], base_path=str(tmp_path))
        groups = repo_groups.list_groups(str(tmp_path))
        assert groups == {"docs-everywhere": ["a", "b", "c"]}

    def test_define_replaces(self, tmp_path):
        repo_groups.define("g", ["a"], base_path=str(tmp_path))
        repo_groups.define("g", ["x", "y"], base_path=str(tmp_path))
        assert repo_groups.list_groups(str(tmp_path))["g"] == ["x", "y"]

    def test_empty_repos_deletes(self, tmp_path):
        repo_groups.define("g", ["a"], base_path=str(tmp_path))
        repo_groups.define("g", [], base_path=str(tmp_path))
        assert "g" not in repo_groups.list_groups(str(tmp_path))

    def test_resolve_unknown(self, tmp_path):
        assert repo_groups.resolve("nope", base_path=str(tmp_path)) == []

    def test_jsonc_comments_tolerated(self, tmp_path):
        path = repo_groups._path(str(tmp_path))
        path.write_text(
            "// hand-edited\n"
            '{ /* block comment */ "groups": { "g": ["a"] } }\n',
            encoding="utf-8",
        )
        assert repo_groups.list_groups(str(tmp_path)) == {"g": ["a"]}

    def test_corrupt_file_returns_empty(self, tmp_path):
        path = repo_groups._path(str(tmp_path))
        path.write_text("{not json", encoding="utf-8")
        assert repo_groups.list_groups(str(tmp_path)) == {}

    def test_filters_non_string_repos(self, tmp_path):
        path = repo_groups._path(str(tmp_path))
        path.write_text(
            '{"groups": {"g": ["a", 42, null, "b", ""]}}',
            encoding="utf-8",
        )
        assert repo_groups.list_groups(str(tmp_path))["g"] == ["a", "b"]


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

class TestRepoGroupTools:
    def test_list_empty(self, tmp_path):
        from jdocmunch_mcp.tools.repo_group_tools import list_repo_groups
        out = list_repo_groups(storage_path=str(tmp_path))
        assert out["groups"] == []

    def test_define_then_list(self, tmp_path):
        from jdocmunch_mcp.tools.repo_group_tools import define_repo_group, list_repo_groups
        define_repo_group(name="alpha", repos=["one", "two"], storage_path=str(tmp_path))
        define_repo_group(name="beta", repos=["three"], storage_path=str(tmp_path))
        out = list_repo_groups(storage_path=str(tmp_path))
        names = [g["name"] for g in out["groups"]]
        assert names == ["alpha", "beta"]
        sizes = {g["name"]: g["size"] for g in out["groups"]}
        assert sizes == {"alpha": 2, "beta": 1}

    def test_define_empty_deletes(self, tmp_path):
        from jdocmunch_mcp.tools.repo_group_tools import define_repo_group, list_repo_groups
        define_repo_group(name="g", repos=["a"], storage_path=str(tmp_path))
        out = define_repo_group(name="g", repos=[], storage_path=str(tmp_path))
        assert out["deleted"] is True
        assert list_repo_groups(storage_path=str(tmp_path))["groups"] == []

    def test_blank_name_error(self, tmp_path):
        from jdocmunch_mcp.tools.repo_group_tools import define_repo_group
        out = define_repo_group(name="", repos=["a"], storage_path=str(tmp_path))
        assert "error" in out


# ---------------------------------------------------------------------------
# search_sections fan-out
# ---------------------------------------------------------------------------

class TestSearchSectionsFanOut:
    def _index_two_repos(self, tmp_path):
        # Repo A: contains "authentication" content.
        rep_a = tmp_path / "a"
        rep_a.mkdir()
        (rep_a / "auth.md").write_text(
            "# Auth\n\n## Tokens\n\nbearer token authentication body\n",
            encoding="utf-8",
        )
        index_local(
            path=str(rep_a), name="repo_a",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        # Repo B: contains "logging" content.
        rep_b = tmp_path / "b"
        rep_b.mkdir()
        (rep_b / "logs.md").write_text(
            "# Logging\n\n## Levels\n\nINFO WARNING ERROR levels\n",
            encoding="utf-8",
        )
        index_local(
            path=str(rep_b), name="repo_b",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

    def test_fan_out_aggregates_results(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections
        from jdocmunch_mcp.tools.repo_group_tools import define_repo_group

        self._index_two_repos(tmp_path)
        define_repo_group(name="all_docs", repos=["repo_a", "repo_b"], storage_path=str(tmp_path))

        # Auth query — should surface repo_a's section.
        out = search_sections(
            repo_group="all_docs", query="bearer authentication",
            semantic=False, storage_path=str(tmp_path),
        )
        assert "results" in out
        assert out["repo_group"] == "all_docs"
        assert set(out["members"]) == {"repo_a", "repo_b"}
        ids = [r["id"] for r in out["results"]]
        assert any("repo_a" in i for i in ids)

        # Logging query — should surface repo_b.
        out2 = search_sections(
            repo_group="all_docs", query="WARNING ERROR levels",
            semantic=False, storage_path=str(tmp_path),
        )
        ids2 = [r["id"] for r in out2["results"]]
        assert any("repo_b" in i for i in ids2)

        # _meta records the fusion mode.
        assert out["_meta"]["fusion"] == "rrf_k60"

    def test_unknown_group_errors(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections
        out = search_sections(
            repo_group="not-defined", query="x", storage_path=str(tmp_path),
        )
        assert "error" in out

    def test_neither_repo_nor_group_errors(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections
        out = search_sections(query="x", storage_path=str(tmp_path))
        assert "error" in out

    def test_per_repo_error_does_not_abort(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections
        from jdocmunch_mcp.tools.repo_group_tools import define_repo_group

        self._index_two_repos(tmp_path)
        # Define a group that includes a non-existent repo.
        define_repo_group(
            name="mixed", repos=["repo_a", "missing_repo", "repo_b"],
            storage_path=str(tmp_path),
        )
        out = search_sections(
            repo_group="mixed", query="bearer authentication",
            semantic=False, storage_path=str(tmp_path),
        )
        # Fan-out completes; per_repo records the error but results still
        # come from the working members.
        assert out["results"]
        per_repo = {p["repo"]: p for p in out["per_repo"]}
        assert per_repo["missing_repo"].get("error")


# ---------------------------------------------------------------------------
# Server registration
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_new_tools_registered(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        assert "list_repo_groups" in names
        assert "define_repo_group" in names

    def test_search_sections_schema_drops_required_repo(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        ss = next(t for t in tools if t.name == "search_sections")
        # repo no longer required (repo_group is the alternative).
        assert "repo" not in (ss.inputSchema.get("required") or [])
        assert "query" in (ss.inputSchema.get("required") or [])
        assert "repo_group" in ss.inputSchema["properties"]
