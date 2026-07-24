"""Unified retrieval verdict — suite-parity honesty contract (jDoc side).

Mirrors the agent-facing ``_meta.verdict`` that jCodeMunch emits on its search
tools: an empty or weak section search is positive, token-saving evidence — a
grounded index can attest "this topic is not documented here" where a nearest-
neighbour search always returns its closest something. Clean-room jDoc
implementation (no cross-suite import); only the wire shape is shared.

Taxonomy:

* ``ok``             — confident matches returned.
* ``low_confidence`` — matches returned but the retrieval confidence is below the
  ambiguity floor (see :mod:`.confidence`: top1 < 0.4 ⇒ ambiguous / uncovered).
* ``absent``         — nothing matched after scanning the index; strong evidence
  the topic is not present, so the agent should stop reformulating the query.
* ``degraded``       — a requested channel was silently downgraded (semantic
  search asked for on an index with no embeddings), so absence is NOT proven.
"""

from __future__ import annotations

from typing import Optional, Sequence

STATE_OK = "ok"
STATE_LOW_CONFIDENCE = "low_confidence"
STATE_ABSENT = "absent"
STATE_DEGRADED = "degraded"

# v1.103.0: version pin for the confidence heuristics this verdict reports.
# Bump whenever the scoring formula (BM25/semantic fusion, the ambiguity
# floor) changes, so a stated confidence ties to the scorer that produced it:
# "0.8 under scorer v1" is a measurable statement, "0.8" alone is not.
SCORER_VERSION = 1

# Below this the top hit is ambiguous or the index likely doesn't cover the
# topic — the threshold the confidence module documents as the trust cliff.
LOW_CONFIDENCE_THRESHOLD = 0.4

_NOTES = {
    STATE_OK: "Confident matches returned.",
    STATE_LOW_CONFIDENCE: (
        "Matches are below the confidence floor; the query may be ambiguous or "
        "thinly covered. Verify before relying on them."
    ),
    STATE_ABSENT: (
        "No section matched after scanning the index. Treat this as strong "
        "evidence the topic is not documented here; do not reformulate the same "
        "query expecting a hit."
    ),
    STATE_DEGRADED: (
        "A requested retrieval channel was unavailable (semantic search on an "
        "index with no embeddings), so results are lexical-only and absence is "
        "NOT proven. Re-index with embeddings for semantic recall."
    ),
}


def index_coverage_meta(index) -> Optional[dict]:
    """Query-time coverage disclosure backing an absence claim (v1.103.0).

    Pulls the coverage contract persisted at the last full discovery walk
    (``DocIndex.coverage``) plus generation metadata off the index. Returns
    None when the index predates coverage recording — absence of the block
    means "coverage unknown", never "nothing was excluded". Suite parity with
    jCodeMunch v1.108.145; clean-room jDoc shape (sections, not symbols).
    """
    cov = getattr(index, "coverage", None)
    if not isinstance(cov, dict) or not cov:
        return None
    generation: dict = {}
    indexed_at = getattr(index, "indexed_at", "") or ""
    if indexed_at:
        generation["indexed_at"] = indexed_at
    index_version = getattr(index, "index_version", None)
    if index_version:
        generation["index_version"] = index_version
    head = getattr(index, "head_sha", "") or ""
    if head:
        generation["git_head"] = head[:12]
    out: dict = {"generation": generation}
    if cov.get("files_indexed") is not None:
        out["files_indexed"] = cov["files_indexed"]
    skips = cov.get("skip_counts") or {}
    if skips:
        out["excluded"] = skips
    if cov.get("no_sections_count"):
        out["no_sections_files"] = cov["no_sections_count"]
    return out


def index_changed_since_load(index) -> bool:
    """Whether the index monolith was rewritten since it was loaded (never raises).

    Backs the fifth absence refusal rule. Answers a question index staleness
    cannot: a reindex of an UNCHANGED source tree rewrites sections while
    reporting ``fresh``, and jdoc scores body text through a lazy content
    loader that reads from disk at scan time, so a rebuild mid-scan can move
    the very bytes being ranked.

    Deliberately a filesystem signal so it still fires when a SEPARATE watcher
    process drives the rebuild, which in-process state cannot observe.

    Unknown is NOT changed: an index with no stamped provenance (a test double,
    a hand-built DocIndex) returns False rather than degrading every verdict.
    """
    try:
        from pathlib import Path

        path = getattr(index, "_index_path", None)
        loaded_at = getattr(index, "_loaded_mtime_ns", None)
        if not path or loaded_at is None:
            return False
        return Path(path).stat().st_mtime_ns != int(loaded_at)
    except Exception:
        return False


def _attach_coverage(verdict: dict, coverage: Optional[dict]) -> None:
    """Attach coverage disclosure to absent/degraded verdicts (in place).

    Only the states where "what wasn't scanned" changes the meaning of the
    result carry the block; ok/low_confidence stay lean.
    """
    if coverage and verdict.get("state") in (STATE_ABSENT, STATE_DEGRADED):
        verdict["coverage"] = coverage


