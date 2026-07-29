"""Issue #95 QA-23 wait-policy and QA-21 stable-lockfile regressions."""

from __future__ import annotations

import inspect
import multiprocessing
import threading
import time

from jdocmunch_mcp.storage import retirements
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.delete_index import delete_index as public_delete
from tests import test_v1_110_0 as legacy


def _pair(tmp_path, monkeypatch):
    _, _, _, store_path = legacy._standard_pair(tmp_path, monkeypatch)
    return store_path, DocStore(base_path=str(store_path))


def _publish(store_path, store):
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
        family="qa23",
    )


def _hold_record_lock(base_path, ready, release):
    with retirements.hold_record_lock(base_path, "local", "old"):
        ready.set()
        if not release.wait(15):
            raise TimeoutError("record-lock holder was not released")


def _contended_record(store_path):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_record_lock,
        args=(str(store_path), ready, release),
    )
    holder.start()
    assert ready.wait(15)
    return holder, release


def _release_holder(holder, release):
    release.set()
    holder.join(15)
    assert holder.exitcode == 0


def test_public_record_lock_contention_returns_typed_busy_promptly(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    _publish(store_path, store)
    holder, release = _contended_record(store_path)

    started = time.perf_counter()
    try:
        result = public_delete(
            "local/modern",
            storage_path=str(store_path),
        )
        elapsed = time.perf_counter() - started
    finally:
        _release_holder(holder, release)

    assert elapsed < 0.5
    assert result["success"] is False
    assert result["reason_code"] == "index_lifecycle_busy"
    assert result["retryable"] is True
    assert store.load_index("local", "modern") is not None


def test_internal_record_lock_coordination_keeps_bounded_wait(
    tmp_path, monkeypatch
):
    store_path, store = _pair(tmp_path, monkeypatch)
    _publish(store_path, store)
    holder, release = _contended_record(store_path)
    timer = threading.Timer(0.2, release.set)
    outcome = {}

    timer.start()
    started = time.perf_counter()
    try:
        deleted = store.delete_index(
            "local",
            "modern",
            outcome=outcome,
            lock_wait=True,
        )
        elapsed = time.perf_counter() - started
    finally:
        timer.cancel()
        _release_holder(holder, release)

    assert elapsed >= 0.1
    assert elapsed < 1.0
    assert deleted is True
    assert outcome["reason_code"] == "index_deleted"


def test_wait_policy_defaults_remain_compatible():
    delete_default = inspect.signature(DocStore.delete_index).parameters[
        "lock_wait"
    ]
    helper_default = inspect.signature(
        retirements.try_void_retirements_referencing
    ).parameters["timeout_seconds"]

    assert delete_default.default is False
    assert helper_default.default == 1.0


def test_successful_delete_preserves_stable_lockfile(tmp_path, monkeypatch):
    store_path, store = _pair(tmp_path, monkeypatch)
    lock_path = store._index_path("local", "old").with_name("old.json.lock")
    with store._index_write_lock("local", "old"):
        pass
    before = lock_path.stat()

    assert store.delete_index("local", "old", lock_wait=False) is True

    after = lock_path.stat()
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
