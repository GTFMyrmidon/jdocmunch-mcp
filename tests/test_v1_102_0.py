"""jdoc#83 (Item B) — worktree-aware corpus resolution.

Covers the PRD's validation families: pure decision-table rows, clean
equivalence across linked worktrees, identity boundaries, freshness guards,
legacy safety, claim-key convergence, branch-local escape, exact precedence,
and read-only non-mutation of discovery.
"""

import hashlib
import subprocess
from pathlib import Path

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools._worktree_corpus import (
    GitEvidence,
    ResolutionRequest,
    collect_git_evidence,
    filter_lineage_candidates,
    resolve_worktree_corpus,
    worktree_claim_key,
)
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.resolve_repo import doc_resolve_repo


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _index(src, storage, **kw):
    return index_local(
        path=str(src), storage_path=storage,
        use_ai_summaries=False, use_embeddings=False, **kw,
    )


def _rows(storage):
    return DocStore(base_path=storage).list_repos()


def _tree_digest(storage):
    root = Path(storage)
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        digest.update(p.relative_to(root).as_posix().encode("utf-8"))
        digest.update(p.read_bytes())
    return digest.hexdigest()


@pytest.fixture()
def worktrees(tmp_path):
    """A git repo with docs/, committed clean, plus a linked worktree."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    _git(main, "config", "user.email", "t@example.invalid")
    _git(main, "config", "user.name", "t")
    docs = main / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Guide\n\nBody.\n", encoding="utf-8")
    (docs / "api.md").write_text("# API\n\nBody.\n", encoding="utf-8")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "init")
    linked = tmp_path / "linked"
    _git(main, "worktree", "add", "-q", str(linked))
    storage = str(tmp_path / "store")
    return main, linked, storage


def _ev(**kw):
    defaults = dict(
        in_git=True, lineage_state="confirmed", lineage_key="k1",
        common_dir="c", toplevel="t", relative_root="docs",
        head_sha="a" * 40, corpus_dirty=False,
    )
    defaults.update(kw)
    return GitEvidence(**defaults)


def _cand(**kw):
    defaults = dict(
        repo="local/docs-a", source_root="/x/docs",
        worktree_lineage_key="k1", repo_relative_root="docs",
        corpus_selection="full", head_sha="a" * 40, sha_certified=True,
    )
    defaults.update(kw)
    return defaults


class TestDecisionTable:
    """Phase 1: every Section 8 row as a pure, filesystem-free unit test."""

    def test_lineage_unknown_fails_closed(self):
        d = resolve_worktree_corpus(
            ResolutionRequest("index_local", _ev(lineage_state="unknown"), selection="full"),
            [],
        )
        assert d.status == "unknown" and d.reason_code == "lineage_unknown"
        assert d.write_policy == "read_only"

    def test_resolver_tool_zero_one_many(self):
        req = ResolutionRequest("doc_resolve_repo", _ev())
        assert resolve_worktree_corpus(req, []).status == "no_match"
        one = resolve_worktree_corpus(req, [_cand()])
        assert one.status == "reference_only"
        assert one.reason_code == "unique_location_candidate"
        assert one.identity["selection"] == "unavailable"
        many = resolve_worktree_corpus(req, [_cand(), _cand(repo="local/docs-b")])
        assert many.status == "ambiguous" and not many.established_handle

    def test_index_local_selection_unavailable_is_related(self):
        d = resolve_worktree_corpus(
            ResolutionRequest("index_local", _ev(), selection=None), [_cand()]
        )
        assert d.status == "related" and d.reason_code == "selection_incomplete"

    def test_index_local_equivalent_fresh_reusable(self):
        d = resolve_worktree_corpus(
            ResolutionRequest("index_local", _ev(), selection="full"), [_cand()]
        )
        assert d.status == "reusable"
        assert d.reason_code == "equivalent_corpus_fresh"
        assert d.established_handle == "local/docs-a"
        assert d.write_policy == "reuse_only"

    def test_index_local_stale_and_dirty_are_reference_only(self):
        stale = resolve_worktree_corpus(
            ResolutionRequest("index_local", _ev(head_sha="b" * 40), selection="full"),
            [_cand()],
        )
        assert stale.status == "reference_only"
        assert stale.reason_code == "equivalent_corpus_stale"
        dirty = resolve_worktree_corpus(
            ResolutionRequest("index_local", _ev(corpus_dirty=True), selection="full"),
            [_cand()],
        )
        assert dirty.status == "reference_only"
        assert dirty.reason_code == "equivalent_corpus_dirty"

    def test_uncertified_candidate_is_never_fresh(self):
        d = resolve_worktree_corpus(
            ResolutionRequest("index_local", _ev(), selection="full"),
            [_cand(sha_certified=False)],
        )
        assert d.status == "reference_only"
        assert d.freshness["revision_relation"] == "unknown"

    def test_index_local_legacy_selection_is_unresolved(self):
        d = resolve_worktree_corpus(
            ResolutionRequest("index_local", _ev(), selection="full"),
            [_cand(corpus_selection="")],
        )
        assert d.status == "related"
        assert d.reason_code == "unresolved_legacy_candidate"

    def test_index_local_different_selection_creates(self):
        d = resolve_worktree_corpus(
            ResolutionRequest("index_local", _ev(), selection="subset:aa:1"),
            [_cand(corpus_selection="full")],
        )
        assert d.status == "created" and d.write_policy == "create_if_claim_wins"

    def test_multiple_equivalents_ambiguous(self):
        d = resolve_worktree_corpus(
            ResolutionRequest("index_local", _ev(), selection="full"),
            [_cand(), _cand(repo="local/docs-b")],
        )
        assert d.status == "ambiguous" and not d.established_handle

    def test_branch_local_short_circuits(self):
        d = resolve_worktree_corpus(
            ResolutionRequest("index_local", _ev(), selection="full", branch_local=True),
            [_cand()],
        )
        assert d.status == "created"
        assert d.reason_code == "branch_local_created"
        assert d.write_policy == "explicit_branch_local"

    def test_symmetry_and_order_independence(self):
        # I2: same decision regardless of candidate list order.
        req = ResolutionRequest("index_local", _ev(), selection="full")
        a, b = _cand(), _cand(repo="local/docs-b")
        d1 = resolve_worktree_corpus(req, [a, b])
        d2 = resolve_worktree_corpus(req, [b, a])
        assert d1.status == d2.status == "ambiguous"

    def test_location_filter_boundaries(self):
        ev = _ev()
        rows = [
            _cand(),  # exact location
            _cand(repo="local/widget", repo_relative_root="packages/widget/docs"),
            _cand(repo="local/other-family", worktree_lineage_key="k2"),
        ]
        got = filter_lineage_candidates(rows, ev)
        assert [e["repo"] for e in got] == ["local/docs-a"]
        # Containment (read-only resolution): a path inside docs resolves to
        # the docs corpus; a sibling corpus never matches.
        inside = _ev(relative_root="docs/sub")
        got = filter_lineage_candidates(rows, inside, allow_containment=True)
        assert [e["repo"] for e in got] == ["local/docs-a"]
        assert filter_lineage_candidates(rows, inside) == []  # index_local mode

    def test_worktree_claim_key_converges_across_roots(self):
        # Two worktrees, different absolute roots, same lineage + location +
        # selection: ONE claim key (I4 across translation).
        a = _ev(common_dir="x")
        b = _ev(common_dir="y")  # common_dir differs but key is what matters
        b.lineage_key = a.lineage_key
        assert worktree_claim_key(a, "full") == worktree_claim_key(b, "full")
        assert worktree_claim_key(a, "full") != worktree_claim_key(a, "subset:aa:1")
        assert worktree_claim_key(_ev(lineage_state="unknown"), "full") is None


class TestCleanEquivalence:
    def test_verified_repro_now_resolves_and_reuses(self, worktrees):
        main, linked, storage = worktrees
        assert _index(main / "docs", storage, name="docs-a")["success"]

        # doc_resolve_repo from the linked worktree: read-only candidate.
        res = doc_resolve_repo(str(linked / "docs"), storage_path=storage)
        assert res["found"] is False and res["indexed"] is False
        wr = res["worktree_resolution"]
        assert wr["status"] == "reference_only"
        assert wr["established_handle"] == "local/docs-a"
        assert wr["identity"]["selection"] == "unavailable"
        assert res["canonical_candidates"][0]["repo"] == "local/docs-a"

        # index_local from the linked worktree: reuse, no second index.
        before = _tree_digest(storage)
        r = _index(linked / "docs", storage, name="docs-b")
        assert r["success"] is True
        assert r["repo"] == "local/docs-a"
        assert r["reused_established_handle"] is True
        assert r["worktree_resolution"]["status"] == "reusable"
        assert r["worktree_resolution"]["did_write"] is False
        assert len(_rows(storage)) == 1
        assert _tree_digest(storage) == before  # reuse wrote NOTHING

    def test_file_inside_worktree_corpus_resolves(self, worktrees):
        main, linked, storage = worktrees
        assert _index(main / "docs", storage, name="docs-a")["success"]
        res = doc_resolve_repo(str(linked / "docs" / "guide.md"), storage_path=storage)
        assert res["found"] is False
        assert res["worktree_resolution"]["established_handle"] == "local/docs-a"


class TestIdentityBoundaries:
    def test_nested_location_is_a_distinct_corpus(self, worktrees):
        main, linked, storage = worktrees
        sub = main / "docs" / "sub"
        sub.mkdir()
        (sub / "inner.md").write_text("# Inner\n\nBody.\n", encoding="utf-8")
        _git(main, "add", "-A")
        _git(main, "commit", "-q", "-m", "sub")
        # Advance the linked worktree to the same commit (it was added at the
        # initial commit; the fixture models same-revision worktrees).
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(main), check=True,
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
        ).stdout.strip()
        _git(linked, "checkout", "-q", head)
        assert _index(main / "docs", storage, name="docs-a")["success"]
        r = _index(linked / "docs" / "sub", storage, name="docs-sub")
        # Different repository-relative location: never collapsed.
        assert r["success"], r
        assert len(_rows(storage)) == 2

    def test_different_durable_selection_not_collapsed(self, worktrees):
        main, linked, storage = worktrees
        assert _index(main / "docs", storage, name="docs-a")["success"]
        r = _index(linked / "docs", storage, name="docs-guide", paths=["guide.md"])
        assert r["success"], r
        assert len(_rows(storage)) == 2


class TestFreshnessGuards:
    def test_different_revision_reference_only_no_write(self, worktrees):
        main, linked, storage = worktrees
        assert _index(main / "docs", storage, name="docs-a")["success"]
        (linked / "docs" / "guide.md").write_text("# Guide\n\nEdited.\n", encoding="utf-8")
        _git(linked, "add", "-A")
        _git(linked, "commit", "-q", "-m", "drift")
        before = _tree_digest(storage)
        r = _index(linked / "docs", storage, name="docs-b")
        assert r["success"] is False
        assert r["error"] == "equivalent_corpus_stale"
        assert r["worktree_resolution"]["status"] == "reference_only"
        assert _tree_digest(storage) == before

    def test_dirty_requesting_docs_no_write(self, worktrees):
        main, linked, storage = worktrees
        assert _index(main / "docs", storage, name="docs-a")["success"]
        (linked / "docs" / "wip.md").write_text("# WIP\n\nDraft.\n", encoding="utf-8")
        before = _tree_digest(storage)
        r = _index(linked / "docs", storage, name="docs-b")
        assert r["success"] is False
        assert r["error"] == "equivalent_corpus_dirty"
        assert _tree_digest(storage) == before

    def test_dirty_origin_index_never_reused(self, worktrees):
        # Index created from dirty content is uncertified: reuse refuses.
        main, linked, storage = worktrees
        (main / "docs" / "wip.md").write_text("# WIP\n\nDraft.\n", encoding="utf-8")
        assert _index(main / "docs", storage, name="docs-a")["success"]
        (main / "docs" / "wip.md").unlink()
        _git(main, "checkout", "--", ".")
        r = _index(linked / "docs", storage, name="docs-b")
        assert r["success"] is False
        assert r["worktree_resolution"]["freshness"]["revision_relation"] == "unknown"


class TestLegacySafety:
    def test_unresolved_legacy_selection_no_write(self, worktrees):
        import json
        main, linked, storage = worktrees
        assert _index(main / "docs", storage, name="docs-a")["success"]
        for suffix in (".json", ".summary.json"):
            p = Path(storage) / "local" / f"docs-a{suffix}"
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                data.pop("corpus_selection", None)
                p.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        before = _tree_digest(storage)
        r = _index(linked / "docs", storage, name="docs-b")
        assert r["success"] is False
        assert r["error"] == "unresolved_legacy_candidate"
        assert _tree_digest(storage) == before

    def test_pre_item_b_index_without_lineage_is_never_inferred(self, worktrees):
        import json
        main, linked, storage = worktrees
        assert _index(main / "docs", storage, name="docs-a")["success"]
        for suffix in (".json", ".summary.json"):
            p = Path(storage) / "local" / f"docs-a{suffix}"
            if p.exists():
                data = json.loads(p.read_text(encoding="utf-8"))
                for k in ("worktree_lineage_key", "repo_relative_root", "corpus_identity_version"):
                    data.pop(k, None)
                p.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        # A true pre-#83 store also has no creation claims.
        import shutil
        from jdocmunch_mcp.storage.corpus_claims import claims_dir
        shutil.rmtree(claims_dir(storage), ignore_errors=True)
        # No lineage evidence on the stored side: I6 forbids inferred
        # equivalence, so the linked worktree creates its own index.
        r = _index(linked / "docs", storage, name="docs-b")
        assert r["success"] is True and r["repo"] == "local/docs-b"
        assert len(_rows(storage)) == 2


class TestBranchLocalAndPrecedence:
    def test_branch_local_creates_and_then_wins_exact_precedence(self, worktrees):
        main, linked, storage = worktrees
        assert _index(main / "docs", storage, name="docs-a")["success"]
        r = _index(
            linked / "docs", storage, name="docs-linked",
            worktree_mode="branch_local",
        )
        assert r["success"] is True and r["repo"] == "local/docs-linked"
        assert len(_rows(storage)) == 2
        # R1: the exact branch-local index now wins over worktree translation.
        res = doc_resolve_repo(str(linked / "docs"), storage_path=storage)
        assert res["found"] is True and res["repo"] == "local/docs-linked"

    def test_discovery_is_read_only(self, worktrees):
        main, linked, storage = worktrees
        assert _index(main / "docs", storage, name="docs-a")["success"]
        before = _tree_digest(storage)
        doc_resolve_repo(str(linked / "docs"), storage_path=storage)
        doc_resolve_repo(str(linked / "docs" / "guide.md"), storage_path=storage)
        assert _tree_digest(storage) == before


class TestNonGitCompatibility:
    def test_plain_folder_behavior_unchanged(self, tmp_path):
        src = tmp_path / "docs"
        src.mkdir()
        (src / "a.md").write_text("# A\n\nBody.\n", encoding="utf-8")
        storage = str(tmp_path / "store")
        r = _index(src, storage, name="plain")
        assert r["success"] and "worktree_resolution" not in r
        res = doc_resolve_repo(str(src), storage_path=storage)
        assert res["found"] is True and "worktree_resolution" not in res


class TestEvidenceCollection:
    def test_collect_git_evidence_shapes(self, worktrees):
        main, linked, storage = worktrees
        ev_main = collect_git_evidence(main / "docs")
        ev_linked = collect_git_evidence(linked / "docs")
        assert ev_main.lineage_state == ev_linked.lineage_state == "confirmed"
        assert ev_main.lineage_key == ev_linked.lineage_key  # same family
        assert ev_main.relative_root == ev_linked.relative_root == "docs"
        assert ev_main.head_sha == ev_linked.head_sha
        assert ev_main.corpus_dirty is False

    def test_non_git_folder_unknown(self, tmp_path):
        d = tmp_path / "plain"
        d.mkdir()
        ev = collect_git_evidence(d)
        assert ev.in_git is False and ev.lineage_state == "unknown"
