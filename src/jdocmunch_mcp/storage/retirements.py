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
from pathlib import Path
from typing import Optional

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


def finish_retirement(base_path, owner: str, name: str) -> None:
    """Remove the record: retirement completed or was voided by conflict."""
    try:
        path = _record_path(base_path, owner, name)
        if path.exists():
            path.unlink()
    except OSError:
        pass


def void_retirements_referencing(base_path, handle: str) -> None:
    """Void every pending record whose RETAINED side is ``handle``.

    jdoc#89 QA-09/QA-10: once the retained peer is rewritten or directly
    deleted, the record's stored proof (its fingerprints, and for a delete
    the peer itself) is stale — the retirement can no longer complete as
    recorded, so it is voided rather than left as misleading pending work.
    The next reconcile re-proves against the current state. Best-effort;
    the records directory is tiny and per-owner.
    """
    try:
        for record in Path(base_path).glob(f"*/{_RECORDS_DIR}/*.json"):
            try:
                data = json.loads(record.read_text(encoding="utf-8"))
                if data.get("retained") == handle:
                    record.unlink()
            except (OSError, ValueError):
                continue
    except OSError:
        pass


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
