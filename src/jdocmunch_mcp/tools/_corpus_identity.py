"""Corpus identity for local doc indexes (jdoc#81, Item A of jdoc#80).

``index_local`` used to let the requested (or derived) *name* become the
physical index identity, so one local corpus could be indexed repeatedly under
different names — duplicated storage and refresh work, and read-time ambiguity
that ``doc_resolve_repo`` (jdoc#79) can detect but not prevent.

This module resolves a structured corpus identity *before* storage is chosen:

* **root** — the normalized local source root (same ``Path.resolve`` +
  ``os.path.normcase`` comparison ``doc_resolve_repo`` uses, so path spellings,
  casing, and symlinked aliases converge). A parent folder and a nested docs
  folder have different roots; containment alone never establishes identity.
* **durable selection** — ``"full"`` for a whole-corpus walk (including
  ``paths=["."]``), or a ``subset:<sha>:<count>`` descriptor for an explicit
  durable subset. A temporary ``paths`` subset used to *refresh* an existing
  index never becomes a new durable identity: a ``full`` index covers any
  subset refresh from the same root, while intentionally different durable
  subsets are never merged merely because they share a root.

Identity is persisted as ``DocIndex.corpus_selection`` (additive; legacy
indexes carry ``""`` and are presumed full-corpus — the same root-based
identity ``doc_resolve_repo`` already applies to them). Repository lineage and
repository-relative corpus location are deliberately *not* folded into the
equivalence check here; they stay separate concepts for Item B (jdoc#80).
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Optional

SELECTION_FULL = "full"

# Bound candidate lists in error responses, mirroring doc_resolve_repo.
MAX_CANDIDATES = 5


def corpus_norm_root(folder_path: Path) -> str:
    """Normalized comparison form of an (already-resolved) source root."""
    return os.path.normcase(str(folder_path))


def selection_descriptor(requested_rels: Optional[list]) -> str:
    """Durable-selection descriptor for this call.

    ``None``/empty → full-corpus walk. A list containing the root itself
    (``""`` or ``"."``) is also full (jdoc#31 root-listed semantics). Anything
    else hashes the sorted unique root-relative entries.
    """
    if not requested_rels:
        return SELECTION_FULL
    entries = sorted({r for r in requested_rels if isinstance(r, str)})
    if not entries or any(r in ("", ".") for r in entries):
        return SELECTION_FULL
    digest = hashlib.sha1(json.dumps(entries).encode("utf-8")).hexdigest()[:16]
    return f"subset:{digest}:{len(entries)}"


def selection_covers(entry_selection: str, call_selection: str) -> bool:
    """Does an existing index's durable selection cover this call?

    A ``full`` index covers any call from the same root (a subset refresh
    belongs to the corpus it is a subset of). A durable subset covers only the
    identical subset. Legacy ``""`` is presumed full — consistent with
    ``doc_resolve_repo``, which already matches subset paths to a root index by
    containment.
    """
    if entry_selection in ("", SELECTION_FULL):
        return True
    return entry_selection == call_selection


def find_equivalent_indexes(
    store,
    root_norm: str,
    call_selection: str,
    exclude_repo: str = "",
) -> list:
    """Existing local indexes equivalent to (root, selection), cheapest path.

    Reads ``store.list_repos()`` (summary-sidecar backed since jdoc#77 — no
    monolith parse). Returns compact entry dicts; never raises.
    """
    try:
        repos = store.list_repos()
    except Exception:
        return []
    matches = []
    for entry in repos:
        repo = entry.get("repo") or ""
        if not repo.startswith("local/") or repo == exclude_repo:
            continue
        sr = entry.get("source_root") or ""
        if not sr:
            continue
        try:
            entry_root = os.path.normcase(str(Path(sr).resolve()))
        except (OSError, ValueError):
            continue
        if entry_root != root_norm:
            continue
        if selection_covers(entry.get("corpus_selection") or "", call_selection):
            matches.append(entry)
    return matches


def candidate_rows(entries: list) -> list:
    rows = []
    for entry in entries[:MAX_CANDIDATES]:
        row = {
            "repo": entry.get("repo", ""),
            "source_root": entry.get("source_root", ""),
        }
        if entry.get("indexed_at"):
            row["indexed_at"] = entry["indexed_at"]
        rows.append(row)
    return rows
