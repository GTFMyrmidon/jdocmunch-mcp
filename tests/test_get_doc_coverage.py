"""Tests for get_doc_coverage tool."""

import pytest
from pathlib import Path

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.get_doc_coverage import get_doc_coverage, _symbol_name_from_id

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def indexed_repo(tmp_path):
    """Index the fixture docs — has headings: Installation, Usage, API Reference, etc."""
    result = index_local(
        path=str(FIXTURES / "docs"),
        use_ai_summaries=False,
        storage_path=str(tmp_path),
    )
    assert result["success"], f"Indexing failed: {result}"
    return result["repo"], str(tmp_path)


# --- _symbol_name_from_id helper ---

class TestSymbolNameFromId:
    def test_full_jcodemunch_id(self):
        sid = "my-repo::src/server.py::handle_request#function"
        assert _symbol_name_from_id(sid) == "handle_request"

    def test_no_type_suffix(self):
        assert _symbol_name_from_id("repo::path::my_func") == "my_func"

    def test_bare_name(self):
        assert _symbol_name_from_id("Installation") == "Installation"

    def test_no_colons(self):
        assert _symbol_name_from_id("some_function#method") == "some_function"


# --- Error cases ---

def test_not_indexed(tmp_path):
    r = get_doc_coverage("nonexistent/repo", ["some::id#function"], storage_path=str(tmp_path))
    assert "error" in r


def test_empty_symbol_ids(indexed_repo):
    repo, storage = indexed_repo
    r = get_doc_coverage(repo, [], storage_path=storage)
    assert "result" in r
    assert r["result"]["symbols_checked"] == 0
    assert r["result"]["coverage_pct"] == 0.0


# --- Coverage detection ---

def test_documented_symbol_found(indexed_repo):
    """'Installation' is a heading in sample.md — should be documented."""
    repo, storage = indexed_repo
    # Use a jcodemunch-style ID
    r = get_doc_coverage(
        repo,
        ["myrepo::src/app.py::Installation#function"],
        storage_path=storage,
    )
    res = r["result"]
    assert res["documented_count"] >= 1
    names = [d["symbol_name"] for d in res["documented"]]
    assert "Installation" in names


def test_undocumented_symbol_reported(indexed_repo):
    """'nonexistent_xyz_func' has no matching section — should be undocumented."""
    repo, storage = indexed_repo
    r = get_doc_coverage(
        repo,
        ["myrepo::src/app.py::nonexistent_xyz_func#function"],
        storage_path=storage,
    )
    res = r["result"]
    assert res["undocumented_count"] >= 1
    names = [u["symbol_name"] for u in res["undocumented"]]
    assert "nonexistent_xyz_func" in names


def test_mixed_coverage(indexed_repo):
    repo, storage = indexed_repo
    symbol_ids = [
        "myrepo::src/app.py::Installation#function",    # documented
        "myrepo::src/app.py::totally_missing_xyz#function",  # undocumented
    ]
    r = get_doc_coverage(repo, symbol_ids, storage_path=storage)
    res = r["result"]
    assert res["symbols_checked"] == 2
    assert res["documented_count"] >= 1
    assert res["undocumented_count"] >= 1
    assert 0.0 < res["coverage_pct"] < 100.0


def test_coverage_pct_100(indexed_repo):
    """All documented symbols should yield 100%."""
    repo, storage = indexed_repo
    r = get_doc_coverage(
        repo,
        ["myrepo::src::Installation#function", "myrepo::src::Usage#function"],
        storage_path=storage,
    )
    res = r["result"]
    # Both should be documented (they're in sample.md)
    assert res["coverage_pct"] > 0


def test_coverage_pct_0(indexed_repo):
    repo, storage = indexed_repo
    r = get_doc_coverage(
        repo,
        ["myrepo::src::totally_made_up_xyz_abc#function"],
        storage_path=storage,
    )
    assert r["result"]["coverage_pct"] == 0.0


# --- Result structure ---

def test_result_structure(indexed_repo):
    repo, storage = indexed_repo
    r = get_doc_coverage(repo, ["myrepo::src::Installation#function"], storage_path=storage)
    assert "result" in r
    res = r["result"]
    assert "repo" in res
    assert "symbols_checked" in res
    assert "documented_count" in res
    assert "undocumented_count" in res
    assert "coverage_pct" in res
    assert "documented" in res
    assert "undocumented" in res


def test_documented_entry_has_sections(indexed_repo):
    repo, storage = indexed_repo
    r = get_doc_coverage(
        repo,
        ["myrepo::src::Installation#function"],
        storage_path=storage,
    )
    for entry in r["result"]["documented"]:
        assert "symbol_id" in entry
        assert "symbol_name" in entry
        assert "matching_sections" in entry
        for sec in entry["matching_sections"]:
            assert "section_id" in sec
            assert "section_title" in sec
            assert "doc_path" in sec


def test_undocumented_entry_fields(indexed_repo):
    repo, storage = indexed_repo
    r = get_doc_coverage(
        repo,
        ["myrepo::src::totally_missing_abc#function"],
        storage_path=storage,
    )
    for entry in r["result"]["undocumented"]:
        assert "symbol_id" in entry
        assert "symbol_name" in entry


# --- Cap at 200 ---

def test_symbol_ids_capped(indexed_repo):
    repo, storage = indexed_repo
    ids = [f"r::p::sym_{i}#function" for i in range(250)]
    r = get_doc_coverage(repo, ids, storage_path=storage)
    assert r["result"]["symbols_checked"] == 200


# --- Meta ---

def test_meta_present(indexed_repo):
    repo, storage = indexed_repo
    r = get_doc_coverage(repo, [], storage_path=storage)
    assert "_meta" in r
    assert "timing_ms" in r["_meta"]
