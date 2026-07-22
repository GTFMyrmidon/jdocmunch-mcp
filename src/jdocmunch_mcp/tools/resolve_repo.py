"""doc_resolve_repo — resolve a filesystem path to its doc-index handle (jdoc#79).

An agent working in a local project knows the folder path, not the index name.
The only general lookup used to be ``doc_list_repos``, whose response grows with
every indexed corpus on the machine. This tool answers the one question — "which
doc index covers this path?" — in a response whose size is independent of how
many indexes exist.

Matching is pure metadata lookup over each index's stored ``source_root``:
exact match first, then the most specific (deepest) containing root. No index
is created, refreshed, or modified, and no documentation is re-scanned.
GitHub-indexed corpora have no ``source_root`` and are never matched.

Sibling of jCodeMunch's ``resolve_repo`` (suite parity, jcm#296 principle); the
``doc_`` prefix keeps the two servers' tool names collision-free when both are
connected, matching ``doc_list_repos`` / ``doc_index_repo``.
"""

import os
import time
from pathlib import Path
from typing import Optional

from ..storage.doc_store import DocStore

# Bound on the candidate list returned for ambiguous matches, so the response
# stays small no matter how many duplicate indexes cover the same root.
_MAX_AMBIGUOUS_CANDIDATES = 5


def _norm(p: Path) -> str:
    """Case/separator-normalized string form for cross-platform comparison."""
    return os.path.normcase(str(p))


def _contains(root_norm: str, path_norm: str) -> bool:
    """True when path_norm is root_norm itself or strictly inside it."""
    if path_norm == root_norm:
        return True
    root = root_norm.rstrip(os.sep)
    return path_norm.startswith(root + os.sep)


def _candidate_row(entry: dict) -> dict:
    """Compact candidate shape for ambiguous responses."""
    row = {"repo": entry.get("repo", ""), "source_root": entry.get("source_root", "")}
    if entry.get("indexed_at"):
        row["indexed_at"] = entry["indexed_at"]
    return row


def _matched_response(entry: dict, match: str, t0: float, resolved: str) -> dict:
    result = {
        "found": True,
        "indexed": True,
        "repo": entry.get("repo", ""),
        "repo_kind": "doc_index",
        "match": match,
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "resolved_path": resolved,
        },
    }
    for key in (
        "owner", "name", "source_root", "indexed_at",
        "section_count", "doc_count", "repo_at_sha",
    ):
        value = entry.get(key)
        if value is not None and value != "":
            result[key] = value
    return result


