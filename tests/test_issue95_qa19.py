"""Issue #95 QA-19 lifecycle-authority regressions."""

from __future__ import annotations

import json
import multiprocessing
from contextlib import contextmanager

import pytest

from jdocmunch_mcp.storage import retirements
from jdocmunch_mcp.storage.doc_store import DocStore, RetirementConflict
from jdocmunch_mcp.tools import _worktree_corpus as wc
from tests import test_v1_110_0 as legacy


def _pair(tmp_path, monkeypatch):
    _, _, _, store_path = legacy._standard_pair(tmp_path, monkeypatch)
    return store_path, DocStore(base_path=str(store_path))


def _fingerprints(store):
    values = {
        "local/old": store.index_fingerprint("local", "old"),
        "local/modern": store.index_fingerprint("local", "modern"),
    }
    assert all(values.values())
    return values


def _publish(store_path, store, retained="local/modern"):
    return retirements.begin_retirement(
        str(store_path),
        "local",
        "old",
        retained=retained,
        fingerprints=_fingerprints(store),
        family="qa19",
    )


def _publication_worker(store_path, retained, ready, start, output):
    store = DocStore(base_path=store_path)
    ready.set()
    if not start.wait(15):
        raise TimeoutError("publisher start barrier was not released")
    publication = retirements.begin_retirement(
        store_path,
        "local",
        "old",
        retained=retained,
        fingerprints=_fingerprints(store),
        family="qa19-process",
    )
    output.put(publication)


def test_publication_receipt_is_unique_and_persisted(tmp_path, monkeypatch):
    store_path, store = _pair(tmp_path, monkeypatch)

    first = _publish(store_path, store)
    second = _publish(store_path, store)
    record = retirements.pending_retirement(str(store_path), "local", "old")

    assert isinstance(first, str) and first
    assert isinstance(second, str) and second
    assert first != second
    assert record["publication_id"] == second


def test_older_completion_cannot_remove_newer_publication(tmp_path, monkeypatch):
    store_path, store = _pair(tmp_path, monkeypatch)
    first = _publish(store_path, store)
    second = _publish(store_path, store)

    assert retirements.finish_retirement(
        str(store_path), "local", "old", publication_id=first
    ) is False
    record = retirements.pending_retirement(str(store_path), "local", "old")
    assert record["publication_id"] == second


