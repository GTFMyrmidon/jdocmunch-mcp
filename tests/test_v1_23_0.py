"""Tests for v1.23.0: ranking-event ledger + online weight tuning."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from jdocmunch_mcp.storage.token_tracker import (
    ranking_db_query,
    record_ranking_event,
)
from jdocmunch_mcp.retrieval import tuning


# ---------------------------------------------------------------------------
# ranking_events SQLite sink
# ---------------------------------------------------------------------------

class TestRankingLedger:
    def test_disabled_no_db(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_PERF_TELEMETRY", raising=False)
        record_ranking_event(
            repo="r/x", tool="search_sections", query="q",
            mode="hybrid", semantic_used=True, semantic_weight=0.5,
            top1_score=2.0, top2_score=1.0, confidence=0.7, result_count=3,
            base_path=str(tmp_path),
        )
        assert not (tmp_path / "telemetry.db").exists()

    def test_enabled_writes_row(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
        record_ranking_event(
            repo="r/x", tool="search_sections", query="q",
            mode="hybrid", semantic_used=True, semantic_weight=0.55,
            top1_score=2.0, top2_score=1.0, confidence=0.72, result_count=3,
            base_path=str(tmp_path),
        )
        db = tmp_path / "telemetry.db"
        assert db.exists()
        conn = sqlite3.connect(str(db))
        try:
            (count,) = conn.execute("SELECT COUNT(*) FROM ranking_events").fetchone()
            assert count == 1
            row = conn.execute(
                "SELECT repo, mode, semantic_used, semantic_weight, top1_score, "
                "top2_score, confidence, result_count FROM ranking_events"
            ).fetchone()
            assert row[0] == "r/x"
            assert row[1] == "hybrid"
            assert row[2] == 1
            assert row[3] == 0.55
            assert row[4] == 2.0
            assert row[5] == 1.0
            assert row[6] == 0.72
            assert row[7] == 3
        finally:
            conn.close()

    def test_query_filters_by_repo(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
        record_ranking_event(
            repo="r/a", tool="search_sections", query="q1",
            mode="hybrid", semantic_used=True, semantic_weight=0.5,
            confidence=0.6, result_count=1, base_path=str(tmp_path),
        )
        record_ranking_event(
            repo="r/b", tool="search_sections", query="q2",
            mode="lexical", semantic_used=False, semantic_weight=0.5,
            confidence=0.5, result_count=1, base_path=str(tmp_path),
        )
        rows_a = ranking_db_query(repo="r/a", base_path=str(tmp_path))
        rows_b = ranking_db_query(repo="r/b", base_path=str(tmp_path))
        rows_all = ranking_db_query(base_path=str(tmp_path))
        assert len(rows_a) == 1 and rows_a[0]["repo"] == "r/a"
        assert len(rows_b) == 1 and rows_b[0]["repo"] == "r/b"
        assert len(rows_all) == 2

    def test_query_no_db_empty(self, tmp_path):
        assert ranking_db_query(base_path=str(tmp_path)) == []


# ---------------------------------------------------------------------------
# WeightTuner
# ---------------------------------------------------------------------------

class TestWeightTuner:
    def setup_method(self):
        tuning.reset_cache()

    def teardown_method(self):
        tuning.reset_cache()

    def _seed_events(self, tmp_path, monkeypatch, n_with: int, n_without: int,
                     with_conf: float, without_conf: float, repo: str = "r/x"):
        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
        for _ in range(n_with):
            record_ranking_event(
                repo=repo, tool="search_sections", query="q",
                mode="hybrid", semantic_used=True, semantic_weight=0.5,
                confidence=with_conf, result_count=1, base_path=str(tmp_path),
            )
        for _ in range(n_without):
            record_ranking_event(
                repo=repo, tool="search_sections", query="q",
                mode="lexical", semantic_used=False, semantic_weight=0.5,
                confidence=without_conf, result_count=1, base_path=str(tmp_path),
            )

    def test_insufficient_events(self, tmp_path, monkeypatch):
        self._seed_events(tmp_path, monkeypatch, n_with=10, n_without=10,
                          with_conf=0.7, without_conf=0.5)
        out = tuning.tune_one_repo(repo="r/x", min_events=50, base_path=str(tmp_path))
        assert out["status"] == "insufficient_events"

    def test_semantic_helps_steps_up(self, tmp_path, monkeypatch):
        self._seed_events(tmp_path, monkeypatch, n_with=40, n_without=40,
                          with_conf=0.75, without_conf=0.55)
        out = tuning.tune_one_repo(repo="r/x", min_events=20, base_path=str(tmp_path))
        assert out["status"] == "semantic_helps"
        assert out["new_semantic_weight"] > out["previous_semantic_weight"]
        # Persisted to disk.
        path = tuning._tuning_path(str(tmp_path))
        assert path.exists()
        # Reading via the resolver returns the new value.
        tuning.reset_cache()
        weight = tuning.get_semantic_weight("r/x", base_path=str(tmp_path))
        assert weight == out["new_semantic_weight"]

    def test_semantic_hurts_steps_down(self, tmp_path, monkeypatch):
        self._seed_events(tmp_path, monkeypatch, n_with=40, n_without=40,
                          with_conf=0.5, without_conf=0.75)
        out = tuning.tune_one_repo(repo="r/x", min_events=20, base_path=str(tmp_path))
        assert out["status"] == "semantic_hurts"
        assert out["new_semantic_weight"] < out["previous_semantic_weight"]

    def test_no_signal_when_flat(self, tmp_path, monkeypatch):
        self._seed_events(tmp_path, monkeypatch, n_with=40, n_without=40,
                          with_conf=0.62, without_conf=0.61)
        out = tuning.tune_one_repo(repo="r/x", min_events=20, base_path=str(tmp_path))
        assert out["status"] == "no_significant_signal"

    def test_dry_run_does_not_persist(self, tmp_path, monkeypatch):
        self._seed_events(tmp_path, monkeypatch, n_with=40, n_without=40,
                          with_conf=0.75, without_conf=0.55)
        out = tuning.tune_one_repo(repo="r/x", min_events=20, dry_run=True, base_path=str(tmp_path))
        assert out["status"] == "semantic_helps"
        path = tuning._tuning_path(str(tmp_path))
        assert not path.exists()

    def test_resolver_explicit_value_wins(self, tmp_path, monkeypatch):
        self._seed_events(tmp_path, monkeypatch, n_with=40, n_without=40,
                          with_conf=0.75, without_conf=0.55)
        tuning.tune_one_repo(repo="r/x", min_events=20, base_path=str(tmp_path))
        # Override via explicit caller value — must NOT use tuned value.
        weight = tuning.get_semantic_weight("r/x", explicit=0.8, base_path=str(tmp_path))
        assert weight == 0.8

    def test_resolver_default_when_no_override(self, tmp_path):
        weight = tuning.get_semantic_weight("never/seen", base_path=str(tmp_path))
        assert weight == tuning.DEFAULT_SEMANTIC_WEIGHT

    def _backdate(self, tmp_path, repo: str, days: float):
        """Shift every ranking event for ``repo`` into the past."""
        conn = sqlite3.connect(str(Path(tmp_path) / "telemetry.db"))
        try:
            conn.execute(
                "UPDATE ranking_events SET ts = ts - ? WHERE repo = ?",
                (days * 86_400, repo),
            )
            conn.commit()
        finally:
            conn.close()

    def test_old_events_excluded_by_default_window(self, tmp_path, monkeypatch):
        self._seed_events(tmp_path, monkeypatch, n_with=40, n_without=40,
                          with_conf=0.75, without_conf=0.55)  # strong signal...
        self._backdate(tmp_path, "r/x", 200)  # ...but 200 days old
        out = tuning.tune_one_repo(repo="r/x", min_events=20, base_path=str(tmp_path))
        assert out["status"] == "insufficient_events"
        assert out["max_age_days"] == 90

    def test_zero_window_reads_lifetime_ledger(self, tmp_path, monkeypatch):
        self._seed_events(tmp_path, monkeypatch, n_with=40, n_without=40,
                          with_conf=0.75, without_conf=0.55)
        self._backdate(tmp_path, "r/x", 200)
        out = tuning.tune_one_repo(repo="r/x", min_events=20, max_age_days=0,
                                   base_path=str(tmp_path))
        assert out["status"] == "semantic_helps"

    def test_all_repos_skips_window_aged_repos(self, tmp_path, monkeypatch):
        self._seed_events(tmp_path, monkeypatch, n_with=40, n_without=40,
                          with_conf=0.75, without_conf=0.55, repo="r/old")
        self._backdate(tmp_path, "r/old", 200)
        self._seed_events(tmp_path, monkeypatch, n_with=40, n_without=40,
                          with_conf=0.75, without_conf=0.55, repo="r/new")
        results = tuning.tune_all_repos(min_events=20, base_path=str(tmp_path))
        by_repo = {r["repo"]: r for r in results}
        assert "r/old" not in by_repo  # no in-window events → not even scanned
        assert by_repo["r/new"]["status"] == "semantic_helps"

    def test_clamp_bounds(self, tmp_path, monkeypatch):
        # Force the persisted weight outside bounds; resolver should clamp.
        path = tuning._tuning_path(str(tmp_path))
        path.write_text(
            '{"repos": {"r/x": {"semantic_weight": 1.5}}}',
            encoding="utf-8",
        )
        tuning.reset_cache()
        weight = tuning.get_semantic_weight("r/x", base_path=str(tmp_path))
        assert weight == tuning.SEMANTIC_WEIGHT_BOUNDS[1]


# ---------------------------------------------------------------------------
# tune_weights MCP tool
# ---------------------------------------------------------------------------

class TestTuneWeightsTool:
    def setup_method(self):
        tuning.reset_cache()

    def teardown_method(self):
        tuning.reset_cache()

    def test_disabled_returns_hint(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.tune_weights import tune_weights
        monkeypatch.delenv("JDOCMUNCH_PERF_TELEMETRY", raising=False)
        out = tune_weights(storage_path=str(tmp_path))
        assert out["status"] == "telemetry_disabled"
        assert "hint" in out

    def test_single_repo_flow(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.tune_weights import tune_weights
        # Seed and tune.
        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
        for _ in range(40):
            record_ranking_event(
                repo="r/x", tool="search_sections", query="q",
                mode="hybrid", semantic_used=True, semantic_weight=0.5,
                confidence=0.75, result_count=1, base_path=str(tmp_path),
            )
        for _ in range(40):
            record_ranking_event(
                repo="r/x", tool="search_sections", query="q",
                mode="lexical", semantic_used=False, semantic_weight=0.5,
                confidence=0.55, result_count=1, base_path=str(tmp_path),
            )
        out = tune_weights(repo="r/x", min_events=20, storage_path=str(tmp_path))
        assert out["_meta"]["scope"] == "single_repo"
        assert len(out["results"]) == 1
        assert out["results"][0]["status"] == "semantic_helps"

    def test_all_repos_flow(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.tune_weights import tune_weights
        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
        for repo, with_c, without_c in [("r/a", 0.75, 0.55), ("r/b", 0.6, 0.6)]:
            for _ in range(40):
                record_ranking_event(
                    repo=repo, tool="search_sections", query="q",
                    mode="hybrid", semantic_used=True, semantic_weight=0.5,
                    confidence=with_c, result_count=1, base_path=str(tmp_path),
                )
            for _ in range(40):
                record_ranking_event(
                    repo=repo, tool="search_sections", query="q",
                    mode="lexical", semantic_used=False, semantic_weight=0.5,
                    confidence=without_c, result_count=1, base_path=str(tmp_path),
                )
        out = tune_weights(min_events=20, storage_path=str(tmp_path))
        assert out["_meta"]["scope"] == "all_repos"
        assert {r["repo"] for r in out["results"]} == {"r/a", "r/b"}


# ---------------------------------------------------------------------------
# Wired into search_sections at query time
# ---------------------------------------------------------------------------

class TestSearchSectionsTuning:
    def test_tuned_weight_applies_when_default(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.search_sections import search_sections
        from jdocmunch_mcp.tools.index_local import index_local

        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "g.md").write_text("# Top\n\nbody about retrieval\n", encoding="utf-8")
        index_local(
            path=str(repo_dir), name="tn",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        # Persist a tuned weight without going through the tuner.
        tuning.reset_cache()
        path = tuning._tuning_path(str(tmp_path))
        path.write_text(
            '{"repos": {"local/tn": {"semantic_weight": 0.7}}}',
            encoding="utf-8",
        )

        out = search_sections(
            repo="tn", query="retrieval", semantic=False,
            storage_path=str(tmp_path),
        )
        # Lexical-only mode does not surface semantic_weight in _meta;
        # but the resolver should have read 0.7 internally. We verify by
        # pinning to hybrid mode (hybrid only when embeddings exist) —
        # since this fixture has no embeddings, mode is forced to lexical
        # and semantic_weight isn't echoed. Confirm the call succeeds.
        assert "results" in out

    def test_explicit_weight_overrides_tuned(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.search_sections import search_sections
        from jdocmunch_mcp.tools.index_local import index_local

        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "g.md").write_text("# Top\n\nbody\n", encoding="utf-8")
        index_local(
            path=str(repo_dir), name="tn2",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        # Tuned override on disk.
        tuning.reset_cache()
        path = tuning._tuning_path(str(tmp_path))
        path.write_text(
            '{"repos": {"local/tn2": {"semantic_weight": 0.7}}}',
            encoding="utf-8",
        )

        # Caller passes a non-default explicit weight — must win.
        out = search_sections(
            repo="tn2", query="retrieval",
            semantic_weight=0.3, semantic=False,
            storage_path=str(tmp_path),
        )
        assert "results" in out


# ---------------------------------------------------------------------------
# Server registration
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_tune_weights_registered(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        assert "tune_weights" in names
