"""Worktree-aware corpus resolution (jdoc#83, Item B of jdoc#80).

Item A (jdoc#81, hardened in jdoc#82) established same-root corpus identity:
normalized source root + deterministic durable selection. Linked Git worktrees
break the root-based half — the same documentation corpus at the same commit
lives under two absolute roots, so v1.101.0 happily created two healthy
duplicate indexes and resolved the second worktree to nothing.

This module adds the worktree translation layer as ONE side-effect-free
resolver shared by ``doc_resolve_repo`` (read-only discovery) and
``index_local`` (a gate before any mutation). The model, per the #83 PRD:

    Logical identity = lineage + relative location + durable selection
    Freshness        = certified revision + relevant dirty state
    Physical state   = source_root + handle + stored index artifacts

* **Lineage** — the linked-worktree family, evidenced by the Git common
  directory (``git rev-parse --path-format=absolute --git-common-dir``).
  Strong local evidence, never a portable ID: nothing is ever written into
  ``.git``, and lineage is never inferred from remote URL, commit, folder
  name, or byte-identical content. States: ``confirmed`` / ``unknown`` /
  ``conflicting``; only ``confirmed`` supports automatic cross-worktree reuse.
* **Relative location** — the repository-relative corpus root. ``docs/`` and
  ``packages/widget/docs/`` in one repository are different corpora.
* **Durable selection** — Item A's deterministic descriptor, compared only
  when the caller can supply it (``index_local`` can; ``doc_resolve_repo``
  reports selection evidence as unavailable and never claims reusability).

Everything uncertain fails closed with no write: ``related``, ``unknown``,
and ``ambiguous`` outcomes never authorize reuse or automatic creation
(invariants I3/I6/I8 of the PRD). Reuse means returning the established
handle; it never refreshes, renames, retargets, or repairs anything.
"""

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ._git import _git

# Evidence states (PRD 4.3).
LINEAGE_CONFIRMED = "confirmed"
LINEAGE_UNKNOWN = "unknown"
LINEAGE_CONFLICTING = "conflicting"

# Public statuses (PRD 9.2).
STATUS_EXACT = "exact"
STATUS_CREATED = "created"
STATUS_REUSABLE = "reusable"
STATUS_REFERENCE_ONLY = "reference_only"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_RELATED = "related"
STATUS_UNKNOWN = "unknown"
STATUS_NO_MATCH = "no_match"

MAX_CANDIDATES = 5

# Identity schema version for the persisted evidence fields (PRD 10.1).
CORPUS_IDENTITY_VERSION = 1


def _norm(p: str) -> str:
    try:
        return os.path.normcase(str(Path(p).resolve()))
    except (OSError, ValueError):
        return os.path.normcase(str(p))


@dataclass
class GitEvidence:
    """Git facts for one requested corpus path, collected ONCE per request
    (PRD 10.3 / I7 — Git work must not grow with the index inventory)."""

    in_git: bool = False
    lineage_state: str = LINEAGE_UNKNOWN
    lineage_key: str = ""          # sha1[:16] of the normalized common dir
    common_dir: str = ""           # normalized absolute git common dir
    toplevel: str = ""             # normalized worktree top level
    relative_root: str = ""        # corpus root relative to toplevel (posix)
    head_sha: Optional[str] = None
    corpus_dirty: bool = False     # uncommitted changes under the corpus root


def lineage_key_for(common_dir_norm: str) -> str:
    return hashlib.sha1(common_dir_norm.encode("utf-8", errors="replace")).hexdigest()[:16]


