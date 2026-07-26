"""jdoc#88 QA-01/QA-03 — coordinated & recoverable retirement (v1.115.0).

QA-01: every retirement path re-verifies proof-time fingerprints INSIDE the
deletion boundary (guarded delete); a changed retained or retiring index
voids the retirement instead of being removed under the stale decision.

QA-03: legacy_reconcile="report" is proof-only — it never enters a save path,
proving from stored snapshots plus live Git evidence.

Recoverability: a durable retiring record survives a failed cleanup; a
refresh of a retiring handle cancels the stale retirement.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore, RetirementConflict
from jdocmunch_mcp.storage.retirements import (
    begin_retirement,
    pending_retirement,
)
from jdocmunch_mcp.tools import _worktree_corpus as wc
from tests import test_v1_109_0 as modern
from tests import test_v1_110_0 as legacy


def _snapshot_tree(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _corrupt_stored_hash(store: Path, name: str) -> None:
    path = DocStore(base_path=str(store))._index_path("local", name)
    data = json.loads(path.read_text(encoding="utf-8"))
    key = sorted(data["file_hashes"])[0]
    data["file_hashes"][key] = "0" * 64
    data["source_dirty"] = True
    data["sha_certified"] = False
    path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")


def _mutate_on_delete(monkeypatch, store: Path, *, removed: str, changed: str):
    """Model an index changing after approval but before removal completes."""
    real_delete = DocStore.delete_index

    def change_then_continue(self, owner, name, *args, **kwargs):
        if name == removed:
            _corrupt_stored_hash(store, changed)
        return real_delete(self, owner, name, *args, **kwargs)

    monkeypatch.setattr(DocStore, "delete_index", change_then_continue)


# --- guarded delete (unit) --------------------------------------------------

def test_guarded_delete_mismatch_raises_and_removes_nothing(tmp_path, monkeypatch):
    _, wt, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    ds = DocStore(base_path=str(store))
    stale = {"local/old": "0" * 64}
    with pytest.raises(RetirementConflict) as exc:
        ds.delete_index("local", "old", expected_fingerprints=stale)
    assert exc.value.changed == ["local/old"]
    assert ds.load_index("local", "old") is not None


def test_guarded_delete_match_deletes(tmp_path, monkeypatch):
    _, wt, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    ds = DocStore(base_path=str(store))
    fps = {
        "local/old": ds.index_fingerprint("local", "old"),
        "local/modern": ds.index_fingerprint("local", "modern"),
    }
    publication = begin_retirement(
        str(store),
        "local",
        "old",
        retained="local/modern",
        fingerprints=fps,
        family="guarded-delete-unit",
    )
    assert publication
    assert ds.delete_index("local", "old", expected_fingerprints=fps) is True
    assert ds.load_index("local", "old") is None


# --- QA-01: conflict instead of stale-decision removal ----------------------

@pytest.mark.parametrize("changed", ["modern", "old"])
def test_legacy_apply_conflict_keeps_both(tmp_path, monkeypatch, changed):
    _, wt, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    _mutate_on_delete(monkeypatch, store, removed="old", changed=changed)
    out = legacy._index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert out["success"], out
    block = out["legacy_reconciliation"]
    assert block["reason_code"] == wc.REASON_LEGACY_CONFLICT
    assert block["changed_handles"] == [f"local/{changed}"]
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "old") is not None
    assert ds.load_index("local", "modern") is not None
    # Voided retirement leaves no pending record.
    assert pending_retirement(str(store), "local", "old") is None


def test_supersession_conflict_keeps_both(tmp_path, monkeypatch):
    repo, wt, _, _ = modern._linear_repo(tmp_path)
    store = tmp_path / "store"
    assert modern._index(repo / "docs", "established", store)["success"]
    modern._create_provisional(monkeypatch, wt / "docs", store, "provisional")
    _mutate_on_delete(
        monkeypatch, store, removed="provisional", changed="established"
    )
    out = modern._index(wt / "docs", "provisional", store)
    assert out["success"], out
    block = out["reconciliation"]
    assert block["reason_code"] == wc.REASON_SUPERSESSION_CONFLICT
    assert block["changed_handles"] == ["local/established"]
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "provisional") is not None
    assert ds.load_index("local", "established") is not None


def test_exact_dedup_conflict_keeps_both(tmp_path, monkeypatch):
    repo, wt, _ = legacy._twin_repo(tmp_path)
    store = tmp_path / "store"
    assert legacy._index(repo / "docs", "established", store)["success"]
    modern._create_provisional(monkeypatch, wt / "docs", store, "provisional")
    _mutate_on_delete(
        monkeypatch, store, removed="provisional", changed="established"
    )
    out = legacy._index(wt / "docs", "provisional", store)
    block = out["reconciliation"]
    assert block["reason_code"] == wc.REASON_GRADUATION_CONFLICT
    assert block["changed_handles"] == ["local/established"]
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "provisional") is not None
    assert ds.load_index("local", "established") is not None


# --- QA-03: report is proof-only --------------------------------------------

def test_report_ready_writes_nothing(tmp_path, monkeypatch):
    _, wt, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    before = _snapshot_tree(store)
    out = legacy._index(wt / "docs", "old", store, legacy_reconcile="report")
    assert out["success"], out
    assert out["legacy_reconciliation"]["reason_code"] == wc.REASON_LEGACY_READY
    assert out["_meta"]["read_only"] is True
    assert _snapshot_tree(store) == before


def test_report_changed_source_writes_nothing(tmp_path, monkeypatch):
    _, wt, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    (wt / "docs" / "guide.md").write_bytes(
        legacy.GUIDE_BYTES + b"\nlocal uncommitted edit\n"
    )
    before = _snapshot_tree(store)
    out = legacy._index(wt / "docs", "old", store, legacy_reconcile="report")
    assert out["success"], out
    # A dirty checkout breaks certification transitivity: stored snapshots no
    # longer provably describe the live tree, so report says not-ready.
    block = out["legacy_reconciliation"]
    assert block["reason_code"] == wc.REASON_LEGACY_UNCERTIFIED
    assert block["checkout_sha"]
    assert _snapshot_tree(store) == before


def test_report_no_peer_writes_nothing(tmp_path, monkeypatch):
    repo, wt, _ = legacy._twin_repo(tmp_path)
    store = tmp_path / "store"
    legacy._plant_legacy(monkeypatch, wt / "docs", store, "old")
    before = _snapshot_tree(store)
    out = legacy._index(wt / "docs", "old", store, legacy_reconcile="report")
    assert out["success"], out
    assert (
        out["legacy_reconciliation"]["reason_code"]
        == wc.REASON_LEGACY_NO_MODERN_PEER
    )
    assert _snapshot_tree(store) == before


# --- recoverability: durable retiring record --------------------------------

def test_failed_cleanup_leaves_pending_record_and_save_cancels(
    tmp_path, monkeypatch
):
    _, wt, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(
        DocStore, "delete_index", lambda self, o, n, **kw: False
    )
    out = legacy._index(wt / "docs", "old", store, legacy_reconcile="apply")
    block = out["legacy_reconciliation"]
    assert block["reason_code"] == wc.REASON_LEGACY_CLEANUP_INCOMPLETE
    assert block["pending_retirement"] is True
    record = pending_retirement(str(store), "local", "old")
    assert record is not None
    assert record["retained"] == "local/modern"
    assert record["family"] == "legacy_reconcile"
    assert record["fingerprints"]

    # A refresh of the retiring handle cancels the stale retirement.
    monkeypatch.undo()
    refreshed = legacy._index(wt / "docs", "old", store)
    assert refreshed["success"], refreshed
    assert pending_retirement(str(store), "local", "old") is None


def test_successful_retirement_leaves_no_record(tmp_path, monkeypatch):
    _, wt, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    out = legacy._index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert (
        out["legacy_reconciliation"]["reason_code"]
        == wc.REASON_LEGACY_RECONCILED
    )
    assert pending_retirement(str(store), "local", "old") is None
    assert DocStore(base_path=str(store)).load_index("local", "old") is None
