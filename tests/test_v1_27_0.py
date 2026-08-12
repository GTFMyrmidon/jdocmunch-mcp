"""Tests for v1.27.0: verify_index + section-boundary golden corpus."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jdocmunch_mcp.parser import parse_file, preprocess_content
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.verify_index import verify_index

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "benchmarks" / "replay" / "corpus"
GOLDEN = ROOT / "tests" / "fixtures" / "golden_sections"


# ---------------------------------------------------------------------------
# verify_index
# ---------------------------------------------------------------------------

class TestVerifyIndex:
    def _index(self, tmp_path):
        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "g.md").write_text(
            "# Top\n\n## Auth\n\nbody alpha\n\n## Logs\n\nbody beta\n",
            encoding="utf-8",
        )
        index_local(
            path=str(repo_dir), name="vi",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        return repo_dir

    def test_clean_index_no_drift(self, tmp_path):
        self._index(tmp_path)
        out = verify_index(repo="vi", storage_path=str(tmp_path))
        assert "error" not in out
        assert out["drift_count"] == 0
        assert out["missing_count"] == 0
        assert out["error_count"] == 0
        assert out["clean_count"] >= 1
        assert out["section_count"] >= 1

    def test_byte_range_mutation_detected(self, tmp_path):
        self._index(tmp_path)
        # Mutate the cached file so byte ranges no longer match content_hash.
        store = DocStore(base_path=str(tmp_path))
        cached = store._safe_content_path(store._content_dir("local", "vi"), "g.md")
        original = cached.read_bytes()
        cached.write_bytes(original.replace(b"alpha", b"BRAVO"))
        out = verify_index(repo="vi", storage_path=str(tmp_path))
        assert out["drift_count"] >= 1
        # Drift section must reference our doc.
        assert any(d["doc_path"] == "g.md" for d in out["drift_sections"])

    def test_missing_file_reported(self, tmp_path):
        self._index(tmp_path)
        store = DocStore(base_path=str(tmp_path))
        cached = store._safe_content_path(store._content_dir("local", "vi"), "g.md")
        cached.unlink()
        out = verify_index(repo="vi", storage_path=str(tmp_path))
        assert out["missing_count"] >= 1
        assert any(m["reason"] == "file_missing" for m in out["missing_sections"])

    def test_unknown_repo_error(self, tmp_path):
        out = verify_index(repo="nope/missing", storage_path=str(tmp_path))
        assert "error" in out

    def test_sample_caps_section_count(self, tmp_path):
        # 10 docs → ~30 sections; sample=5 limits the walk.
        repo_dir = tmp_path / "many"
        repo_dir.mkdir()
        for i in range(10):
            (repo_dir / f"d{i}.md").write_text(
                f"# Page {i}\n\nbody {i}\n", encoding="utf-8",
            )
        index_local(
            path=str(repo_dir), name="many",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        out = verify_index(repo="many", sample=5, storage_path=str(tmp_path))
        assert out["section_count"] == 5


# ---------------------------------------------------------------------------
# Section-boundary golden corpus
# ---------------------------------------------------------------------------

GOLDEN_FIXTURES = sorted(GOLDEN.glob("*.json")) if GOLDEN.exists() else []


@pytest.mark.skipif(not GOLDEN_FIXTURES, reason="golden corpus not generated")
class TestGoldenSectionSnapshots:
    @pytest.mark.parametrize("golden_path", GOLDEN_FIXTURES, ids=[p.name for p in GOLDEN_FIXTURES])
    def test_parser_matches_snapshot(self, golden_path):
        # Snapshot filename encodes "<subdir>__<filename>.json".
        stem = golden_path.stem  # e.g. "markdown__install.md"
        if "__" not in stem:
            pytest.skip(f"unexpected golden filename: {golden_path.name}")
        subdir, fname = stem.split("__", 1)
        corpus_file = CORPUS / subdir / fname
        assert corpus_file.exists(), f"corpus source missing: {corpus_file}"

        # Parse fresh.
        rel = corpus_file.relative_to(CORPUS).as_posix()
        content = corpus_file.read_text(encoding="utf-8")
        preprocessed = preprocess_content(content, rel)
        sections = parse_file(preprocessed, rel, "golden/test")
        actual = [
            {
                "id": s.id,
                "title": s.title,
                "level": s.level,
                "byte_start": s.byte_start,
                "byte_end": s.byte_end,
                "content_hash": s.content_hash,
                "parent_id": s.parent_id,
            }
            for s in sections
        ]

        expected = json.loads(golden_path.read_text(encoding="utf-8"))
        assert actual == expected, (
            f"section snapshot drift in {rel}; "
            f"regenerate via the v1.27 helper if intentional"
        )


# ---------------------------------------------------------------------------
# Server registration
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_verify_index_registered(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        assert "verify_index" in names