def collect_git_evidence(corpus_root: Path) -> GitEvidence:
    """Collect lineage + freshness evidence for one corpus root.

    Exactly four bounded git subprocesses (common-dir, toplevel, HEAD,
    status), all through the timeout-guarded ``_git`` helper. Any failure
    degrades to ``unknown`` — a Git failure fails closed, never open
    (PRD Section 11).
    """
    ev = GitEvidence()
    ok, common = _git(
        corpus_root, ["rev-parse", "--path-format=absolute", "--git-common-dir"]
    )
    if not ok or not common:
        # Older git (<2.31) lacks --path-format; fall back and absolutize.
        ok, common = _git(corpus_root, ["rev-parse", "--git-common-dir"])
        if ok and common and not os.path.isabs(common):
            common = str((corpus_root / common))
    if not ok or not common:
        return ev  # not a git repo (or git failed): in_git False, unknown
    ev.in_git = True
    ev.common_dir = _norm(common)
    ev.lineage_key = lineage_key_for(ev.common_dir)

    ok, top = _git(corpus_root, ["rev-parse", "--show-toplevel"])
    if not ok or not top:
        return ev  # inside .git dir or bare oddity: lineage stays unknown
    ev.toplevel = _norm(top)
    try:
        rel = Path(_norm(str(corpus_root))).relative_to(ev.toplevel).as_posix()
    except ValueError:
        return ev
    ev.relative_root = rel if rel != "." else ""
    ev.lineage_state = LINEAGE_CONFIRMED

    ok, head = _git(corpus_root, ["rev-parse", "HEAD"])
    if ok and len(head) == 40:
        ev.head_sha = head
    # Relevant dirty state: any uncommitted change under the corpus root
    # (tracked or untracked) makes automatic cross-worktree reuse unsafe.
    # git runs with cwd == corpus_root, so the pathspec is "." (cwd-relative),
    # NOT the toplevel-relative root.
    ok, status = _git(
        corpus_root, ["status", "--porcelain", "--untracked-files=all", "--", "."]
    )
    ev.corpus_dirty = bool(status) if ok else True  # unknown status = dirty
    return ev


@dataclass
class ResolutionRequest:
    """One tool call's identity question for the pure resolver."""

    tool: str                       # "doc_resolve_repo" | "index_local"
    evidence: GitEvidence
    selection: Optional[str] = None  # Item A descriptor; None = unavailable
    branch_local: bool = False       # explicit worktree_mode="branch_local"


@dataclass
class ResolutionDecision:
    status: str
    reason_code: str
    established_handle: str = ""
    write_policy: str = "read_only"
    candidates: list = field(default_factory=list)
    total_candidates: int = 0
    next_action: str = ""
    identity: dict = field(default_factory=dict)
    freshness: dict = field(default_factory=dict)

    def to_public(self, did_write: bool = False) -> dict:
        """The additive ``worktree_resolution`` response object (PRD 9.1)."""
        out = {
            "status": self.status,
            "reason_code": self.reason_code,
            "write_policy": self.write_policy,
            "did_write": bool(did_write),
        }
        if self.established_handle:
            out["established_handle"] = self.established_handle
        if self.identity:
            out["identity"] = dict(self.identity)
        if self.freshness:
            out["freshness"] = dict(self.freshness)
        if self.candidates:
            out["candidates"] = list(self.candidates[:MAX_CANDIDATES])
            out["total_candidates"] = self.total_candidates
        if self.next_action:
            out["next_action"] = self.next_action
        return out


def _candidate_row(entry: dict) -> dict:
    row = {
        "repo": entry.get("repo", ""),
        "source_root": entry.get("source_root", ""),
    }
    if entry.get("indexed_at"):
        row["indexed_at"] = entry["indexed_at"]
    return row


def _identity_block(ev: GitEvidence, selection_state: str) -> dict:
    return {
        "lineage": ev.lineage_state,
        "relative_source_root": ev.relative_root,
        "selection": selection_state,
    }


def _freshness_block(ev: GitEvidence, entry: Optional[dict]) -> dict:
    if entry is None:
        return {}
    cand_sha = entry.get("head_sha") or None
    certified = bool(entry.get("sha_certified"))
    if not ev.head_sha or not cand_sha or not certified:
        relation = "unknown"
    elif ev.head_sha == cand_sha:
        relation = "same"
    else:
        relation = "different"
    state = (
        "fresh"
        if relation == "same" and not ev.corpus_dirty and certified
        else ("unknown" if relation == "unknown" else "stale")
    )
    if ev.corpus_dirty and state != "unknown":
        state = "dirty"
    return {
        "state": state,
        "revision_relation": relation,
        "corpus_dirty": bool(ev.corpus_dirty),
    }


