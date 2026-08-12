"""Tests for v1.24.0: related-graph sidecar + boilerplate detector."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.retrieval import boilerplate as bp
from jdocmunch_mcp.retrieval import related_persist as rp
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import index_local


# ---------------------------------------------------------------------------
# related_persist
# ---------------------------------------------------------------------------

class TestRelatedPersist:
    def _build_dicts(self):
        secs = parse_file(
            "# Top\n\n## Auth\n\n### Tokens\n\nbody\n\n### Sessions\n\nbody\n",
            "g.md", "local/r",
        )
        return [s.to_dict() for s in secs]

    def test_build_emits_per_section_neighbors(self):
        secs = self._build_dicts()
        data = rp.build(secs)
        assert data["version"] >= 1
        assert data["section_count"] == len(secs)
        # Every section in the index has an entry.
        for s in secs:
            assert s["id"] in data["by_section"]

    def test_round_trip(self, tmp_path):
        secs = self._build_dicts()
        n = rp.write(str(tmp_path), "owner", "name", secs)
        assert n == len(secs)
        loaded = rp.load(str(tmp_path), "owner", "name")
        assert loaded is not None
        assert loaded["section_count"] == n
        # lookup() returns one section's payload.
        first = secs[0]["id"]
        entry = rp.lookup(str(tmp_path), "owner", "name", first)
        assert entry is not None
        assert "structural" in entry and "semantic" in entry

    def test_load_missing_returns_none(self, tmp_path):
        assert rp.load(str(tmp_path), "owner", "missing") is None
        assert rp.lookup(str(tmp_path), "owner", "missing", "sid") is None

    def test_purge(self, tmp_path):
        secs = self._build_dicts()
        rp.write(str(tmp_path), "owner", "name", secs)
        assert rp.purge(str(tmp_path), "owner", "name") is True
        assert rp.purge(str(tmp_path), "owner", "name") is False


class TestRelatedPersistEndToEnd:
    def test_index_local_writes_sidecar(self, tmp_path):
        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "g.md").write_text(
            "# Top\n\n## Auth\n\n### Tokens\n\nbody\n\n### Sessions\n\nbody\n",
            encoding="utf-8",
        )
        index_local(
            path=str(repo_dir), name="rg",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        # Sidecar must exist after a full index.
        path = rp._path(str(tmp_path), "local", "rg")
        assert path.exists()

    def test_get_related_sections_uses_sidecar(self, tmp_path):
        from jdocmunch_mcp.tools.get_related_sections import get_related_sections

        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "g.md").write_text(
            "# Top\n\n## Auth\n\n### Tokens\n\nbody\n\n### Sessions\n\nbody\n",
            encoding="utf-8",
        )
        index_local(
            path=str(repo_dir), name="rg2",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "rg2")
        tokens = next(s for s in idx.sections if s["title"] == "Tokens")
        out = get_related_sections(repo="rg2", section_id=tokens["id"], storage_path=str(tmp_path))
        assert out["_meta"]["source"] == "sidecar"

    def test_get_related_falls_back_when_sidecar_missing(self, tmp_path):
        from jdocmunch_mcp.tools.get_related_sections import get_related_sections

        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "g.md").write_text(
            "# Top\n\n## A\n\nbody\n\n## B\n\nbody\n",
            encoding="utf-8",
        )
        index_local(
            path=str(repo_dir), name="rg3",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        # Purge the sidecar so the on-demand path runs.
        rp.purge(str(tmp_path), "local", "rg3")
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "rg3")
        a = next(s for s in idx.sections if s["title"] == "A")
        out = get_related_sections(repo="rg3", section_id=a["id"], storage_path=str(tmp_path))
        assert out["_meta"]["source"] == "on_demand"


# ---------------------------------------------------------------------------
# boilerplate
# ---------------------------------------------------------------------------

class TestBoilerplateDetect:
    def test_detect_repeated_line(self):
        common = "© 2026 Example Corp — all rights reserved."
        secs = [
            {"content": f"Section A body.\n{common}\n"},
            {"content": f"Section B body different.\n{common}\n"},
            {"content": f"Section C body again.\n{common}\n"},
            {"content": f"Section D more.\n{common}\n"},
        ]
        out = bp.detect(secs, min_section_ratio=0.5, min_sections=2)
        assert common in out

    def test_short_lines_skipped(self):
        secs = [{"content": "ok\n"} for _ in range(5)]
        # Single 2-char line below _MIN_LINE_LEN — never flagged.
        assert bp.detect(secs) == []

    def test_unique_lines_not_flagged(self):
        secs = [{"content": f"unique line for section {i} only.\n"} for i in range(5)]
        assert bp.detect(secs) == []

    def test_min_sections_floor(self):
        common = "boilerplate present everywhere"
        secs = [{"content": f"a\n{common}\n"}, {"content": f"b\n{common}\n"}]
        # min_sections=3 floors the threshold even though ratio would qualify.
        assert bp.detect(secs, min_sections=3) == []


class TestBoilerplateRoundTrip:
    def test_persist_and_load(self, tmp_path):
        common = "© 2026 Example Corp"
        secs = [{"content": f"a\n{common}\n"} for _ in range(5)]
        n = bp.write(str(tmp_path), "owner", "name", secs, min_sections=2)
        assert n >= 1
        out = bp.load(str(tmp_path), "owner", "name")
        assert common in out

    def test_load_missing_empty(self, tmp_path):
        assert bp.load(str(tmp_path), "owner", "missing") == []

    def test_purge(self, tmp_path):
        bp.write(str(tmp_path), "o", "n", [{"content": "a"}])
        assert bp.purge(str(tmp_path), "o", "n") is True


class TestBoilerplateStrip:
    def test_strip_fragments(self):
        content = "This is a paragraph.\n© 2026 Example Corp\nSecond paragraph.\n"
        new, removed = bp.strip(content, ["© 2026 Example Corp"])
        assert "© 2026 Example Corp" not in new
        assert removed > 0
        assert "This is a paragraph." in new
        assert "Second paragraph." in new

    def test_empty_content_passthrough(self):
        new, removed = bp.strip("", ["x"])
        assert new == ""
        assert removed == 0

    def test_empty_fragments_passthrough(self):
        content = "body"
        new, removed = bp.strip(content, [])
        assert new == content
        assert removed == 0


class TestBoilerplateEndToEnd:
    def test_index_local_writes_sidecar(self, tmp_path):
        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        common = "Edit this page on GitHub"
        for i in range(4):
            (repo_dir / f"page{i}.md").write_text(
                f"# Page {i}\n\nbody for page {i}.\n\n{common}\n",
                encoding="utf-8",
            )
        index_local(
            path=str(repo_dir), name="bp",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        out = bp.load(str(tmp_path), "local", "bp")
        assert any(common in line for line in out)

    def test_get_section_strip_boilerplate(self, tmp_path):
        from jdocmunch_mcp.tools.get_section import get_section

        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        common = "Edit this page on GitHub"
        for i in range(4):
            (repo_dir / f"page{i}.md").write_text(
                f"# Page {i}\n\nbody for page {i}.\n\n{common}\n",
                encoding="utf-8",
            )
        index_local(
            path=str(repo_dir), name="bp2",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "bp2")
        page0 = next(s for s in idx.sections if s["doc_path"] == "page0.md" and s["level"] == 1)

        out_default = get_section(repo="bp2", section_id=page0["id"], storage_path=str(tmp_path))
        assert common in out_default["section"]["content"]

        out_stripped = get_section(
            repo="bp2", section_id=page0["id"],
            strip_boilerplate=True, storage_path=str(tmp_path),
        )
        assert common not in out_stripped["section"]["content"]
        assert out_stripped["_meta"]["boilerplate_stripped_bytes"] > 0


# ---------------------------------------------------------------------------
# Schema additions
# ---------------------------------------------------------------------------

class TestSchema:
    def test_strip_boilerplate_in_three_tool_schemas(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        by_name = {t.name: t for t in tools}
        for tool_name in ("get_section", "get_sections", "get_section_context"):
            schema = by_name[tool_name].inputSchema
            assert "strip_boilerplate" in schema["properties"], tool_name
