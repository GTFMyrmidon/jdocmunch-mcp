"""Durable retirement records (jdoc#88 QA-01/QA-02, jdoc#89 QA-07..QA-11).

A retirement is approved, then physically executed. Between those moments —
and across a crash mid-cleanup — the fact that a specific handle is being
retired in favor of a specific retained peer must survive as durable state,
not as a response string. Each record lives at
``<store>/<owner>/.retirements/<name>.json`` and holds the retiring and
retained handles, the proof-time monolith fingerprints, the retirement
family, and the start time.

Lifecycle: written immediately before the destructive step — and the writer
returns a publication RECEIPT that callers must require before any cleanup
starts (jdoc#89 QA-07: no destructive step without authoritative recovery
state on disk). Removed on successful cleanup and on conflict (the
retirement is void, nothing pending); kept when cleanup fails, so the
pending work is discoverable and the documented retry can resume it. A
rewrite of the retiring handle cancels the record; a rewrite or direct
delete of the RETAINED handle voids any record naming it as the retained
peer (jdoc#89 QA-09/QA-10 — the stored proof is stale by definition once
either participant changes, and a voided retirement must not linger as
pending work). Fail-visible policy throughout: a caller's write goes where
they aimed it, and the next reconcile re-proves against the new state.

Durability (jdoc#89 QA-11): the record is fsync'd before the atomic replace
(and the directory entry flushed where the platform supports it), so a
returned receipt survives sudden power loss.

Truthfulness (jdoc#89 QA-08): a record whose retiring index no longer
exists describes a COMPLETED retirement whose finalization was interrupted,
not pending work — ``pending_retirement`` self-heals it and reports None.
"""

from __future__ import annotations

import itertools
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None

_RECORDS_DIR = ".retirements"

# jdoc#89 QA-07: a PID-only temp suffix let two same-process publishers
# collide on one temporary path (one replace then fails with
# FileNotFoundError). PID + thread id + a process-wide counter makes every
# publication's temp path unique.
_tmp_counter = itertools.count()


def _record_path(base_path, owner: str, name: str) -> Path:
    return Path(base_path) / owner / _RECORDS_DIR / f"{name}.json"


def _retiring_index_path(base_path, owner: str, name: str) -> Path:
    # Mirrors DocStore._index_path's layout: <store>/<owner>/<name>.json.
    return Path(base_path) / owner / f"{name}.json"


def begin_retirement(
    base_path, owner: str, name: str, *,
    retained: str, fingerprints: dict, family: str,
) -> bool:
    """Durably publish the record for ``owner/name`` being retired.

    Returns True only when the record is fsync'd and atomically in place —
    the publication receipt every retirement path must require before its
    destructive step (jdoc#89 QA-07). False means nothing may be removed.
    """
    try:
        path = _record_path(base_path, owner, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "retiring": f"{owner}/{name}",
            "retained": retained,
            "fingerprints": fingerprints,
            "family": family,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        }
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}."
            f"{next(_tmp_counter)}.tmp"
        )
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
            f.flush()
            # jdoc#89 QA-11: the receipt promises the record survives sudden
            # power loss, so flush the bytes before the atomic replace.
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
        return True
    except OSError:
        return False


def _fsync_dir(directory: Path) -> None:
    """Flush the directory entry after ``os.replace`` (POSIX; Windows has no
    directory fsync — NTFS journals the rename). Best-effort."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _lock_path(base_path, owner: str, name: str) -> Path:
    return _record_path(base_path, owner, name).with_suffix(".json.retlock")


def _acquire_fd(lock_path: Path, blocking: bool) -> Optional[int]:
    """One lock-acquisition attempt. Returns the locked fd, or None when
    ``blocking`` is False and the lock is held elsewhere. POSIX re-verifies
    the inode after acquiring (same jdoc#89 QA-15 pattern as the index write
    lock) so an unlinked-and-recreated lock path can't split coordination."""
    while True:
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        except OSError:
            return None
        try:
            if fcntl is not None:
                fcntl.flock(
                    fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
                )
                try:
                    path_stat = os.stat(str(lock_path))
                    fd_stat = os.fstat(fd)
                    if (path_stat.st_ino, path_stat.st_dev) == (
                        fd_stat.st_ino, fd_stat.st_dev
                    ):
                        return fd
                except OSError:
                    pass  # unlinked under us — stale inode, retry
                os.close(fd)
                continue
            if msvcrt is not None:
                # An open file can't be unlinked on Windows — no inode split.
                if blocking:
                    while True:
                        try:
                            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                            return fd
                        except OSError:
                            time.sleep(0.05)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return fd
            return fd  # no lock primitive — degrade like the index lock
        except OSError:
            os.close(fd)
            return None


