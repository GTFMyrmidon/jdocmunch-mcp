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

Truthfulness (jdoc#89 QA-08): a record whose retiring index no longer exists
describes a COMPLETED retirement whose finalization was interrupted, not
pending work — ``pending_retirement`` self-heals it under the record lock and
reports None. When that self-heal cannot remove the record, the durable
record is returned instead, so cleanup state that needs recovery is disclosed
rather than hidden behind a false "nothing pending".
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import secrets
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


class RetirementRecordLockError(RuntimeError):
    """The authoritative retirement-record lock could not be acquired."""


def _record_path(base_path, owner: str, name: str) -> Path:
    return Path(base_path) / owner / _RECORDS_DIR / f"{name}.json"


def _retiring_index_path(base_path, owner: str, name: str) -> Path:
    # Mirrors DocStore._index_path's layout: <store>/<owner>/<name>.json.
    return Path(base_path) / owner / f"{name}.json"


def fingerprint_index_file(index_path) -> Optional[str]:
    """sha256 of a stored monolith's bytes, or None when unreadable.

    The ONE definition of the retirement precondition token (jdoc#88 QA-01).
    ``DocStore.index_fingerprint`` captures it at proof time and this module
    re-proves it under the record lock, so the two must agree byte for byte —
    any divergence would make ``begin_retirement`` refuse every publication.
    They share this implementation rather than keeping two copies in step.
    """
    try:
        return hashlib.sha256(Path(index_path).read_bytes()).hexdigest()
    except OSError:
        return None


def _fingerprint_handle(base_path, handle: str) -> Optional[str]:
    if not isinstance(handle, str) or "/" not in handle:
        return None
    owner, _, name = handle.partition("/")
    # A handle reaches the path join only as two ordinary directory-level
    # names. Anything that could traverse out of the store fails closed here
    # rather than being hashed from wherever it pointed.
    if not all(
        part and part not in {".", ".."} and not set(part) & {"/", "\\"}
        for part in (owner, name)
    ):
        return None
    return fingerprint_index_file(
        _retiring_index_path(base_path, owner, name)
    )


def _fingerprints_match(base_path, fingerprints: dict) -> bool:
    if not isinstance(fingerprints, dict) or not fingerprints:
        return False
    return all(
        isinstance(expected, str)
        and expected
        and _fingerprint_handle(base_path, handle) == expected
        for handle, expected in fingerprints.items()
    )


def begin_retirement(
    base_path, owner: str, name: str, *,
    retained: str, fingerprints: dict, family: str,
) -> Optional[str]:
    """Durably publish the record for ``owner/name`` being retired.

    Returns a stable internal publication identity only when the record is
    fsync'd, atomically in place, and re-read under its authoritative lock.
    ``None`` means nothing may be removed.
    """
    tmp = None
    try:
        path = _record_path(base_path, owner, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        publication_id = secrets.token_hex(16)
        payload = {
            "retiring": f"{owner}/{name}",
            "retained": retained,
            "fingerprints": fingerprints,
            "family": family,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
            "publication_id": publication_id,
        }
        with hold_record_lock(base_path, owner, name):
            # QA-19 publication authority starts only after the proof is
            # repeated under the slot lock. A publisher that waited behind a
            # completed retirement or concurrent replacement cannot publish
            # stale fingerprints into the newly available slot.
            if not _fingerprints_match(base_path, fingerprints):
                return None
            tmp = path.with_name(
                f"{path.name}.{os.getpid()}.{threading.get_ident()}."
                f"{next(_tmp_counter)}.tmp"
            )
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, separators=(",", ":"))
                f.flush()
                # jdoc#89 QA-11: the receipt promises the record survives
                # sudden power loss, so flush the bytes before replacement.
                os.fsync(f.fileno())
            os.replace(tmp, path)
            tmp = None
            _fsync_dir(path.parent)
            current = _read_record(path)
            if current is None or current.get("publication_id") != publication_id:
                return None
        return publication_id
    except (OSError, RetirementRecordLockError):
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        return None


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
def hold_record_lock(base_path, owner: str, name: str, *, blocking: bool = True):
    """Exclusive cross-process lock on ``owner/name``'s retirement record.

    jdoc#90 QA-17: the record is the PAIR coordination point. The guarded
    delete holds this lock across its final fingerprint check, the
    record-existence check, and the primary unlink, so those become one
    destructive step; a retained-handle delete coordinates through the same
    lock (bounded, non-blocking — see ``try_void_retirements_referencing``)
    instead of taking a second handle lock. No caller ever blocks on two
    locks, so the QA-14 cross-handle deadlock surface stays closed."""
    fd = _acquire_fd(_lock_path(base_path, owner, name), blocking=blocking)
    if fd is None:
        raise RetirementRecordLockError(
            f"retirement record lock unavailable for {owner}/{name}"
        )
    try:
        yield
    finally:
        _release_fd(fd)


def _read_record(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def retirement_record(base_path, owner: str, name: str) -> Optional[dict]:
    """Read the current publication without mutating the retirement slot."""
    return _read_record(_record_path(base_path, owner, name))


def _retirement_record_state(
    base_path, owner: str, name: str
) -> tuple[str, Optional[dict]]:
    """Return the durable slot state without treating unreadable as absent."""
    path = _record_path(base_path, owner, name)
    current = _read_record(path)
    if current is not None:
        return "readable", current
    try:
        if path.is_file():
            return "unreadable", None
    except OSError:
        return "unreadable", None
    return "absent", None


def _remove_publication_locked(
    path: Path, publication_id: Optional[str]
) -> bool:
    current = _read_record(path)
    if current is None:
        return False
    current_id = current.get("publication_id")
    if publication_id is None:
        # Load-compatible cleanup for pre-QA-19 records only. A current record
        # with stable identity is never removable by unscoped old code.
        if current_id is not None:
            return False
    elif current_id != publication_id:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def finish_retirement(
    base_path,
    owner: str,
    name: str,
    *,
    publication_id: Optional[str] = None,
    _lock_held: bool = False,
) -> bool:
    """Remove only the exact publication completed or voided by its caller."""
    path = _record_path(base_path, owner, name)
    if _lock_held:
        return _remove_publication_locked(path, publication_id)
    try:
        with hold_record_lock(base_path, owner, name):
            return _remove_publication_locked(path, publication_id)
    except RetirementRecordLockError:
        return False


def void_retirement_if_stale(
    base_path, owner: str, name: str, current_fingerprint: Optional[str]
) -> bool:
    """Remove a current retiring-side publication only when its proof is stale."""
    path = _record_path(base_path, owner, name)
    if not path.is_file():
        return False
    try:
        with hold_record_lock(base_path, owner, name, blocking=False):
            current = _read_record(path)
            if current is None:
                return False
            handle = f"{owner}/{name}"
            if (
                current_fingerprint is not None
                and (current.get("fingerprints") or {}).get(handle)
                == current_fingerprint
            ):
                return False
            return _remove_publication_locked(
                path, current.get("publication_id")
            )
    except RetirementRecordLockError:
        return False


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


def void_retirements_referencing(
    base_path, handle: str, current_fingerprint: Optional[str] = None
) -> None:
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
            current = _read_record(record)
            if current is not None and current.get("retained") == handle:
                if (
                    current_fingerprint is not None
                    and (current.get("fingerprints") or {}).get(handle)
                    == current_fingerprint
                ):
                    continue
                _remove_publication_locked(
                    record, current.get("publication_id")
                )
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
            current = _read_record(record)
            if current is not None and current.get("retained") == handle:
                _remove_publication_locked(
                    record, current.get("publication_id")
                )
        finally:
            _release_fd(fd)
    return True


def pending_retirement(base_path, owner: str, name: str) -> Optional[dict]:
    """The pending record for ``owner/name``, or None.

    jdoc#89 QA-08: a record whose retiring index no longer exists represents
    cleanup after a completed retirement, not another destructive attempt.
    It is removed and reported None when self-healing succeeds. If removal
    fails, the durable record is returned so recovery state is not hidden.
    """
    try:
        path = _record_path(base_path, owner, name)
        state, current = _retirement_record_state(base_path, owner, name)
        if state == "absent":
            return None
        if state == "unreadable":
            return {"record_state": "unreadable"}
        if not _retiring_index_path(base_path, owner, name).is_file():
            try:
                with hold_record_lock(
                    base_path, owner, name, blocking=False
                ):
                    state, current = _retirement_record_state(
                        base_path, owner, name
                    )
                    if state == "absent":
                        return None
                    if state == "unreadable":
                        return {"record_state": "unreadable"}
                    if (
                        _retiring_index_path(
                            base_path, owner, name
                        ).is_file()
                    ):
                        return current
                    removed = _remove_publication_locked(
                        path, current.get("publication_id")
                    )
                    if removed:
                        return None
                    state, current = _retirement_record_state(
                        base_path, owner, name
                    )
                    if state == "readable":
                        return current
                    if state == "unreadable":
                        return {"record_state": "unreadable"}
                    return None
            except RetirementRecordLockError:
                return current
        return current
    except OSError:
        return {"record_state": "unreadable"}
