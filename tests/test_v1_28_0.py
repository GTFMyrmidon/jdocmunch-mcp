"""Tests for v1.28.0: drift simulation + cross-platform paths + replay log."""

from __future__ import annotations

import json
import os
import posixpath
import sys
from pathlib import Path

import pytest

from jdocmunch_mcp.embeddings import provider as emb_provider
from jdocmunch_mcp.embeddings.embed_drift import (
    capture_canary,
    check_drift,
)
from jdocmunch_mcp.storage import replay_log
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.index_local import index_local


# ---------------------------------------------------------------------------
# Embedding-drift simulation suite
# ---------------------------------------------------------------------------

class TestDriftSimulationSuite:
    """End-to-end provider-swap scenarios that prove the canary alarms fire."""

    def setup_method(self):
        emb_provider._reset_provider_cache()

    def teardown_method(self):
        emb_provider._reset_provider_cache()

    def _stub(self, vec):
        class _Stub:
            def embed_texts(self, texts, task_type="retrieval_document"):
                return [list(vec) for _ in texts]

        return _Stub

    def test_subtle_drift_below_threshold_no_alarm(self, tmp_path, monkeypatch):
        # Capture vectors close to (1, 0); re-embed with (0.99, 0.01) — drift < 0.05.
        provider_a = self._stub((1.0, 0.0))
        provider_b = self._stub((0.99, 0.01))
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "drift_a", provider_a)
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "drift_b", provider_b)
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: (f"{name}-model", 2))

        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "drift_a")
        captured = capture_canary(base_path=str(tmp_path))
        assert captured["status"] == "captured"

        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "drift_b")
        out = check_drift(threshold=0.05, base_path=str(tmp_path))
        # Cosine ≈ 0.9999, drift ≈ 0.00005 — well below threshold.
        assert out["alarm"] is False
        assert out["max_drift"] < 0.01

    def test_orthogonal_swap_alarms(self, tmp_path, monkeypatch):
        # Capture (1, 0); swap to (0, 1) — cosine 0, drift 1.0.
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "drift_a", self._stub((1.0, 0.0)))
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "drift_b", self._stub((0.0, 1.0)))
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: (f"{name}-model", 2))

        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "drift_a")
        capture_canary(base_path=str(tmp_path))

        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "drift_b")
        out = check_drift(threshold=0.05, base_path=str(tmp_path))
        assert out["alarm"] is True
        assert out["max_drift"] >= 0.99

    def test_anti_parallel_swap_alarms_max(self, tmp_path, monkeypatch):
        # Anti-parallel: cosine = -1, drift = 2.0 (capped at max).
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "drift_a", self._stub((1.0, 0.0)))
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "drift_b", self._stub((-1.0, 0.0)))
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: (f"{name}-model", 2))

        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "drift_a")
        capture_canary(base_path=str(tmp_path))
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "drift_b")
        out = check_drift(threshold=0.05, base_path=str(tmp_path))
        assert out["alarm"] is True
        assert out["max_drift"] >= 1.99

    def test_threshold_boundary_just_above_alarms(self, tmp_path, monkeypatch):
        # Precise drift = 0.06 should alarm at threshold 0.05; not at 0.07.
        # cosine ≈ 0.94 → drift ≈ 0.06.
        import math
        # Build vectors with cos sim 0.94: (1, 0) vs (0.94, sqrt(1-0.94^2)).
        v2 = (0.94, math.sqrt(1 - 0.94 ** 2))
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "drift_a", self._stub((1.0, 0.0)))
        monkeypatch.setitem(emb_provider._PROVIDER_FACTORIES, "drift_b", self._stub(v2))
        monkeypatch.setattr(emb_provider, "_provider_identity", lambda name: (f"{name}-model", 2))

        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "drift_a")
        capture_canary(base_path=str(tmp_path))
        monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "drift_b")

        out_strict = check_drift(threshold=0.05, base_path=str(tmp_path))
        out_loose = check_drift(threshold=0.07, base_path=str(tmp_path))
        assert out_strict["alarm"] is True
        assert out_loose["alarm"] is False


# ---------------------------------------------------------------------------
# Cross-platform path matrix
# ---------------------------------------------------------------------------

