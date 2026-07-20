"""jdoc#80 Part B (v1.106.0) — reconciliation quarantine (quarantine only, no
graduation).

When Git lineage cannot be *verified* (both common-dir probes were unavailable
— timeout / missing binary / OS error, NOT a clean not-a-repository answer),
index_local still creates the index but stamps it provisional:

- B1 provisional stamp on failed verification, distinct from confirmed-non-Git;
- I4 authority-free: a provisional index never wins worktree reuse;
- B3 per-source_root cap: creation beyond the cap fails closed and loud;
- B2 legacy_index_present disclosure when a pre-1.102 sibling exists;
- B4 vocabulary drift-guard: every Part B status/reason_code is documented.

Part B ships NO graduation path — a provisional index stays provisional across
refresh (reconciliation is Part C, behind the #80 proof gate).
"""

from __future__ import annotations

import jdocmunch_mcp.tools._worktree_corpus as wc
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools._git import GIT_NOT_A_REPO, GIT_UNAVAILABLE
from jdocmunch_mcp.tools._worktree_corpus import (
    PROVISIONAL_PER_ROOT_CAP,
    REASON_PROVISIONAL_CAP,
    REASON_PROVISIONAL_CREATED,
    RECONCILIATION_PROVISIONAL,
    GitEvidence,
    collect_git_evidence,
    count_provisional_for_root,
    filter_lineage_candidates,
    legacy_sibling_handles,
)
from jdocmunch_mcp.tools.index_local import index_local


def _docdir(tmp_path, name="docs", body="# Title\n\nText.\n"):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.md").write_text(body, encoding="utf-8")
    return d


def _index(path, storage, **kw):
    return index_local(
        path=str(path), storage_path=str(storage),
        use_ai_summaries=False, use_embeddings=False, **kw,
    )


def _force_verification_failed(monkeypatch):
    ev = GitEvidence(in_git=False, verification_failed=True)
    monkeypatch.setattr(wc, "collect_git_evidence", lambda root: ev)


# ── B1: provisional stamp on failed verification ──────────────────────────

def test_failed_verification_creates_provisional(tmp_path, monkeypatch):
    _force_verification_failed(monkeypatch)
    store_dir = tmp_path / "store"
    out = _index(_docdir(tmp_path), store_dir, name="proj")
    assert out["success"] is True
    assert out["reconciliation"]["state"] == RECONCILIATION_PROVISIONAL
    assert out["reconciliation"]["reason_code"] == REASON_PROVISIONAL_CREATED

    # Persisted + visible in list_repos without loading the monolith.
    store = DocStore(base_path=str(store_dir))
    loaded = store.load_index("local", "proj")
    assert loaded.reconciliation_state == RECONCILIATION_PROVISIONAL
    row = next(r for r in store.list_repos() if r["repo"] == "local/proj")
    assert row["reconciliation_state"] == RECONCILIATION_PROVISIONAL


def test_confirmed_non_git_is_not_provisional(tmp_path, monkeypatch):
    # git ran and said "not a repository" → a determination, not a failure.
    monkeypatch.setattr(wc, "_git_probe", lambda cwd, args: (False, "", GIT_NOT_A_REPO))
    out = _index(_docdir(tmp_path), tmp_path / "store", name="plain")
    assert out["success"] is True
    assert "reconciliation" not in out
    store = DocStore(base_path=str(tmp_path / "store"))
    assert store.load_index("local", "plain").reconciliation_state == ""


def test_collect_evidence_classifies_unavailable_vs_not_a_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(wc, "_git_probe", lambda cwd, args: (False, "", GIT_UNAVAILABLE))
    assert collect_git_evidence(tmp_path).verification_failed is True
    monkeypatch.setattr(wc, "_git_probe", lambda cwd, args: (False, "", GIT_NOT_A_REPO))
    assert collect_git_evidence(tmp_path).verification_failed is False


def test_provisional_survives_refresh_no_graduation(tmp_path, monkeypatch):
    _force_verification_failed(monkeypatch)
    store_dir = tmp_path / "store"
    src = _docdir(tmp_path)
    _index(src, store_dir, name="proj")
    # Refresh (even if git would verify now, Part B never graduates).
    (src / "b.md").write_text("# More\n\nx\n", encoding="utf-8")
    out2 = _index(src, store_dir, name="proj")
    assert out2["success"] is True
    store = DocStore(base_path=str(store_dir))
    assert store.load_index("local", "proj").reconciliation_state == RECONCILIATION_PROVISIONAL


# ── I4: authority-free ────────────────────────────────────────────────────

def test_provisional_excluded_from_lineage_candidates():
    ev = GitEvidence(in_git=True, lineage_state="confirmed", lineage_key="abc123", relative_root="")
    rows = [
        {"repo": "local/prov", "worktree_lineage_key": "abc123",
         "repo_relative_root": "", "reconciliation_state": RECONCILIATION_PROVISIONAL},
        {"repo": "local/good", "worktree_lineage_key": "abc123",
         "repo_relative_root": ""},
    ]
    got = filter_lineage_candidates(rows, ev, allow_containment=False)
    handles = {c["repo"] for c in got}
    assert "local/good" in handles
    assert "local/prov" not in handles  # provisional never a reuse candidate


