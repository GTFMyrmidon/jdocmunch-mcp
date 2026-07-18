"""v1.99.0 — doc_resolve_repo: path → doc-index handle lookup (jdoc#79)."""

import json
import os

import pytest

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.resolve_repo import doc_resolve_repo


def _index(corpus, store, name, files=("README.md",)):
    corpus.mkdir(parents=True, exist_ok=True)
    for rel in files:
        f = corpus / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f"# {rel}\n\nBody of {rel}.\n", encoding="utf-8")
    res = index_local(
        path=str(corpus),
        name=name,
        use_ai_summaries=False,
        storage_path=str(store),
    )
    assert res["success"] is True
    return res


class TestExactMatch:
    def test_exact_root_resolves(self, tmp_path):
        store = tmp_path / "store"
        _index(tmp_path / "proj-docs", store, "proj-docs")
        res = doc_resolve_repo(str(tmp_path / "proj-docs"), storage_path=str(store))
        assert res["found"] is True
        assert res["indexed"] is True
        assert res["repo"] == "local/proj-docs"
        assert res["match"] == "exact_source_root"
        assert res["repo_kind"] == "doc_index"
        assert "source_root" in res

    def test_windows_casing_and_separators(self, tmp_path):
        if os.name != "nt":
            pytest.skip("case/separator normalization is Windows-specific")
        store = tmp_path / "store"
        _index(tmp_path / "proj-docs", store, "proj-docs")
        variant = str(tmp_path / "proj-docs").upper().replace("\\", "/")
        res = doc_resolve_repo(variant, storage_path=str(store))
        assert res["found"] is True
        assert res["repo"] == "local/proj-docs"


class TestContainment:
    def test_file_inside_root_resolves(self, tmp_path):
        store = tmp_path / "store"
        _index(tmp_path / "proj-docs", store, "proj-docs", files=("guides/intro.md",))
        res = doc_resolve_repo(
            str(tmp_path / "proj-docs" / "guides" / "intro.md"), storage_path=str(store)
        )
        assert res["found"] is True
        assert res["repo"] == "local/proj-docs"
        assert res["match"] == "source_root_containment"

    def test_subfolder_resolves(self, tmp_path):
        store = tmp_path / "store"
        _index(tmp_path / "proj-docs", store, "proj-docs", files=("guides/intro.md",))
        res = doc_resolve_repo(
            str(tmp_path / "proj-docs" / "guides"), storage_path=str(store)
        )
        assert res["found"] is True
        assert res["repo"] == "local/proj-docs"

    def test_most_specific_root_wins(self, tmp_path):
        store = tmp_path / "store"
        _index(tmp_path / "mono", store, "mono-docs", files=("README.md",))
        _index(
            tmp_path / "mono" / "pkg" / "docs", store, "pkg-docs",
            files=("api.md",),
        )
        res = doc_resolve_repo(
            str(tmp_path / "mono" / "pkg" / "docs" / "api.md"), storage_path=str(store)
        )
        assert res["repo"] == "local/pkg-docs"

    def test_sibling_prefix_name_not_contained(self, tmp_path):
        # /root/docs must not claim /root/docs-extra (string-prefix trap).
        store = tmp_path / "store"
        _index(tmp_path / "docs", store, "docs")
        extra = tmp_path / "docs-extra"
        extra.mkdir()
        (extra / "x.md").write_text("# x\n", encoding="utf-8")
        res = doc_resolve_repo(str(extra), storage_path=str(store))
        assert res["found"] is False


class TestNotFoundAndErrors:
    def test_unindexed_path_compact_not_found(self, tmp_path):
        store = tmp_path / "store"
        _index(tmp_path / "proj-docs", store, "proj-docs")
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        res = doc_resolve_repo(str(outside), storage_path=str(store))
        assert res["found"] is False
        assert res["indexed"] is False
        assert "hint" in res
        assert "repos" not in res  # never the full listing

    def test_nonexistent_path_errors(self, tmp_path):
        res = doc_resolve_repo(
            str(tmp_path / "no-such-dir"), storage_path=str(tmp_path / "store")
        )
        assert res["found"] is False
        assert "error" in res

    def test_empty_path_errors(self, tmp_path):
        res = doc_resolve_repo("  ", storage_path=str(tmp_path / "store"))
        assert res["found"] is False
        assert "error" in res

    def test_relative_path_reports_resolution(self, tmp_path, monkeypatch):
        store = tmp_path / "store"
        _index(tmp_path / "proj-docs", store, "proj-docs")
        monkeypatch.chdir(tmp_path)
        res = doc_resolve_repo("proj-docs", storage_path=str(store))
        assert res["found"] is True
        assert res["_meta"]["resolved_path"]


