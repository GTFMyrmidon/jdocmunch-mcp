"""Regression tests for the v1.10.0 critical bug fixes (B1–B7)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.parser.markdown_parser import parse_markdown
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.storage.doc_store import (
    _INDEX_CACHE,
    _INDEX_CACHE_MAXSIZE,
    DocIndex,
)


# ---------------------------------------------------------------------------
# B6 — _INDEX_CACHE bounded to maxsize=8 with LRU eviction
# ---------------------------------------------------------------------------

class TestB6IndexCacheBounded:
    def test_cache_is_ordered_dict(self):
        from collections import OrderedDict
        assert isinstance(_INDEX_CACHE, OrderedDict)

    def test_cache_evicts_oldest_when_overflowing(self, tmp_path):
        _INDEX_CACHE.clear()
        store = DocStore(base_path=str(tmp_path))

        # Save N+2 distinct indices, where N = _INDEX_CACHE_MAXSIZE (8)
        n = _INDEX_CACHE_MAXSIZE + 2
        sections = parse_file("# h\n\ntext", "r.md", "local/x")
        for i in range(n):
            store.save_index(
                owner="local",
                name=f"repo{i}",
                sections=sections,
                raw_files={"r.md": "# h\n\ntext"},
                doc_types={".md": 1},
            )

        # Load each index — populates cache.
        for i in range(n):
            store.load_index("local", f"repo{i}")

        assert len(_INDEX_CACHE) == _INDEX_CACHE_MAXSIZE, (
            f"cache should be capped at {_INDEX_CACHE_MAXSIZE}, found {len(_INDEX_CACHE)}"
        )

    def test_lru_promotes_recently_used(self, tmp_path):
        _INDEX_CACHE.clear()
        store = DocStore(base_path=str(tmp_path))
        sections = parse_file("# h\n\ntext", "r.md", "local/x")

        # Fill cache exactly to capacity
        for i in range(_INDEX_CACHE_MAXSIZE):
            store.save_index(
                owner="local",
                name=f"repo{i}",
                sections=sections,
                raw_files={"r.md": "# h\n\ntext"},
                doc_types={".md": 1},
            )
            store.load_index("local", f"repo{i}")

        # Touch repo0 (oldest) — moves it to MRU end
        store.load_index("local", "repo0")

        # Add one more, forcing eviction of next-oldest (repo1)
        store.save_index(
            owner="local",
            name="repo_extra",
            sections=sections,
            raw_files={"r.md": "# h\n\ntext"},
            doc_types={".md": 1},
        )
        store.load_index("local", "repo_extra")

        keys = [k[0] for k in _INDEX_CACHE.keys()]
        # repo0 should still be in cache (we promoted it); repo1 should be gone.
        assert any("repo0.json" in k for k in keys), "promoted entry should survive eviction"
        assert all("repo1.json" not in k for k in keys), "next-oldest entry should be evicted"


# ---------------------------------------------------------------------------
# B7 — Embedding provider cached across queries
# ---------------------------------------------------------------------------

class TestB7ProviderCached:
    def test_provider_cached_across_calls(self, monkeypatch):
        """_get_provider() must return the same instance on repeated calls
        when env is unchanged."""
        from jdocmunch_mcp.embeddings import provider as p

        # Force a deterministic provider name with a stub class.
        instances = []

        class _FakeProvider:
            def __init__(self):
                instances.append(self)

            def embed_texts(self, texts, task_type="retrieval_document"):
                return [[0.1] * 4 for _ in texts]

        # Reset cache and stub get_provider_name to a known value.
        p._reset_provider_cache()
        monkeypatch.setattr(p, "get_provider_name", lambda: "fake")
        monkeypatch.setitem(p._PROVIDER_FACTORIES, "fake", _FakeProvider)

        a = p._get_provider()
        b = p._get_provider()
        c = p._get_provider()
        assert a is b is c, "provider should be cached"
        assert len(instances) == 1, "provider must instantiate only once"

    def test_provider_invalidates_on_env_change(self, monkeypatch):
        from jdocmunch_mcp.embeddings import provider as p

        class _FakeA:
            def __init__(self):
                pass

            def embed_texts(self, texts, task_type="retrieval_document"):
                return [[1.0]] * len(texts)

        class _FakeB:
            def __init__(self):
                pass

            def embed_texts(self, texts, task_type="retrieval_document"):
                return [[2.0]] * len(texts)

        p._reset_provider_cache()
        monkeypatch.setitem(p._PROVIDER_FACTORIES, "fake_a", _FakeA)
        monkeypatch.setitem(p._PROVIDER_FACTORIES, "fake_b", _FakeB)

        monkeypatch.setattr(p, "get_provider_name", lambda: "fake_a")
        first = p._get_provider()

        monkeypatch.setattr(p, "get_provider_name", lambda: "fake_b")
        second = p._get_provider()

        assert first is not second
        assert isinstance(first, _FakeA)
        assert isinstance(second, _FakeB)


# ---------------------------------------------------------------------------
# B5 — Anchor normalization no longer collides foo-bar with foobar
# ---------------------------------------------------------------------------

class TestB5AnchorCollision:
    def test_distinct_slugs_do_not_collide(self, tmp_path):
        """A link to '#foo-bar' must not match a section whose slug is 'foobar'."""
        from jdocmunch_mcp.tools.get_broken_links import get_broken_links

        store = DocStore(base_path=str(tmp_path))

        content = (
            "# Foobar\n\n"
            "See [the foo bar section](#foo-bar) for details.\n\n"
            "## Other\n\nUnrelated.\n"
        )
        sections = parse_file(content, "guide.md", "local/repo")
        store.save_index(
            owner="local",
            name="repo",
            sections=sections,
            raw_files={"guide.md": content},
            doc_types={".md": 1},
        )

        result = get_broken_links(repo="local/repo", storage_path=str(tmp_path))
        broken = result["result"]["broken_links"]
        targets = {b["target"] for b in broken}
        assert "#foo-bar" in targets, (
            "anchor '#foo-bar' must be flagged as broken; "
            f"got broken={broken}"
        )

    def test_exact_slug_match_still_resolves(self, tmp_path):
        """An anchor that matches an actual slug must NOT be flagged broken."""
        from jdocmunch_mcp.tools.get_broken_links import get_broken_links

        store = DocStore(base_path=str(tmp_path))
        content = (
            "# Top\n\n[link](#foo-bar)\n\n## Foo Bar\n\nbody\n"
        )
        sections = parse_file(content, "doc.md", "local/repo")
        store.save_index(
            owner="local",
            name="repo",
            sections=sections,
            raw_files={"doc.md": content},
            doc_types={".md": 1},
        )

        result = get_broken_links(repo="local/repo", storage_path=str(tmp_path))
        broken = result["result"]["broken_links"]
        assert not any(b["target"] == "#foo-bar" for b in broken), (
            f"anchor '#foo-bar' should resolve to slug 'foo-bar'; got {broken}"
        )


# ---------------------------------------------------------------------------
# B4 — INDEX_VERSION parity between code and CLAUDE.md
# ---------------------------------------------------------------------------

class TestB4DocsParity:
    def test_claude_md_index_version_matches_code(self):
        from jdocmunch_mcp.storage.doc_store import INDEX_VERSION

        claude_md = Path(__file__).parent.parent / "CLAUDE.md"
        if not claude_md.exists():
            pytest.skip("CLAUDE.md not present")
        text = claude_md.read_text(encoding="utf-8")
        # Look for any "INDEX_VERSION=N" pattern; assert it matches the code.
        import re
        matches = re.findall(r"INDEX_VERSION\s*=\s*(\d+)", text)
        assert matches, "CLAUDE.md should mention INDEX_VERSION"
        for m in matches:
            assert int(m) == INDEX_VERSION, (
                f"CLAUDE.md says INDEX_VERSION={m}, code says {INDEX_VERSION}"
            )


# ---------------------------------------------------------------------------
# B3 — Setext detector no longer fires on tables / horizontal rules
# ---------------------------------------------------------------------------

class TestB3SetextGuards:
    def test_table_separator_not_a_heading(self):
        content = (
            "# Real\n\n"
            "| col1 | col2 |\n"
            "| --- | --- |\n"
            "| a | b |\n"
        )
        sections = parse_markdown(content, "t.md", "local/r")
        titles = [s.title for s in sections]
        # Only one real heading: "Real". The previous bug parsed "| col1 | col2 |"
        # as a setext H2 because '| --- | --- |' looked like the underline.
        assert "| col1 | col2 |" not in titles, (
            f"table header line should not be promoted to a heading; got {titles}"
        )

    def test_dashed_hr_not_a_heading(self):
        content = (
            "# Real\n\n"
            "Some intro paragraph.\n"
            "\n"
            "---\n"
            "\n"
            "More text after horizontal rule.\n"
        )
        sections = parse_markdown(content, "t.md", "local/r")
        titles = [s.title for s in sections]
        assert "Some intro paragraph." not in titles
        assert titles.count("Real") == 1


# ---------------------------------------------------------------------------
# B2 — Code-fence-aware splitter
# ---------------------------------------------------------------------------

class TestB2CodeFenceAware:
    def test_atx_inside_fence_is_not_a_heading(self):
        content = (
            "# Real Heading\n\n"
            "Example output:\n\n"
            "```\n"
            "# This is a comment, not a heading\n"
            "## Neither is this\n"
            "```\n\n"
            "## Real Subheading\n\nbody\n"
        )
        sections = parse_markdown(content, "t.md", "local/r")
        titles = [s.title for s in sections]
        assert "This is a comment, not a heading" not in titles
        assert "Neither is this" not in titles
        # Should be exactly: top-level "Real Heading" + "Real Subheading"
        # plus possibly a level-0 root if there's pre-heading content (none here).
        non_root = [s for s in sections if s.level > 0]
        assert {s.title for s in non_root} == {"Real Heading", "Real Subheading"}, (
            f"unexpected sections: {titles}"
        )

    def test_tilde_fence_also_skipped(self):
        content = (
            "# Top\n\n"
            "~~~\n"
            "# fake heading\n"
            "~~~\n\n"
            "## Sub\n\nbody\n"
        )
        sections = parse_markdown(content, "t.md", "local/r")
        titles = {s.title for s in sections if s.level > 0}
        assert "fake heading" not in titles
        assert titles == {"Top", "Sub"}

    def test_indented_code_block_skipped(self):
        content = (
            "# Top\n\n"
            "Example:\n\n"
            "    # indented code, not a heading\n"
            "    ## also not\n"
            "\n"
            "## Sub\n\nbody\n"
        )
        sections = parse_markdown(content, "t.md", "local/r")
        titles = {s.title for s in sections if s.level > 0}
        assert "indented code, not a heading" not in titles
        assert "also not" not in titles


# ---------------------------------------------------------------------------
# B1 — Lexical content channel works on loaded index
# ---------------------------------------------------------------------------

class TestB1ContentChannelRestored:
    def test_content_match_outranks_titleless_match(self, tmp_path):
        """Two sections, both titled blandly. Section A's BODY contains the rare
        query token; section B's body does not. After save+load, A must rank above B.
        Pre-fix this failed because content was always empty after load."""
        from jdocmunch_mcp.tools.search_sections import search_sections

        content = (
            "# Doc\n\n"
            "## Alpha\n\n"
            "This section discusses xyzzyfrobnicate in detail.\n\n"
            "## Beta\n\n"
            "This section is about something completely different.\n"
        )

        store = DocStore(base_path=str(tmp_path))
        sections = parse_file(content, "doc.md", "local/repo")
        store.save_index(
            owner="local",
            name="repo",
            sections=sections,
            raw_files={"doc.md": content},
            doc_types={".md": 1},
        )

        result = search_sections(
            repo="local/repo",
            query="xyzzyfrobnicate",
            storage_path=str(tmp_path),
            semantic=False,
        )
        results = result["results"]
        assert results, f"expected at least one result; got {result}"
        top = results[0]
        assert "Alpha" in top["title"], (
            f"section with body match must rank #1; got top={top.get('title')!r}, all={[r.get('title') for r in results]}"
        )
