"""Content-hash-keyed embedding cache (v1.15.0).

Sidecar at ``~/.doc-index/<owner>/<name>.embeddings.jsonl``. One JSON line
per cached vector keyed by section ``content_hash``. The first line is a
header line containing the provider/model identity — provider rotation
purges the cache automatically on next load.

Cache schema:

    Line 0 (header):  {"_header": true, "provider": "...", "model": "...", "dim": 384}
    Line 1+:          {"hash": "<sha256>", "vector": [f, f, ...]}

Why JSONL not SQLite:

- Append-only. Every embed pass adds a few hundred lines; no schema migration.
- Diff-friendly. Reviewers can inspect what changed across releases.
- Simple recovery. Truncated files are still partially usable — corrupt
  lines are skipped on load.

Cache hits short-circuit ``provider.embed_texts`` for unchanged sections,
which dominates the cost on a typical incremental re-index (most sections
unchanged).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Iterable, Optional

_CACHE_FILE = "{name}.embeddings.jsonl"
_CACHE_LOCK = threading.Lock()


def _cache_path(base_path: Optional[str], owner: str, name: str) -> Path:
    root = Path(base_path) if base_path else Path.home() / ".doc-index"
    safe_owner = owner.strip().replace("/", "_").replace("\\", "_")
    safe_name = name.strip().replace("/", "_").replace("\\", "_")
    if not safe_owner or not safe_name:
        raise ValueError(f"Invalid cache target: owner={owner!r} name={name!r}")
    return root / safe_owner / _CACHE_FILE.format(name=safe_name)


def _identity(provider: str, model: str, dim: Optional[int]) -> dict:
    return {
        "_header": True,
        "provider": provider,
        "model": model,
        "dim": int(dim) if dim is not None else None,
    }


def load(
    base_path: Optional[str],
    owner: str,
    name: str,
    *,
    provider: str,
    model: str,
    dim: Optional[int],
) -> dict[str, list]:
    """Return ``{content_hash: vector}`` for the matching identity.

    Identity mismatch (provider/model/dim) ⇒ empty dict (caller will
    re-embed and rewrite the cache). Missing file ⇒ empty dict.
    Corrupt lines are silently skipped.
    """
    path = _cache_path(base_path, owner, name)
    if not path.exists():
        return {}

    out: dict[str, list] = {}
    header_ok = False
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("_header") is True:
                    if (
                        entry.get("provider") == provider
                        and entry.get("model") == model
                        and (dim is None or entry.get("dim") == dim)
                    ):
                        header_ok = True
                        continue
                    # Identity mismatch — bail; caller rewrites.
                    return {}
                if not header_ok:
                    # Body line before a matching header — file is from an
                    # older identity, treat as miss.
                    return {}
                h = entry.get("hash")
                vec = entry.get("vector")
                if isinstance(h, str) and isinstance(vec, list):
                    out[h] = vec
    except OSError:
        return {}
    return out


def write(
    base_path: Optional[str],
    owner: str,
    name: str,
    *,
    provider: str,
    model: str,
    dim: Optional[int],
    entries: Iterable[tuple[str, list]],
) -> None:
    """Atomically rewrite the cache for this index.

    ``entries`` is an iterable of ``(content_hash, vector)`` pairs. Order
    is not significant. Writes via tmp file + replace so a crash mid-write
    leaves the previous cache intact.
    """
    path = _cache_path(base_path, owner, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with _CACHE_LOCK:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(_identity(provider, model, dim)) + "\n")
            for h, vec in entries:
                if not isinstance(h, str) or not isinstance(vec, list):
                    continue
                fh.write(json.dumps({"hash": h, "vector": vec}) + "\n")
        tmp.replace(path)


def stored_hashes(base_path: Optional[str], owner: str, name: str) -> set:
    """Return the set of BARE content hashes this sidecar holds (jdoc#107).

    Identity-agnostic and vector-free: reads keys only, so a 26k-section
    corpus costs no memory. The ``#pv<N>`` embed-text-version salt is
    stripped so keys compare directly against a section's ``content_hash``.

    Exists so a caller can report embedding coverage. #107's reporter had to
    compute exactly this externally — comparing sidecar rows against
    ``section_count`` after every reindex — because the tool emitted no
    signal, which is why a total vector loss could exit 0 unnoticed.
    """
    try:
        path = _cache_path(base_path, owner, name)
    except ValueError:
        return set()
    if not path.exists():
        return set()
    out: set = set()
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if entry.get("_header") is True:
                    continue
                h = entry.get("hash")
                if isinstance(h, str) and h:
                    out.add(h.rsplit("#", 1)[0])
    except OSError:
        return set()
    return out


def append_entries(
    base_path: Optional[str],
    owner: str,
    name: str,
    *,
    entries: Iterable[tuple[str, list]],
    identity_if_new: tuple[str, str, Optional[int]],
) -> int:
    """Add vectors to a sidecar WITHOUT rewriting what is already there.

    Returns the number of entries actually written.

    ⚠ Unlike :func:`write`, this never removes anything and never touches an
    existing header — the caller may not know the true provider identity
    (``_ensure_sidecar_from_sections`` writes a ``__inline__`` placeholder),
    and stamping that over a real one would make the next embed pass see an
    identity mismatch and purge every vector on disk. Keys already present
    are skipped, so an existing vector always wins over a late arrival.

    jdoc#107: added because the safety net's only options used to be "bail"
    or "clobber", so a sidecar that existed could never be extended.
    """
    path = _cache_path(base_path, owner, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE_LOCK:
        existing: set = set()
        header_present = False
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except Exception:
                            continue
                        if entry.get("_header") is True:
                            header_present = True
                            continue
                        h = entry.get("hash")
                        if isinstance(h, str):
                            existing.add(h)
            except OSError:
                return 0

        pending = [
            (h, vec) for h, vec in entries
            if isinstance(h, str) and isinstance(vec, list) and h and h not in existing
        ]
        if not pending:
            return 0

        # Append-only: a JSONL sidecar tolerates a torn trailing line (both
        # readers skip unparseable lines), so this needs no tmp-file dance
        # and cannot lose the rows already on disk.
        try:
            with path.open("a", encoding="utf-8") as fh:
                if not header_present:
                    prov, model, dim = identity_if_new
                    fh.write(json.dumps(_identity(prov, model, dim)) + "\n")
                for h, vec in pending:
                    fh.write(json.dumps({"hash": h, "vector": vec}) + "\n")
        except OSError:
            return 0
        return len(pending)


def purge(base_path: Optional[str], owner: str, name: str) -> bool:
    """Delete the cache for one index. Returns True on success."""
    try:
        path = _cache_path(base_path, owner, name)
    except ValueError:
        return False
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False
