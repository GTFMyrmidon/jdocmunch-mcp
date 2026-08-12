"""jdoc#88 QA-02 (contained fixes): the retirement delete result is
authoritative, and a partial cleanup stays discoverable and retryable.

Reproduces the two QA-02 findings from @rknighton's adversarial harness as
permanent regressions. (QA-01 refresh/retirement coordination and QA-03
read-only report are the follow-on coordinated-retirement release.)
"""
from __future__ import annotations

from pathlib import Path

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.storage import doc_store as doc_store_module
from jdocmunch_mcp.tools import _worktree_corpus as wc
from tests import test_v1_109_0 as modern
from tests import test_v1_110_0 as legacy


def _exact_duplicate_pair(tmp_path: Path, monkeypatch):
    """Established peer + a provisional exact-duplicate at a linked worktree."""
    repo, wt, _ = legacy._twin_repo(tmp_path)
    store = tmp_path / "store"
    established = legacy._index(repo / "docs", "established", store)
    assert established["success"], established
    modern._create_provisional(monkeypatch, wt / "docs", store, "provisional")
    return wt, store


# ── QA-02.1: exact-duplicate graduation honors the delete result ──────────

def test_exact_dedup_delete_failure_is_not_reported_reconciled(
    tmp_path: Path, monkeypatch
):
    wt, store = _exact_duplicate_pair(tmp_path, monkeypatch)

    # Retirement removal fails outright.
    monkeypatch.setattr(DocStore, "delete_index", lambda *a, **k: False)
    out = legacy._index(wt / "docs", "provisional", store)

    # The reconcile did NOT happen: never claim reconciled, never emit a
    # removed_handle for a loser that still exists.
    recon = out["reconciliation"]
    assert recon["reason_code"] == wc.REASON_GRADUATION_CLEANUP_INCOMPLETE
    assert recon["reason_code"] != wc.REASON_RECONCILED
    assert "removed_handle" not in recon
    # The provisional stays discoverable and the peer is untouched.
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "provisional") is not None
    assert ds.load_index("local", "established") is not None


def test_exact_dedup_delete_success_still_reconciles(tmp_path: Path, monkeypatch):
    """The fix must not change the happy path: a real removal still reconciles
    and returns the established handle with a removed_handle."""
    wt, store = _exact_duplicate_pair(tmp_path, monkeypatch)
    out = legacy._index(wt / "docs", "provisional", store)

    recon = out["reconciliation"]
    assert recon["reason_code"] == wc.REASON_RECONCILED
    assert recon["removed_handle"] == "local/provisional"
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "provisional") is None
    assert ds.load_index("local", "established") is not None


# ── QA-02.2: partial cleanup keeps the loser discoverable + retryable ─────

def test_legacy_partial_cleanup_stays_discoverable_and_retryable(
    tmp_path: Path, monkeypatch
):
    _, wt, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    real_rmtree = doc_store_module.shutil.rmtree

    def fail_loser_content_delete(path, *args, **kwargs):
        if Path(path).name == "old":
            raise OSError("injected content-directory removal failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(
        doc_store_module.shutil, "rmtree", fail_loser_content_delete
    )
    out = legacy._index(wt / "docs", "old", store, legacy_reconcile="apply")

    assert out["success"], out
    assert (
        out["legacy_reconciliation"]["reason_code"]
        == wc.REASON_LEGACY_CLEANUP_INCOMPLETE
    )
    # The primary record is removed LAST, so a mid-cleanup failure leaves the
    # loser fully loadable — the documented retry can find the handle.
    assert DocStore(base_path=str(store)).load_index("local", "old") is not None

    monkeypatch.undo()
    retried = legacy._index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert (
        retried.get("legacy_reconciliation", {}).get("reason_code")
        == wc.REASON_LEGACY_RECONCILED
    )


# ── delete_index ordering invariant (the primitive behind QA-02.2) ────────

def test_delete_index_removes_primary_record_last(tmp_path: Path, monkeypatch):
    """A content-cache removal failure must leave the primary <name>.json
    intact, so the index stays loadable and the delete is retryable."""
    store = tmp_path / "store"
    ds = DocStore(base_path=str(store))
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# A\n\nbody\n", encoding="utf-8")
    from jdocmunch_mcp.tools.index_local import index_local

    created = index_local(
        path=str(docs), name="solo", storage_path=str(store),
        use_ai_summaries=False, use_embeddings=False,
    )
    assert created["success"], created
    assert ds.load_index("local", "solo") is not None

    real_rmtree = doc_store_module.shutil.rmtree

    def boom(path, *a, **k):
        raise OSError("injected cache removal failure")

    monkeypatch.setattr(doc_store_module.shutil, "rmtree", boom)
    try:
        ds.delete_index("local", "solo")
    except OSError:
        pass
    # Primary record survived the failed cleanup → still discoverable.
    assert DocStore(base_path=str(store)).load_index("local", "solo") is not None

    monkeypatch.undo()
    assert ds.delete_index("local", "solo") is True
    assert DocStore(base_path=str(store)).load_index("local", "solo") is None
