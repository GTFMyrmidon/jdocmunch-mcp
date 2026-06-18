"""Tests for v1.86.0 — jdoc#67 (local/ name round-trip) + jdoc#68 (cross-suite
repo identity: typed doc_list_repos fields, bridge invalid-code-handle
diagnostics, and resolve_related_code_repos)."""

import pytest

from jdocmunch_mcp.tools.index_local import index_local, normalize_local_index_name
from jdocmunch_mcp.tools.list_repos import list_repos
from jdocmunch_mcp.tools import link_code_to_symbols as lcts_mod
from jdocmunch_mcp.tools import get_undocumented_symbols as gus_mod
from jdocmunch_mcp.tools import resolve_related_code_repos as rrcr_mod
from jdocmunch_mcp.tools.link_code_to_symbols import link_code_to_symbols
from jdocmunch_mcp.tools.get_undocumented_symbols import get_undocumented_symbols
from jdocmunch_mcp.tools.resolve_related_code_repos import resolve_related_code_repos


def _make_docs_index(tmp_path, store_dir, name="example-docs", incremental=True):
    corpus = tmp_path / "corpus"
    corpus.mkdir(exist_ok=True)
    (corpus / "README.md").write_text(
        "# Title\n\nUses `DocumentedClass` and `auth_helper`.\n", encoding="utf-8"
    )
    return index_local(
        path=str(corpus),
        name=name,
        use_ai_summaries=False,
        storage_path=str(store_dir),
        incremental=incremental,
    ), corpus


# ─────────────────────────── #67: normalize_local_index_name ───────────────

class TestNormalizeLocalIndexName:
    def test_bare_name_unchanged(self):
        assert normalize_local_index_name("example-docs", "folder") == "example-docs"

    def test_none_falls_back_to_folder(self):
        assert normalize_local_index_name(None, "folder") == "folder"
        assert normalize_local_index_name("", "folder") == "folder"

    def test_local_prefix_stripped(self):
        assert normalize_local_index_name("local/example-docs", "folder") == "example-docs"

    def test_empty_local_name_rejected(self):
        with pytest.raises(ValueError):
            normalize_local_index_name("local/", "folder")

    def test_non_local_owner_prefix_rejected(self):
        with pytest.raises(ValueError):
            normalize_local_index_name("github/foo", "folder")

    def test_nested_slashes_rejected(self):
        with pytest.raises(ValueError):
            normalize_local_index_name("local/a/b", "folder")

    def test_backslash_rejected(self):
        with pytest.raises(ValueError):
            normalize_local_index_name("a\\b", "folder")


# ─────────────────────────── #67: round trip + typed fields ────────────────

class TestLocalNameRoundTrip:
    def test_bare_name_returns_local_handle(self, tmp_path):
        store = tmp_path / "store"
        res, _ = _make_docs_index(tmp_path, store, name="example-docs")
        assert res["success"] is True
        assert res["repo"] == "local/example-docs"

    def test_doc_list_repos_exposes_repo_kind_and_name(self, tmp_path):
        store = tmp_path / "store"
        _make_docs_index(tmp_path, store, name="example-docs")
        out = list_repos(storage_path=str(store))
        row = next(r for r in out["repos"] if r["repo"] == "local/example-docs")
        assert row["repo"] == "local/example-docs"
        assert row["name"] == "example-docs"
        assert row["repo_kind"] == "doc_index"
        assert row["owner"] == "local"

    def test_refresh_with_local_prefix_handle(self, tmp_path):
        # jdoc#67: the exact failure mode — discover local/example-docs, reuse
        # it as the refresh name. Previously raised "Invalid name".
        store = tmp_path / "store"
        _make_docs_index(tmp_path, store, name="example-docs")
        res = index_local(
            path=str(tmp_path / "corpus"),
            name="local/example-docs",
            use_ai_summaries=False,
            storage_path=str(store),
            incremental=True,
        )
        assert res["success"] is True
        assert res["repo"] == "local/example-docs"

    def test_invalid_prefix_returns_clean_error(self, tmp_path):
        store = tmp_path / "store"
        (tmp_path / "corpus").mkdir()
        (tmp_path / "corpus" / "README.md").write_text("# X\n", encoding="utf-8")
        res = index_local(
            path=str(tmp_path / "corpus"),
            name="github/foo",
            use_ai_summaries=False,
            storage_path=str(store),
        )
        assert res["success"] is False
        assert "Invalid name" in res["error"]
        # Clean error, not wrapped by the "Indexing failed:" handler.
        assert "Indexing failed" not in res["error"]


# ─────────────────────────── #68: bridge invalid-code-handle diagnostics ────

class _FakeSearch:
    """Stand-in for jcodemunch search_symbols with controllable resolution."""

    def __init__(self, resolves: bool):
        self.resolves = resolves
        self.calls = []

    def __call__(self, repo, query, max_results=3, **kw):
        self.calls.append((repo, query))
        if not self.resolves:
            return {"error": f"Repository index is not loadable: {repo}"}
        return {"results": []}