def doc_resolve_repo(path: str, storage_path: Optional[str] = None) -> dict:
    """Resolve a filesystem path to its indexed documentation repo handle.

    Accepts an index root, a subfolder, or a file inside an indexed folder.
    Prefers an exact ``source_root`` match, then the most specific containing
    root. Equally-specific matches (duplicate indexes over one root) return a
    bounded ``candidates`` list with ``ambiguous: true`` instead of guessing.

    Read-only: never creates, refreshes, or deletes an index. Response size
    does not grow with the number of indexed repositories.

    Relative paths are resolved against the server process's current working
    directory, reported back as ``_meta.resolved_path`` — pass absolute paths
    for unambiguous results.
    """
    t0 = time.perf_counter()

    if not isinstance(path, str) or not path.strip():
        return {
            "found": False,
            "indexed": False,
            "error": "path must be a non-empty string",
            "_meta": {"latency_ms": int((time.perf_counter() - t0) * 1000)},
        }

    p = Path(path)
    if not p.exists():
        return {
            "found": False,
            "indexed": False,
            "error": f"Path does not exist: {path}",
            "_meta": {"latency_ms": int((time.perf_counter() - t0) * 1000)},
        }

    # resolve() canonicalizes case-insensitive drive letters, `..`, and
    # symlinks, so a symlinked alias of an indexed root still matches and a
    # symlink pointing outside every root correctly misses.
    p_resolved = p.resolve()
    p_norm = _norm(p_resolved)
    resolved_str = str(p_resolved)

    store = DocStore(base_path=storage_path)
    try:
        repos = store.list_repos()
    except Exception:
        repos = []

    exact: list[dict] = []
    containing: list[tuple[int, dict]] = []
    for entry in repos:
        sr = entry.get("source_root") or ""
        if not sr:
            continue  # GitHub-indexed corpus — no local root to match
        try:
            root_norm = _norm(Path(sr).resolve())
        except (OSError, ValueError):
            continue
        if p_norm == root_norm:
            exact.append(entry)
        elif _contains(root_norm, p_norm):
            containing.append((len(root_norm), entry))

    if len(exact) == 1:
        return _matched_response(exact[0], "exact_source_root", t0, resolved_str)
    if len(exact) > 1:
        return _ambiguous_response(exact, "exact_source_root", t0, resolved_str)

    if containing:
        containing.sort(key=lambda x: x[0], reverse=True)
        deepest = containing[0][0]
        best = [entry for depth, entry in containing if depth == deepest]
        if len(best) == 1:
            return _matched_response(
                best[0], "source_root_containment", t0, resolved_str
            )
        return _ambiguous_response(best, "source_root_containment", t0, resolved_str)

    # jdoc#83 (Item B): no exact or containing source_root matched — the path
    # may be a linked Git worktree of an already-indexed corpus. Worktree
    # discovery is strictly read-only and additive: the requested path stays
    # unindexed (found/indexed false), the established handles appear in
    # bounded canonical_candidates, and selection evidence is reported as
    # unavailable — a path alone can never prove Item A selection identity.
    not_found: dict = {
        "found": False,
        "indexed": False,
        "hint": (
            "No documentation index was found for this path. "
            "Call index_local to index it, or doc_list_repos to browse indexes."
        ),
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "resolved_path": resolved_str,
        },
    }
    try:
        from ._worktree_corpus import (
            MAX_CANDIDATES,
            REASON_GIT_VERIFICATION_UNAVAILABLE,
            ResolutionRequest,
            collect_git_evidence,
            filter_lineage_candidates,
            resolve_worktree_corpus,
        )

        probe_root = p_resolved if p_resolved.is_dir() else p_resolved.parent
        evidence = collect_git_evidence(probe_root)
        if evidence.in_git:
            candidates = filter_lineage_candidates(
                repos, evidence, allow_containment=True
            )
            decision = resolve_worktree_corpus(
                ResolutionRequest(tool="doc_resolve_repo", evidence=evidence),
                candidates,
            )
            if decision.status != "no_match":
                not_found["worktree_resolution"] = decision.to_public()
                if decision.candidates:
                    # jdoc#84 item 1: both public candidate lists share the
                    # MAX_CANDIDATES bound; the full count stays reported via
                    # worktree_resolution.total_candidates.
                    not_found["canonical_candidates"] = list(
                        decision.candidates[:MAX_CANDIDATES]
                    )
                if decision.next_action:
                    not_found["hint"] = decision.next_action
        elif evidence.verification_failed:
            # jdoc#88 QA-04: Git could not answer at all (missing binary,
            # timeout, permissions) — a state previously indistinguishable
            # from a confirmed non-Git path. Disclose that identity
            # verification was unavailable and that worktree discovery was
            # skipped. index_local still works; a new index created in this
            # state is quarantined provisional (jdoc#80 Part B), unchanged.
            not_found["git_verification"] = {
                "verified": False,
                "reason_code": REASON_GIT_VERIFICATION_UNAVAILABLE,
                "detail": (
                    "Git verification failed for this path (the git binary, "
                    "a timeout, or permissions), so it could not be checked "
                    "against indexed repository worktrees. This is distinct "
                    "from a confirmed non-Git path."
                ),
            }
            not_found["hint"] = (
                "No documentation index was found for this path, and Git "
                "verification was unavailable, so worktree-based canonical-"
                "index discovery was skipped. index_local can still index "
                "it; the new index will be provisional until verification "
                "succeeds."
            )
        not_found["_meta"]["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    except Exception:
        pass  # discovery is best-effort; the plain not-found stays valid
    return not_found


def _ambiguous_response(entries: list, match: str, t0: float, resolved: str) -> dict:
    return {
        "found": True,
        "indexed": True,
        "ambiguous": True,
        "match": match,
        "candidates": [_candidate_row(e) for e in entries[:_MAX_AMBIGUOUS_CANDIDATES]],
        "total_matches": len(entries),
        "hint": (
            "Multiple documentation indexes match this path equally well — "
            "pick one candidate explicitly."
        ),
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "resolved_path": resolved,
        },
    }
