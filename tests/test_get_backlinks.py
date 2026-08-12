"""Tests for get_backlinks tool."""

import pytest

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.get_backlinks import get_backlinks


def _index_and_get_repo(docs_path, tmp_path):
    storage = str(tmp_path / "store")
    result = index_local(path=docs_path, use_ai_summaries=False, storage_path=storage)
    assert result["success"], f"Indexing failed: {result}"
    return result["repo"], storage


@pytest.fixture
def wiki_with_links(tmp_path):
    """Wiki where multiple pages link to a shared target."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "overview.md").write_text(
        "# Overview\n\n"
        "See [Auth](concepts/auth.md) for authentication.\n"
        "Also [Users](concepts/users.md).\n"
    )
    concepts = docs / "concepts"
    concepts.mkdir()
    (concepts / "auth.md").write_text(
        "# Authentication\n\n"
        "Auth handles login flows.\n"
        "Related: [Users](users.md)\n"
    )
    (concepts / "users.md").write_text(
        "# Users\n\n"
        "User management. See [Auth](auth.md) for login.\n"
    )
    return str(docs)


@pytest.fixture
def wiki_no_links(tmp_path):
    """Wiki with no cross-references."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "page-a.md").write_text("# Page A\n\nStandalone content.\n")
    (docs / "page-b.md").write_text("# Page B\n\nMore standalone content.\n")
    return str(docs)


# --- Basic functionality ---

def test_finds_backlinks(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_backlinks(repo, "concepts/auth.md", storage_path=storage)
    assert "result" in r
    assert r["result"]["backlink_count"] >= 1
    source_files = [bl["source_file"] for bl in r["result"]["backlinks"]]
    assert "overview.md" in source_files


def test_mutual_links(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    # auth.md links to users.md
    r = get_backlinks(repo, "concepts/users.md", storage_path=storage)
    assert r["result"]["backlink_count"] >= 1
    source_files = [bl["source_file"] for bl in r["result"]["backlinks"]]
    assert any("auth.md" in f for f in source_files)


def test_no_backlinks(tmp_path, wiki_no_links):
    repo, storage = _index_and_get_repo(wiki_no_links, tmp_path)
    r = get_backlinks(repo, "page-a.md", storage_path=storage)
    assert r["result"]["backlink_count"] == 0
    assert r["result"]["backlinks"] == []


def test_nonexistent_target(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_backlinks(repo, "does-not-exist.md", storage_path=storage)
    assert r["result"]["backlink_count"] == 0


# --- Error handling ---

def test_repo_not_found(tmp_path):
    r = get_backlinks("nonexistent/repo", "anything.md", storage_path=str(tmp_path))
    assert "error" in r


# --- Result structure ---

def test_result_structure(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_backlinks(repo, "concepts/auth.md", storage_path=storage)
    res = r["result"]
    assert "repo" in res
    assert "target" in res
    assert "backlink_count" in res
    assert "source_file_count" in res
    assert isinstance(res["backlinks"], list)
    for bl in res["backlinks"]:
        assert "source_file" in bl
        assert "source_section" in bl
        assert "source_section_id" in bl
        assert "link" in bl


def test_meta_timing(tmp_path, wiki_with_links):
    repo, storage = _index_and_get_repo(wiki_with_links, tmp_path)
    r = get_backlinks(repo, "concepts/auth.md", storage_path=storage)
    assert "_meta" in r
    assert r["_meta"]["timing_ms"] >= 0
