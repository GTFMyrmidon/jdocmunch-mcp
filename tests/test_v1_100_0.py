"""jdoc#81 — corpus identity: index_local must not duplicate an equivalent
local documentation source under a second name (Item A of jdoc#80)."""

import json
import os
import time
from pathlib import Path

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.storage.corpus_claims import (
    claim_key,
    claims_dir,
    cleanup_claims_for_repo,
    read_claim,
    release_claim,
    try_claim,
)
from jdocmunch_mcp.tools._corpus_identity import (
    corpus_norm_root,
    find_equivalent_indexes,
    selection_descriptor,
    selection_covers,
)
from jdocmunch_mcp.tools.index_local import index_local


@pytest.fixture()
def corpus(tmp_path):
    src = tmp_path / "project" / "docs"
    src.mkdir(parents=True)
    (src / "guide.md").write_text("# Guide\n\nHello.\n", encoding="utf-8")
    (src / "api.md").write_text("# API\n\nCalls.\n", encoding="utf-8")
    storage = tmp_path / "storage"
    return src, str(storage)


def _index(src, storage, **kw):
    return index_local(
        path=str(src), storage_path=storage,
        use_ai_summaries=False, use_embeddings=False, **kw,
    )


def _monolith_count(storage):
    return len(DocStore(base_path=storage).list_repos())


class TestSelectionDescriptor:
    def test_full_shapes(self):
        assert selection_descriptor(None) == "full"
        assert selection_descriptor([]) == "full"
        assert selection_descriptor(["."]) == "full"
        assert selection_descriptor(["", "sub/a.md"]) == "full"

    def test_subset_stable_and_order_free(self):
        a = selection_descriptor(["b.md", "a.md"])
        b = selection_descriptor(["a.md", "b.md", "a.md"])
        assert a == b and a.startswith("subset:") and a.endswith(":2")
        assert selection_descriptor(["a.md"]) != a

    def test_covers(self):
        sub = selection_descriptor(["a.md"])
        assert selection_covers("full", sub)
        assert selection_covers("", "full")  # legacy presumed full
        assert selection_covers(sub, sub)
        assert not selection_covers(sub, "full")
        assert not selection_covers(sub, selection_descriptor(["b.md"]))


