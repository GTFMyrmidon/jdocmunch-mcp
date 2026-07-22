"""Durable retirement records (jdoc#88 QA-01/QA-02 lifecycle coordination).

A retirement is approved, then physically executed. Between those moments —
and across a crash mid-cleanup — the fact that a specific handle is being
retired in favor of a specific retained peer must survive as durable state,
not as a response string. Each record lives at
``<store>/<owner>/.retirements/<name>.json`` and holds the retiring and
retained handles, the proof-time monolith fingerprints, the retirement
family, and the start time.

Lifecycle: written immediately before the destructive step; removed on
successful cleanup and on conflict (the retirement is void, nothing pending);
kept when cleanup fails partway, so the pending work is discoverable and the
documented retry can resume it. A refresh of a retiring handle cancels the
record — the fingerprints are stale by definition, and a voided retirement
must not linger as pending work.

Everything here is best-effort: retirement records improve truthfulness and
recovery, and must never turn a working index operation into a failure.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

_RECORDS_DIR = ".retirements"


def _record_path(base_path, owner: str, name: str) -> Path:
    return Path(base_path) / owner / _RECORDS_DIR / f"{name}.json"


def begin_retirement(
    base_path, owner: str, name: str, *,
    retained: str, fingerprints: dict, family: str,
) -> None:
    """Write the durable record for ``owner/name`` being retired."""
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
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )
        os.replace(tmp, path)
    except OSError:
        pass


def finish_retirement(base_path, owner: str, name: str) -> None:
    """Remove the record: retirement completed or was voided by conflict."""
    try:
        path = _record_path(base_path, owner, name)
        if path.exists():
            path.unlink()
    except OSError:
        pass


def pending_retirement(base_path, owner: str, name: str) -> Optional[dict]:
    """The pending record for ``owner/name``, or None."""
    try:
        path = _record_path(base_path, owner, name)
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
