"""Tests for v1.29.0: Sphinx toctree + VuePress + OpenAPI 3.1/Swagger 2.0 + autotune."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import index_local

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Tutorial path: Sphinx toctree
# ---------------------------------------------------------------------------

class TestSphinxToctree:
    def _index(self, tmp_path):
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "index.rst").write_text(textwrap.dedent("""
            Welcome
            =======

            Getting started.

            .. toctree::
               :maxdepth: 2
               :caption: Contents

               install
               usage
               advanced

        """).lstrip(), encoding="utf-8")
        (repo / "install.rst").write_text("Install\n=======\n\nbody\n", encoding="utf-8")
        (repo / "usage.rst").write_text("Usage\n=====\n\nbody\n", encoding="utf-8")
        (repo / "advanced.rst").write_text("Advanced\n========\n\nbody\n", encoding="utf-8")
        index_local(
            path=str(repo), name="sx",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

    def test_toctree_chain_resolves(self, tmp_path):
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path

        self._index(tmp_path)
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "sx")
        intro = next(s for s in idx.sections if s["doc_path"] == "index.rst" and s["level"] == 1)

        out = get_tutorial_path(repo="sx", section_id=intro["id"], storage_path=str(tmp_path))
        assert out["strategy"] == "sphinx_toctree"
        chain = [c["doc_path"] for c in out["chain"]]
        assert chain == ["index.rst", "install.rst", "usage.rst", "advanced.rst"]

    def test_toctree_skips_directive_options(self, tmp_path):
        # Same fixture; the parser must not treat ":maxdepth: 2" as an entry.
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path

        self._index(tmp_path)
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "sx")
        intro = next(s for s in idx.sections if s["doc_path"] == "index.rst" and s["level"] == 1)

        out = get_tutorial_path(repo="sx", section_id=intro["id"], storage_path=str(tmp_path))
        chain = [c["doc_path"] for c in out["chain"]]
        # No bogus entries.
        assert all(c.endswith(".rst") for c in chain)
        for c in chain:
            assert "maxdepth" not in c and "caption" not in c

    def test_toctree_handles_label_target_form(self, tmp_path):
        # "Display label <real-doc>" entries.
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "index.rst").write_text(textwrap.dedent("""
            Welcome
            =======

            .. toctree::

               Get Started <install>
               Run It <usage>
        """).lstrip(), encoding="utf-8")
        (repo / "install.rst").write_text("Install\n=======\n\nbody\n", encoding="utf-8")
        (repo / "usage.rst").write_text("Usage\n=====\n\nbody\n", encoding="utf-8")
        index_local(
            path=str(repo), name="sx2",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "sx2")
        intro = next(s for s in idx.sections if s["doc_path"] == "index.rst" and s["level"] == 1)
        out = get_tutorial_path(repo="sx2", section_id=intro["id"], storage_path=str(tmp_path))
        assert out["strategy"] == "sphinx_toctree"
        assert [c["doc_path"] for c in out["chain"]] == ["index.rst", "install.rst", "usage.rst"]


# ---------------------------------------------------------------------------
# Tutorial path: VuePress sidebar
# ---------------------------------------------------------------------------

class TestVuePressSidebar:
    def test_sidebar_chain_resolves(self, tmp_path):
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path

        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "README.md").write_text("# Home\n\nintro\n", encoding="utf-8")
        (repo / "install.md").write_text("# Install\n\nbody\n", encoding="utf-8")
        (repo / "usage.md").write_text("# Usage\n\nbody\n", encoding="utf-8")
        vp = repo / ".vuepress"
        vp.mkdir()
        (vp / "config.json").write_text(json.dumps({
            "themeConfig": {
                "sidebar": ["/", "/install/", "/usage/"]
            }
        }), encoding="utf-8")

        index_local(
            path=str(repo), name="vp",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "vp")
        home = next(s for s in idx.sections if s["doc_path"] == "README.md" and s["level"] == 1)
        out = get_tutorial_path(repo="vp", section_id=home["id"], storage_path=str(tmp_path))
        assert out["strategy"] == "vuepress_sidebar"
        chain = [c["doc_path"] for c in out["chain"]]
        # README is always the home; install/ and usage/ resolve to .md.
        assert "install.md" in chain
        assert "usage.md" in chain

    def test_sidebar_grouped_dict_known_limitation(self, tmp_path):
        """v1.29 known limitation: grouped-dict sidebar form
        ``[{text, children:[...]}]`` is not detectable because
        ``convert_json`` flattens nested objects into ``Item N``-style
        heading placeholders that lose the inner paths. This test pins
        the current behavior so the limitation is explicit; planned fix
        in a future minor when ``index_local`` persists ``source_root``
        and the raw JSON can be re-read at query time."""
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path

        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "intro.md").write_text("# Intro\n\nbody\n", encoding="utf-8")
        (repo / "step.md").write_text("# Step\n\nbody\n", encoding="utf-8")
        vp = repo / ".vuepress"
        vp.mkdir()
        (vp / "config.json").write_text(json.dumps({
            "themeConfig": {
                "sidebar": [
                    {"text": "Guide", "children": ["/intro", "/step"]}
                ]
            }
        }), encoding="utf-8")

        index_local(
            path=str(repo), name="vp2",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "vp2")
        intro = next(s for s in idx.sections if s["doc_path"] == "intro.md" and s["level"] == 1)
        out = get_tutorial_path(repo="vp2", section_id=intro["id"], storage_path=str(tmp_path))
        # Grouped form is not yet supported through the converted-markdown
        # cache; falls through to other strategies or returns "none".
        assert out["strategy"] in ("none", "ordered_filename", "vuepress_sidebar")


# ---------------------------------------------------------------------------
# OpenAPI 3.1 + Swagger 2.0 fixtures
# ---------------------------------------------------------------------------

REALWORLD_NEW = ("openapi31_realworld", "swagger20_realworld")


class TestNewFormatFixtures:
    @pytest.mark.parametrize("name", REALWORLD_NEW)
    def test_fixture_loads(self, name):
        path = ROOT / "benchmarks" / "replay" / "fixtures" / f"{name}.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == name
        assert len(data["queries"]) >= 3

    @pytest.mark.parametrize("name", REALWORLD_NEW)
    def test_meets_lock(self, name):
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from benchmarks.replay.run_replay import run_fixture

        report = run_fixture(name, baseline=None, gate=0.02, write_results=False)
        agg = report["aggregates"]
        assert agg["ndcg"] >= 0.98, (name, agg)
        assert agg["mrr"] >= 0.98, (name, agg)


# ---------------------------------------------------------------------------
# autotune flag on index_local
# ---------------------------------------------------------------------------

class TestAutotune:
    def test_autotune_off_by_default(self, tmp_path):
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "g.md").write_text("# Top\n\nbody\n", encoding="utf-8")
        out = index_local(
            path=str(repo), name="at",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        assert out.get("success") is True
        assert "autotune" not in out

    def test_autotune_with_telemetry_disabled_returns_hint(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_PERF_TELEMETRY", raising=False)
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "g.md").write_text("# Top\n\nbody\n", encoding="utf-8")
        out = index_local(
            path=str(repo), name="at2",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False, autotune=True,
        )
        assert out.get("autotune", {}).get("status") == "telemetry_disabled"

    def test_autotune_runs_when_telemetry_on_with_seeded_events(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.storage.token_tracker import record_ranking_event
        from jdocmunch_mcp.retrieval import tuning

        tuning.reset_cache()
        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")

        # Seed a strong "semantic helps" signal under repo "local/at3".
        for _ in range(40):
            record_ranking_event(
                repo="local/at3", tool="search_sections", query="q",
                mode="hybrid", semantic_used=True, semantic_weight=0.5,
                confidence=0.75, result_count=1, base_path=str(tmp_path),
            )
        for _ in range(40):
            record_ranking_event(
                repo="local/at3", tool="search_sections", query="q",
                mode="lexical", semantic_used=False, semantic_weight=0.5,
                confidence=0.55, result_count=1, base_path=str(tmp_path),
            )

        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "g.md").write_text("# Top\n\nbody\n", encoding="utf-8")
        out = index_local(
            path=str(repo), name="at3",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False, autotune=True,
        )
        # The tuner should have seen 80 events and decided to step up.
        results = out["autotune"]["results"]
        assert results
        assert any(r["status"] == "semantic_helps" for r in results)
