"""Retrieval confidence score (v1.16.0).

Returns a 0–1 number that an agent can use to decide:

- top1 ≈ 1.0  → trust the top result; no need to read more.
- top1 ≈ 0.6  → top result is the best of several similar candidates.
- top1 < 0.4  → query is ambiguous or the index doesn't cover the topic.

Weighted geometric mean of four sub-signals:

- **gap** (0.35): ``(top1 - top2) / top1`` — bigger gap = more decisive.
- **strength** (0.35): ``1 - exp(-3 * top1 / ceiling)`` — saturates at the
  scorer's ceiling. See the scale warning below.
- **identity** (0.15): exact title match in top-3 → 1.0; else 0.7 (no penalty).
- **freshness** (0.15): 1.0 fresh / 0.6 stale (any non-fresh in top-3).

Geometric mean (rather than arithmetic) means a near-zero sub-signal
drags the whole score down — appropriate for "is this trustworthy."

⚠⚠ **`strength` reads a RAW score, so it is meaningless without knowing
which scorer produced it.** Through v1.125.0 it was hardcoded to the BM25
scale (``1 - exp(-top1 / 4)``, saturating near top1=12) and applied to every
mode. BM25 tops out in the tens; an RRF fused score tops out at ``1/(k+1)``
≈ 0.0164; a cosine tops out at 1.0. So the SAME ranking quality scored
0.62 confidence in lexical mode and 0.087 in hybrid — a ~7x penalty for
nothing but the units.

That is not cosmetic. Two consumers read this number:

  - `build_verdict`'s `low_confidence` state, which REFUSES to mint a
    citable absence claim. Hybrid searches were being disqualified from
    evidence they had earned.
  - `retrieval.tuning`, which compares mean confidence with vs without the
    semantic channel. The scale gap swamped the real signal, so the tuner
    read `semantic_hurts` on every mixed-mode ledger and walked
    `semantic_weight` DOWN to its 0.10 floor — measured, monotonic, on data
    where the semantic channel was the one answering the queries.

Each scorer now declares its ceiling. ⚠ The lexical curve is UNCHANGED:
``1 - exp(-3 * t / 12)`` is algebraically identical to ``1 - exp(-t / 4)``,
so BM25 confidences are byte-identical to v1.125.0 and only the modes that
were wrong move.
"""

from __future__ import annotations

import math

WEIGHTS = {
    "gap": 0.35,
    "strength": 0.35,
    "identity": 0.15,
    "freshness": 0.15,
}

#: Score at which `strength` reaches 1-exp(-3) ≈ 0.95, per scorer.
#: BM25 keeps its historical 12. RRF's maximum possible fused score is
#: `sum(w_i)/(k+1)` = `1/(k+1)` when the weights sum to 1 — independent of
#: how the weight is split, which is what makes it a usable ceiling.
#: Cosine is bounded at 1.0 by definition.
BM25_CEILING = 12.0
COSINE_CEILING = 1.0


def rrf_ceiling(k: int = 60) -> float:
    """Maximum achievable RRF score: an item ranked 1st in every channel."""
    return 1.0 / (k + 1)


def _gap(top1: float, top2: float) -> float:
    if top1 <= 0:
        return 0.0
    return max(0.0, min(1.0, (top1 - top2) / top1))


def _strength(top1: float, ceiling: float = BM25_CEILING) -> float:
    """Saturating strength, normalized to the scorer's own ceiling.

    ⚠ `ceiling` is where the curve reaches ~0.95, NOT a hard cap: a score
    above it is legal and simply saturates further.
    """
    if top1 <= 0:
        return 0.0
    if ceiling <= 0:
        ceiling = BM25_CEILING
    return 1.0 - math.exp(-3.0 * top1 / ceiling)


def _identity(query: str, results: list) -> float:
    """Return 1.0 if any of the top-3 has an exact (case-insensitive)
    title match for the query string. Otherwise 0.7 — no penalty for
    legitimate paraphrase matches."""
    q = (query or "").strip().lower()
    if not q:
        return 0.7
    for sec in results[:3]:
        if (sec.get("title", "") or "").strip().lower() == q:
            return 1.0
    return 0.7


def _freshness(results: list) -> float:
    """0.6 when any non-fresh marker appears in top-3, else 1.0."""
    for sec in results[:3]:
        bucket = sec.get("_freshness")
        if bucket and bucket != "fresh":
            return 0.6
    return 1.0


def ceiling_for_mode(mode: str, k: int = 60) -> float:
    """Strength ceiling for a `search_mode` string.

    ⚠ Defaults to the BM25 ceiling for an unknown mode, so a caller that
    forgets to pass one gets exactly the pre-v1.126.0 behaviour rather than
    a silently different number.
    """
    if mode == "hybrid":
        return rrf_ceiling(k)
    if mode == "semantic_only":
        return COSINE_CEILING
    return BM25_CEILING


def compute_confidence(
    query: str,
    results: list,
    score_field: str = "_score",
    score_ceiling: float = BM25_CEILING,
) -> dict:
    """Return ``{value, components}`` ∈ [0, 1].

    ``score_field`` is the per-result score key (defaults to ``_score`` —
    the BM25/RRF value the search code attaches before confidence runs).

    ``score_ceiling`` is the scale of the scorer that produced those scores
    — see the module docstring. Use :func:`ceiling_for_mode`. It defaults to
    BM25 so an un-updated caller is unchanged, not newly wrong.
    """
    if not results:
        return {
            "value": 0.0,
            "components": {"gap": 0.0, "strength": 0.0, "identity": 0.7, "freshness": 1.0},
        }

    scores = []
    for sec in results[:5]:
        s = sec.get(score_field)
        if isinstance(s, (int, float)):
            scores.append(float(s))
    top1 = scores[0] if len(scores) >= 1 else 0.0
    top2 = scores[1] if len(scores) >= 2 else 0.0

    components = {
        "gap": _gap(top1, top2),
        "strength": _strength(top1, score_ceiling),
        "identity": _identity(query, results),
        "freshness": _freshness(results),
    }

    # Weighted geometric mean. Floor sub-signals at 1e-6 so log isn't
    # negative-infinity when a sub-signal is exactly zero.
    log_sum = 0.0
    for key, val in components.items():
        v = max(1e-6, min(1.0, float(val)))
        log_sum += WEIGHTS[key] * math.log(v)
    value = math.exp(log_sum)
    return {"value": round(max(0.0, min(1.0, value)), 4), "components": {k: round(v, 4) for k, v in components.items()}}


def attach_confidence(
    query: str,
    results: list,
    meta: dict,
    *,
    include_components: bool = False,
    score_field: str = "_score",
    score_ceiling: float = BM25_CEILING,
) -> None:
    """Mutate ``meta`` in place with ``confidence`` (and optionally
    ``confidence_components``)."""
    out = compute_confidence(
        query, results, score_field=score_field, score_ceiling=score_ceiling
    )
    meta["confidence"] = out["value"]
    if include_components:
        meta["confidence_components"] = out["components"]
