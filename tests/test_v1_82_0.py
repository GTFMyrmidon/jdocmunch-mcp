"""jdoc#62: vectorized related-graph semantic build + sidecar ordering.

`related_persist.build`'s semantic half was an O(N^2) pure-Python all-pairs
cosine that stalled `index_local` on large embedded corpora, and the sidecar
was built before `save_index` so it gated the core index. v1.82.0 vectorizes
the semantic half with a single numpy matmul (pure-Python fallback when numpy
is absent) and persists the core index before the optional sidecars.

These tests are the parity safety net: the vectorized output must be IDENTICAL
to calling the untouched `semantic_neighbors` reference for every section.
"""
from __future__ import annotations

import math
import random
import time

import pytest

from jdocmunch_mcp.retrieval import related_persist as rp
from jdocmunch_mcp.retrieval.related_persist import build
from jdocmunch_mcp.retrieval.related import semantic_neighbors, structural_neighbors


def _make_corpus(n: int, *, dim: int = 24, k: int = 12, seed: int = 42) -> list[dict]:
    """Clustered embeddings so within-cluster cosines clear the 0.6 threshold
    and produce real neighbor lists (the top-5 cap and ordering actually bite),
    under a flat root/children hierarchy so structural edges exist too."""
    rng = random.Random(seed)
    centers = [[rng.gauss(0, 1) for _ in range(dim)] for _ in range(k)]
    secs: list[dict] = [
        {"id": "root", "title": "Root", "level": 1, "parent_id": "",
         "embedding": [rng.gauss(0, 1) for _ in range(dim)]}
    ]
    for i in range(n):
        c = centers[i % k]
        emb = [c[d] * 3.0 + rng.gauss(0, 0.3) for d in range(dim)]
        secs.append({"id": f"s{i}", "title": f"S{i}", "level": 2,
                     "parent_id": "root", "embedding": emb})
    return secs


def _reference(corpus: list[dict]) -> dict:
    """The pre-fix output shape, built straight from the untouched reference
    functions (this is exactly what build() produced before vectorization)."""
    out = {}
    for sec in corpus:
        sid = sec.get("id")
        if not sid:
            continue
        out[sid] = {
            "structural": structural_neighbors(corpus, sid),
            "semantic": semantic_neighbors(corpus, sid, top_n=5, min_score=0.6),
        }
    return out


class TestVectorizedParity:
    def test_matches_reference_with_edge_cases(self):
        np = pytest.importorskip("numpy")  # vectorized path requires numpy
        # 650 crosses the 512-row matmul block boundary.
        corpus = _make_corpus(650)
        by_id = {s["id"]: s for s in corpus}
        # Exact-duplicate embeddings -> tied scores -> exercises the tie-break.
        by_id["s6"]["embedding"] = list(by_id["s4"]["embedding"])
        by_id["s7"]["embedding"] = list(by_id["s4"]["embedding"])
        # A section with no embedding -> semantic must be [].
        del by_id["s8"]["embedding"]
        # A zero vector -> cosine 0 everywhere -> excluded, never NaN.
        by_id["s9"]["embedding"] = [0.0] * 24

        result = build(corpus)
        reference = _reference(corpus)

        assert result["section_count"] == len(reference)
        assert set(result["by_section"]) == set(reference)
        # Exact per-section parity: ids, scores (4dp), and ordering.
        for sid, ref in reference.items():
            assert result["by_section"][sid]["semantic"] == ref["semantic"], sid
            assert result["by_section"][sid]["structural"] == ref["structural"], sid

        # Sanity: the fixture actually exercised the paths we care about.
        assert result["by_section"]["s8"]["semantic"] == []          # no embedding
        assert result["by_section"]["s9"]["semantic"] == []          # zero vector
        assert any(len(v["semantic"]) == 5                            # top-5 cap hit
                   for v in result["by_section"].values())
        # The tie-break: for some target, s4/s6/s7 (identical vectors) appear
        # in ascending-index order when tied.
        saw_tie = False
        for v in result["by_section"].values():
            ids = [e["id"] for e in v["semantic"]]
            tied = [i for i in ("s4", "s6", "s7") if i in ids]
            if len(tied) >= 2:
                saw_tie = True
                assert tied == sorted(tied, key=lambda x: int(x[1:]))
        assert saw_tie, "fixture did not exercise the tie-break"

    def test_completes_quickly_at_4k(self):
        pytest.importorskip("numpy")
        corpus = _make_corpus(4000)
        t0 = time.perf_counter()
        out = build(corpus)
        elapsed = time.perf_counter() - t0
        assert out["section_count"] == 4001
        # The pre-fix pure-Python all-pairs path was minutes at this size.
        assert elapsed < 20.0, f"4k embedded sections took {elapsed:.2f}s"
        assert any(v["semantic"] for v in out["by_section"].values())


class TestPurePythonFallback:
    def test_fallback_matches_reference_without_numpy(self, monkeypatch):
        # Force the numpy-absent branch; build must use semantic_neighbors and
        # produce identical output.
        monkeypatch.setattr(rp, "_semantic_edges_matrix", lambda *a, **k: None)
        corpus = _make_corpus(40)
        result = build(corpus)
        reference = _reference(corpus)
        for sid, ref in reference.items():
            assert result["by_section"][sid]["semantic"] == ref["semantic"], sid

    def test_size_guard_skips_semantic_when_numpy_absent(self, monkeypatch, caplog):
        # numpy absent AND corpus over the cap -> skip semantic (never stall),
        # keep structural, and log a warning.
        monkeypatch.setattr(rp, "_semantic_edges_matrix", lambda *a, **k: None)
        monkeypatch.setattr(rp, "_PUREPY_SEMANTIC_MAX", 10)
        corpus = _make_corpus(30)
        with caplog.at_level("WARNING"):
            result = build(corpus)
        assert all(v["semantic"] == [] for v in result["by_section"].values())
        # structural edges still built (root <-> children).
        assert any(v["structural"] for v in result["by_section"].values())
        assert any("skipping semantic edges" in r.message for r in caplog.records)
