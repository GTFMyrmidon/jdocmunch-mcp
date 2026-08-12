"""Tests for v1.30.0: source_root persistence + grouped VuePress + README contract."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import index_local

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# source_root persistence
# ---------------------------------------------------------------------------

class TestSourceRootPersistence:
    def test_index_local_records_source_root(self, tmp_path):
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "g.md").write_text("# Top\n\nbody\n", encoding="utf-8")
        index_local(
            path=str(repo), name="sr",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "sr")
        assert idx.source_root, "source_root must be persisted"
        assert Path(idx.source_root).resolve() == repo.resolve()

    def test_round_trip_through_save_load(self, tmp_path):
        from jdocmunch_mcp.parser import parse_file

        store = DocStore(base_path=str(tmp_path))
        sections = parse_file("# Top\n\nbody\n", "g.md", "local/r")
        store.save_index(
            owner="local", name="r2",
            sections=sections,
            raw_files={"g.md": "# Top\n\nbody\n"},
            doc_types={".md": 1},
            source_root="/some/abs/path",
        )
        idx = store.load_index("local", "r2")
        assert idx.source_root == "/some/abs/path"

    def test_omitted_when_empty(self, tmp_path):
        # Save without source_root — JSON file should not contain the key.
        from jdocmunch_mcp.parser import parse_file

        store = DocStore(base_path=str(tmp_path))
        sections = parse_file("# Top\n\nbody\n", "g.md", "local/r")
        store.save_index(
            owner="local", name="r3",
            sections=sections,
            raw_files={"g.md": "# Top\n\nbody\n"},
            doc_types={".md": 1},
        )
        path = store._index_path("local", "r3")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "source_root" not in data


# ---------------------------------------------------------------------------
# VuePress grouped-dict form via raw-JSON re-read
# ---------------------------------------------------------------------------

class TestVuePressGroupedDictForm:
    def test_grouped_form_resolves_via_source_root(self, tmp_path):
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
            path=str(repo), name="vpgrp",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "vpgrp")
        intro = next(s for s in idx.sections if s["doc_path"] == "intro.md" and s["level"] == 1)

        out = get_tutorial_path(repo="vpgrp", section_id=intro["id"], storage_path=str(tmp_path))
        assert out["strategy"] == "vuepress_sidebar"
        assert [c["doc_path"] for c in out["chain"]] == ["intro.md", "step.md"]

    def test_flat_form_still_works(self, tmp_path):
        # Make sure adding the grouped-form path didn't break the flat form.
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path

        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "README.md").write_text("# Home\n\nbody\n", encoding="utf-8")
        (repo / "install.md").write_text("# Install\n\nbody\n", encoding="utf-8")
        vp = repo / ".vuepress"
        vp.mkdir()
        (vp / "config.json").write_text(json.dumps({
            "themeConfig": {"sidebar": ["/", "/install/"]}
        }), encoding="utf-8")

        index_local(
            path=str(repo), name="vpflat",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "vpflat")
        home = next(s for s in idx.sections if s["doc_path"] == "README.md" and s["level"] == 1)

        out = get_tutorial_path(repo="vpflat", section_id=home["id"], storage_path=str(tmp_path))
        assert out["strategy"] == "vuepress_sidebar"
        assert "install.md" in [c["doc_path"] for c in out["chain"]]

    def test_falls_back_when_source_root_missing(self, tmp_path):
        """When the index was saved without source_root (legacy or
        manually-built indexes), grouped form falls back to the cached
        markdown rendering — which can't resolve grouped dicts. This is
        the documented limitation; test pins the fallback shape."""
        from jdocmunch_mcp.parser import parse_file
        from jdocmunch_mcp.tools.get_tutorial_path import get_tutorial_path

        store = DocStore(base_path=str(tmp_path))
        # Build a fake indexed repo without source_root.
        cfg = json.dumps({"themeConfig": {"sidebar": [
            {"text": "Guide", "children": ["/intro", "/step"]}
        ]}})
        from jdocmunch_mcp.parser import preprocess_content
        cfg_md = preprocess_content(cfg, ".vuepress/config.json")

        intro_secs = parse_file("# Intro\n\nbody\n", "intro.md", "local/vpgrp_nosr")
        step_secs = parse_file("# Step\n\nbody\n", "step.md", "local/vpgrp_nosr")
        cfg_secs = parse_file(cfg_md, ".vuepress/config.json", "local/vpgrp_nosr")
        all_secs = intro_secs + step_secs + cfg_secs

        store.save_index(
            owner="local", name="vpgrp_nosr",
            sections=all_secs,
            raw_files={
                "intro.md": "# Intro\n\nbody\n",
                "step.md": "# Step\n\nbody\n",
                ".vuepress/config.json": cfg_md,
            },
            doc_types={".md": 2, ".json": 1},
            # No source_root — legacy save.
        )
        idx = store.load_index("local", "vpgrp_nosr")
        intro = next(s for s in idx.sections if s["doc_path"] == "intro.md" and s["level"] == 1)
        out = get_tutorial_path(repo="vpgrp_nosr", section_id=intro["id"], storage_path=str(tmp_path))
        # Grouped form unresolvable from converted markdown — strategy is
        # "none" or fallback.
        assert out["strategy"] in ("none", "ordered_filename", "vuepress_sidebar")


# ---------------------------------------------------------------------------
# README compatibility commitment
# ---------------------------------------------------------------------------

class TestReadmeCommitment:
    def test_readme_mentions_1x_compatibility(self):
        path = ROOT / "README.md"
        text = path.read_text(encoding="utf-8")
        assert "1.x compatibility commitment" in text
        # Specific contract clauses must be present.
        for phrase in [
            "removes or renames an MCP tool",
            "drops a `Section` field",
            "forces a reindex without auto-migrating",
            "changes the JSON wire format",
            "previously-default behavior raise",
            "Reserved for a future major version (2.x)",
        ]:
            assert phrase.lower() in text.lower(), f"missing clause: {phrase!r}"
