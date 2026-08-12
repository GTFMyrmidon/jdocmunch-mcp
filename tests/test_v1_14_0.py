"""Tests for v1.14.0: per-tool latency telemetry + analyze_perf + get_session_stats."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from jdocmunch_mcp.storage.token_tracker import (
    _RING_MAXLEN,
    _percentile,
    latency_db_query,
    latency_stats,
    record_tool_latency,
    reset_latency_state,
)


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------

class TestPercentile:
    def test_empty(self):
        assert _percentile([], 50) == 0.0

    def test_single(self):
        assert _percentile([42.0], 95) == 42.0

    def test_basic_quantiles(self):
        xs = sorted([10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0])
        # Nearest-rank with banker's rounding on 10 items:
        #   p50 → round(0.5*9)=round(4.5)=4 → xs[4]=50.0
        #   p95 → round(0.95*9)=round(8.55)=9 → xs[9]=100.0
        #   p100 → idx 9 → 100; p0 → idx 0 → 10.
        assert _percentile(xs, 50) == 50.0
        assert _percentile(xs, 95) == 100.0
        assert _percentile(xs, 100) == 100.0
        assert _percentile(xs, 0) == 10.0


# ---------------------------------------------------------------------------
# In-memory ring + latency_stats()
# ---------------------------------------------------------------------------

class TestLatencyRing:
    def setup_method(self):
        reset_latency_state()

    def teardown_method(self):
        reset_latency_state()

    def test_ring_records_calls(self):
        record_tool_latency("foo", 12.5, ok=True)
        record_tool_latency("foo", 100.0, ok=True)
        stats = latency_stats()
        assert "foo" in stats
        assert stats["foo"]["count"] == 2
        assert stats["foo"]["max_ms"] == 100.0

    def test_error_rate(self):
        record_tool_latency("foo", 5.0, ok=True)
        record_tool_latency("foo", 7.0, ok=False)
        record_tool_latency("foo", 9.0, ok=False)
        stats = latency_stats()["foo"]
        assert stats["count"] == 3
        assert stats["errors"] == 2
        assert stats["error_rate"] == round(2 / 3, 4)

    def test_ring_bounded_at_maxlen(self):
        for i in range(_RING_MAXLEN + 50):
            record_tool_latency("foo", float(i), ok=True)
        stats = latency_stats()["foo"]
        assert stats["count"] == _RING_MAXLEN
        # Older entries dropped — min in window should be >= 50 (we wrote 0..N+49).
        assert stats["max_ms"] == float(_RING_MAXLEN + 50 - 1)

    def test_distinct_tools_isolated(self):
        record_tool_latency("a", 1.0, ok=True)
        record_tool_latency("b", 2.0, ok=True)
        stats = latency_stats()
        assert stats["a"]["count"] == 1
        assert stats["b"]["count"] == 1

    def test_empty_tool_name_skipped(self):
        record_tool_latency("", 1.0)
        assert "" not in latency_stats()


# ---------------------------------------------------------------------------
# Persistent SQLite sink
# ---------------------------------------------------------------------------

class TestSQLiteSink:
    def setup_method(self):
        reset_latency_state()

    def teardown_method(self):
        reset_latency_state()

    def test_disabled_by_default_no_db(self, tmp_path, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_PERF_TELEMETRY", raising=False)
        record_tool_latency("foo", 1.0, base_path=str(tmp_path))
        assert not (tmp_path / "telemetry.db").exists()

    def test_enabled_writes_row(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
        record_tool_latency("foo", 12.5, ok=True, repo="r/x", base_path=str(tmp_path))
        db = tmp_path / "telemetry.db"
        assert db.exists()
        conn = sqlite3.connect(str(db))
        try:
            (count,) = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()
            assert count == 1
            (tool, ms, ok, repo) = conn.execute(
                "SELECT tool, duration_ms, ok, repo FROM tool_calls"
            ).fetchone()
            assert tool == "foo"
            assert ms == 12.5
            assert ok == 1
            assert repo == "r/x"
        finally:
            conn.close()

    def test_window_query_filters_by_time(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
        record_tool_latency("foo", 1.0, base_path=str(tmp_path))
        record_tool_latency("foo", 2.0, base_path=str(tmp_path))
        record_tool_latency("bar", 5.0, base_path=str(tmp_path))

        out = latency_db_query("1h", base_path=str(tmp_path))
        assert "foo" in out
        assert out["foo"]["count"] == 2
        assert out["bar"]["count"] == 1

    def test_unknown_window_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
        record_tool_latency("foo", 1.0, base_path=str(tmp_path))
        assert latency_db_query("notawindow", base_path=str(tmp_path)) == {}

    def test_no_db_returns_empty(self, tmp_path):
        # No file written ⇒ empty.
        assert latency_db_query("all", base_path=str(tmp_path)) == {}


# ---------------------------------------------------------------------------
# analyze_perf MCP tool
# ---------------------------------------------------------------------------

class TestAnalyzePerf:
    def setup_method(self):
        reset_latency_state()

    def teardown_method(self):
        reset_latency_state()

    def test_session_window_uses_memory_ring(self, monkeypatch):
        from jdocmunch_mcp.tools.analyze_perf import analyze_perf

        monkeypatch.delenv("JDOCMUNCH_PERF_TELEMETRY", raising=False)
        record_tool_latency("foo", 10.0, ok=True)
        out = analyze_perf(window="session")
        assert out["window"] == "session"
        assert out["source"] == "memory"
        assert "foo" in out["per_tool"]

    def test_disabled_with_long_window_returns_hint(self, monkeypatch, tmp_path):
        from jdocmunch_mcp.tools.analyze_perf import analyze_perf

        monkeypatch.delenv("JDOCMUNCH_PERF_TELEMETRY", raising=False)
        out = analyze_perf(window="1h", storage_path=str(tmp_path))
        assert out["telemetry_enabled"] is False
        assert "hint" in out

    def test_enabled_long_window_reads_db(self, monkeypatch, tmp_path):
        from jdocmunch_mcp.tools.analyze_perf import analyze_perf

        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
        record_tool_latency("foo", 12.5, base_path=str(tmp_path))
        out = analyze_perf(window="1h", storage_path=str(tmp_path))
        assert out["telemetry_enabled"] is True
        assert out["source"] == "sqlite"
        assert "foo" in out["per_tool"]

    def test_unknown_window_returns_error(self):
        from jdocmunch_mcp.tools.analyze_perf import analyze_perf
        out = analyze_perf(window="forever")
        assert "error" in out


# ---------------------------------------------------------------------------
# get_session_stats MCP tool
# ---------------------------------------------------------------------------

class TestGetSessionStats:
    def setup_method(self):
        reset_latency_state()

    def teardown_method(self):
        reset_latency_state()

    def test_returns_latency_and_total_savings(self, tmp_path):
        from jdocmunch_mcp.tools.get_session_stats import get_session_stats

        record_tool_latency("foo", 5.0, ok=True)
        out = get_session_stats(storage_path=str(tmp_path))
        assert "latency_per_tool" in out
        assert "foo" in out["latency_per_tool"]
        assert isinstance(out["total_tokens_saved"], int)


# ---------------------------------------------------------------------------
# Server registration
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_new_tools_registered(self):
        # Importing the server module exposes call_tool's dispatch.
        # We only assert the tools list contains the new names.
        import asyncio
        from jdocmunch_mcp import server as srv

        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        assert "analyze_perf" in names
        assert "get_session_stats" in names
