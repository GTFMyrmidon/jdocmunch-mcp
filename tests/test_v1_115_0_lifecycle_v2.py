"""Lifecycle coordination regressions for the v1.115.0 candidate (revision 2).

Revision 2 makes the two race regressions design-neutral: they accept either
a retirement that WAITS for in-flight retained-handle work and re-proves, or
one that FAILS CLOSED with a non-"retired" outcome. They reject only the
current behavior: completing as "retired" against a peer that concurrent,
already-running work is about to change or remove. Tests that exercise the
optional pair-lock helper API are skipped when that API is absent, so the
file is runnable against any chosen fix design.

Classification per test (unfixed tree = PR #92 head e1ca39e, Windows):
  regression (fails on unfixed tree):
    - test_retirement_waits_for_retained_delete
    - test_retirement_waits_for_retained_save
    - test_public_delete_reports_lifecycle_busy
  acceptance (requires the fix's new public contract; fails or is vacuous
  on the unfixed tree):
    - test_public_delete_reports_deleted_then_missing (fails: no reason_code)
    - test_delete_preserves_the_coordination_lockfile (vacuously passes on
      Windows -- the deleter itself holds the lockfile open so the branch's
      best-effort unlink fails silently; on POSIX it fails until fixed)
    - test_three_processes_keep_one_lock_inode (Linux only)
  conditional (pair-lock design path only; skipped when API absent):
    - test_pair_lock_order_is_independent_of_argument_order
    - test_pair_coordination_is_cross_process
"""

from __future__ import annotations

import multiprocessing
import os
import queue
import sys
import threading

import pytest

from jdocmunch_mcp.storage import retirements
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.delete_index import delete_index as public_delete
from jdocmunch_mcp.tools.index_local import _execute_retirement
from tests import test_v1_110_0 as legacy

_PAIR_LOCK_API = hasattr(DocStore, "hold_index_locks")


def _pair(tmp_path, monkeypatch):
    _, _, _, store = legacy._standard_pair(tmp_path, monkeypatch)
    return store, DocStore(base_path=str(store))


def _retire(store: DocStore):
    return _execute_retirement(
        store,
        "local",
        "old",
        "local/old",
        "local/modern",
        "lifecycle-test",
        lambda selected, retained: selected is not None and retained is not None,
    )


def _process_public_delete(base_path, output):
    output.put(public_delete("local/modern", storage_path=base_path))


def _process_incremental_save(base_path, output):
    saved = DocStore(base_path=base_path).incremental_save(
        "local",
        "modern",
        [],
        [],
        [],
        [],
        {},
        {},
    )
    output.put(saved is not None)


def _process_hold_lock(base_path, locked, release, output):
    store = DocStore(base_path=base_path)
    with store._index_write_lock("local", "qa15"):
        stat = os.stat(store._index_path("local", "qa15").with_name(
            "qa15.json.lock"
        ))
        output.put((stat.st_dev, stat.st_ino))
        locked.set()
        if not release.wait(15):
            raise TimeoutError("lock holder was not released")


def _process_blocking_delete(base_path, output):
    # jdoc#95 QA-25: state the intent rather than relying on the default.
    # This is the internal blocking deleter; contention here is an ordinary
    # cross-process writer that will release, so waiting is correct.
    output.put(
        DocStore(base_path=base_path).delete_index(
            "local", "qa15", lock_wait=True
        )
    )


def _process_public_try_delete(base_path, output):
    result = public_delete("local/qa15", storage_path=base_path)
    output.put((result["success"], result.get("reason_code")))


def test_retirement_waits_for_retained_delete(tmp_path, monkeypatch):
    """An in-flight retained-handle delete must not be crossed by retirement.

    Design-neutral contract: the fixed tree may make the retirement WAIT for
    the delete and re-prove, or FAIL CLOSED with a non-"retired" outcome
    while the delete is in flight. It may never complete as "retired"
    against a peer an already-running delete is about to remove. On the
    unfixed tree this interleaving finishes with BOTH participating indexes
    absent while both operations report success.
    """
    store_path, store = _pair(tmp_path, monkeypatch)
    scanned = threading.Event()
    release_delete = threading.Event()
    retirement_done = threading.Event()
    real_try_void = retirements.try_void_retirements_referencing
    delete_result = {}
    retirement_result = {}

    def pause_after_reverse_scan(base_path, handle, timeout_seconds=1.0):
        result = real_try_void(base_path, handle, timeout_seconds)
        if handle == "local/modern":
            scanned.set()
            assert release_delete.wait(10)
        return result

    monkeypatch.setattr(
        retirements,
        "try_void_retirements_referencing",
        pause_after_reverse_scan,
    )

    def delete_retained():
        delete_result["value"] = DocStore(
            base_path=str(store_path)
        ).delete_index("local", "modern")

    def retire_old():
        retirement_result["value"] = _retire(store)
        retirement_done.set()

    delete_thread = threading.Thread(target=delete_retained)
    retirement_thread = threading.Thread(target=retire_old)
    delete_thread.start()
    assert scanned.wait(10)
    retirement_thread.start()
    finished_early = retirement_done.wait(0.5)
    early_result = retirement_result.get("value") if finished_early else None
    release_delete.set()
    delete_thread.join(10)
    retirement_thread.join(10)

    assert not delete_thread.is_alive()
    assert not retirement_thread.is_alive()
    assert delete_result["value"] is True
    assert (
        store.load_index("local", "old") is not None
        or store.load_index("local", "modern") is not None
    ), "both participating indexes are absent after delete + retirement"
    if early_result is not None:
        assert early_result[0] != "retired", (
            "retirement completed as 'retired' while a retained-handle "
            "delete that had already passed its reverse-record scan was "
            "still in flight"
        )