def suggest_docs(
    query: str,
    sections: Optional[Sequence[dict]],
    cap: int = 5,
) -> list:
    """Documents whose path or title contains a query term (near-misses).

    Returned on an absent verdict so the agent can redirect instead of retrying
    the same empty query. Deduped by ``doc_path``.
    """
    terms = [t for t in (query or "").lower().split() if len(t) >= 3]
    if not terms or not sections:
        return []
    out: list = []
    seen: set = set()
    for sec in sections:
        doc_path = sec.get("doc_path") or ""
        title = (sec.get("title") or "").lower()
        hay = doc_path.lower() + " " + title
        if any(t in hay for t in terms):
            if doc_path and doc_path not in seen:
                seen.add(doc_path)
                out.append(doc_path)
                if len(out) >= cap:
                    break
    return out


def suggest_endpoints(
    requested_path: Optional[str],
    op_paths: Sequence[str],
    cap: int = 5,
) -> list:
    """Existing endpoint paths that share a literal segment with a missed glob."""
    if not requested_path or not op_paths:
        return []
    literal = requested_path.replace("*", " ").replace("/", " ")
    segs = [s.lower() for s in literal.split() if len(s) >= 2]
    if not segs:
        return []
    out: list = []
    seen: set = set()
    for p in op_paths:
        pl = (p or "").lower()
        if p and p not in seen and any(s in pl for s in segs):
            seen.add(p)
            out.append(p)
            if len(out) >= cap:
                break
    return out


def filter_verdict(
    result_count: int,
    did_you_mean: Optional[Sequence[str]] = None,
    coverage: Optional[dict] = None,
) -> dict:
    """Verdict for structured (non-ranked) lookups — ``ok`` / ``absent`` only.

    No lexical/semantic channels: a path/method/tag filter either matches or it
    doesn't, so ``low_confidence`` and ``degraded`` do not apply. No scores or
    thresholds either, so no ``scorer`` pin. ``coverage`` (from
    :func:`index_coverage_meta`) is attached only on ``absent``.
    """
    state = STATE_OK if result_count else STATE_ABSENT
    verdict = {"state": state, "note": _NOTES[state]}
    if did_you_mean:
        verdict["did_you_mean"] = list(did_you_mean)[:5]
    _attach_coverage(verdict, coverage)
    return verdict


def build_verdict(
    *,
    result_count: int,
    confidence: Optional[float] = None,
    semantic_requested: bool = False,
    semantic_available: bool = True,
    lexical_used: bool = True,
    index_stale: bool = False,
    index_changed: bool = False,
    did_you_mean: Optional[Sequence[str]] = None,
    coverage: Optional[dict] = None,
) -> dict:
    """Compute the ``_meta.verdict`` dict for a section search.

    ``degraded`` takes precedence over ``absent``: a downgraded channel means a
    partial scan, which cannot prove absence. ``coverage`` (from
    :func:`index_coverage_meta`) is attached only on ``absent``/``degraded`` —
    an absence claim discloses what index-time discovery excluded.
    """
    below = confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD

    if semantic_requested and not semantic_available:
        state = STATE_DEGRADED
    elif result_count == 0 and index_changed:
        # The index was rewritten underneath this scan, so "we looked and it is
        # not there" describes a corpus that was moving while we read it. Same
        # reasoning as the stale gate: degraded cannot prove absence, so the
        # refusal falls out of the existing "only `absent` proves absence"
        # check with no new rule to keep in sync. Scoped to the absence path:
        # a scan that RETURNED sections still returns them.
        state = STATE_DEGRADED
    elif result_count == 0:
        state = STATE_ABSENT
    elif below:
        state = STATE_LOW_CONFIDENCE
    else:
        state = STATE_OK

    if semantic_requested and not semantic_available:
        semantic_channel = "unavailable"
    elif semantic_requested:
        semantic_channel = "ok"
    else:
        semantic_channel = "off"

    verdict = {
        "state": state,
        "channels": {
            "lexical": "ok" if lexical_used else "off",
            "semantic": semantic_channel,
            # "rebuilding" is disclosed on EVERY state, not just the degraded
            # one: a caller reading an `ok` result still deserves to know the
            # index moved under it. Only the absence CLAIM is refused.
            "index": (
                "rebuilding" if index_changed else ("stale" if index_stale else "fresh")
            ),
        },
        "scorer": SCORER_VERSION,
        "note": _NOTES[state],
    }
    if confidence is not None:
        verdict["confidence"] = round(float(confidence), 4)
    if did_you_mean:
        verdict["did_you_mean"] = list(did_you_mean)[:5]
    _attach_coverage(verdict, coverage)
    return verdict
