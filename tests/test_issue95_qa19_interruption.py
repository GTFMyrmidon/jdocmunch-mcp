"""Real-process interruption recovery proofs for Issue #95 QA-19."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

from jdocmunch_mcp.storage import retirements
from jdocmunch_mcp.storage.doc_store import DocStore, RetirementConflict
from tests import test_v1_110_0 as legacy


_PROCESS_TIMEOUT = 15
_EXIT_AFTER_PUBLICATION = 91
_EXIT_AFTER_PRIMARY = 92
_EXIT_DURING_SELF_HEAL = 93


def _record_path(store_path: str) -> Path:
    return Path(store_path) / "local" / ".retirements" / "old.json"


def _disk_state(store_path: str) -> dict:
    record_path = _record_path(store_path)
    return {
        "primary_exists": (
            Path(store_path) / "local" / "old.json"
        ).is_file(),
        "record": (
            json.loads(record_path.read_text(encoding="utf-8"))
            if record_path.is_file()
            else None
        ),
    }


def _fingerprints(store: DocStore, retained: str = "local/modern") -> dict:
    return {
        "local/old": store.index_fingerprint("local", "old"),
        retained: store.index_fingerprint(*retained.split("/", 1)),
    }


def _publish(store_path: str, retained: str = "local/modern"):
    store = DocStore(base_path=store_path)
    return retirements.begin_retirement(
        store_path,
        "local",
        "old",
        retained=retained,
        fingerprints=_fingerprints(store, retained),
        family="qa19-interruption",
    )


def _trio(tmp_path, monkeypatch) -> tuple[Path, DocStore]:
    _, _, _, store_path = legacy._standard_pair(tmp_path, monkeypatch)
    store = DocStore(base_path=str(store_path))
    store.save_index(
        "local",
        "next",
        [],
        {"next.md": "# Next\n"},
        {".md": 1},
    )
    return store_path, store


def _join(process, label: str, expected_exit: int = 0) -> None:
    process.join(_PROCESS_TIMEOUT)
    if process.is_alive():
        process.terminate()
        process.join(5)
        raise AssertionError(f"{label} did not exit within {_PROCESS_TIMEOUT}s")
    assert process.exitcode == expected_exit, (
        f"{label} exit code {process.exitcode}, expected {expected_exit}"
    )


def _delete_from_record(store_path: str, output) -> None:
    record = json.loads(_record_path(store_path).read_text(encoding="utf-8"))
    store = DocStore(base_path=store_path)
    kwargs = {
        "expected_fingerprints": record["fingerprints"],
        "lock_wait": True,
    }
    publication_id = record.get("publication_id")
    if isinstance(publication_id, str):
        kwargs["retirement_publication"] = publication_id
    try:
        output.put(("removed", store.delete_index("local", "old", **kwargs)))
    except RetirementConflict as exc:
        output.put(("conflict", str(exc)))


def _pending_reader(store_path: str, output) -> None:
    output.put(
        retirements.pending_retirement(store_path, "local", "old")
    )


def _crash_after_publication(store_path: str, published) -> None:
    assert _publish(store_path)
    assert _record_path(store_path).is_file()
    published.set()
    os._exit(_EXIT_AFTER_PUBLICATION)


def _crash_after_primary_delete(store_path: str, deleted) -> None:
    store = DocStore(base_path=store_path)
    fingerprints = _fingerprints(store)
    publication = retirements.begin_retirement(
        store_path,
        "local",
        "old",
        retained="local/modern",
        fingerprints=fingerprints,
        family="qa19-interruption",
    )
    assert publication
    original_finish = retirements.finish_retirement

    def exit_before_completion(*args, **kwargs):
        assert store.load_index("local", "old") is None
        assert _record_path(store_path).is_file()
        deleted.set()
        os._exit(_EXIT_AFTER_PRIMARY)

    retirements.finish_retirement = exit_before_completion
    try:
        kwargs = {
            "expected_fingerprints": fingerprints,
            "lock_wait": True,
        }
        if isinstance(publication, str):
            kwargs["retirement_publication"] = publication
        store.delete_index("local", "old", **kwargs)
    finally:
        retirements.finish_retirement = original_finish


def _crash_during_self_heal(store_path: str, entered) -> None:
    original_unlink = Path.unlink
    record_path = _record_path(store_path)

    def exit_before_record_unlink(path, *args, **kwargs):
        if path == record_path:
            assert record_path.is_file()
            entered.set()
            os._exit(_EXIT_DURING_SELF_HEAL)
        return original_unlink(path, *args, **kwargs)

    Path.unlink = exit_before_record_unlink
    try:
        retirements.pending_retirement(store_path, "local", "old")
    finally:
        Path.unlink = original_unlink


def _publish_competing_after_recreation(store_path: str, output) -> None:
    store = DocStore(base_path=store_path)
    store.save_index(
        "local",
        "old",
        [],
        {"old.md": "# Recreated\n"},
        {".md": 1},
    )
    publication = _publish(store_path, retained="local/next")
    output.put(publication)


def _finish_older_publication(
    store_path: str, older_publication, output
) -> None:
    if isinstance(older_publication, str):
        removed = retirements.finish_retirement(
            store_path,
            "local",
            "old",
            publication_id=older_publication,
        )
    else:
        removed = retirements.finish_retirement(
            store_path, "local", "old"
        )
    output.put(removed)


def test_spawn_interrupt_after_publication_is_discoverable_and_retryable(
    tmp_path, monkeypatch
):
    store_path, store = _trio(tmp_path, monkeypatch)
    context = multiprocessing.get_context("spawn")
    published = context.Event()
    interrupted = context.Process(
        target=_crash_after_publication,
        args=(str(store_path), published),
    )

    interrupted.start()
    assert published.wait(_PROCESS_TIMEOUT)
    _join(
        interrupted,
        "pre-delete retirement",
        expected_exit=_EXIT_AFTER_PUBLICATION,
    )

    state = _disk_state(str(store_path))
    assert state["primary_exists"] is True
    assert state["record"]["retained"] == "local/modern"

    pending_output = context.Queue()
    pending_reader = context.Process(
        target=_pending_reader,
        args=(str(store_path), pending_output),
    )
    pending_reader.start()
    _join(pending_reader, "fresh pending reader")
    assert pending_output.get(timeout=5)["retained"] == "local/modern"

    retry_output = context.Queue()
    retry = context.Process(
        target=_delete_from_record,
        args=(str(store_path), retry_output),
    )
    retry.start()
    _join(retry, "fresh retirement retry")
    assert retry_output.get(timeout=5) == ("removed", True)
    assert store.load_index("local", "old") is None
    assert _record_path(str(store_path)).exists() is False


def test_spawn_interrupt_after_primary_is_not_falsely_pending(
    tmp_path, monkeypatch
):
    store_path, store = _trio(tmp_path, monkeypatch)
    context = multiprocessing.get_context("spawn")
    deleted = context.Event()
    interrupted = context.Process(
        target=_crash_after_primary_delete,
        args=(str(store_path), deleted),
    )

    interrupted.start()
    assert deleted.wait(_PROCESS_TIMEOUT)
    _join(
        interrupted,
        "post-primary retirement",
        expected_exit=_EXIT_AFTER_PRIMARY,
    )

    state = _disk_state(str(store_path))
    assert state["primary_exists"] is False
    assert state["record"]["retained"] == "local/modern"

    pending_output = context.Queue()
    recovery = context.Process(
        target=_pending_reader,
        args=(str(store_path), pending_output),
    )
    recovery.start()
    _join(recovery, "fresh completion recovery")
    assert pending_output.get(timeout=5) is None
    assert store.load_index("local", "old") is None
    assert _record_path(str(store_path)).exists() is False


def test_spawn_interrupted_self_heal_cannot_remove_new_publication(
    tmp_path, monkeypatch
):
    store_path, _ = _trio(tmp_path, monkeypatch)
    older_publication = _publish(str(store_path))
    older_state = _disk_state(str(store_path))
    older_identity = older_state["record"].get("publication_id")
    if isinstance(older_identity, str):
        older_publication = older_identity
    (store_path / "local" / "old.json").unlink()

    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    interrupted = context.Process(
        target=_crash_during_self_heal,
        args=(str(store_path), entered),
    )
    interrupted.start()
    assert entered.wait(_PROCESS_TIMEOUT)
    _join(
        interrupted,
        "interrupted self-heal",
        expected_exit=_EXIT_DURING_SELF_HEAL,
    )

    interrupted_state = _disk_state(str(store_path))
    assert interrupted_state["primary_exists"] is False
    assert interrupted_state["record"]["retained"] == "local/modern"

    publish_output = context.Queue()
    publisher = context.Process(
        target=_publish_competing_after_recreation,
        args=(str(store_path), publish_output),
    )
    publisher.start()
    _join(publisher, "competing publisher")
    assert publish_output.get(timeout=5)
    newer_state = _disk_state(str(store_path))
    assert newer_state["primary_exists"] is True
    assert newer_state["record"]["retained"] == "local/next"

    cleanup_output = context.Queue()
    older_cleanup = context.Process(
        target=_finish_older_publication,
        args=(str(store_path), older_publication, cleanup_output),
    )
    older_cleanup.start()
    _join(older_cleanup, "older cleanup retry")
    cleanup_result = cleanup_output.get(timeout=5)

    final_output = context.Queue()
    final_reader = context.Process(
        target=_pending_reader,
        args=(str(store_path), final_output),
    )
    final_reader.start()
    _join(final_reader, "fresh final-state reader")
    final_record = final_output.get(timeout=5)
    assert final_record is not None
    assert final_record["retained"] == "local/next"
    assert final_record == newer_state["record"]
    assert cleanup_result is False