def _release_fd(fd: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            try:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
    finally:
        os.close(fd)


@contextmanager
def hold_record_lock(base_path, owner: str, name: str):
    """Exclusive cross-process lock on ``owner/name``'s retirement record.

    jdoc#90 QA-17: the record is the PAIR coordination point. The guarded
    delete holds this lock across its final fingerprint check, the
    record-existence check, and the primary unlink, so those become one
    destructive step; a retained-handle delete coordinates through the same
    lock (bounded, non-blocking — see ``try_void_retirements_referencing``)
    instead of taking a second handle lock. No caller ever blocks on two
    locks, so the QA-14 cross-handle deadlock surface stays closed."""
    fd = _acquire_fd(_lock_path(base_path, owner, name), blocking=True)
    try:
        yield
    finally:
        if fd is not None:
            _release_fd(fd)


def finish_retirement(base_path, owner: str, name: str) -> None:
    """Remove the record: retirement completed or was voided by conflict."""
    try:
        path = _record_path(base_path, owner, name)
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _records_referencing(base_path, handle: str):
    """(record_path, owner, name) for every record whose retained is ``handle``."""
    hits = []
    try:
        for record in Path(base_path).glob(f"*/{_RECORDS_DIR}/*.json"):
            try:
                data = json.loads(record.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if data.get("retained") == handle:
                hits.append((record, record.parent.parent.name, record.stem))
    except OSError:
        pass
    return hits


def void_retirements_referencing(base_path, handle: str) -> None:
    """Void every pending record whose RETAINED side is ``handle``.

    jdoc#89 QA-09/QA-10: once the retained peer is rewritten or directly
    deleted, the record's stored proof (its fingerprints, and for a delete
    the peer itself) is stale — the retirement can no longer complete as
    recorded, so it is voided rather than left as misleading pending work.
    The next reconcile re-proves against the current state. jdoc#90 QA-17:
    voiding happens under the record's lock; a record whose retirement is
    executing its destructive step this instant is skipped (that retirement
    removes its own record on completion, or its final gate observes the
    changed state). Best-effort; the records directory is tiny.
    """
    for record, r_owner, r_name in _records_referencing(base_path, handle):
        fd = _acquire_fd(_lock_path(base_path, r_owner, r_name), blocking=False)
        if fd is None:
            continue  # mid-destructive-step — its own gate coordinates
        try:
            record.unlink()
        except OSError:
            pass
        finally:
            _release_fd(fd)


def try_void_retirements_referencing(
    base_path, handle: str, timeout_seconds: float = 1.0
) -> bool:
    """Void every record naming ``handle`` as retained, or report busy.

    jdoc#90 QA-17: called by ``delete_index`` BEFORE any destructive step on
    ``handle``. Each referencing record's lock is acquired with a bounded
    wait; acquiring it proves the owning retirement is not inside its final
    gate, so the void lands before that gate runs (which then finds the
    record gone and conflicts, keeping the retiring handle). Returns False
    when a record's lock stays held past ``timeout_seconds`` — the owning
    retirement is executing its destructive step RIGHT NOW and the caller
    must refuse the delete rather than remove the peer out from under it
    (a retry succeeds as soon as the gate closes, normally milliseconds).
    """
    deadline = time.monotonic() + timeout_seconds
    for record, r_owner, r_name in _records_referencing(base_path, handle):
        fd = None
        while fd is None:
            fd = _acquire_fd(
                _lock_path(base_path, r_owner, r_name), blocking=False
            )
            if fd is None:
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.01)
        try:
            try:
                record.unlink()
            except OSError:
                pass
        finally:
            _release_fd(fd)
    return True


def pending_retirement(base_path, owner: str, name: str) -> Optional[dict]:
    """The pending record for ``owner/name``, or None.

    jdoc#89 QA-08: a record whose retiring index no longer exists is a
    COMPLETED retirement whose record finalization was interrupted — not
    pending work. It is self-healed (best-effort unlink) and reported None,
    so completed deletions are never claimed as pending.
    """
    try:
        path = _record_path(base_path, owner, name)
        if not path.is_file():
            return None
        if not _retiring_index_path(base_path, owner, name).is_file():
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
