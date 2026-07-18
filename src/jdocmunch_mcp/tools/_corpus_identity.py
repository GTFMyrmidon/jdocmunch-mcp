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


def selection_descriptor(
    requested_rels: Optional[list],
    extra_ignore_patterns: Optional[list] = None,
    follow_symlinks: bool = False,
) -> str:
    """Durable-selection descriptor for this call.

    ``None``/empty → full-corpus walk. A list containing the root itself
    (``""`` or ``"."``) is also full (jdoc#31 root-listed semantics). Anything
    else hashes the sorted unique root-relative entries.

    jdoc#82 (no silent retargeting): corpus-shaping inputs that change which
    documents the selection covers — ``extra_ignore_patterns`` and
    ``follow_symlinks`` — are folded into the descriptor as a ``+shape:``
    digest, so changing them changes identity instead of silently retargeting
    stored coverage. Calls without shaping inputs keep the bare descriptor,
    so existing ``full`` indexes see no churn.
    """
    if not requested_rels:
        base = SELECTION_FULL
    else:
        entries = sorted({r for r in requested_rels if isinstance(r, str)})
        if not entries or any(r in ("", ".") for r in entries):
            base = SELECTION_FULL
        else:
            digest = hashlib.sha1(json.dumps(entries).encode("utf-8")).hexdigest()[:16]
            base = f"subset:{digest}:{len(entries)}"
    shape_inputs = []
    if extra_ignore_patterns:
        patterns = sorted({p for p in extra_ignore_patterns if isinstance(p, str) and p})
        if patterns:
            shape_inputs.append(("ignore", patterns))
    if follow_symlinks:
        shape_inputs.append(("follow_symlinks", True))
    if shape_inputs:
        shape = hashlib.sha1(
            json.dumps(shape_inputs).encode("utf-8")
        ).hexdigest()[:12]
        return f"{base}+shape:{shape}"
    return base


def _normalize_selection(selection: str) -> str:
    """Legacy ``""`` is presumed full — consistent with ``doc_resolve_repo``,
    which already matches subset paths to a root index by containment."""
    return selection or SELECTION_FULL


def selection_identical(entry_selection: str, call_selection: str) -> bool:
    """Symmetric durable-selection identity (jdoc#82 invariant 3).

    Reflexive, symmetric, transitive, and independent of creation order:
    two selections are the same identity only when their descriptors are
    equal (legacy ``""`` normalizing to ``full``). ``full`` covering a subset
    refresh is a *directional refresh rule* (see :func:`selection_covers`),
    never identity — so a full index and an intentional durable subset are
    distinct corpora in either creation order.
    """
    return _normalize_selection(entry_selection) == _normalize_selection(call_selection)


def selection_covers(entry_selection: str, call_selection: str) -> bool:
    """Directional refresh coverage: may an *omitted-name* call route to this
    existing index as a refresh?

    Identity always covers itself. Additionally an unshaped ``full`` index
    covers a temporary unshaped subset call from the same root (a subset
    refresh belongs to the corpus it is a subset of). Shaped selections cover
    only their identical selves — differing shaping is a different durable
    coverage, never silently absorbed (jdoc#82 invariant 4).
    """
    entry_norm = _normalize_selection(entry_selection)
    call_norm = _normalize_selection(call_selection)
    if entry_norm == call_norm:
        return True
    return (
        entry_norm == SELECTION_FULL
        and call_norm.startswith("subset:")
        and "+shape:" not in call_norm
    )


def find_equivalent_indexes(
    store,
    root_norm: str,
    call_selection: str,
    exclude_repo: str = "",
    mode: str = "identity",
) -> list:
    """Existing local indexes matching (root, selection), cheapest path.

    ``mode="identity"`` (jdoc#82) applies the symmetric identity relation —
    the basis for conflict and ambiguity decisions, independent of creation
    order. ``mode="refresh"`` applies the directional coverage relation used
    only to route omitted-name calls to an established handle (a full index
    also covers a temporary subset refresh).

    Reads ``store.list_repos()`` (summary-sidecar backed since jdoc#77 — no
    monolith parse). Returns compact entry dicts; never raises.
    """
    matcher = selection_covers if mode == "refresh" else selection_identical
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
        if matcher(entry.get("corpus_selection") or "", call_selection):
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