# ── B3: per-root cap fails closed ─────────────────────────────────────────

def test_provisional_cap_fails_closed(tmp_path, monkeypatch):
    # The multi-provisional-per-root vector comes from distinct corpus
    # selections (Item A already dedups same-root+same-selection). Plant CAP
    # provisional indexes for one source_root, then a further failed-
    # verification create against that root must fail closed.
    _force_verification_failed(monkeypatch)
    store_dir = tmp_path / "store"
    src = _docdir(tmp_path)
    store = DocStore(base_path=str(store_dir))
    for i in range(PROVISIONAL_PER_ROOT_CAP):
        store.save_index(
            owner="local", name=f"planted{i}", sections=[], raw_files={},
            doc_types={}, source_root=str(src),
            corpus_selection=f"subset:sha{i}:1",
            reconciliation_state=RECONCILIATION_PROVISIONAL,
        )
    assert count_provisional_for_root(store.list_repos(), str(src)) == PROVISIONAL_PER_ROOT_CAP
    blocked = _index(src, store_dir, name="over")
    assert blocked["success"] is False
    assert blocked["error"] == REASON_PROVISIONAL_CAP
    assert blocked["provisional_count"] == PROVISIONAL_PER_ROOT_CAP
    # The blocked call wrote nothing.
    assert DocStore(base_path=str(store_dir)).load_index("local", "over") is None


def test_count_provisional_for_root_metadata_only(tmp_path):
    rows = [
        {"repo": "local/a", "source_root": str(tmp_path / "x"),
         "reconciliation_state": RECONCILIATION_PROVISIONAL},
        {"repo": "local/b", "source_root": str(tmp_path / "x"),
         "reconciliation_state": RECONCILIATION_PROVISIONAL},
        {"repo": "local/c", "source_root": str(tmp_path / "x")},  # not provisional
        {"repo": "local/d", "source_root": str(tmp_path / "y"),
         "reconciliation_state": RECONCILIATION_PROVISIONAL},  # different root
    ]
    assert count_provisional_for_root(rows, str(tmp_path / "x")) == 2


# ── B2: legacy_index_present disclosure ───────────────────────────────────

def test_legacy_index_present_disclosure(tmp_path, monkeypatch):
    # Two corpora with the same basename ("docs") under different parents; both
    # non-git so they carry no identity fields (legacy-shaped).
    monkeypatch.setattr(wc, "_git_probe", lambda cwd, args: (False, "", GIT_NOT_A_REPO))
    store_dir = tmp_path / "store"
    first = _docdir(tmp_path / "a", "docs")
    second = _docdir(tmp_path / "b", "docs")
    _index(first, store_dir, name="first")
    out = _index(second, store_dir, name="second")
    assert out["success"] is True
    assert "local/first" in out["legacy_index_present"]["handles"]


def test_legacy_sibling_handles_excludes_same_root_and_identity_indexes(tmp_path):
    root = str(tmp_path / "docs")
    rows = [
        {"repo": "local/same", "source_root": root},                      # same root
        {"repo": "local/ident", "source_root": str(tmp_path / "other" / "docs"),
         "corpus_identity_version": 1},                                    # already in system
        {"repo": "local/legacy", "source_root": str(tmp_path / "b" / "docs")},  # legacy sibling
        {"repo": "github/x", "source_root": str(tmp_path / "c" / "docs")},  # not local
    ]
    got = legacy_sibling_handles(rows, root)
    assert got == ["local/legacy"]


# ── B4: vocabulary drift-guard ────────────────────────────────────────────

def test_part_b_vocabulary_is_documented():
    """Every Part B status/reason_code the runtime can emit must appear in the
    documented allowlist. Adding a new one without documenting it fails here
    (the teeth for the #84 item-4 vocabulary contract until Part C publishes
    the full runtime-matched table)."""
    documented_statuses = {
        wc.STATUS_EXACT, wc.STATUS_CREATED, wc.STATUS_REUSABLE,
        wc.STATUS_REFERENCE_ONLY, wc.STATUS_AMBIGUOUS, wc.STATUS_RELATED,
        wc.STATUS_UNKNOWN, wc.STATUS_NO_MATCH,
    }
    documented_reason_codes = {
        REASON_PROVISIONAL_CREATED, REASON_PROVISIONAL_CAP,
        # jdoc#80 Part C graduation outcomes (v1.107.0):
        wc.REASON_GRADUATED, wc.REASON_RECONCILED,
        wc.REASON_GRADUATION_AMBIGUOUS, wc.REASON_GRADUATION_DIVERGED,
    }
    # Enumerate the STATUS_* constants actually defined on the module.
    live_statuses = {
        v for k, v in vars(wc).items()
        if k.startswith("STATUS_") and isinstance(v, str)
    }
    assert live_statuses == documented_statuses
    live_reasons = {
        v for k, v in vars(wc).items()
        if k.startswith("REASON_") and isinstance(v, str)
    }
    assert live_reasons == documented_reason_codes
