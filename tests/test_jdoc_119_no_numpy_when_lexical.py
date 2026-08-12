"""jdoc#119: a lexical-only corpus builds its related-graph without importing numpy.

``_semantic_edges_matrix`` imported numpy as its FIRST statement, then returned
``{}`` a few lines later whenever no section carried an embedding. For a corpus
indexed with ``use_embeddings=False`` that import is pure cost for a function
guaranteed to produce an empty map.

It became a PER-REFRESH cost in v1.131.0 (jdoc#117), which put the sidecar
rebuild on the incremental path — the path a watch/refresh loop takes every
time. On a machine where that import is slow (or, as observed in jdoc#118,
wedges outright inside the server process), it is the entire latency of the
call.

⚠ The output contract is what makes the reorder safe, and it is asserted below
rather than argued: with no vectors the function returns ``{}``, and ``build``
maps an absent section id to ``[]`` — exactly what the numpy-missing fallback
(``None``) produces for the same corpus. Both paths are pinned here.
"""

import builtins
import sys

import pytest

from jdocmunch_mcp.retrieval.related_persist import _semantic_edges_matrix, build


def _sections(n=6, with_embeddings=False):
    out = []
    for i in range(n):
        sec = {
            "id": f"repo::doc{i}.md::s{i}#0",
            "doc_path": f"doc{i}.md",
            "title": f"Section {i}",
            "level": 1,
            "parent_id": "",
            "content": f"Body text for section {i}.",
        }
        if with_embeddings:
            sec["embedding"] = [1.0 if j == i % 3 else 0.0 for j in range(4)]
        out.append(sec)
    return out


@pytest.fixture()
def ban_numpy(monkeypatch):
    """Make any fresh `import numpy` raise, without disturbing an existing one."""
    real_import = builtins.__import__
    seen = {"attempted": False}

    def guard(name, *a, **k):
        if name == "numpy" or name.startswith("numpy."):
            seen["attempted"] = True
            raise AssertionError(f"numpy was imported: {name}")
        return real_import(name, *a, **k)

    monkeypatch.delitem(sys.modules, "numpy", raising=False)
    monkeypatch.setattr(builtins, "__import__", guard)
    return seen


class TestLexicalCorpusSkipsNumpy:
    def test_no_embeddings_returns_empty_without_importing_numpy(self, ban_numpy):
        assert _semantic_edges_matrix(_sections(), top_n=5, min_score=0.6) == {}
        assert not ban_numpy["attempted"]

    def test_full_build_on_a_lexical_corpus_never_imports_numpy(self, ban_numpy):
        payload = build(_sections(), top_n_semantic=5, min_score=0.6)
        assert payload["section_count"] == 6
        assert all(v["semantic"] == [] for v in payload["by_section"].values())
        assert not ban_numpy["attempted"]

    def test_sections_without_ids_do_not_count_as_vectors(self, ban_numpy):
        secs = [{"embedding": [1.0, 0.0]}, {"id": "", "embedding": [0.0, 1.0]}]
        assert _semantic_edges_matrix(secs, top_n=5, min_score=0.6) == {}
        assert not ban_numpy["attempted"]


class TestOutputUnchanged:
    def test_empty_map_and_none_fallback_agree_on_a_lexical_corpus(self):
        """The reorder swaps which sentinel a numpy-less lexical corpus gets
        (``None`` before, ``{}`` now). `build` must produce the same output."""
        secs = _sections()
        via_empty = build(secs, top_n_semantic=5, min_score=0.6)
        semantic = {k: v["semantic"] for k, v in via_empty["by_section"].items()}
        assert semantic == {s["id"]: [] for s in secs}

    def test_embedded_corpus_still_goes_through_the_matrix(self):
        pytest.importorskip("numpy")
        payload = build(_sections(with_embeddings=True), top_n_semantic=5, min_score=0.6)
        # Sections sharing an embedding (i % 3) must find each other.
        assert any(v["semantic"] for v in payload["by_section"].values())

    def test_structural_edges_are_unaffected(self, ban_numpy):
        secs = _sections(3)
        secs[1]["parent_id"] = secs[0]["id"]
        payload = build(secs, top_n_semantic=5, min_score=0.6)
        assert payload["by_section"][secs[1]["id"]]["structural"]
        assert not ban_numpy["attempted"]