def test_retirement_waits_for_retained_save(tmp_path, monkeypatch):
    """An unpublished retained-handle save must not be crossed by retirement.

    Design-neutral contract: while the save's replacement is unpublished,
    the retirement may wait and then re-prove, or fail closed with a
    non-"retired" outcome. It may not complete as "retired": the QA-09/QA-10
    policy makes a retained-handle rewrite void the pending record so proof
    is re-run, and completing before the save lands skips that voiding
    contract silently.
    """
    store_path, store = _pair(tmp_path, monkeypatch)
    writer = DocStore(base_path=str(store_path))
    save_ready = threading.Event()
    release_save = threading.Event()
    retirement_done = threading.Event()
    real_replace = DocStore._atomic_replace
    retirement_result = {}
    save_result = {}

    def pause_before_replace(tmp_file, index_file, *args, **kwargs):
        save_ready.set()
        assert release_save.wait(10)
        return real_replace(tmp_file, index_file, *args, **kwargs)

    monkeypatch.setattr(writer, "_atomic_replace", pause_before_replace)

    def save_retained():
        save_result["value"] = writer.incremental_save(
            "local",
            "modern",
            [],
            [],
            [],
            [],
            {},
            {},
        )

    def retire_old():
        retirement_result["value"] = _retire(store)
        retirement_done.set()

    save_thread = threading.Thread(target=save_retained)
    retirement_thread = threading.Thread(target=retire_old)
    save_thread.start()
    assert save_ready.wait(10)
    retirement_thread.start()
    finished_early = retirement_done.wait(0.5)
    early_result = retirement_result.get("value") if finished_early else None
    release_save.set()
    save_thread.join(10)
    retirement_thread.join(10)

    assert not save_thread.is_alive()
    assert not retirement_thread.is_alive()
    assert save_result["value"] is not None
    assert store.load_index("local", "modern") is not None
    if early_result is not None:
        assert early_result[0] != "retired", (
            "retirement completed as 'retired' while the retained-handle "
            "save was still waiting to publish its replacement"
        )


def test_public_delete_reports_lifecycle_busy(tmp_path, monkeypatch):
    """A temporary lifecycle refusal must not be reported as a missing index."""
    store_path, store = _pair(tmp_path, monkeypatch)
    fingerprints = {
        "local/old": store.index_fingerprint("local", "old"),
        "local/modern": store.index_fingerprint("local", "modern"),
    }
    assert all(fingerprints.values())
    assert retirements.begin_retirement(
        str(store_path),
        "local",
        "old",
        retained="local/modern",
        fingerprints=fingerprints,
        family="lifecycle-test",
    )

    locked = threading.Event()
    release = threading.Event()

    def hold_gate():
        with retirements.hold_record_lock(str(store_path), "local", "old"):
            locked.set()
            assert release.wait(10)

    holder = threading.Thread(target=hold_gate)
    holder.start()
    assert locked.wait(10)
    try:
        result = public_delete(
            "local/modern",
            storage_path=str(store_path),
        )
    finally:
        release.set()
        holder.join(10)

    assert not holder.is_alive()
    assert result["success"] is False
    assert result["reason_code"] == "index_lifecycle_busy"
    assert result["retryable"] is True
    assert "retry" in result["message"].lower()


def test_delete_preserves_the_coordination_lockfile(tmp_path, monkeypatch):
    """A completed delete must not replace the lock inode for later writers.

    Note: on Windows this passes even against the unfixed tree, because the
    deleter itself holds the lockfile open and the branch's best-effort
    unlink fails silently. The assertion is meaningful on POSIX, where the
    unfixed unlink succeeds mid-critical-section.
    """
    store_path, store = _pair(tmp_path, monkeypatch)
    lock_path = store._index_path("local", "old").with_name("old.json.lock")
    with store._index_write_lock("local", "old"):
        pass
    assert lock_path.exists()

    assert store.delete_index("local", "old") is True
    assert lock_path.exists()


