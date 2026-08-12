"""Tests for v1.36.1 hotfix: deterministic tie-break in BM25/semantic ranking.

CI was failing on Linux because score-only sorts left tied results in
insertion order, which depends on `os.walk` traversal — different across
filesystems. v1.36.1 changes every ranking sort to
``(-score, section_id)`` so order is identical on every platform.
"""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


class TestRankingDeterminism:
    def _index_tied(self, tmp_path):
        # Two sections with identical body text → identical BM25 score.
        # The deterministic tie-break must pick the lexicographically
        # smaller section_id every time.
        body = "configuration loader retry default endpoint timeout"
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "z_late.md").write_text(f"# Page Z\n\n{body}\n", encoding="utf-8")
        (repo / "a_early.md").write_text(f"# Page A\n\n{body}\n", encoding="utf-8")
        index_local(
            path=str(repo), name="tied",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

    def test_tied_results_break_by_section_id(self, tmp_path):
        self._index_tied(tmp_path)
        # Run the same query 5 times; the top result must be identical.
        tops = []
        for _ in range(5):
            out = search_sections(
                repo="tied", query="configuration loader",
                semantic=False, storage_path=str(tmp_path),
            )
            tops.append(out["results"][0]["id"])
        assert len(set(tops)) == 1, f"non-deterministic: {tops}"

    def test_tied_results_lex_smaller_id_wins(self, tmp_path):
        self._index_tied(tmp_path)
        out = search_sections(
            repo="tied", query="configuration loader",
            semantic=False, storage_path=str(tmp_path),
        )
        # Top two must be in section_id ascending order when scores tie.
        ids = [r["id"] for r in out["results"][:2]]
        assert ids == sorted(ids), ids
