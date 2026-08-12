"""Tests for v2.0.0: legacy-engine drop, related graph, section diff, doc health, adaptive context."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.retrieval.related import (
    get_related,
    semantic_neighbors,
    structural_neighbors,
)
from jdocmunch_mcp.storage import DocStore


# ---------------------------------------------------------------------------
# Breaking change: legacy lexical_engine removed
# ---------------------------------------------------------------------------

class TestLegacyEngineRemoved:
    def test_legacy_request_returns_error(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections

        store = DocStore(base_path=str(tmp_path))
        sections = parse_file("# Top\n\nbody\n", "g.md", "local/r")
        store.save_index(
            owner="local", name="r", sections=sections,
            raw_files={"g.md": "# Top\n\nbody\n"}, doc_types={".md": 1},
        )
        out = search_sections(
            repo="local/r", query="body", semantic=False,
            lexical_engine="legacy", storage_path=str(tmp_path),
        )
        assert "error" in out
        assert "legacy" in out["error"].lower()

    def test_unknown_engine_also_errors(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections
        store = DocStore(base_path=str(tmp_path))
        sections = parse_file("# Top\n\nbody\n", "g.md", "local/r")
        store.save_index(
            owner="local", name="r", sections=sections,
            raw_files={"g.md": "# Top\n\nbody\n"}, doc_types={".md": 1},
        )
        out = search_sections(
            repo="local/r", query="body", semantic=False,
            lexical_engine="bogus", storage_path=str(tmp_path),
        )
        assert "error" in out


# ---------------------------------------------------------------------------
# Token estimate helpers — text variant
# ---------------------------------------------------------------------------

class TestTokenEstimateText:
    def test_estimate_savings_text_uses_count_tokens(self):
        from jdocmunch_mcp.storage.token_tracker import estimate_savings_text

        long = "alpha beta gamma delta epsilon " * 100
        short = "alpha"
        savings = estimate_savings_text(long, short)
        assert savings > 0


# ---------------------------------------------------------------------------
# Related-section graph
# ---------------------------------------------------------------------------

class TestStructuralNeighbors:
    def _build(self):
        return parse_file(
            textwrap.dedent("""
                # Root

                ## Auth

                ### Tokens

                token body

                ### Sessions

                session body

                ## Logging

                log body
            """).lstrip(),
            "g.md",
            "local/r",
        )

    def test_siblings_resolved(self):
        secs = self._build()
        sec_dicts = [s.to_dict() for s in secs]
        # Pre-load: dicts have parent_id wired by hierarchy.py.
        tokens = next(s for s in sec_dicts if s["title"] == "Tokens")
        out = structural_neighbors(sec_dicts, tokens["id"])
        kinds = {n["kind"] for n in out}
        assert "sibling" in kinds  # Sessions is a sibling
        titles = [n["title"] for n in out]
        assert "Sessions" in titles
        assert "Auth" in titles  # parent

    def test_cousins_optional(self):
        secs = self._build()
        sec_dicts = [s.to_dict() for s in secs]
        tokens = next(s for s in sec_dicts if s["title"] == "Tokens")
        # Without cousins flag → no Logging.
        out = structural_neighbors(sec_dicts, tokens["id"], include_cousins=False)
        assert all(n["title"] != "Logging" for n in out)
        # Cousins enabled — but Logging is at level 2 (parent of Auth), so
        # it's an ancestor's sibling, not a cousin. Only same-level cousins
        # (children of Auth's siblings) qualify. Logging has no children
        # in the fixture so the cousin list stays empty here, which is the
        # expected behavior.

    def test_unknown_section_returns_empty(self):
        sec_dicts = [s.to_dict() for s in self._build()]
        out = structural_neighbors(sec_dicts, "no::such::id")
        assert out == []


class TestSemanticNeighbors:
    def test_returns_empty_without_embeddings(self):
        secs = parse_file("# T\n\n## A\n\nbody\n## B\n\nbody\n", "g.md", "r")
        sec_dicts = [s.to_dict() for s in secs]
        out = semantic_neighbors(sec_dicts, sec_dicts[1]["id"])
        assert out == []

    def test_finds_neighbors_with_stub_embeddings(self):
        # Synthesize sections with hand-rolled embeddings.
        sec_dicts = [
            {"id": "a#1", "title": "A", "level": 1, "embedding": [1.0, 0.0]},
            {"id": "b#1", "title": "B", "level": 1, "embedding": [0.95, 0.05]},
            {"id": "c#1", "title": "C", "level": 1, "embedding": [0.0, 1.0]},
        ]
        out = semantic_neighbors(sec_dicts, "a#1", top_n=2, min_score=0.5)
        assert any(n["id"] == "b#1" for n in out)
        # 'c#1' is orthogonal (cosine 0) — below threshold.
        assert all(n["id"] != "c#1" for n in out)


class TestGetRelatedDispatch:
    def test_mode_structural(self):
        secs = parse_file("# T\n\n## A\n\nbody\n", "g.md", "r")
        sec_dicts = [s.to_dict() for s in secs]
        out = get_related(sec_dicts, sec_dicts[1]["id"], mode="structural")
        assert "structural" in out
        assert out["semantic"] == []

    def test_mode_semantic_only(self):
        sec_dicts = [
            {"id": "a#1", "title": "A", "level": 1, "embedding": [1.0, 0.0], "parent_id": ""},
            {"id": "b#1", "title": "B", "level": 1, "embedding": [0.9, 0.1], "parent_id": ""},
        ]
        out = get_related(sec_dicts, "a#1", mode="semantic")
        assert out["structural"] == []
        assert any(n["id"] == "b#1" for n in out["semantic"])


# ---------------------------------------------------------------------------
# get_related_sections MCP tool
# ---------------------------------------------------------------------------

class TestRelatedTool:
    def _build(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        content = textwrap.dedent("""
            # Top

            ## Auth

            ### Tokens

            token body

            ### Sessions

            session body
        """).lstrip()
        sections = parse_file(content, "g.md", "local/r")
        store.save_index(
            owner="local", name="r", sections=sections,
            raw_files={"g.md": content}, doc_types={".md": 1},
        )

    def test_returns_structural_when_no_embeddings(self, tmp_path):
        from jdocmunch_mcp.tools.get_related_sections import get_related_sections
        self._build(tmp_path)
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "r")
        tokens = next(s for s in idx.sections if s["title"] == "Tokens")
        out = get_related_sections(repo="local/r", section_id=tokens["id"], storage_path=str(tmp_path))
        assert out["section_id"] == tokens["id"]
        assert out["structural"]
        assert out["semantic"] == []
        assert "hint" in out["_meta"]

    def test_unknown_section_error(self, tmp_path):
        from jdocmunch_mcp.tools.get_related_sections import get_related_sections
        self._build(tmp_path)
        out = get_related_sections(repo="local/r", section_id="nope", storage_path=str(tmp_path))
        assert "error" in out

    def test_unknown_mode_error(self, tmp_path):
        from jdocmunch_mcp.tools.get_related_sections import get_related_sections
        self._build(tmp_path)
        out = get_related_sections(repo="local/r", section_id="x", mode="cosmic", storage_path=str(tmp_path))
        assert "error" in out


# ---------------------------------------------------------------------------
# get_section_diff
# ---------------------------------------------------------------------------

class TestSectionDiff:
    def _build(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        content = "# Top\n\n## Auth\n\nbody alpha\n"
        sections = parse_file(content, "g.md", "local/r")
        store.save_index(
            owner="local", name="r", sections=sections,
            raw_files={"g.md": content}, doc_types={".md": 1},
        )
        return store, content

    def test_identical_when_unchanged(self, tmp_path):
        from jdocmunch_mcp.tools.get_section_diff import get_section_diff
        store, content = self._build(tmp_path)
        idx = store.load_index("local", "r")
        auth = next(s for s in idx.sections if s["title"] == "Auth")
        out = get_section_diff(repo="local/r", section_id=auth["id"], storage_path=str(tmp_path))
        assert out["identical"] is True
        assert out["diff"] == ""

    def test_diff_after_byte_range_change(self, tmp_path):
        from jdocmunch_mcp.tools.get_section_diff import get_section_diff
        from jdocmunch_mcp.storage.doc_store import _INDEX_CACHE

        store, content = self._build(tmp_path)
        # Mutate the cached file so the byte-range hash differs.
        cached = store._safe_content_path(store._content_dir("local", "r"), "g.md")
        cached.write_bytes(content.replace("alpha", "BETA").encode("utf-8"))
        _INDEX_CACHE.clear()

        idx = store.load_index("local", "r")
        auth = next(s for s in idx.sections if s["title"] == "Auth")
        out = get_section_diff(repo="local/r", section_id=auth["id"], storage_path=str(tmp_path))
        assert out["identical"] is False
        assert out["indexed_hash"] != out["current_hash"]
        # Diff text only present when indexed_text is also kept inline; for
        # standard markdown sections the in-memory content was dropped, so
        # diff may be empty but the hashes still tell the story.
        assert out["current_text"]

    def test_unknown_section_error(self, tmp_path):
        from jdocmunch_mcp.tools.get_section_diff import get_section_diff
        self._build(tmp_path)
        out = get_section_diff(repo="local/r", section_id="nope", storage_path=str(tmp_path))
        assert "error" in out


# ---------------------------------------------------------------------------
# get_doc_health
# ---------------------------------------------------------------------------

class TestDocHealth:
    def test_summary_shape(self, tmp_path):
        from jdocmunch_mcp.tools.get_doc_health import get_doc_health
        from jdocmunch_mcp.tools.index_local import index_local

        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "g.md").write_text("# Top\n\n## Auth\n\nbody\n", encoding="utf-8")
        index_local(
            path=str(repo_dir), name="hx",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        out = get_doc_health(repo="hx", storage_path=str(tmp_path))
        assert out["section_count"] >= 2
        assert out["doc_count"] == 1
        assert "role_distribution" in out
        assert "freshness" in out
        assert out["freshness"]["fresh"] >= 1
        assert "bm25" in out

    def test_unknown_repo_error(self, tmp_path):
        from jdocmunch_mcp.tools.get_doc_health import get_doc_health
        out = get_doc_health(repo="nope/missing", storage_path=str(tmp_path))
        assert "error" in out


# ---------------------------------------------------------------------------
# Adaptive context — get_section_context include_related
# ---------------------------------------------------------------------------

class TestAdaptiveContext:
    def test_include_related_off_by_default(self, tmp_path):
        from jdocmunch_mcp.tools.get_section_context import get_section_context
        from jdocmunch_mcp.tools.index_local import index_local

        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "g.md").write_text(
            "# Top\n\n## Auth\n\nbody\n\n## Logs\n\nbody\n",
            encoding="utf-8",
        )
        index_local(
            path=str(repo_dir), name="adp",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "adp")
        auth = next(s for s in idx.sections if s["title"] == "Auth")
        out = get_section_context(repo="adp", section_id=auth["id"], storage_path=str(tmp_path))
        assert "related" not in out

    def test_include_related_appends_related(self, tmp_path):
        from jdocmunch_mcp.tools.get_section_context import get_section_context
        from jdocmunch_mcp.tools.index_local import index_local

        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "g.md").write_text(
            "# Top\n\n## Auth\n\nbody\n\n## Logs\n\nbody\n",
            encoding="utf-8",
        )
        index_local(
            path=str(repo_dir), name="adp",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "adp")
        auth = next(s for s in idx.sections if s["title"] == "Auth")
        out = get_section_context(
            repo="adp", section_id=auth["id"],
            include_related=True, storage_path=str(tmp_path),
        )
        assert "related" in out
        # Auth has Logs as a sibling and Top as parent.
        kinds = {r["kind"] for r in out["related"]}
        assert kinds & {"sibling", "parent"}


# ---------------------------------------------------------------------------
# Server registration — 27 → 30 tools
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_new_tools_registered(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        for n in ("get_related_sections", "get_section_diff", "get_doc_health"):
            assert n in names, f"{n} not registered"

    def test_total_count_at_least_30(self):
        # v1.20.0 introduced the 30-tool count; later minors only add tools.
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        assert len(tools) >= 30