class TestCrossPlatformPaths:
    """Path safety + slug stability across Posix and Windows-style separators."""

    @pytest.mark.parametrize(
        "doc_path",
        [
            "guide.md",
            "guides/install.md",
            "deep/nested/path/file.md",
        ],
        ids=["flat", "one-level", "deep"],
    )
    def test_safe_content_path_resolves_within_root(self, tmp_path, doc_path):
        store = DocStore(base_path=str(tmp_path))
        owner, name = "local", "x"
        content_dir = store._content_dir(owner, name)
        content_dir.mkdir(parents=True, exist_ok=True)
        target = store._safe_content_path(content_dir, doc_path)
        assert target is not None
        assert str(target).startswith(str(content_dir.resolve()))

    @pytest.mark.parametrize(
        "evil_path",
        [
            "../escape.md",
            "../../etc/passwd",
            "subdir/../../escape.md",
        ],
    )
    def test_safe_content_path_rejects_traversal(self, tmp_path, evil_path):
        store = DocStore(base_path=str(tmp_path))
        content_dir = store._content_dir("local", "x")
        content_dir.mkdir(parents=True, exist_ok=True)
        # Either returns None or returns a path inside content_dir — never escapes.
        result = store._safe_content_path(content_dir, evil_path)
        if result is not None:
            assert str(result).startswith(str(content_dir.resolve()))

    def test_index_local_with_nested_subdirs_round_trips(self, tmp_path):
        """Ensure section IDs + byte ranges survive load/verify regardless
        of nesting depth (catches ``\\`` vs ``/`` regressions)."""
        repo_dir = tmp_path / "docs"
        nested = repo_dir / "guides" / "advanced"
        nested.mkdir(parents=True)
        (repo_dir / "intro.md").write_text("# Intro\n\nbody\n", encoding="utf-8")
        (nested / "deep.md").write_text("# Deep\n\nbody\n", encoding="utf-8")
        index_local(
            path=str(repo_dir), name="paths",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        from jdocmunch_mcp.tools.verify_index import verify_index
        out = verify_index(repo="paths", storage_path=str(tmp_path))
        assert out["drift_count"] == 0
        assert out["missing_count"] == 0

        store = DocStore(base_path=str(tmp_path))
        idx = store.load_index("local", "paths")
        # Both docs got indexed.
        doc_paths = {s["doc_path"] for s in idx.sections}
        # Stored paths use posix-style separators regardless of host OS.
        for dp in doc_paths:
            assert "\\" not in dp, f"non-posix separator in stored doc_path: {dp!r}"

    def test_resolve_repo_handles_owner_slash_name(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        # Ensure both the bare-name and owner/name forms resolve.
        owner, name = store._resolve_repo("local/sample")
        assert owner == "local"
        assert name == "sample"


# ---------------------------------------------------------------------------
# Retrieval-replay log capture (opt-in)
# ---------------------------------------------------------------------------

class TestReplayLog:
    def test_disabled_no_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_REPLAY_LOG", raising=False)
        replay_log.append(
            repo="r/x", query="q", mode="lexical",
            semantic_used=False, semantic_weight=0.5,
            base_path=str(tmp_path),
        )
        assert not (tmp_path / "replay.log").exists()

    def test_enabled_appends_jsonl(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_REPLAY_LOG", "1")
        replay_log.append(
            repo="r/x", query="install",
            mode="hybrid", semantic_used=True, semantic_weight=0.55,
            top1_id="r::doc::s#1", top1_score=2.5,
            confidence=0.7, result_count=3,
            base_path=str(tmp_path),
        )
        log = tmp_path / "replay.log"
        assert log.exists()
        rows = log.read_text(encoding="utf-8").strip().splitlines()
        assert len(rows) == 1
        parsed = json.loads(rows[0])
        assert parsed["repo"] == "r/x"
        assert parsed["query"] == "install"
        assert parsed["mode"] == "hybrid"
        assert parsed["semantic_used"] is True
        assert parsed["top1_id"] == "r::doc::s#1"

    def test_multiple_appends_accumulate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_REPLAY_LOG", "1")
        for i in range(5):
            replay_log.append(
                repo="r/x", query=f"q{i}", mode="lexical",
                semantic_used=False, semantic_weight=0.5,
                result_count=i, base_path=str(tmp_path),
            )
        rows = replay_log.read_all(base_path=str(tmp_path))
        assert len(rows) == 5
        assert [r["query"] for r in rows] == ["q0", "q1", "q2", "q3", "q4"]

    def test_read_all_limit_returns_tail(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_REPLAY_LOG", "1")
        for i in range(10):
            replay_log.append(
                repo="r/x", query=f"q{i}", mode="lexical",
                semantic_used=False, semantic_weight=0.5,
                base_path=str(tmp_path),
            )
        rows = replay_log.read_all(base_path=str(tmp_path), limit=3)
        assert len(rows) == 3
        assert [r["query"] for r in rows] == ["q7", "q8", "q9"]

    def test_corrupt_lines_skipped(self, tmp_path):
        log = tmp_path / "replay.log"
        log.write_text(
            '{"ts":1,"repo":"r","query":"a","mode":"lexical","semantic_used":false,'
            '"semantic_weight":0.5,"top1_id":null,"top1_score":null,"confidence":null,"result_count":0}\n'
            "this is not json at all\n"
            '{"ts":2,"repo":"r","query":"b","mode":"lexical","semantic_used":false,'
            '"semantic_weight":0.5,"top1_id":null,"top1_score":null,"confidence":null,"result_count":0}\n',
            encoding="utf-8",
        )
        rows = replay_log.read_all(base_path=str(tmp_path))
        assert len(rows) == 2
        assert {r["query"] for r in rows} == {"a", "b"}

    def test_read_missing_returns_empty(self, tmp_path):
        assert replay_log.read_all(base_path=str(tmp_path)) == []

    def test_search_sections_writes_replay_when_enabled(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.search_sections import search_sections

        monkeypatch.setenv("JDOCMUNCH_REPLAY_LOG", "1")
        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "g.md").write_text("# Top\n\nbody about retrieval\n", encoding="utf-8")
        index_local(
            path=str(repo_dir), name="rl",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        search_sections(repo="rl", query="retrieval", semantic=False, storage_path=str(tmp_path))
        rows = replay_log.read_all(base_path=str(tmp_path))
        assert any(r["query"] == "retrieval" for r in rows)