class TestAmbiguity:
    def test_duplicate_roots_bounded_candidates(self, tmp_path):
        store = tmp_path / "store"
        corpus = tmp_path / "proj-docs"
        _index(corpus, store, "copy-a")
        _index(corpus, store, "copy-b")
        res = doc_resolve_repo(str(corpus), storage_path=str(store))
        assert res["found"] is True
        assert res["ambiguous"] is True
        assert res["total_matches"] == 2
        assert len(res["candidates"]) == 2
        assert {c["repo"] for c in res["candidates"]} == {"local/copy-a", "local/copy-b"}

    def test_candidates_capped_at_five(self, tmp_path):
        store = tmp_path / "store"
        corpus = tmp_path / "proj-docs"
        for i in range(7):
            _index(corpus, store, f"copy-{i}")
        res = doc_resolve_repo(str(corpus), storage_path=str(store))
        assert res["ambiguous"] is True
        assert res["total_matches"] == 7
        assert len(res["candidates"]) == 5


class TestReadOnlyAndBoundedness:
    def test_lookup_mutates_nothing(self, tmp_path):
        store = tmp_path / "store"
        _index(tmp_path / "proj-docs", store, "proj-docs")
        before = sorted(str(p) for p in store.rglob("*"))
        mtimes = {p: os.path.getmtime(p) for p in before if os.path.isfile(p)}
        doc_resolve_repo(str(tmp_path / "proj-docs"), storage_path=str(store))
        doc_resolve_repo(str(tmp_path), storage_path=str(store))  # not-found path
        after = sorted(str(p) for p in store.rglob("*"))
        assert after == before
        assert {p: os.path.getmtime(p) for p in mtimes} == mtimes

    def test_response_size_independent_of_index_count(self, tmp_path):
        store = tmp_path / "store"
        _index(tmp_path / "target", store, "target-docs")
        baseline = len(json.dumps(
            doc_resolve_repo(str(tmp_path / "target"), storage_path=str(store))
        ))
        for i in range(10):
            _index(tmp_path / f"other-{i}", store, f"other-{i}")
        grown = len(json.dumps(
            doc_resolve_repo(str(tmp_path / "target"), storage_path=str(store))
        ))
        # Identical match payload; only latency digits may differ.
        assert abs(grown - baseline) < 20

    def test_github_index_without_source_root_skipped(self, tmp_path):
        # A registry row with no source_root (GitHub corpus) must never match.
        store = tmp_path / "store"
        _index(tmp_path / "proj-docs", store, "proj-docs")
        gh_dir = store / "github"
        gh_dir.mkdir(parents=True, exist_ok=True)
        (gh_dir / "remote-docs.json").write_text(json.dumps({
            "repo": "github/remote-docs",
            "indexed_at": "2026-01-01T00:00:00",
            "doc_paths": [], "doc_types": {}, "sections": [],
        }), encoding="utf-8")
        res = doc_resolve_repo(str(tmp_path / "proj-docs"), storage_path=str(store))
        assert res["repo"] == "local/proj-docs"


class TestDispatch:
    @pytest.mark.asyncio
    async def test_call_tool_dispatch(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.server import call_tool

        store = tmp_path / "store"
        _index(tmp_path / "proj-docs", store, "proj-docs")
        monkeypatch.setenv("DOC_INDEX_PATH", str(store))
        result = await call_tool(
            "doc_resolve_repo", {"path": str(tmp_path / "proj-docs")}
        )
        data = json.loads(result[0].text)
        assert data["found"] is True
        assert data["repo"] == "local/proj-docs"

    @pytest.mark.asyncio
    async def test_advertised_and_readonly(self):
        from jdocmunch_mcp.server import list_tools

        tools = await list_tools()
        tool = next(t for t in tools if t.name == "doc_resolve_repo")
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
