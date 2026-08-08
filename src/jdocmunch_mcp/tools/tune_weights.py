"""tune_weights — propose per-repo semantic_weight from ranking ledger (v1.23.0)."""

from __future__ import annotations

import time
from typing import Optional

from ..retrieval.tuning import (
    MAX_AGE_DAYS,
    MIN_EVENTS,
    set_semantic_weight,
    tune_all_repos,
    tune_one_repo,
)
from ..storage.token_tracker import _telemetry_enabled


def tune_weights(
    repo: Optional[str] = None,
    min_events: int = MIN_EVENTS,
    dry_run: bool = False,
    max_age_days: int = MAX_AGE_DAYS,
    set_weight: Optional[float] = None,
    storage_path: Optional[str] = None,
) -> dict:
    """Run online weight tuning across one or every indexed repo.

    Without telemetry enabled (``JDOCMUNCH_PERF_TELEMETRY=1``) there are
    no ranking events to learn from; we report that and return without
    touching disk. ``max_age_days`` (default 90) windows the ledger read
    so stale events can't anchor the proposal; 0 = lifetime.

    ``set_weight`` skips learning entirely and persists an explicit value
    for ``repo`` (jdoc#106). ⚠ It does NOT require telemetry — that gate
    exists because there is nothing to learn from without a ledger, and
    writing down a value you already measured needs no ledger at all.
    Requires ``repo``; a weight outside the bounds is clamped and the
    response says so.
    """
    t0 = time.perf_counter()

    if set_weight is not None:
        if not repo:
            return {
                "status": "repo_required",
                "hint": "set_weight applies to one repo; pass repo=owner/name.",
                "_meta": {"latency_ms": int((time.perf_counter() - t0) * 1000)},
            }
        result = set_semantic_weight(repo, set_weight, base_path=storage_path)
        return {
            "results": [result],
            "_meta": {
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "scope": "single_repo",
                "mode": "set_weight",
            },
        }

    if not _telemetry_enabled():
        return {
            "status": "telemetry_disabled",
            "hint": "Set JDOCMUNCH_PERF_TELEMETRY=1 to begin recording ranking events.",
            "_meta": {"latency_ms": int((time.perf_counter() - t0) * 1000)},
        }

    if repo:
        result = tune_one_repo(
            repo=repo,
            min_events=min_events,
            dry_run=dry_run,
            max_age_days=max_age_days,
            base_path=storage_path,
        )
        return {
            "results": [result],
            "_meta": {
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "scope": "single_repo",
                "dry_run": dry_run,
            },
        }
    results = tune_all_repos(
        min_events=min_events,
        dry_run=dry_run,
        max_age_days=max_age_days,
        base_path=storage_path,
    )
    return {
        "results": results,
        "_meta": {
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "scope": "all_repos",
            "repo_count": len(results),
            "dry_run": dry_run,
        },
    }
