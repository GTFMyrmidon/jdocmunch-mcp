"""Tests for v1.21.0: real-world replay corpora."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.replay.run_replay import run_fixture  # noqa: E402

REALWORLD_FIXTURES = (
    "markdown_realworld",
    "rst_realworld",
    "openapi_realworld",
    "notebook_realworld",
)


class TestRealworldFixtureShapes:
    """Each fixture file must have the required keys before run_replay."""

    @pytest.mark.parametrize("name", REALWORLD_FIXTURES)
    def test_fixture_loads(self, name):
        path = ROOT / "benchmarks" / "replay" / "fixtures" / f"{name}.json"
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["name"] == name
        assert "repo_path" in data
        assert "repo_id" in data
        assert isinstance(data["queries"], list)
        assert len(data["queries"]) >= 3
        for q in data["queries"]:
            assert q["query"]
            assert q["expected_top_k"]
            assert q["k"] >= 1


class TestRealworldFixtureBaseline:
    """Each fixture must hit >= 0.98 against itself (locked at v1.21.0)."""

    @pytest.mark.parametrize("name", REALWORLD_FIXTURES)
    def test_meets_lock(self, name):
        report = run_fixture(name, baseline=None, gate=0.02, write_results=False)
        agg = report["aggregates"]
        assert agg["ndcg"] >= 0.98, (name, agg)
        assert agg["mrr"] >= 0.98, (name, agg)
        assert agg["recall"] >= 0.98, (name, agg)


class TestRealworldGateAgainstStoredBaseline:
    """Each fixture must pass the v1.21.0 baseline gate."""

    @pytest.mark.parametrize("name", REALWORLD_FIXTURES)
    def test_gate_passes(self, name):
        report = run_fixture(name, baseline="1.21.0", gate=0.02, write_results=False)
        gate = report.get("gate") or {}
        assert gate.get("status") == "pass", (name, gate)


class TestCorpusFilesExist:
    """Sanity: each corpus directory has at least one file the fixture indexes."""

    @pytest.mark.parametrize(
        "subdir,extension",
        [
            ("markdown", ".md"),
            ("rst", ".rst"),
            ("openapi", ".yaml"),
            ("notebook", ".ipynb"),
        ],
    )
    def test_corpus_present(self, subdir, extension):
        d = ROOT / "benchmarks" / "replay" / "corpus" / subdir
        assert d.is_dir(), f"corpus dir missing: {d}"
        files = [p for p in d.iterdir() if p.suffix == extension]
        assert files, f"no {extension} files in {d}"
