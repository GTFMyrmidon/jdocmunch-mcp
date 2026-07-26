"""jdoc#90 QA-17: the retirement record is the PAIR coordination point.

The guarded delete's final gate (fingerprint re-verify, record-existence
check, primary unlink, record removal) executes under the retirement
record's lock. A delete of the RETAINED peer must void that record through
the same lock before touching anything; while the gate is closed the delete
is refused. No interleaving can finish with both participating indexes
absent.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from jdocmunch_mcp.storage import retirements
from jdocmunch_mcp.storage.doc_store import DocStore, RetirementConflict
from tests import test_v1_110_0 as legacy


def _pair_with_record(tmp_path, monkeypatch):
    _, _, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    ds = DocStore(base_path=str(store))
    fingerprints = {
        "local/old": ds.index_fingerprint("local", "old"),
        "local/modern": ds.index_fingerprint("local", "modern"),
    }
    assert all(fingerprints.values())
    assert retirements.begin_retirement(
        str(store), "local", "old",
        retained="local/modern", fingerprints=fingerprints,
        family="qa17",
    )
    return store, ds, fingerprints


def test_qa17_retained_delete_refused_inside_final_gate(
    tmp_path, monkeypatch
):
    """A retained-peer delete landing between the final check and the
    primary unlink is refused, and the retirement completes with the
    retained index intact — never both absent."""
    store, ds, fingerprints = _pair_with_record(tmp_path, monkeypatch)
    retiring_path = ds._index_path("local", "old")
    reached = threading.Event()
    release = threading.Event()
    real_unlink = Path.unlink

    def pause_at_primary_unlink(path, *args, **kwargs):
        if path == retiring_path:
            reached.set()
            assert release.wait(10)
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", pause_at_primary_unlink)
    result = {}

    def retire():
        try:
            result["removed"] = ds.delete_index(
                "local", "old", expected_fingerprints=fingerprints
            )
        except BaseException as exc:  # pragma: no cover - failure detail
            result["error"] = exc

    worker = threading.Thread(target=retire)
    worker.start()
    try:
        assert reached.wait(10)
        # The gate is closed: the retained-peer delete must be refused.
        # jdoc#95 QA-25: state the intent rather than relying on the default.
        # The gate holds this handle through work that cannot finish until the
        # test releases it, so this caller must refuse rather than wait.
        refused = DocStore(base_path=str(store)).delete_index(
            "local", "modern", lock_wait=False
        )
        assert refused is False
        assert ds.load_index("local", "modern") is not None
    finally:
        release.set()
        worker.join(10)

    assert not worker.is_alive()
    assert "error" not in result, result
    assert result.get("removed") is True
    # Retirement completed; the retained peer survived the whole exchange.
    assert ds.load_index("local", "modern") is not None
    assert ds.load_index("local", "old") is None
    # A retry of the refused delete succeeds once the gate is open.
    assert DocStore(base_path=str(store)).delete_index(
        "local", "modern"
    ) is True


def test_qa17_voided_record_conflicts_at_final_gate(tmp_path, monkeypatch):
    """A record voided after entry (retained-peer lifecycle in another
    process) turns the retirement into a conflict at the gate even when
    the fingerprints still match."""
    store, ds, fingerprints = _pair_with_record(tmp_path, monkeypatch)
    record_path = store / "local" / ".retirements" / "old.json"
    real_rmtree_target = ds._content_dir("local", "old")

    import jdocmunch_mcp.storage.doc_store as doc_store_module
    real_rmtree = doc_store_module.shutil.rmtree
    fired = False

    def void_then_rmtree(path, *args, **kwargs):
        nonlocal fired
        if not fired and Path(path) == real_rmtree_target:
            fired = True
            record_path.unlink()  # the void, with fingerprints untouched
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(doc_store_module.shutil, "rmtree", void_then_rmtree)
    with pytest.raises(RetirementConflict) as exc:
        ds.delete_index("local", "old", expected_fingerprints=fingerprints)
    assert exc.value.changed == ["local/modern"]
    assert ds.load_index("local", "old") is not None
    assert ds.load_index("local", "modern") is not None


def test_qa17_retained_delete_before_gate_voids_and_wins(
    tmp_path, monkeypatch
):
    """Before the gate closes, a retained-peer delete voids the record and
    proceeds; the retirement then conflicts and keeps the retiring handle
    — at least one index always survives."""
    store, ds, fingerprints = _pair_with_record(tmp_path, monkeypatch)
    assert DocStore(base_path=str(store)).delete_index(
        "local", "modern"
    ) is True
    assert retirements.pending_retirement(str(store), "local", "old") is None
    with pytest.raises(RetirementConflict):
        ds.delete_index("local", "old", expected_fingerprints=fingerprints)
    assert ds.load_index("local", "old") is not None


def test_qa17_completed_gate_leaves_no_pending_record(tmp_path, monkeypatch):
    """The record is removed inside the gate: the moment the guarded delete
    returns, no observer can see index-gone-but-record-pending."""
    store, ds, fingerprints = _pair_with_record(tmp_path, monkeypatch)
    assert ds.delete_index(
        "local", "old", expected_fingerprints=fingerprints
    ) is True
    assert retirements.pending_retirement(str(store), "local", "old") is None
    assert (store / "local" / ".retirements" / "old.json").exists() is False
