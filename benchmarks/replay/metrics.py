"""Pure-Python retrieval-quality metrics.

All metrics treat relevance as binary: a result id is relevant iff it appears
in the fixture's expected_top_k list. Per-query scores are real-valued in [0,1];
aggregate() returns the arithmetic mean across queries.

These are intentionally simple — fixtures encode binary relevance because
retrieval-quality regressions show up at the rank-position level, not at the
fine-grained relevance-grade level.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence


def _binary_gains(predicted: Sequence[str], expected: Iterable[str], k: int) -> list[int]:
    """Return [1 if predicted[i] in expected else 0 for i in range(k)]."""
    expected_set = set(expected)
    out = []
    for i in range(k):
        if i < len(predicted) and predicted[i] in expected_set:
            out.append(1)
        else:
            out.append(0)
    return out


def ndcg_at_k(predicted: Sequence[str], expected: Sequence[str], k: int) -> float:
    """Normalized DCG@k with binary gains.

    DCG = sum_{i=0..k-1} gain[i] / log2(i+2)
    Ideal DCG places all relevant items first, capped at k.
    Returns 0.0 when no relevant items exist (degenerate fixture).
    """
    if k <= 0:
        return 0.0
    gains = _binary_gains(predicted, expected, k)
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    n_relevant = min(len(set(expected)), k)
    if n_relevant == 0:
        return 0.0
    ideal_dcg = sum(1 / math.log2(i + 2) for i in range(n_relevant))
    if ideal_dcg == 0:
        return 0.0
    return dcg / ideal_dcg


def mrr_at_k(predicted: Sequence[str], expected: Sequence[str], k: int) -> float:
    """Reciprocal rank of the first relevant item within the top k. 0 if none."""
    if k <= 0:
        return 0.0
    expected_set = set(expected)
    for i in range(min(k, len(predicted))):
        if predicted[i] in expected_set:
            return 1.0 / (i + 1)
    return 0.0


def recall_at_k(predicted: Sequence[str], expected: Sequence[str], k: int) -> float:
    """Fraction of expected items appearing within the top k predictions."""
    expected_set = set(expected)
    if not expected_set:
        return 0.0
    top_k = set(predicted[:k]) if k > 0 else set()
    return len(top_k & expected_set) / len(expected_set)


def aggregate(per_query: Sequence[dict]) -> dict:
    """Mean each metric across queries.

    Input: list of {ndcg, mrr, recall} dicts (one per query).
    Output: {ndcg, mrr, recall} dict of arithmetic means.
    Empty input returns zeros — the harness treats that as a degenerate fixture.
    """
    n = len(per_query)
    if n == 0:
        return {"ndcg": 0.0, "mrr": 0.0, "recall": 0.0}
    out = {"ndcg": 0.0, "mrr": 0.0, "recall": 0.0}
    for entry in per_query:
        for key in out:
            out[key] += float(entry.get(key, 0.0))
    for key in out:
        out[key] /= n
    return out