@pytest.mark.skipif(
    not _PAIR_LOCK_API,
    reason="pair-lock helper API absent (applies only to the "
           "canonical-order pair-lock design path)",
)
def test_pair_lock_order_is_independent_of_argument_order(tmp_path, monkeypatch):
    """Opposite handle order must not deadlock overlapping operations."""
    _, store = _pair(tmp_path, monkeypatch)
    entered = []
    start = threading.Barrier(3)

    def hold(handles, marker):
        start.wait()
        with store.hold_index_locks(handles):
            entered.append(marker)

    first = threading.Thread(
        target=hold,
        args=(
            (("local", "old"), ("local", "modern")),
            "first",
        ),
    )
    second = threading.Thread(
        target=hold,
        args=(
            (("local", "modern"), ("local", "old")),
            "second",
        ),
    )
    first.start()
    second.start()
    start.wait()
    first.join(10)
    second.join(10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert sorted(entered) == ["first", "second"]


def test_public_delete_reports_deleted_then_missing(tmp_path, monkeypatch):
    """The additive response fields preserve ordinary delete outcomes."""
    store_path, _ = _pair(tmp_path, monkeypatch)

    deleted = public_delete("local/old", storage_path=str(store_path))
    missing = public_delete("local/old", storage_path=str(store_path))

    assert deleted["success"] is True
    assert deleted["reason_code"] == "index_deleted"
    assert deleted["retryable"] is False
    assert missing["success"] is False
    assert missing["reason_code"] == "index_not_found"
    assert missing["retryable"] is False


@pytest.mark.skipif(
    not _PAIR_LOCK_API,
    reason="pair-lock helper API absent (applies only to the "
           "canonical-order pair-lock design path)",
)
def test_pair_coordination_is_cross_process(tmp_path, monkeypatch):
    """Other processes report a delete busy and make a save wait."""
    store_path, store = _pair(tmp_path, monkeypatch)
    context = multiprocessing.get_context("spawn")
    delete_output = context.Queue()
    save_output = context.Queue()
    deleting = context.Process(
        target=_process_public_delete,
        args=(str(store_path), delete_output),
    )
    saving = context.Process(
        target=_process_incremental_save,
        args=(str(store_path), save_output),
    )

    with store.hold_index_locks(
        (("local", "old"), ("local", "modern"))
    ):
        deleting.start()
        saving.start()
        delete_result = delete_output.get(timeout=15)
        assert delete_result["reason_code"] == "index_lifecycle_busy"
        assert delete_result["retryable"] is True
        with pytest.raises(queue.Empty):
            save_output.get(timeout=0.5)

    assert save_output.get(timeout=15) is True
    deleting.join(10)
    saving.join(10)
    assert deleting.exitcode == 0
    assert saving.exitcode == 0


@pytest.mark.skipif(sys.platform != "linux", reason="POSIX inode regression")
def test_three_processes_keep_one_lock_inode(tmp_path):
    """Holder, deleter, and contender must coordinate on one POSIX inode.

    Scope honesty (QA-24): this proves that normal deletion preserves one
    stable lockfile inode and that a holder, a blocking internal deleter,
    and a nonblocking public contender stay coordinated on it. It does NOT
    unlink and recreate the lock pathname; external pathname replacement is
    a separate claim needing its own test if documented.
    """
    base_path = tmp_path / "store"
    index_path = base_path / "local" / "qa15.json"
    index_path.parent.mkdir(parents=True)
    index_path.write_text("{}", encoding="utf-8")
    lock_path = index_path.with_name("qa15.json.lock")

    context = multiprocessing.get_context("spawn")
    locked = context.Event()
    release = context.Event()
    holder_output = context.Queue()
    delete_output = context.Queue()
    contender_output = context.Queue()
    holder = context.Process(
        target=_process_hold_lock,
        args=(str(base_path), locked, release, holder_output),
    )
    deleter = context.Process(
        target=_process_blocking_delete,
        args=(str(base_path), delete_output),
    )
    contender = context.Process(
        target=_process_public_try_delete,
        args=(str(base_path), contender_output),
    )

    holder.start()
    assert locked.wait(15)
    original_identity = holder_output.get(timeout=15)
    deleter.start()
    with pytest.raises(queue.Empty):
        delete_output.get(timeout=0.5)
    contender.start()
    assert contender_output.get(timeout=15) == (
        False,
        "index_lifecycle_busy",
    )

    release.set()
    assert delete_output.get(timeout=15) is True
    for process in (holder, deleter, contender):
        process.join(10)
        assert process.exitcode == 0

    final_stat = os.stat(lock_path)
    assert (final_stat.st_dev, final_stat.st_ino) == original_identity
    assert index_path.exists() is False