def filter_lineage_candidates(
    stored_rows: list, ev: GitEvidence, allow_containment: bool = False
) -> list:
    """Bounded metadata-only candidate filter (PRD 10.3): confirmed lineage
    key + repository-relative corpus location, from list_repos rows. No git
    subprocess per candidate — evidence was collected once for the request.

    ``allow_containment`` is for read-only path resolution ONLY
    (``doc_resolve_repo``): a file or subfolder inside a candidate corpus
    resolves to its owning index. ``index_local`` must pass False — indexing
    a nested location is a *different corpus* (PRD 4.2), never a containment
    match of the parent."""
    if ev.lineage_state != LINEAGE_CONFIRMED or not ev.lineage_key:
        return []
    exact: list = []
    containing: list = []
    for entry in stored_rows:
        repo = entry.get("repo") or ""
        if not repo.startswith("local/"):
            continue
        if (entry.get("worktree_lineage_key") or "") != ev.lineage_key:
            continue
        entry_rel = entry.get("repo_relative_root") or ""
        if entry_rel == ev.relative_root:
            exact.append(entry)
        elif not allow_containment:
            continue
        elif ev.relative_root.startswith(entry_rel + "/") if entry_rel else bool(ev.relative_root):
            # The requested location sits INSIDE the candidate corpus —
            # the worktree analog of source_root containment (a file or
            # subfolder resolves to its owning corpus). Distinct corpus
            # locations (siblings, or a parent asked about a child corpus)
            # never match: containment runs candidate-root -> request only.
            containing.append((len(entry_rel), entry))
    if exact:
        return exact
    if containing:
        containing.sort(key=lambda x: x[0], reverse=True)
        deepest = containing[0][0]
        return [entry for depth, entry in containing if depth == deepest]
    return []


