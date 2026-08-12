"""Corpus-creation claims — close the concurrent-create race (jdoc#81).

Two overlapping ``index_local`` calls for the same *new* equivalent source must
not leave two physical indexes behind. The summary-scan equivalence check in
``tools/_corpus_identity`` can't see an index that a sibling process is still
building, so creation is preceded by an atomic claim: a tiny JSON file named by
the corpus-identity key, created with ``O_CREAT | O_EXCL`` so exactly one
creator wins. The loser reads the winner's claim and routes to the established
handle instead of creating a duplicate.

Claims live under ``<base>/local/.corpus_claims/`` and persist after a
successful create (they are cleaned up by ``delete_index`` and by a staleness
rule: a claim older than ``_CLAIM_TTL_SECONDS`` whose repo never produced an
index is treated as abandoned and may be re-claimed).
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Callable, Optional

_CLAIMS_DIRNAME = ".corpus_claims"
_CLAIM_TTL_SECONDS = 24 * 3600  # abandoned-claim steal window


def claims_dir(base_path) -> Path:
    return Path(base_path) / "local" / _CLAIMS_DIRNAME


def claim_key(root_norm: str, selection: str) -> str:
    """Stable identity key for (normalized corpus root, durable selection)."""
    payload = f"{root_norm}\n{selection}".encode("utf-8", errors="replace")
    return hashlib.sha1(payload).hexdigest()


def _claim_path(base_path, key: str) -> Path:
    return claims_dir(base_path) / f"{key}.json"


def read_claim(base_path, key: str) -> Optional[dict]:
    try:
        with open(_claim_path(base_path, key), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def try_claim(
    base_path,
    key: str,
    repo: str,
    root_norm: str,
    selection: str,
    index_exists: Optional[Callable[[str], bool]] = None,
) -> tuple:
    """Atomically claim corpus creation for ``repo``.

    Returns ``(acquired, existing_claim)``. ``acquired`` is True when this
    caller now holds the claim (create may proceed). When False,
    ``existing_claim`` is the winner's payload (may name a different repo the
    caller should route to). A stale claim — older than the TTL and whose repo
    has no index (per ``index_exists``) — is treated as abandoned and stolen.
    Any filesystem failure degrades to ``(True, None)``: the claim layer is a
    race-closer, not a gate, and the per-repo write lock still serializes
    same-name writers.
    """
    path = _claim_path(base_path, key)
    payload = {
        "repo": repo,
        "root": root_norm,
        "selection": selection,
        "created_at": time.time(),
    }
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    for _ in range(2):  # second pass only after stealing a stale claim
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # jdoc#82 (single-winner invariant): publish the claim with its
            # payload already complete. The payload is written to a private
            # per-PID temp file first, then os.link makes it appear at the
            # claim path atomically — a competitor can never observe a claim
            # file whose ownership payload isn't readable yet. os.link is
            # atomic-fail-if-exists on POSIX and NTFS alike.
            tmp = path.parent / f".{key}.{os.getpid()}.tmp"
            fd = os.open(str(tmp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY)
            try:
                os.write(fd, encoded)
            finally:
                os.close(fd)
            try:
                os.link(str(tmp), str(path))
                return True, None
            except FileExistsError:
                raise
            except OSError:
                # Filesystem without hardlink support: fall back to an
                # exclusive create of the final path. This reopens a tiny
                # payload-visibility window, which the reader retry below
                # (and the caller's contested handling) covers.
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, encoded)
                finally:
                    os.close(fd)
                return True, None
            finally:
                try:
                    os.unlink(str(tmp))
                except OSError:
                    pass
        except FileExistsError:
            # jdoc#82: brief retry for the fallback path's payload window —
            # a claim that exists but isn't readable yet is a winner mid-write.
            existing = None
            for _attempt in range(5):
                existing = read_claim(base_path, key)
                if existing is not None:
                    break
                time.sleep(0.04)
            if existing is None:
                # Still unreadable: a winner holds the claim but its identity
                # is unknown. The caller must NOT create — returning
                # (False, None) signals a contested claim.
                return False, None
            age = time.time() - float(existing.get("created_at") or 0)
            claimed_repo = existing.get("repo") or ""
            if (
                age > _CLAIM_TTL_SECONDS
                and index_exists is not None
                and claimed_repo
                and not index_exists(claimed_repo)
            ):
                try:
                    os.unlink(str(path))
                except OSError:
                    return False, existing
                continue  # retry the exclusive create once
            return False, existing
        except OSError:
            return True, None  # degrade open: never block indexing on the claim layer
    return False, read_claim(base_path, key)


def release_claim(base_path, key: str) -> None:
    """Best-effort removal (failed creation cleanup)."""
    try:
        os.unlink(str(_claim_path(base_path, key)))
    except OSError:
        pass


def cleanup_claims_for_repo(base_path, repo: str) -> None:
    """Remove claims naming ``repo`` — called by delete_index so a deleted
    corpus doesn't leave a claim that conflicts future re-creation."""
    try:
        d = claims_dir(base_path)
        if not d.is_dir():
            return
        for entry in d.glob("*.json"):
            try:
                with open(entry, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("repo") == repo:
                    entry.unlink()
            except (OSError, ValueError):
                continue
    except OSError:
        pass
