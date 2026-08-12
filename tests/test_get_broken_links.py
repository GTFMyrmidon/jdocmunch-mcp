"""Tests for get_broken_links tool."""

import pytest
from pathlib import Path

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.get_broken_links import get_broken_links


@pytest.fixture
def docs_with_good_links(tmp_path):
    """Docs folder where all internal links resolve correctly."""
    docs = tmp_path / "docs"
    docs.mkdir()

    (docs / "index.md").write_text(
        "# Index\n\n"
        "See [Installation](install.md) for setup.\n"
        "Also see the [Config section](install.md#configuration).\n"
    )
    (docs / "install.md").write_text(
        "# Installation\n\n"
        "Run pip install.\n\n"
        "## Configuration\n\n"
        "Set your env vars.\n"
    )
    return str(docs)


@pytest.fixture
def docs_with_broken_file_link(tmp_path):
    """A doc that references a file that doesn't exist."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(
        "# Index\n\n"
        "See [Missing](missing-file.md) for details.\n"
    )
    return str(docs)


@pytest.fixture
def docs_with_broken_anchor(tmp_path):
    """A doc with a cross-file anchor that doesn't exist in the target."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(
        "# Index\n\n"
        "See [Bad anchor](install.md#no-such-section) for details.\n"
    )
    (docs / "install.md").write_text(
        "# Installation\n\n"
        "Run pip install.\n"
    )
    return str(docs)


@pytest.fixture
def docs_with_external_links(tmp_path):
    """A doc with only external links — should report zero broken."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.md").write_text(
        "# Index\n\n"
        "Visit [PyPI](https://pypi.org) or [GitHub](http://github.com).\n"
        "Also email [support](mailto:support@example.com).\n"
    )
    return str(docs)


@pytest.fixture
def docs_with_anchor_only(tmp_path):
    """A doc with anchor-only link to a heading that exists in the same doc."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "# Guide\n\n"
        "See [Installation](#installation) below.\n\n"
        "## Installation\n\n"
        "Run pip install.\n"
    )
    return str(docs)


def _index_and_get_repo(docs_path, tmp_path):
    storage = str(tmp_path / "store")
    result = index_local(path=docs_path, use_ai_summaries=False, storage_path=storage)
    assert result["success"], f"Indexing failed: {result}"
    return result["repo"], storage


# --- Error cases ---

def test_not_indexed(tmp_path):
    r = get_broken_links("nonexistent/repo", storage_path=str(tmp_path))
    assert "error" in r


# --- Good links: zero broken ---

def test_good_links_no_broken(tmp_path, docs_with_good_links):
    repo, storage = _index_and_get_repo(docs_with_good_links, tmp_path)
    r = get_broken_links(repo, storage_path=storage)
    assert "result" in r
    assert r["result"]["broken_link_count"] == 0
    assert r["result"]["broken_links"] == []


def test_external_links_skipped(tmp_path, docs_with_external_links):
    repo, storage = _index_and_get_repo(docs_with_external_links, tmp_path)
    r = get_broken_links(repo, storage_path=storage)
    assert r["result"]["broken_link_count"] == 0


def test_anchor_only_good(tmp_path, docs_with_anchor_only):
    repo, storage = _index_and_get_repo(docs_with_anchor_only, tmp_path)
    r = get_broken_links(repo, storage_path=storage)
    # "Installation" heading exists in the same doc
    assert r["result"]["broken_link_count"] == 0


# --- Broken file link ---

def test_broken_file_link_detected(tmp_path, docs_with_broken_file_link):
    repo, storage = _index_and_get_repo(docs_with_broken_file_link, tmp_path)
    r = get_broken_links(repo, storage_path=storage)
    assert r["result"]["broken_link_count"] >= 1
    reasons = [b["reason"] for b in r["result"]["broken_links"]]
    assert "file_not_found" in reasons


def test_broken_file_link_target(tmp_path, docs_with_broken_file_link):
    repo, storage = _index_and_get_repo(docs_with_broken_file_link, tmp_path)
    r = get_broken_links(repo, storage_path=storage)
    targets = [b["target"] for b in r["result"]["broken_links"]]
    assert any("missing-file.md" in t for t in targets)


# --- Broken anchor (cross-file) ---

def test_broken_cross_file_anchor(tmp_path, docs_with_broken_anchor):
    repo, storage = _index_and_get_repo(docs_with_broken_anchor, tmp_path)
    r = get_broken_links(repo, storage_path=storage)
    assert r["result"]["broken_link_count"] >= 1
    reasons = [b["reason"] for b in r["result"]["broken_links"]]
    assert "section_not_found" in reasons


# --- Result structure ---

def test_result_structure(tmp_path, docs_with_good_links):
    repo, storage = _index_and_get_repo(docs_with_good_links, tmp_path)
    r = get_broken_links(repo, storage_path=storage)
    assert "result" in r
    res = r["result"]
    assert "repo" in res
    assert "docs_scanned" in res
    assert "sections_scanned" in res
    assert "broken_link_count" in res
    assert "broken_links" in res
    assert isinstance(res["broken_links"], list)


def test_broken_link_entry_has_required_fields(tmp_path, docs_with_broken_file_link):
    repo, storage = _index_and_get_repo(docs_with_broken_file_link, tmp_path)
    r = get_broken_links(repo, storage_path=storage)
    for entry in r["result"]["broken_links"]:
        assert "source_file" in entry
        assert "source_section" in entry
        assert "target" in entry
        assert "reason" in entry
        assert entry["reason"] in ("file_not_found", "section_not_found", "anchor_not_found")


def test_meta_timing(tmp_path, docs_with_good_links):
    repo, storage = _index_and_get_repo(docs_with_good_links, tmp_path)
    r = get_broken_links(repo, storage_path=storage)
    assert "_meta" in r
    assert r["_meta"]["timing_ms"] >= 0
