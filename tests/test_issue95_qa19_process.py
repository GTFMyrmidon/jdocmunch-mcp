"""Portable cross-process proofs for Issue #95 QA-19 commit authority."""

from __future__ import annotations

import json
import multiprocessing
import os
from contextlib import contextmanager
from pathlib import Path

from jdocmunch_mcp.storage import doc_store as doc_store_module
from jdocmunch_mcp.storage import retirements
from jdocmunch_mcp.storage.doc_store import DocStore, RetirementConflict
from tests import test_v1_110_0 as legacy


_PROCESS_TIMEOUT = 15


def _fingerprints(store: DocStore, retiring: str, retained: str) -> dict:
    return {
        retiring: store.index_fingerprint(*retiring.split("/", 1)),
        retained: store.index_fingerprint(*retained.split("/", 1)),
    }


def _publish(
    store_path: Path, store: DocStore, retiring: str, retained: str
) -> dict:
    fingerprints = _fingerprints(store, retiring, retained)
    owner, name = retiring.split("/", 1)
    assert retirements.begin_retirement(
        str(store_path),
        owner,
        name,
        retained=retained,
        fingerprints=fingerprints,
        family="qa19-process",
    )
    return fingerprints


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


def _join(process, label: str, timeout: int = _PROCESS_TIMEOUT) -> None:
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        raise AssertionError(f"{label} did not exit within {timeout}s")
    assert process.exitcode == 0, f"{label} exit code: {process.exitcode}"


def _guarded_delete_worker(
    store_path: str,
    retiring: str,
    fingerprints: dict,
    output,
) -> None:
    owner, name = retiring.split("/", 1)
    store = DocStore(base_path=store_path)
    try:
        removed = store.delete_index(
            owner,
            name,
            expected_fingerprints=fingerprints,
            lock_wait=True,
        )
    except RetirementConflict as exc:
        output.put(("conflict", str(exc)))
    else:
        output.put(("removed" if removed else "not_removed", ()))


