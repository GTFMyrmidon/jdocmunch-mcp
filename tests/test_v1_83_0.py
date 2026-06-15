"""jdoc#63: vectorized query-time semantic scoring in DocIndex.

`_semantic_search` and the semantic half of `_hybrid_search` scored the query
against every embedded section with a per-section pure-Python `cosine_similarity`
(O(N*D) per query, ~242 ms on a 10k-section corpus, blocking the event loop).
v1.83.0 precomputes an L2-normalized embedding matrix once per DocIndex (cached)
and scores with a single matrix-vector product, pure-Python fallback when numpy
is absent.

These tests pin parity: the vectorized scores match the untouched
`cosine_similarity` reference to floating-point noise, and ordering/filtering is
unchanged. (`_hybrid_search` integration is covered by the existing suite.)
"""
from __future__ import annotations

import random

import pytest

import jdocmunch_mcp.storage.doc_store as doc_store
from jdocmunch_mcp.storage.doc_store import DocIndex
from jdocmunch_mcp.embeddings import cosine_similarity

DIM = 24


def _make_index(n: int, *, k: int = 12, seed: int = 7):
    """Clustered embeddings so neighbor structure is real and top-k is well
    separated; query vector sits near cluster 0."""
    rng = random.Random(seed)
    centers = [[rng.gauss(0, 1) for _ in range(DIM)] for _ in range(k)]
    sections = []
    for i in range(n):
        c = centers[i % k]
        sections.append({
            "id": f"s{i}", "title": f"S{i}", "level": 2, "doc_path": f"d{i % 5}.md",
            "embedding": [c[d] * 3.0 + rng.gauss(0, 0.3) for d in range(DIM)],
        })
    idx = DocIndex(
        repo="local/t", owner="local", name="t", indexed_at="2026-01-01T00:00:00Z",
        doc_paths=["d.md"], doc_types={".md": 1}, sections=sections,
    )
    query_vec = [centers[0][d] * 3.0 + rng.gauss(0, 0.3) for d in range(DIM)]
    return idx, query_vec


def _reference(idx, qv, doc_path, glob):
    """The exact pre-fix scoring loop, built from the untouched primitive."""
    out = []
    for sec in idx.sections:
        if idx._path_excluded(sec, doc_path, glob):
            continue
        emb = sec.get("embedding")
        if not emb:
            continue
        out.append((cosine_similarity(qv, emb), sec))
    return out


_KEY = lambda x: (-x[0], x[1].get("id", ""))


class TestVectorizedQueryScoring:
    def test_semantic_search_parity(self, monkeypatch):
        np = pytest.importorskip("numpy")
        idx, qv = _make_index(800)
        monkeypatch.setattr(doc_store, "embed_query", lambda q: qv)

        result = idx._semantic_search("anything", None, 10, None)  # vectorized
        ref_top = sorted(_reference(idx, qv, None, None), key=_KEY)[:10]
        ref_score = {s["id"]: sc for sc, s in _reference(idx, qv, None, None)}

        assert [r["id"] for r in result] == [s["id"] for _, s in ref_top]
        for r in result:
            assert abs(r["_score"] - ref_score[r["id"]]) < 1e-9, r["id"]
            assert "embedding" not in r and "content" not in r  # _strip preserved

    def test_helper_edge_cases(self):
        pytest.importorskip("numpy")
        idx, qv = _make_index(300)
        by_id = {s["id"]: s for s in idx.sections}
        by_id["s5"]["embedding"] = list(by_id["s4"]["embedding"])  # exact tie
        by_id["s6"]["embedding"] = list(by_id["s4"]["embedding"])  # exact tie
        del by_id["s7"]["embedding"]                               # no embedding
        by_id["s8"]["embedding"] = [0.0] * DIM                     # zero vector

        vec = idx._semantic_scored(qv, None, None)
        ref_score = {s["id"]: sc for sc, s in _reference(idx, qv, None, None)}
        vec_score = {s["id"]: sc for sc, s in vec}

        assert set(vec_score) == set(ref_score)          # same section set
        assert "s7" not in vec_score                      # no embedding -> absent
        assert abs(vec_score["s8"]) < 1e-12               # zero vector -> cosine 0
        for sid, sc in ref_score.items():
            assert abs(vec_score[sid] - sc) < 1e-9, sid   # scores match to fp noise
        # exact-duplicate vectors tie -> ascending id order, like the stable ref.
        order = [s["id"] for _, s in sorted(vec, key=_KEY)]
        tied = [i for i in order if i in ("s4", "s5", "s6")]
        assert tied == ["s4", "s5", "s6"]

    def test_pure_python_fallback_is_identical(self, monkeypatch):
        # Force the numpy-absent branch; helper must reproduce the loop exactly.
        idx, qv = _make_index(120)
        monkeypatch.setattr(DocIndex, "_ensure_semantic_matrix", lambda self: None)
        vec = idx._semantic_scored(qv, None, None)
        ref = _reference(idx, qv, None, None)
        assert [(round(sc, 12), s["id"]) for sc, s in vec] == \
               [(round(sc, 12), s["id"]) for sc, s in ref]

    def test_matrix_cached_and_not_serialized(self):
        pytest.importorskip("numpy")
        idx, _ = _make_index(50)
        first = idx._ensure_semantic_matrix()
        second = idx._ensure_semantic_matrix()
        assert first is second                                    # cache hit, no rebuild
        assert idx._sem_matrix_cache is first
        assert "_sem_matrix_cache" not in DocIndex.__dataclass_fields__  # never persisted

    def test_path_glob_parity(self):
        pytest.importorskip("numpy")
        idx, qv = _make_index(400)
        glob = "d1.md"
        vec = {s["id"]: sc for sc, s in idx._semantic_scored(qv, None, glob)}
        ref = {s["id"]: sc for sc, s in _reference(idx, qv, None, glob)}
        assert set(vec) == set(ref) and len(vec) > 0
        assert all(idx._path_excluded(s, None, glob) is False
                   for s in idx.sections if s["id"] in vec)
        for sid, sc in ref.items():
            assert abs(vec[sid] - sc) < 1e-9, sid