class TestCreationAndReuse:
    def test_new_source_creates_one_index_with_identity(self, corpus):
        src, storage = corpus
        r = _index(src, storage, name="docs-a")
        assert r["success"] and r["repo"] == "local/docs-a"
        store = DocStore(base_path=storage)
        idx = store.load_index("local", "docs-a")
        assert idx.corpus_selection == "full"
        rows = store.list_repos()
        assert rows[0]["corpus_selection"] == "full"

    def test_explicit_conflicting_name_returns_conflict_no_write(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        before = _monolith_count(storage)
        r = _index(src, storage, name="docs-b")
        assert r["success"] is False
        assert r["error"] == "corpus_already_indexed"
        assert r["requested_handle"] == "local/docs-b"
        assert r["established_handle"] == "local/docs-a"
        assert "hint" in r
        assert _monolith_count(storage) == before
        assert DocStore(base_path=storage).load_index("local", "docs-b") is None

    def test_omitted_name_reuses_established_handle(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        r = _index(src, storage)  # derived name would be "docs"
        assert r["success"]
        assert r["repo"] == "local/docs-a"
        assert r["reused_established_handle"] is True
        assert r["requested_handle"] == "local/docs"
        assert r["established_handle"] == "local/docs-a"
        assert _monolith_count(storage) == 1

    def test_established_handle_stays_refreshable(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        (src / "new.md").write_text("# New\n\nMore.\n", encoding="utf-8")
        r = _index(src, storage, name="docs-a")
        assert r["success"] and r["incremental"] and r["new"] == 1

    def test_local_prefix_round_trip_is_not_a_conflict(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        r = _index(src, storage, name="local/docs-a")
        assert r["success"]

    def test_path_spelling_variants_reuse(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        spelled = str(src) + os.sep  # trailing separator variant
        r = index_local(
            path=spelled, storage_path=storage,
            use_ai_summaries=False, use_embeddings=False,
        )
        assert r["success"] and r["repo"] == "local/docs-a"
        assert _monolith_count(storage) == 1


class TestSelectionBoundaries:
    def test_nested_root_is_a_distinct_corpus(self, corpus, tmp_path):
        src, storage = corpus
        nested = src / "sub"
        nested.mkdir()
        (nested / "inner.md").write_text("# Inner\n\nBody.\n", encoding="utf-8")
        assert _index(src, storage, name="docs-a")["success"]
        r = _index(nested, storage, name="docs-sub")
        assert r["success"], r  # containment alone never establishes identity
        assert _monolith_count(storage) == 2

    def test_subset_refresh_does_not_redefine_durable_selection(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        r = _index(src, storage, name="docs-a", paths=["guide.md"])
        assert r["success"]
        idx = DocStore(base_path=storage).load_index("local", "docs-a")
        assert idx.corpus_selection == "full"

    def test_intentional_subset_index_not_merged_with_full_call(self, corpus):
        src, storage = corpus
        r1 = _index(src, storage, name="docs-guide", paths=["guide.md"])
        assert r1["success"]
        idx = DocStore(base_path=storage).load_index("local", "docs-guide")
        assert idx.corpus_selection.startswith("subset:")
        # A full-corpus create under a different name is a different durable
        # selection — allowed, not a conflict.
        r2 = _index(src, storage, name="docs-all")
        assert r2["success"], r2
        assert _monolith_count(storage) == 2

    def test_subset_call_reuses_full_index(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        r = _index(src, storage, paths=["guide.md"])  # omitted name, subset
        assert r["success"]
        assert r["repo"] == "local/docs-a"
        assert _monolith_count(storage) == 1


class TestLegacyIndexes:
    def _make_legacy_duplicates(self, src, storage, names):
        """Create indexes then strip corpus_selection to simulate pre-#81."""
        store = DocStore(base_path=storage)
        for i, n in enumerate(names):
            if i == 0:
                assert _index(src, storage, name=n)["success"]
            else:
                # Bypass the new guard the way pre-#81 callers could: save a
                # second physical index directly.
                first = store.load_index("local", names[0])
                store.save_index(
                    owner="local", name=n,
                    sections=[], raw_files={}, doc_types={},
                    source_root=first.source_root,
                )
        for n in names:
            p = Path(storage) / "local" / f"{n}.json"
            data = json.loads(p.read_text(encoding="utf-8"))
            data.pop("corpus_selection", None)
            p.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            sp = Path(storage) / "local" / f"{n}.summary.json"
            if sp.exists():
                s = json.loads(sp.read_text(encoding="utf-8"))
                s.pop("corpus_selection", None)
                sp.write_text(json.dumps(s, separators=(",", ":")), encoding="utf-8")
        # Invalidate cached loads (mtime-keyed cache may hold stale objects).
        cleanup_claims_for_repo(storage, "ignored/nothing")

    def test_single_legacy_index_participates_in_reuse(self, corpus):
        src, storage = corpus
        self._make_legacy_duplicates(src, storage, ["docs-a"])
        cleanup_claims_for_repo(storage, "local/docs-a")  # clear creation claim
        r = _index(src, storage)
        assert r["success"]
        assert r["repo"] == "local/docs-a"

    def test_multiple_legacy_duplicates_omitted_name_is_ambiguous(self, corpus):
        src, storage = corpus
        self._make_legacy_duplicates(src, storage, ["docs-a", "docs-b"])
        before = _monolith_count(storage)
        r = _index(src, storage)
        assert r["success"] is False
        assert r["error"] == "ambiguous_corpus_identity"
        assert 1 <= len(r["candidates"]) <= 5
        assert r["total_matches"] >= 2
        assert _monolith_count(storage) == before

    def test_explicitly_selecting_a_legacy_duplicate_refreshes_it(self, corpus):
        src, storage = corpus
        self._make_legacy_duplicates(src, storage, ["docs-a", "docs-b"])
        r = _index(src, storage, name="docs-b")
        assert r["success"], r

    def test_explicit_new_name_over_legacy_duplicates_is_ambiguous(self, corpus):
        # jdoc#82 invariant 2: several matches are never ordered into a
        # winner — the pre-hardening behavior promoted equivalents[0] as
        # established_handle, which registry order must not decide.
        src, storage = corpus
        self._make_legacy_duplicates(src, storage, ["docs-a", "docs-b"])
        r = _index(src, storage, name="docs-c")
        assert r["success"] is False
        assert r["error"] == "ambiguous_corpus_identity"
        assert "established_handle" not in r
        assert DocStore(base_path=storage).load_index("local", "docs-c") is None


class TestCreationClaims:
    def test_claim_race_second_caller_routes_to_winner(self, corpus):
        src, storage = corpus
        root = corpus_norm_root(src.resolve())
        key = claim_key(root, "full")
        acquired, existing = try_claim(storage, key, "local/docs-w", root, "full")
        assert acquired and existing is None
        # Simulate the loser: same source, omitted name — must land on the
        # winner's handle, not create a second physical index.
        r = _index(src, storage)
        assert r["success"]
        assert r["repo"] == "local/docs-w"
        assert r["reused_established_handle"] is True
        assert _monolith_count(storage) == 1

    def test_claim_race_explicit_other_name_conflicts(self, corpus):
        src, storage = corpus
        root = corpus_norm_root(src.resolve())
        key = claim_key(root, "full")
        assert try_claim(storage, key, "local/docs-w", root, "full")[0]
        r = _index(src, storage, name="docs-x")
        assert r["success"] is False
        assert r["error"] == "corpus_already_indexed"
        assert r["established_handle"] == "local/docs-w"

    def test_stale_abandoned_claim_is_stolen(self, corpus):
        src, storage = corpus
        root = corpus_norm_root(src.resolve())
        key = claim_key(root, "full")
        assert try_claim(storage, key, "local/ghost", root, "full")[0]
        # Age the claim past the TTL; local/ghost has no index → abandoned.
        p = claims_dir(storage) / f"{key}.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["created_at"] = time.time() - 90000
        p.write_text(json.dumps(data), encoding="utf-8")
        acquired, _ = try_claim(
            storage, key, "local/docs-a", root, "full",
            index_exists=lambda r: False,
        )
        assert acquired
        assert read_claim(storage, key)["repo"] == "local/docs-a"

    def test_delete_index_removes_claims(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        store = DocStore(base_path=storage)
        assert any(
            read_claim(storage, p.stem) and read_claim(storage, p.stem)["repo"] == "local/docs-a"
            for p in claims_dir(storage).glob("*.json")
        )
        assert store.delete_index("local", "docs-a")
        assert not any(
            (c := read_claim(storage, p.stem)) and c["repo"] == "local/docs-a"
            for p in claims_dir(storage).glob("*.json")
        )
        # Re-creation under a fresh name now succeeds (no phantom conflict).
        r = _index(src, storage, name="docs-fresh")
        assert r["success"], r

    def test_failed_create_releases_claim(self, tmp_path):
        empty = tmp_path / "empty-docs"
        empty.mkdir()
        storage = str(tmp_path / "storage")
        r = index_local(
            path=str(empty), storage_path=storage,
            use_ai_summaries=False, use_embeddings=False, name="nothing",
        )
        assert r["success"] is False
        # No-files fails before the claim; a later create must not conflict.
        (empty / "doc.md").write_text("# Doc\n\nBody.\n", encoding="utf-8")
        r2 = index_local(
            path=str(empty), storage_path=storage,
            use_ai_summaries=False, use_embeddings=False, name="real",
        )
        assert r2["success"], r2


class TestFindEquivalents:
    def test_excludes_target_and_github_entries(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        store = DocStore(base_path=storage)
        root = corpus_norm_root(src.resolve())
        assert find_equivalent_indexes(store, root, "full", exclude_repo="local/docs-a") == []
        found = find_equivalent_indexes(store, root, "full", exclude_repo="local/other")
        assert [e["repo"] for e in found] == ["local/docs-a"]
