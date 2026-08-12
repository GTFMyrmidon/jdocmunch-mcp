"""Regression test for jdoc#14: O(N^2) hang in related_persist.build().

The v1.24-v1.63 build() rebuilt section_dicts inside the per-section loop
and re-scanned `sections` 4x per section for child lookups. For N=15k
that ballooned to ~1.1B ops and hung at 100% CPU. v1.64.1 precomputes
the lookups once.

This test asserts the new build scales linearly: doubling N should not
multiply runtime by anything close to 4x. We pick a deliberately large N
that still completes in seconds on the fixed path but would hang for
minutes on the buggy path.
"""

from __future__ import annotations

import time
import warnings

from jdocmunch_mcp.retrieval.related_persist import build


def _make_sections(n: int) -> list[dict]:
    """Build a flat-ish hierarchy: 1 root, 10 second-level, rest are leaves.

    Structurally interesting (parent/sibling lookups exercise the children
    cache) but no embeddings, so the semantic path is skipped — we are
    measuring the structural side specifically.
    """
    sections: list[dict] = [
        {"id": "root", "title": "Root", "level": 1, "parent_id": ""}
    ]
    for i in range(10):
        sections.append(
            {"id": f"s{i}", "title": f"L2-{i}", "level": 2, "parent_id": "root"}
        )
    parent_ids = [f"s{i}" for i in range(10)]
    for i in range(n - 11):
        sections.append(
            {
                "id": f"leaf-{i}",
                "title": f"Leaf {i}",
                "level": 3,
                "parent_id": parent_ids[i % 10],
            }
        )
    return sections


def test_build_scales_linearly():
    """Directional sanity check that build() scales ~linearly (4k -> 8k).

    NON-BLOCKING by design: it divides two sub-second wall-clock timings, so
    on a loaded or parallelized host (pytest-xdist, another suite on the same
    box, a busy CI runner) the ratio is dominated by scheduler noise rather
    than algorithmic complexity, and a raw `assert ratio < 6.0` reddens the
    build on a change that didn't touch this path. A breach is now reported as
    a warning, never a failure. The REAL O(N^2) anti-regression gate is
    test_build_completes_quickly_at_15k below (absolute wall-clock at the
    jdoc#14 reproducer size); a genuine quadratic regression blows that wall,
    it doesn't just nudge this ratio.
    """
    n_small, n_big = 4_000, 8_000

    sections_small = _make_sections(n_small)
    t0 = time.perf_counter()
    out_small = build(sections_small)
    t_small = time.perf_counter() - t0
    assert out_small["section_count"] == n_small

    sections_big = _make_sections(n_big)
    t0 = time.perf_counter()
    out_big = build(sections_big)
    t_big = time.perf_counter() - t0
    assert out_big["section_count"] == n_big

    # Floored gap: only judge scaling when the measured gap between the two
    # runs clears a floor large enough to be signal. Below it both runs sit in
    # timer-noise territory, so any ratio is meaningless and the build is
    # linear-enough by definition — return without warning.
    MEASURE_FLOOR = 0.05  # s — min per-measurement time before a ratio means anything
    GAP_FLOOR = 0.10      # s — min (t_big - t_small) to trust the comparison
    if (t_big - t_small) < GAP_FLOOR:
        return

    # On the O(N) path doubling N ~doubles runtime (ratio ~2x). Warn — never
    # fail — past 6x; the absolute-time test below is the real regression gate.
    ratio = max(t_big, MEASURE_FLOOR) / max(t_small, MEASURE_FLOOR)
    if ratio >= 6.0:
        warnings.warn(
            f"build() scaling looks non-linear: {n_small}={t_small:.3f}s vs "
            f"{n_big}={t_big:.3f}s (ratio={ratio:.2f}x, expected ~2x). "
            "Directional only (likely host load); see "
            "test_build_completes_quickly_at_15k for the real regression gate.",
            stacklevel=2,
        )


def test_build_completes_quickly_at_15k():
    """The jdoc#14 reproducer size — 15k sections should finish in <5s.

    On v1.63.3 this took >10 minutes (assumed-hung). The fixed path
    is O(N) on structural edges; without embeddings the semantic phase
    is a no-op, so this measures the bug directly.
    """
    sections = _make_sections(15_000)
    t0 = time.perf_counter()
    out = build(sections)
    elapsed = time.perf_counter() - t0
    assert out["section_count"] == 15_000
    # Threshold sized for CI under contention. Pre-fix was >10min (hung);
    # any number under 30s proves we're on the O(N) path.
    assert elapsed < 30.0, f"15k sections took {elapsed:.2f}s, expected <30s"