def _retained_writer_worker(
    store_path: str,
    locked,
    release,
    output,
) -> None:
    store = DocStore(base_path=store_path)
    path = store._index_path("local", "modern")
    with store._index_write_lock("local", "modern"):
        locked.set()
        if not release.wait(_PROCESS_TIMEOUT):
            raise TimeoutError("retained writer was not released")
        data = json.loads(path.read_text(encoding="utf-8"))
        data["indexed_at"] = "qa19-cross-process-change"
        temp = path.with_name(f"{path.name}.{os.getpid()}.qa19.tmp")
        temp.write_text(
            json.dumps(data, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp, path)
        output.put("changed")


def _retained_writer_at_gate_worker(
    store_path: str,
    at_gate,
    changed,
    output,
) -> None:
    if not at_gate.wait(_PROCESS_TIMEOUT):
        raise TimeoutError("retirement did not reach the retained gate")
    store = DocStore(base_path=store_path)
    path = store._index_path("local", "modern")
    with store._index_write_lock("local", "modern"):
        data = json.loads(path.read_text(encoding="utf-8"))
        data["indexed_at"] = "qa19-change-before-final-proof"
        temp = path.with_name(f"{path.name}.{os.getpid()}.qa19.tmp")
        temp.write_text(
            json.dumps(data, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temp, path)
    output.put("changed")
    changed.set()


def _guarded_delete_paused_at_retained_gate_worker(
    store_path: str,
    fingerprints: dict,
    at_gate,
    changed,
    output,
) -> None:
    original_try_lock = DocStore._try_index_write_lock

    @contextmanager
    def pause_before_retained_gate(self, owner, name):
        if (owner, name) == ("local", "modern"):
            at_gate.set()
            if not changed.wait(_PROCESS_TIMEOUT):
                raise TimeoutError("retained writer did not finish")
        with original_try_lock(self, owner, name) as acquired:
            yield acquired

    DocStore._try_index_write_lock = pause_before_retained_gate
    try:
        _guarded_delete_worker(
            store_path,
            "local/old",
            fingerprints,
            output,
        )
    finally:
        DocStore._try_index_write_lock = original_try_lock


def _guarded_delete_paused_before_gate_worker(
    store_path: str,
    fingerprints: dict,
    before_gate,
    resume,
    output,
) -> None:
    store = DocStore(base_path=store_path)
    retiring_content = store._content_dir("local", "old")
    original_rmtree = doc_store_module.shutil.rmtree

    def pause_after_auxiliary_removal(path, *args, **kwargs):
        result = original_rmtree(path, *args, **kwargs)
        if Path(path) == retiring_content:
            before_gate.set()
            if not resume.wait(_PROCESS_TIMEOUT):
                raise TimeoutError("retiring delete was not resumed")
        return result

    doc_store_module.shutil.rmtree = pause_after_auxiliary_removal
    try:
        _guarded_delete_worker(
            store_path,
            "local/old",
            fingerprints,
            output,
        )
    finally:
        doc_store_module.shutil.rmtree = original_rmtree


def test_spawn_retained_writer_gate_refuses_promptly(tmp_path, monkeypatch):
    """An in-flight retained writer makes final authorization fail closed."""
    store_path, store = _trio(tmp_path, monkeypatch)
    fingerprints = _publish(
        store_path, store, "local/old", "local/modern"
    )
    context = multiprocessing.get_context("spawn")
    locked = context.Event()
    release = context.Event()
    writer_output = context.Queue()
    delete_output = context.Queue()
    writer = context.Process(
        target=_retained_writer_worker,
        args=(str(store_path), locked, release, writer_output),
    )
    deleting = context.Process(
        target=_guarded_delete_worker,
        args=(
            str(store_path),
            "local/old",
            fingerprints,
            delete_output,
        ),
    )

    writer.start()
    assert locked.wait(_PROCESS_TIMEOUT)
    deleting.start()
    try:
        _join(deleting, "retiring delete", timeout=5)
        status, changed = delete_output.get(timeout=5)
        assert status == "conflict"
        assert "local/modern" in changed
    finally:
        release.set()
        _join(writer, "retained writer")

    assert writer_output.get(timeout=5) == "changed"
    assert store.load_index("local", "old") is not None
    assert store.load_index("local", "modern") is not None


def test_spawn_writer_change_is_reproved_after_retained_gate(
    tmp_path, monkeypatch
):
    """A retained write landing before gate acquisition invalidates authority."""
    store_path, store = _trio(tmp_path, monkeypatch)
    fingerprints = _publish(
        store_path, store, "local/old", "local/modern"
    )
    context = multiprocessing.get_context("spawn")
    at_gate = context.Event()
    changed = context.Event()
    writer_output = context.Queue()
    delete_output = context.Queue()
    writer = context.Process(
        target=_retained_writer_at_gate_worker,
        args=(str(store_path), at_gate, changed, writer_output),
    )
    deleting = context.Process(
        target=_guarded_delete_paused_at_retained_gate_worker,
        args=(
            str(store_path),
            fingerprints,
            at_gate,
            changed,
            delete_output,
        ),
    )

    writer.start()
    deleting.start()
    _join(writer, "retained writer")
    _join(deleting, "retiring delete")

    assert writer_output.get(timeout=5) == "changed"
    status, detail = delete_output.get(timeout=5)
    assert status == "conflict", detail
    assert store.load_index("local", "old") is not None
    assert store.load_index("local", "modern") is not None


def test_spawn_overlapping_chain_fails_closed_before_a_commit(
    tmp_path, monkeypatch
):
    """B-to-C removing B before A's commit invalidates A-to-B."""
    store_path, store = _trio(tmp_path, monkeypatch)
    a_fingerprints = _publish(
        store_path, store, "local/old", "local/modern"
    )
    b_fingerprints = _publish(
        store_path, store, "local/modern", "local/next"
    )
    context = multiprocessing.get_context("spawn")
    before_gate = context.Event()
    resume = context.Event()
    a_output = context.Queue()
    b_output = context.Queue()
    retiring_a = context.Process(
        target=_guarded_delete_paused_before_gate_worker,
        args=(
            str(store_path),
            a_fingerprints,
            before_gate,
            resume,
            a_output,
        ),
    )
    retiring_b = context.Process(
        target=_guarded_delete_worker,
        args=(
            str(store_path),
            "local/modern",
            b_fingerprints,
            b_output,
        ),
    )

    retiring_a.start()
    assert before_gate.wait(_PROCESS_TIMEOUT)
    retiring_b.start()
    _join(retiring_b, "B-to-C retirement")
    assert b_output.get(timeout=5)[0] == "removed"
    assert store.load_index("local", "modern") is None
    assert store.load_index("local", "next") is not None

    resume.set()
    _join(retiring_a, "A-to-B retirement")
    assert a_output.get(timeout=5)[0] == "conflict"
    assert store.load_index("local", "old") is not None


def test_spawn_sequential_chain_allows_later_retirement(
    tmp_path, monkeypatch
):
    """A-to-B commits with B loadable, then authorized B-to-C may retire B."""
    store_path, store = _trio(tmp_path, monkeypatch)
    context = multiprocessing.get_context("spawn")
    a_output = context.Queue()
    a_fingerprints = _publish(
        store_path, store, "local/old", "local/modern"
    )
    retiring_a = context.Process(
        target=_guarded_delete_worker,
        args=(
            str(store_path),
            "local/old",
            a_fingerprints,
            a_output,
        ),
    )

    retiring_a.start()
    _join(retiring_a, "A-to-B retirement")
    assert a_output.get(timeout=5)[0] == "removed"
    assert store.load_index("local", "old") is None
    assert store.load_index("local", "modern") is not None

    b_output = context.Queue()
    b_fingerprints = _publish(
        store_path, store, "local/modern", "local/next"
    )
    retiring_b = context.Process(
        target=_guarded_delete_worker,
        args=(
            str(store_path),
            "local/modern",
            b_fingerprints,
            b_output,
        ),
    )
    retiring_b.start()
    _join(retiring_b, "B-to-C retirement")

    assert b_output.get(timeout=5)[0] == "removed"
    assert store.load_index("local", "modern") is None
    assert store.load_index("local", "next") is not None