def resolve_worktree_corpus(
    request: ResolutionRequest, stored_candidates: list
) -> ResolutionDecision:
    """The pure, side-effect-free decision (PRD Sections 7-8).

    ``stored_candidates`` are lineage-and-location matches (from
    :func:`filter_lineage_candidates`), each already known NOT to be an
    exact-path hit — exact and containing ``source_root`` precedence (R1)
    is applied by the caller before this resolver runs.
    """
    ev = request.evidence

    if request.branch_local:
        # R9: explicit branch-local intent takes the exact-path creation
        # path; this resolver imposes nothing beyond identity claiming,
        # which the caller handles.
        return ResolutionDecision(
            status=STATUS_CREATED,
            reason_code="branch_local_created",
            write_policy="explicit_branch_local",
            identity=_identity_block(ev, "not_compared"),
            next_action="Branch-local index requested explicitly; exact-path rules apply.",
        )

    if not ev.in_git:
        return ResolutionDecision(
            status=STATUS_NO_MATCH,
            reason_code="no_equivalent_candidate",
            write_policy="current_behavior",
            identity=_identity_block(ev, "not_compared"),
        )

    if ev.lineage_state != LINEAGE_CONFIRMED:
        return ResolutionDecision(
            status=STATUS_UNKNOWN,
            reason_code=(
                "lineage_conflict"
                if ev.lineage_state == LINEAGE_CONFLICTING
                else "lineage_unknown"
            ),
            write_policy="read_only",
            identity=_identity_block(ev, "unavailable"),
            next_action=(
                "Worktree lineage could not be confirmed; no automatic "
                "cross-worktree decision. Use worktree_mode='branch_local' "
                "if a local index is required."
            ),
        )

    rows = [_candidate_row(e) for e in stored_candidates]
    total = len(stored_candidates)

    if request.tool == "doc_resolve_repo":
        # R4: read-only path discovery. Selection evidence is unavailable
        # here — a path alone can't prove Item A selection identity, so the
        # result is reference material, never a reuse authorization.
        if total == 0:
            return ResolutionDecision(
                status=STATUS_NO_MATCH,
                reason_code="no_equivalent_candidate",
                write_policy="read_only",
                identity=_identity_block(ev, "unavailable"),
            )
        if total == 1:
            entry = stored_candidates[0]
            return ResolutionDecision(
                status=STATUS_REFERENCE_ONLY,
                reason_code="unique_location_candidate",
                established_handle=entry.get("repo", ""),
                write_policy="read_only",
                candidates=rows,
                total_candidates=total,
                identity=_identity_block(ev, "unavailable"),
                freshness=_freshness_block(ev, entry),
                next_action=(
                    "Use the canonical candidate for read-only retrieval; "
                    "durable selection was not compared."
                ),
            )
        return ResolutionDecision(
            status=STATUS_AMBIGUOUS,
            reason_code="multiple_equivalent_candidates",
            write_policy="read_only",
            candidates=rows,
            total_candidates=total,
            identity=_identity_block(ev, "unavailable"),
            next_action=(
                "Multiple established indexes match this worktree family and "
                "corpus location; select an existing handle explicitly."
            ),
        )

    # --- index_local decision (PRD 7.2, right column of Section 8) ---
    if request.selection is None:
        return ResolutionDecision(
            status=STATUS_RELATED,
            reason_code="selection_incomplete",
            write_policy="read_only",
            candidates=rows,
            total_candidates=total,
            identity=_identity_block(ev, "unavailable"),
            next_action=(
                "Durable-selection evidence was incomplete; no automatic "
                "decision. Retry with complete inputs or use "
                "worktree_mode='branch_local'."
            ),
        )

    # Compare Item A selection identity against each lineage-and-location
    # candidate. Legacy candidates without a stored selection are
    # UNRESOLVED, never inferred equivalent (I6).
    from ._corpus_identity import selection_identical

    equivalent = []
    unresolved_legacy = []
    for entry in stored_candidates:
        stored_sel = entry.get("corpus_selection") or ""
        if not stored_sel:
            unresolved_legacy.append(entry)
        elif selection_identical(stored_sel, request.selection):
            equivalent.append(entry)

    if len(equivalent) > 1:
        return ResolutionDecision(
            status=STATUS_AMBIGUOUS,
            reason_code="multiple_equivalent_candidates",
            write_policy="read_only",
            candidates=[_candidate_row(e) for e in equivalent],
            total_candidates=len(equivalent),
            identity=_identity_block(ev, "equivalent"),
            next_action=(
                "Multiple established indexes are equivalent to this corpus; "
                "select one existing handle explicitly. No index was created."
            ),
        )

    if len(equivalent) == 1:
        entry = equivalent[0]
        freshness = _freshness_block(ev, entry)
        if freshness.get("state") == "fresh":
            return ResolutionDecision(
                status=STATUS_REUSABLE,
                reason_code="equivalent_corpus_fresh",
                established_handle=entry.get("repo", ""),
                write_policy="reuse_only",
                candidates=[_candidate_row(entry)],
                total_candidates=1,
                identity=_identity_block(ev, "equivalent"),
                freshness=freshness,
                next_action="Use the established handle; no index was created.",
            )
        reason = (
            "equivalent_corpus_dirty"
            if freshness.get("state") == "dirty"
            else "equivalent_corpus_stale"
        )
        return ResolutionDecision(
            status=STATUS_REFERENCE_ONLY,
            reason_code=reason,
            established_handle=entry.get("repo", ""),
            write_policy="read_only",
            candidates=[_candidate_row(entry)],
            total_candidates=1,
            identity=_identity_block(ev, "equivalent"),
            freshness=freshness,
            next_action=(
                "The established index is not proven fresh for this "
                "worktree; read it, refresh it through its own handle, or "
                "use worktree_mode='branch_local'. No index was created."
            ),
        )

    if unresolved_legacy:
        return ResolutionDecision(
            status=STATUS_RELATED,
            reason_code="unresolved_legacy_candidate",
            write_policy="read_only",
            candidates=[_candidate_row(e) for e in unresolved_legacy],
            total_candidates=len(unresolved_legacy),
            identity=_identity_block(ev, "unresolved"),
            next_action=(
                "A related index predates selection identity and cannot be "
                "proven equivalent. Refresh it through its own handle to "
                "backfill identity, or use worktree_mode='branch_local'. "
                "No index was created."
            ),
        )

    return ResolutionDecision(
        status=STATUS_CREATED,
        reason_code="new_corpus_created",
        write_policy="create_if_claim_wins",
        identity=_identity_block(ev, "equivalent"),
        next_action="No established equivalent exists; creation may proceed under claim.",
    )


def worktree_claim_key(ev: GitEvidence, selection: str) -> Optional[str]:
    """Versioned claim key for worktree-translated first creation (PRD 10.4).

    Confirmed lineage + relative location + Item A selection, so two
    worktrees racing to create the same logical corpus contend on ONE claim
    even though their absolute roots differ. Returns None when lineage is
    unconfirmed (callers fall back to the exact-path key)."""
    if ev.lineage_state != LINEAGE_CONFIRMED or not ev.lineage_key:
        return None
    payload = f"wt1\n{ev.lineage_key}\n{ev.relative_root}\n{selection}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()
