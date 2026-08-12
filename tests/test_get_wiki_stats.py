"""Tests for get_wiki_stats tool."""

import pytest

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.get_wiki_stats import get_wiki_stats


def _index_and_get_repo(docs_path, tmp_path):
    storage = str(tmp_path / "store")
    result = index_local(path=docs_path, use_ai_summaries=False, storage_path=storage)
    assert result["success"], f"Indexing failed: {result}"
    return result["repo"], storage


@pytest.fixture
def wiki_with_links(tmp_path):
    """Wiki with a mix of linked and orphan pages."""
    docs = tmp_path / "wiki"
    docs.mkdir()

    (docs / "index.md").write_text(
        "# Index\n\n"
        "See [Guide](guide.md) and [API](api.md).\n"
    )
    (docs / "guide.md").write_text(
        "# Guide\n\n"
        "Setup instructions. See [API](api.md) for details.\n\n"
        "## Installation\n\nRun pip install.\n"
    )
    (docs / "api.md").write_text(
        "# API Reference\n\n"
        "Endpoints listed below.\n\n"
        "## Auth\n\n#auth #security\n\nLogin endpoint.\n\n"
        "## Users\n\n#users\n\nUser CRUD.\n"
    )
    (docs / "orphan.md").write_text(
        "# Orphan Page\n\n"
        "Nobody links here.\n"
    )
    return str(docs)


@pytest.fixture
def empty_wiki(tmp_path):
    """Wiki with a single page, no links."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "solo.md").write_text("# Solo\n\nJust one page.\n")
    return str(docs)


# --- Orphan detection ---

def test_orphan_detected(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_wiki_stats(repo, storage_path=storage)
    assert "result" in r
    assert r["result"]["orphan_page_count"] >= 1
    assert "orphan.md" in r["result"]["orphan_pages"]


def test_linked_pages_not_orphans(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_wiki_stats(repo, storage_path=storage)
    orphans = r["result"]["orphan_pages"]
    assert "guide.md" not in orphans
    assert "api.md" not in orphans


# --- Most linked ---

def test_most_linked(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_wiki_stats(repo, storage_path=storage)
    most = r["result"]["most_linked"]
    assert len(most) >= 1
    # api.md should be most linked (referenced by both index.md and guide.md)
    top = most[0]
    assert top["doc_path"] == "api.md"
    assert top["inbound_links"] >= 2


# --- Tag distribution ---

def test_tag_distribution(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_wiki_stats(repo, storage_path=storage)
    tags = r["result"]["tag_distribution"]
    assert "auth" in tags
    assert "security" in tags
    assert "users" in tags


# --- Sections per doc ---

def test_sections_per_doc(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_wiki_stats(repo, storage_path=storage)
    spd = r["result"]["sections_per_doc"]
    assert spd["min"] >= 1
    assert spd["max"] >= spd["min"]
    assert spd["avg"] > 0


# --- Edge cases ---

def test_single_page_wiki(tmp_path, empty_wiki):
    repo, storage = _index_and_get_repo(empty_wiki, tmp_path)
    r = get_wiki_stats(repo, storage_path=storage)
    assert r["result"]["page_count"] == 1
    assert r["result"]["orphan_page_count"] == 1
    assert r["result"]["total_internal_links"] == 0


# --- Error handling ---

def test_repo_not_found(tmp_path):
    r = get_wiki_stats("nonexistent/repo", storage_path=str(tmp_path))
    assert "error" in r


# --- Result structure ---

def test_result_structure(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_wiki_stats(repo, storage_path=storage)
    res = r["result"]
    assert "repo" in res
    assert "page_count" in res
    assert "section_count" in res
    assert "total_internal_links" in res
    assert "orphan_page_count" in res
    assert isinstance(res["orphan_pages"], list)
    assert isinstance(res["most_linked"], list)
    assert isinstance(res["tag_distribution"], dict)
    assert "tag_count" in res
    assert "sections_per_doc" in res


def test_meta_timing(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_wiki_stats(repo, storage_path=storage)
    assert "_meta" in r
    assert r["_meta"]["timing_ms"] >= 0
