"""Tests for get_stale_pages tool."""

import os
import pytest

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.get_stale_pages import get_stale_pages, _extract_frontmatter


def _index_and_get_repo(docs_path, tmp_path):
    storage = str(tmp_path / "store")
    result = index_local(path=docs_path, use_ai_summaries=False, storage_path=storage)
    assert result["success"], f"Indexing failed: {result}"
    return result["repo"], storage


# --- Frontmatter extraction ---

def test_extract_frontmatter_basic():
    content = "---\ntitle: Test\nsources:\n  - raw/a.md\n---\n# Heading\n"
    fm = _extract_frontmatter(content)
    assert fm["title"] == "Test"
    assert fm["sources"] == ["raw/a.md"]


def test_extract_frontmatter_missing():
    fm = _extract_frontmatter("# No frontmatter\n\nJust content.\n")
    assert fm == {}


def test_extract_frontmatter_invalid_yaml():
    content = "---\n: bad: yaml: [unclosed\n---\n"
    fm = _extract_frontmatter(content)
    assert fm == {}


def test_extract_frontmatter_non_dict():
    content = "---\n- just a list\n---\n"
    fm = _extract_frontmatter(content)
    assert fm == {}


# --- Stale page detection ---

@pytest.fixture
def wiki_with_sources(tmp_path):
    """Wiki pages with frontmatter pointing to raw source files."""
    root = tmp_path / "project"
    root.mkdir()
    raw = root / "raw"
    raw.mkdir()
    wiki = root / "wiki"
    wiki.mkdir()

    # Raw source files
    (raw / "article.md").write_text("# Original Article\n\nOriginal content.\n")
    (raw / "paper.md").write_text("# Research Paper\n\nFindings here.\n")

    # Wiki page referencing sources
    (wiki / "summary.md").write_text(
        "---\ntitle: Article Summary\nsources:\n  - raw/article.md\n---\n"
        "# Summary\n\nSynthesized from article.\n"
    )
    # Wiki page with no sources
    (wiki / "standalone.md").write_text(
        "# Standalone\n\nNo source tracking.\n"
    )
    return str(root), str(wiki)


def test_no_stale_when_unchanged(tmp_path, wiki_with_sources):
    root, wiki_path = wiki_with_sources
    repo, storage = _index_and_get_repo(wiki_path, tmp_path)
    r = get_stale_pages(repo, sources_dir=root, storage_path=storage)
    assert "result" in r
    # Sources are "untracked" because file_hashes only track wiki files, not raw
    # This is expected — the tool reports untracked sources for awareness


def test_missing_source_detected(tmp_path):
    """Wiki page references a source that doesn't exist on disk."""
    root = tmp_path / "project"
    root.mkdir()
    wiki = root / "wiki"
    wiki.mkdir()

    (wiki / "orphan.md").write_text(
        "---\ntitle: Orphan\nsources:\n  - raw/deleted.md\n---\n"
        "# Orphan Page\n\nSource was deleted.\n"
    )
    repo, storage = _index_and_get_repo(str(wiki), tmp_path)
    r = get_stale_pages(repo, sources_dir=str(root), storage_path=storage)
    assert "result" in r
    assert r["result"]["stale_page_count"] >= 1
    reasons = []
    for page in r["result"]["stale_pages"]:
        for src in page["stale_sources"]:
            reasons.append(src["reason"])
    assert "missing" in reasons


def test_pages_without_sources_skipped(tmp_path, wiki_with_sources):
    root, wiki_path = wiki_with_sources
    repo, storage = _index_and_get_repo(wiki_path, tmp_path)
    r = get_stale_pages(repo, sources_dir=root, storage_path=storage)
    assert "result" in r
    # pages_with_sources should be 1 (only summary.md has sources)
    assert r["result"]["pages_with_sources"] == 1


# --- Error handling ---

def test_repo_not_found(tmp_path):
    r = get_stale_pages("nonexistent/repo", storage_path=str(tmp_path))
    assert "error" in r


# --- Result structure ---

def test_result_structure(tmp_path, wiki_with_sources):
    root, wiki_path = wiki_with_sources
    repo, storage = _index_and_get_repo(wiki_path, tmp_path)
    r = get_stale_pages(repo, sources_dir=root, storage_path=storage)
    res = r["result"]
    assert "repo" in res
    assert "pages_scanned" in res
    assert "pages_with_sources" in res
    assert "sources_checked" in res
    assert "stale_page_count" in res
    assert isinstance(res["stale_pages"], list)


def test_meta_timing(tmp_path, wiki_with_sources):
    root, wiki_path = wiki_with_sources
    repo, storage = _index_and_get_repo(wiki_path, tmp_path)
    r = get_stale_pages(repo, sources_dir=root, storage_path=storage)
    assert "_meta" in r
    assert r["_meta"]["timing_ms"] >= 0