class TestBridgeInvalidCodeHandle:
    def test_link_unresolvable_code_repo_returns_diagnostic(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        _make_docs_index(tmp_path, store, name="example-docs")
        monkeypatch.setattr(lcts_mod, "import_search_symbols", lambda: _FakeSearch(False))
        out = link_code_to_symbols(
            repo="local/example-docs",
            code_repo="local/example-docs",
            storage_path=str(store),
        )
        assert out["error"] == "code_repo_not_found"
        assert out["_meta"]["bridge_available"] is True
        assert out["_meta"]["code_repo_resolved"] is False
        assert "not loadable" in (out["_meta"]["code_repo_error"] or "")

    def test_link_resolvable_code_repo_no_matches_is_empty_not_error(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        _make_docs_index(tmp_path, store, name="example-docs")
        monkeypatch.setattr(lcts_mod, "import_search_symbols", lambda: _FakeSearch(True))
        out = link_code_to_symbols(
            repo="local/example-docs",
            code_repo="owner/realcode",
            storage_path=str(store),
        )
        assert "error" not in out
        assert out["by_block"] == {}
        assert out["by_symbol"] == {}
        assert out["_meta"]["bridge_available"] is True

    def test_undocumented_unresolvable_code_repo_returns_diagnostic(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        _make_docs_index(tmp_path, store, name="example-docs")
        monkeypatch.setattr(gus_mod, "import_search_symbols", lambda: _FakeSearch(False))
        out = get_undocumented_symbols(
            repo="local/example-docs",
            code_repo="local/example-docs",
            storage_path=str(store),
        )
        assert out["error"] == "code_repo_not_found"
        assert out["_meta"]["code_repo_resolved"] is False

    def test_undocumented_resolvable_code_repo_keeps_coverage_shape(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        _make_docs_index(tmp_path, store, name="example-docs")
        monkeypatch.setattr(gus_mod, "import_search_symbols", lambda: _FakeSearch(True))
        out = get_undocumented_symbols(
            repo="local/example-docs",
            code_repo="owner/realcode",
            storage_path=str(store),
        )
        assert "error" not in out
        assert "coverage" in out
        assert out["_meta"]["bridge_available"] is True


# ─────────────────────────── #68: resolve_related_code_repos ────────────────

class _FakeIndexStore:
    """Stand-in for jcodemunch IndexStore.list_repos()."""

    _rows: list = []

    def __init__(self, base_path=None):
        pass

    def list_repos(self):
        return list(self._rows)


def _patch_code_store(monkeypatch, rows):
    cls = type("FakeStore", (_FakeIndexStore,), {"_rows": rows})
    monkeypatch.setattr(rrcr_mod, "import_code_index_store", lambda: cls)


class TestResolveRelatedCodeRepos:
    def test_exact_source_root_match_high_confidence(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        _, corpus = _make_docs_index(tmp_path, store, name="example-docs")
        _patch_code_store(monkeypatch, [
            {"repo": "owner/example", "source_root": str(corpus)},
        ])
        out = resolve_related_code_repos(repo="local/example-docs", storage_path=str(store))
        assert out["_meta"]["bridge_available"] is True
        assert len(out["candidates"]) == 1
        cand = out["candidates"][0]
        assert cand["repo"] == "owner/example"
        assert cand["confidence"] == "high"
        assert cand["reason"] == "source_root_exact_match"
        assert out["ambiguous"] is False

    def test_docs_inside_code_root_medium(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        _, corpus = _make_docs_index(tmp_path, store, name="example-docs")
        _patch_code_store(monkeypatch, [
            {"repo": "owner/example", "source_root": str(corpus.parent)},
        ])
        out = resolve_related_code_repos(repo="local/example-docs", storage_path=str(store))
        cand = out["candidates"][0]
        assert cand["confidence"] == "medium"
        assert cand["reason"] == "source_root_contains_docs_root"

    def test_multiple_exact_matches_flag_ambiguous(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        _, corpus = _make_docs_index(tmp_path, store, name="example-docs")
        _patch_code_store(monkeypatch, [
            {"repo": "owner/a", "source_root": str(corpus)},
            {"repo": "owner/b", "source_root": str(corpus)},
        ])
        out = resolve_related_code_repos(repo="local/example-docs", storage_path=str(store))
        assert len(out["candidates"]) == 2
        assert out["ambiguous"] is True

    def test_no_match_returns_hint(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        _make_docs_index(tmp_path, store, name="example-docs")
        _patch_code_store(monkeypatch, [
            {"repo": "owner/unrelated", "source_root": str(tmp_path / "somewhere-else")},
        ])
        out = resolve_related_code_repos(repo="local/example-docs", storage_path=str(store))
        assert out["candidates"] == []
        assert out["_meta"]["hint"]

    def test_bridge_unavailable(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        _make_docs_index(tmp_path, store, name="example-docs")
        monkeypatch.setattr(rrcr_mod, "import_code_index_store", lambda: None)
        out = resolve_related_code_repos(repo="local/example-docs", storage_path=str(store))
        assert out["_meta"]["bridge_available"] is False
        assert out["candidates"] == []
