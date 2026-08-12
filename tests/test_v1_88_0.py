"""Tests for v1.88.0 — find_code_examples doc_path / path_glob scope filters (#73).

`find_code_examples` previously searched fenced blocks across the whole corpus
with only repo/query/lang/max_results, so a scoped docs audit could return code
evidence from outside the intended document or folder. This adds `doc_path` and
`path_glob` scope filters with the same filter-before-score contract as
`search_sections` (jdoc#32): scope is applied while collecting candidate blocks,
before BM25 scoring, so a single-document scope can't be starved by a
corpus-wide top-k cut. `_meta` echoes the active filters.
"""

from __future__ import annotations

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.find_code_examples import find_code_examples


def _setup(tmp_path):
    """Two folders, each with a python block containing the token 'authenticate'."""
    store = DocStore(base_path=str(tmp_path))
    api_doc = (
        "# Endpoints\n\n"
        "## Login\n\n"
        "```python\ndef authenticate(token):\n    return verify(token)\n```\n"
    )
    guide_doc = (
        "# Intro\n\n"
        "## Getting started\n\n"
        "```python\nclient.authenticate(api_key)  # guide example\n```\n"
    )
    sections = (
        parse_file(api_doc, "docs/api/endpoints.md", "local/r")
        + parse_file(guide_doc, "docs/guide/intro.md", "local/r")
    )
    store.save_index(
        owner="local", name="r", sections=sections,
        raw_files={"docs/api/endpoints.md": api_doc, "docs/guide/intro.md": guide_doc},
        doc_types={".md": 2},
    )
    return str(tmp_path)


def test_unscoped_search_spans_both_docs(tmp_path):
    storage = _setup(tmp_path)
    out = find_code_examples(repo="local/r", query="authenticate", storage_path=storage)
    paths = {r["doc_path"] for r in out["results"]}
    assert paths == {"docs/api/endpoints.md", "docs/guide/intro.md"}, out
    assert out["_meta"]["doc_path"] is None
    assert out["_meta"]["path_glob"] is None


def test_doc_path_limits_to_exact_document(tmp_path):
    storage = _setup(tmp_path)
    out = find_code_examples(
        repo="local/r", query="authenticate",
        doc_path="docs/api/endpoints.md", storage_path=storage,
    )
    assert out["results"], out
    assert all(r["doc_path"] == "docs/api/endpoints.md" for r in out["results"]), out
    assert out["_meta"]["doc_path"] == "docs/api/endpoints.md"


def test_path_glob_limits_to_matching_paths(tmp_path):
    storage = _setup(tmp_path)
    out = find_code_examples(
        repo="local/r", query="authenticate",
        path_glob="docs/api/**", storage_path=storage,
    )
    assert out["results"], out
    assert all(r["doc_path"].startswith("docs/api/") for r in out["results"]), out
    assert out["_meta"]["path_glob"] == "docs/api/**"


def test_path_glob_excludes_other_folder(tmp_path):
    """The guide folder must not leak into an api-scoped search."""
    storage = _setup(tmp_path)
    out = find_code_examples(
        repo="local/r", query="authenticate",
        path_glob="docs/guide/**", storage_path=storage,
    )
    paths = {r["doc_path"] for r in out["results"]}
    assert paths == {"docs/guide/intro.md"}, out


def test_scope_combines_with_lang(tmp_path):
    storage = _setup(tmp_path)
    out = find_code_examples(
        repo="local/r", query="authenticate", lang="python",
        path_glob="docs/api/**", storage_path=storage,
    )
    assert out["results"], out
    for r in out["results"]:
        assert r["lang"].lower() == "python"
        assert r["doc_path"].startswith("docs/api/")


def test_blocks_scanned_reflects_scoped_set(tmp_path):
    """blocks_scanned counts the scoped candidate set, not the whole corpus."""
    storage = _setup(tmp_path)
    full = find_code_examples(repo="local/r", query="authenticate", storage_path=storage)
    scoped = find_code_examples(
        repo="local/r", query="authenticate",
        doc_path="docs/api/endpoints.md", storage_path=storage,
    )
    assert scoped["_meta"]["blocks_scanned"] < full["_meta"]["blocks_scanned"]
    assert scoped["_meta"]["blocks_scanned"] == 1


def test_out_of_scope_path_glob_returns_zero_with_filters_echoed(tmp_path):
    storage = _setup(tmp_path)
    out = find_code_examples(
        repo="local/r", query="authenticate",
        path_glob="docs/nonexistent/**", storage_path=storage,
    )
    assert out["results"] == []
    assert out["_meta"]["reason"] == "no_code_blocks_for_filter"
    assert out["_meta"]["path_glob"] == "docs/nonexistent/**"
