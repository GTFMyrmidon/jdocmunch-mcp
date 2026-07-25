"""POSIX-only lock semantics for jdoc#93 (QA-19 / QA-21).

Windows cannot exercise either behaviour, and quietly "passing" there is worse
than not running:

* **QA-21** — the deleter holds the lockfile open, so Windows fails the unlink
  silently and the pathname survives for the wrong reason. A test that passes
  on that basis reports coverage it does not have (the QA-24 objection).
* **QA-19** — the gate's non-blocking acquisition goes through ``msvcrt`` on
  Windows. The ``fcntl`` branch (``LOCK_EX | LOCK_NB``) never executes there.

So this module SKIPS on Windows rather than passing. It also runs standalone
(``python3 tests/test_posix_lock_semantics.py``) because the environments where
POSIX behaviour matters most are often the ones without pytest installed.

Requires a real POSIX filesystem: it uses ``/tmp``, which is ext4 under WSL,
not the DrvFs mount where inode semantics do not apply.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from multiprocessing import Process, Queue
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError:  # pragma: no cover - standalone path
    # The standalone runner exists precisely for POSIX boxes without pytest,
    # so importing it unconditionally would defeat the purpose.
    pytest = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jdocmunch_mcp.storage.doc_store import DocStore  # noqa: E402

_SKIP_REASON = (
    "POSIX lock semantics: Windows cannot unlink an open file, so the "
    "QA-21 hazard cannot occur and the fcntl branch never runs"
)

if pytest is not None:
    pytestmark = pytest.mark.skipif(sys.platform == "win32", reason=_SKIP_REASON)


def _store_with_index(tmpdir):
    """A DocStore with one index physically present, plus its lock path."""
    store = DocStore(base_path=str(tmpdir))
    owner, name = "local", "probe"
    idx = store._index_path(owner, name)
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text(json.dumps({"repo": f"{owner}/{name}", "sections": []}))
    return store, owner, name, idx, idx.with_name(idx.name + ".lock")


def _hold_in_child(base, owner, name, q_ready, q_release):
    """Hold the write lock in a genuinely separate process."""
    s = DocStore(base_path=base)
    with s._index_write_lock(owner, name):
        q_ready.put("held")
        q_release.get()


def test_delete_does_not_unlink_the_lockfile(tmp_path):
    """QA-21: the deleter must not remove the lock it is coordinating on.

    Pre-fix, the unlink succeeded mid-critical-section: a newcomer then
    ``O_CREAT``d a fresh inode, acquired it uncontended, passed the QA-15
    self-check (nothing about the new inode is stale) and ran concurrently
    with the deleter.
    """
    store, owner, name, idx, lock_path = _store_with_index(tmp_path)
    with store._index_write_lock(owner, name):
        pass
    assert lock_path.exists()

    store.delete_index(owner, name)

    assert not idx.exists(), "the index itself should be gone"
    assert lock_path.exists(), (
        "delete_index unlinked the coordination lockfile; a newcomer can now "
        "create a fresh inode and run concurrently with the deleter"
    )


def test_nonblocking_acquire_uses_the_fcntl_branch(tmp_path):
    """QA-19: LOCK_EX | LOCK_NB — the branch Windows never runs."""
    store, owner, name, _idx, _lock = _store_with_index(tmp_path)

    with store._try_index_write_lock(owner, name) as got:
        assert got is True, "should acquire when free"
        with store._try_index_write_lock(owner, name) as nested:
            assert nested is False, "should refuse while already held"

    with store._try_index_write_lock(owner, name) as after:
        assert after is True, "should re-acquire once released"


def test_nonblocking_acquire_refuses_across_processes(tmp_path):
    """The case that matters: a separate process holds the handle.

    The deployment this protects is a `watch-all` service reindexing while an
    MCP server answers queries — different processes, not threads.
    """
    store, owner, name, _idx, _lock = _store_with_index(tmp_path)
    q_ready, q_release = Queue(), Queue()
    p = Process(
        target=_hold_in_child, args=(str(tmp_path), owner, name, q_ready, q_release)
    )
    p.start()
    try:
        assert q_ready.get(timeout=15) == "held"
        with store._try_index_write_lock(owner, name) as got:
            assert got is False, "must refuse while another process holds the lock"
    finally:
        q_release.put("go")
        p.join(15)

    with store._try_index_write_lock(owner, name) as got:
        assert got is True, "should acquire once the holding process exits"


def test_lock_inode_is_stable_across_acquisitions(tmp_path):
    """QA-15 interaction: with nothing unlinking the path, the inode holds."""
    store, owner, name, _idx, lock_path = _store_with_index(tmp_path)
    with store._index_write_lock(owner, name):
        pass
    first = lock_path.stat().st_ino
    with store._index_write_lock(owner, name):
        pass
    assert lock_path.stat().st_ino == first


def test_missing_index_reports_not_found(tmp_path):
    """QA-20: a genuinely missing index is not lifecycle contention."""
    store = DocStore(base_path=str(tmp_path))
    outcome: dict = {}
    assert store.delete_index("local", "never-existed", outcome=outcome) is False
    assert outcome.get("reason_code") == "index_not_found"


if __name__ == "__main__":  # pragma: no cover - standalone path
    # Runs without pytest, because a POSIX box often has none installed.
    if sys.platform == "win32":
        print("SKIP: POSIX-only module")
        raise SystemExit(0)
    failures = []
    for fn in (
        test_delete_does_not_unlink_the_lockfile,
        test_nonblocking_acquire_uses_the_fcntl_branch,
        test_nonblocking_acquire_refuses_across_processes,
        test_lock_inode_is_stable_across_acquisitions,
        test_missing_index_reports_not_found,
    ):
        d = Path(tempfile.mkdtemp(prefix="jdoc-posix-", dir="/tmp"))
        try:
            fn(d)
            print(f"  [PASS] {fn.__name__}")
        except AssertionError as exc:
            failures.append(fn.__name__)
            print(f"  [FAIL] {fn.__name__}: {exc}")
    print(f"\nfs=/tmp statvfs_blocks={os.statvfs('/tmp').f_blocks}")
    print("ALL POSIX CHECKS PASSED" if not failures else f"FAILED: {failures}")
    raise SystemExit(1 if failures else 0)
