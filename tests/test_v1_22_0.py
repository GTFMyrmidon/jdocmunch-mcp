"""Tests for v1.22.0: get_tutorial_path + get_undocumented_symbols."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.tools.index_local import index_local


# ---------------------------------------------------------------------------
# get_tutorial_path
# ---------------------------------------------------------------------------

class TestTutorialPath:
    def _index(self, tmp_path, files: dict):
        repo = tmp_path / "docs"
        repo.mkdir()
        for name, body in files.items():
            (repo / name).write_text(body, encoding="utf-8")
        index_local(
            path=str(repo), name="tut",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

    def test_frontmatter_chain(self, tmp_path):
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path

        self._index(tmp_path, {
            "intro.md": textwrap.dedent("""
                ---
                next: setup.md
                ---

                # Intro

                welcome
            """).lstrip(),
            "setup.md": textwrap.dedent("""
                ---
                next: usage.md
                prev: intro.md
                ---

                # Setup

                run pip
            """).lstrip(),
            "usage.md": textwrap.dedent("""
                ---
                prev: setup.md
                ---

                # Usage

                call api
            """).lstrip(),
        })

        from jdocmunch_mcp.storage import DocStore
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "tut")
        intro = next(s for s in idx.sections if s["doc_path"] == "intro.md" and s["level"] == 1)

        out = get_tutorial_path(repo="tut", section_id=intro["id"], storage_path=str(tmp_path))
        assert out["strategy"] == "frontmatter"
        chain_paths = [c["doc_path"] for c in out["chain"]]
        assert chain_paths == ["intro.md", "setup.md", "usage.md"]

    def test_inline_link_chain(self, tmp_path):
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path

        self._index(tmp_path, {
            "step1.md": "# Step 1\n\nintro\n\nNext: [Step 2](step2.md)\n",
            "step2.md": "# Step 2\n\nbody\n\nNext: [Step 3](step3.md)\n",
            "step3.md": "# Step 3\n\nfin\n",
        })
        from jdocmunch_mcp.storage import DocStore
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "tut")
        s1 = next(s for s in idx.sections if s["doc_path"] == "step1.md" and s["level"] == 1)

        out = get_tutorial_path(repo="tut", section_id=s1["id"], storage_path=str(tmp_path))
        assert out["strategy"] == "inline_link"
        assert [c["doc_path"] for c in out["chain"]] == ["step1.md", "step2.md", "step3.md"]

    def test_ordered_filename_chain(self, tmp_path):
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path

        self._index(tmp_path, {
            "01-intro.md": "# Intro\n\nbody\n",
            "02-setup.md": "# Setup\n\nbody\n",
            "03-usage.md": "# Usage\n\nbody\n",
            "extras.md": "# Extras\n\nbody\n",
        })
        from jdocmunch_mcp.storage import DocStore
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "tut")
        intro = next(s for s in idx.sections if s["doc_path"] == "01-intro.md" and s["level"] == 1)

        out = get_tutorial_path(repo="tut", section_id=intro["id"], storage_path=str(tmp_path))
        assert out["strategy"] == "ordered_filename"
        names = [c["doc_path"] for c in out["chain"]]
        assert names == ["01-intro.md", "02-setup.md", "03-usage.md"]

    def test_no_chain_when_no_signals(self, tmp_path):
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path

        self._index(tmp_path, {
            "alpha.md": "# Alpha\n\nbody\n",
            "beta.md": "# Beta\n\nbody\n",
        })
        from jdocmunch_mcp.storage import DocStore
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "tut")
        alpha = next(s for s in idx.sections if s["doc_path"] == "alpha.md" and s["level"] == 1)

        out = get_tutorial_path(repo="tut", section_id=alpha["id"], storage_path=str(tmp_path))
        assert out["strategy"] == "none"
        assert out["chain"] == []

    def test_unknown_repo_error(self, tmp_path):
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path
        out = get_tutorial_path(repo="nope/missing", section_id="x", storage_path=str(tmp_path))
        assert "error" in out

    def test_unknown_section_error(self, tmp_path):
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path
        self._index(tmp_path, {"a.md": "# A\n\nbody\n"})
        out = get_tutorial_path(repo="tut", section_id="nope::nope", storage_path=str(tmp_path))
        assert "error" in out


# ---------------------------------------------------------------------------
# get_undocumented_symbols
# ---------------------------------------------------------------------------

class TestUndocumentedSymbols:
    def _index(self, tmp_path):
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "g.md").write_text(
            "# Top\n\n## DocumentedClass\n\nThis class is described.\n\n## auth helper\n\nbody about auth_helper\n",
            encoding="utf-8",
        )
        index_local(
            path=str(repo), name="cov",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

    def test_bridge_unavailable(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.get_undocumented_symbols import get_undocumented_symbols
        import jdocmunch_mcp.tools.get_undocumented_symbols as mod

        self._index(tmp_path)
        monkeypatch.setattr(mod, "import_search_symbols", lambda: None)

        out = get_undocumented_symbols(repo="cov", code_repo="x/y", storage_path=str(tmp_path))
        assert out["_meta"]["bridge_available"] is False
        assert "hint" in out["_meta"]
        assert out["undocumented"] == []
        assert out["coverage"]["total_symbols"] == 0

    def test_documented_vs_undocumented(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.get_undocumented_symbols import get_undocumented_symbols
        import jdocmunch_mcp.tools.get_undocumented_symbols as mod

        self._index(tmp_path)

        # Stub jcodemunch to return three symbols: one mentioned, two not.
        def _fake_search(repo, query, max_results=10):
            if query in ("*", "."):
                return {"results": [
                    {"id": "a#1", "name": "DocumentedClass", "kind": "class", "qualified_name": "pkg.DocumentedClass"},
                    {"id": "b#1", "name": "MissingFunction", "kind": "function", "qualified_name": "pkg.MissingFunction"},
                    {"id": "c#1", "name": "auth_helper", "kind": "function", "qualified_name": "pkg.auth.auth_helper"},
                ]}
            return {"results": []}
        monkeypatch.setattr(mod, "import_search_symbols", lambda: _fake_search)

        out = get_undocumented_symbols(repo="cov", code_repo="x/y", storage_path=str(tmp_path))
        assert out["_meta"]["bridge_available"] is True
        assert out["coverage"]["total_symbols"] == 3
        # DocumentedClass and auth_helper appear in section title/content.
        assert out["coverage"]["documented"] == 2
        names = {row["name"] for row in out["undocumented"]}
        assert names == {"MissingFunction"}

    def test_unknown_repo_error(self, tmp_path):
        from jdocmunch_mcp.tools.get_undocumented_symbols import get_undocumented_symbols
        out = get_undocumented_symbols(repo="nope/missing", code_repo="x/y", storage_path=str(tmp_path))
        assert "error" in out


# ---------------------------------------------------------------------------
# Server registration
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_new_tools_registered(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        assert "get_tutorial_path" in names
        assert "get_undocumented_symbols" in names