def test_publication_fails_closed_when_record_lock_is_unavailable(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    monkeypatch.setattr(retirements, "_acquire_fd", lambda *args, **kwargs: None)

    assert _publish(store_path, store) is None
    record_path = store_path / "local" / ".retirements" / "old.json"
    assert record_path.exists() is False


def test_publication_revalidates_fingerprints_after_lock_acquisition(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    fingerprints = _fingerprints(store)
    retained_path = store._index_path("local", "modern")
    real_acquire = retirements._acquire_fd
    changed = False

    def mutate_before_lock(lock_path, blocking):
        nonlocal changed
        if not changed:
            changed = True
            data = json.loads(retained_path.read_text(encoding="utf-8"))
            data["source_dirty"] = not data.get("source_dirty", False)
            retained_path.write_text(
                json.dumps(data, separators=(",", ":")), encoding="utf-8"
            )
        return real_acquire(lock_path, blocking)

    monkeypatch.setattr(retirements, "_acquire_fd", mutate_before_lock)
    publication = retirements.begin_retirement(
        str(store_path),
        "local",
        "old",
        retained="local/modern",
        fingerprints=fingerprints,
        family="qa19",
    )

    assert changed is True
    assert publication is None
    assert retirements.retirement_record(
        str(store_path), "local", "old"
    ) is None


def test_authoritative_record_lock_never_yields_without_a_lock(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(retirements, "_acquire_fd", lambda *args, **kwargs: None)

    with pytest.raises(RuntimeError):
        with retirements.hold_record_lock(str(tmp_path), "local", "old"):
            pytest.fail("critical section entered without an authoritative lock")


def test_completion_lock_failure_preserves_the_publication(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    publication = _publish(store_path, store)
    monkeypatch.setattr(retirements, "_acquire_fd", lambda *args, **kwargs: None)

    assert retirements.finish_retirement(
        str(store_path), "local", "old", publication_id=publication
    ) is False
    current = retirements.retirement_record(str(store_path), "local", "old")
    assert current["publication_id"] == publication


def test_final_gate_lock_failure_keeps_the_retiring_monolith(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    publication = _publish(store_path, store)
    monkeypatch.setattr(retirements, "_acquire_fd", lambda *args, **kwargs: None)

    with pytest.raises(RetirementConflict):
        store.delete_index(
            "local",
            "old",
            expected_fingerprints=_fingerprints(store),
            retirement_publication=publication,
            lock_wait=True,
        )

    assert store.load_index("local", "old") is not None


def test_final_gate_requires_the_exact_current_publication(tmp_path, monkeypatch):
    store_path, store = _pair(tmp_path, monkeypatch)
    first = _publish(store_path, store)
    second = _publish(store_path, store)

    with pytest.raises(RetirementConflict):
        store.delete_index(
            "local",
            "old",
            expected_fingerprints=_fingerprints(store),
            retirement_publication=first,
            lock_wait=True,
        )

    assert store.load_index("local", "old") is not None
    record = retirements.pending_retirement(str(store_path), "local", "old")
    assert record["publication_id"] == second


def test_guarded_delete_cannot_adopt_a_replacement_publication(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    first = _publish(store_path, store)
    second = _publish(store_path, store)
    assert first != second

    with pytest.raises(RetirementConflict):
        store.delete_index(
            "local",
            "old",
            expected_fingerprints=_fingerprints(store),
            lock_wait=True,
        )

    assert store.load_index("local", "old") is not None
    record = retirements.retirement_record(
        str(store_path), "local", "old"
    )
    assert record["publication_id"] == second


def test_guarded_delete_requires_a_current_publication(tmp_path, monkeypatch):
    store_path, store = _pair(tmp_path, monkeypatch)

    with pytest.raises(RetirementConflict):
        store.delete_index(
            "local",
            "old",
            expected_fingerprints=_fingerprints(store),
            lock_wait=True,
        )

    assert store.load_index("local", "old") is not None
    assert retirements.retirement_record(
        str(store_path), "local", "old"
    ) is None


def test_guarded_delete_rejects_an_unreadable_publication(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    record_path = store_path / "local" / ".retirements" / "old.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(RetirementConflict):
        store.delete_index(
            "local",
            "old",
            expected_fingerprints=_fingerprints(store),
            lock_wait=True,
        )

    assert store.load_index("local", "old") is not None
    assert record_path.read_text(encoding="utf-8") == "{not-json"


def test_completion_unlink_failure_reports_retired_with_pending_cleanup(
    tmp_path, monkeypatch
):
    _, worktree, _, store_path = legacy._standard_pair(
        tmp_path, monkeypatch
    )
    record_path = store_path / "local" / ".retirements" / "old.json"
    real_unlink = type(record_path).unlink

    def fail_record_unlink(path, *args, **kwargs):
        if path == record_path:
            raise OSError("injected publication completion failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(record_path), "unlink", fail_record_unlink)
    result = legacy._index(
        worktree / "docs",
        "old",
        store_path,
        legacy_reconcile="apply",
    )

    block = result["legacy_reconciliation"]
    assert block["reason_code"] == wc.REASON_LEGACY_RECONCILED
    assert block["retirement_cleanup_pending"] is True
    assert block["retirement_completion_marker_persisted"] is True
    record = retirements.pending_retirement(
        str(store_path), "local", "old"
    )
    assert isinstance(record["publication_id"], str)
    assert record["publication_id"]
    store = DocStore(base_path=str(store_path))
    assert store.load_index("local", "old") is None
    assert store.load_index("local", "modern") is not None


def test_marker_persistence_failure_is_disclosed_from_durable_state(
    tmp_path, monkeypatch
):
    _, worktree, _, store_path = legacy._standard_pair(
        tmp_path, monkeypatch
    )
    record_path = store_path / "local" / ".retirements" / "old.json"
    real_unlink = type(record_path).unlink
    real_replace = retirements.os.replace
    record_replacements = 0

    def fail_record_unlink(path, *args, **kwargs):
        if path == record_path:
            raise OSError("injected publication completion failure")
        return real_unlink(path, *args, **kwargs)

    def fail_marker_replace(source, destination, *args, **kwargs):
        nonlocal record_replacements
        if destination == record_path:
            record_replacements += 1
            if record_replacements > 1:
                raise OSError("injected completion marker failure")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(type(record_path), "unlink", fail_record_unlink)
    monkeypatch.setattr(retirements.os, "replace", fail_marker_replace)

    result = legacy._index(
        worktree / "docs",
        "old",
        store_path,
        legacy_reconcile="apply",
    )

    block = result["legacy_reconciliation"]
    assert block["reason_code"] == wc.REASON_LEGACY_RECONCILED
    assert block["retirement_cleanup_pending"] is True
    assert block["retirement_completion_marker_persisted"] is False
    record = retirements.pending_retirement(
        str(store_path), "local", "old"
    )
    assert record is not None
    assert record.get("completion_pending") is not True
    store = DocStore(base_path=str(store_path))
    assert store.load_index("local", "old") is None
    assert store.load_index("local", "modern") is not None


def test_pending_read_reports_record_when_stale_cleanup_unlink_fails(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    publication = _publish(store_path, store)
    record_path = store_path / "local" / ".retirements" / "old.json"
    store._index_path("local", "old").unlink()
    real_unlink = type(record_path).unlink

    def fail_record_unlink(path, *args, **kwargs):
        if path == record_path:
            raise OSError("injected stale-record cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(record_path), "unlink", fail_record_unlink)

    record = retirements.pending_retirement(
        str(store_path), "local", "old"
    )
    assert record is not None
    assert record["publication_id"] == publication
    assert record_path.exists()


def test_fingerprints_are_reproved_after_the_retained_gate(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    publication = _publish(store_path, store)
    fingerprints = _fingerprints(store)
    retained_path = store._index_path("local", "modern")

    @contextmanager
    def mutate_after_gate(retained, owner, name):
        data = json.loads(retained_path.read_text(encoding="utf-8"))
        data["source_dirty"] = not data.get("source_dirty", False)
        retained_path.write_text(
            json.dumps(data, separators=(",", ":")), encoding="utf-8"
        )
        yield True

    monkeypatch.setattr(store, "_gate_retained_handle", mutate_after_gate)

    with pytest.raises(RetirementConflict):
        store.delete_index(
            "local",
            "old",
            expected_fingerprints=fingerprints,
            retirement_publication=publication,
            lock_wait=True,
        )

    assert store.load_index("local", "old") is not None


def test_reverse_scan_revalidates_candidate_under_its_lock(tmp_path, monkeypatch):
    store_path, store = _pair(tmp_path, monkeypatch)
    first = _publish(store_path, store)
    record_path = store_path / "local" / ".retirements" / "old.json"
    replacement = json.loads(record_path.read_text(encoding="utf-8"))
    replacement["publication_id"] = f"{first}-replacement"
    replacement["retained"] = "local/other"
    real_acquire = retirements._acquire_fd
    replaced = False

    def replace_before_lock(lock_path, blocking):
        nonlocal replaced
        if not replaced:
            replaced = True
            record_path.write_text(
                json.dumps(replacement, separators=(",", ":")),
                encoding="utf-8",
            )
        return real_acquire(lock_path, blocking)

    monkeypatch.setattr(retirements, "_acquire_fd", replace_before_lock)
    retirements.void_retirements_referencing(
        str(store_path), "local/modern"
    )

    assert replaced is True
    current = json.loads(record_path.read_text(encoding="utf-8"))
    assert current["publication_id"] == replacement["publication_id"]


def test_stale_self_healing_revalidates_missing_index_under_lock(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    first = _publish(store_path, store)
    record_path = store_path / "local" / ".retirements" / "old.json"
    retiring_path = store._index_path("local", "old")
    retiring_bytes = retiring_path.read_bytes()
    retiring_path.unlink()
    replacement = json.loads(record_path.read_text(encoding="utf-8"))
    replacement["publication_id"] = f"{first}-replacement"
    real_acquire = retirements._acquire_fd
    replaced = False

    def recreate_before_lock(lock_path, blocking):
        nonlocal replaced
        if not replaced:
            replaced = True
            retiring_path.write_bytes(retiring_bytes)
            record_path.write_text(
                json.dumps(replacement, separators=(",", ":")),
                encoding="utf-8",
            )
        return real_acquire(lock_path, blocking)

    monkeypatch.setattr(retirements, "_acquire_fd", recreate_before_lock)
    current = retirements.pending_retirement(str(store_path), "local", "old")

    assert replaced is True
    assert current["publication_id"] == replacement["publication_id"]
    assert record_path.exists()


def test_cross_process_older_cleanup_cannot_remove_winning_publication(
    tmp_path, monkeypatch
):
    store_path, _ = _pair(tmp_path, monkeypatch)
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    ready = [context.Event(), context.Event()]
    output = context.Queue()
    publishers = [
        context.Process(
            target=_publication_worker,
            args=(str(store_path), retained, signal, start, output),
        )
        for retained, signal in zip(
            ("local/modern", "local/modern"), ready, strict=True
        )
    ]

    for process in publishers:
        process.start()
    assert all(signal.wait(15) for signal in ready)
    start.set()
    publications = [output.get(timeout=15), output.get(timeout=15)]
    for process in publishers:
        process.join(15)
        assert process.exitcode == 0

    record = retirements.pending_retirement(str(store_path), "local", "old")
    winner = record["publication_id"]
    loser = next(publication for publication in publications if publication != winner)
    assert retirements.finish_retirement(
        str(store_path), "local", "old", publication_id=loser
    ) is False
    assert retirements.pending_retirement(
        str(store_path), "local", "old"
    )["publication_id"] == winner
