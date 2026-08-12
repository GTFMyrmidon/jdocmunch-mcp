"""Tests for doc_health_radar + diff_doc_health_radar (v1.62.0)."""

from __future__ import annotations

import pytest

from jdocmunch_mcp.tools.doc_health_radar import doc_health_radar
from jdocmunch_mcp.tools.health_radar import (
    compute_radar,
    diff_doc_health_radar,
    diff_radar,
)
from jdocmunch_mcp.tools.index_local import index_local


# ── compute_radar pure-function tests ─────────────────────────────────────────

def test_compute_radar_healthy_repo_scores_well():
    r = compute_radar(
        fresh=20, edited=0, stale=0,
        broken_links=0,
        orphan_count=0,
        embedded_sections=20, section_count=20,
        role_distribution={"tutorial": 10, "reference": 10},
        has_canary=True, drift_alarm=False,
    )
    assert r["composite"] >= 90.0
    assert r["grade"] == "A"
    assert r["omitted_axes"] == []


def test_compute_radar_unhealthy_repo_scores_poorly():
    r = compute_radar(
        fresh=2, edited=2, stale=16,
        broken_links=10,
        orphan_count=10,
        embedded_sections=0, section_count=20,
        role_distribution={"unknown": 20},
        has_canary=True, drift_alarm=True,
    )
    assert r["composite"] < 30.0
    assert r["grade"] == "F"


def test_compute_radar_omits_drift_when_no_canary():
    r = compute_radar(
        fresh=10, edited=0, stale=0,
        broken_links=0, orphan_count=0,
        embedded_sections=10, section_count=10,
        role_distribution={"tutorial": 10},
        has_canary=False, drift_alarm=None,
    )
    assert "drift_health" in r["omitted_axes"]
    assert "drift_health" not in r["axes"]


def test_compute_radar_omits_freshness_on_empty():
    r = compute_radar(
        fresh=0, edited=0, stale=0,
        broken_links=0, orphan_count=0,
        embedded_sections=0, section_count=0,
        role_distribution={},
    )
    assert "freshness" in r["omitted_axes"]


def test_link_integrity_penalty():
    r = compute_radar(
        fresh=10, edited=0, stale=0,
        broken_links=2, orphan_count=0,  # 20% broken
        embedded_sections=10, section_count=10,
        role_distribution={"tutorial": 10},
    )
    assert r["axes"]["link_integrity"]["score"] == 0.0


def test_orphan_health_penalty():
    r = compute_radar(
        fresh=10, edited=0, stale=0,
        broken_links=0, orphan_count=5,  # 50% orphans
        embedded_sections=10, section_count=10,
        role_distribution={"tutorial": 10},
    )
    assert r["axes"]["orphan_health"]["score"] == 0.0


# ── diff_radar tests ──────────────────────────────────────────────────────────

def test_diff_flags_regressions():
    baseline = compute_radar(
        fresh=20, edited=0, stale=0,
        broken_links=0, orphan_count=0,
        embedded_sections=20, section_count=20,
        role_distribution={"tutorial": 20},
    )
    current = compute_radar(
        fresh=10, edited=0, stale=10,
        broken_links=0, orphan_count=0,
        embedded_sections=20, section_count=20,
        role_distribution={"tutorial": 20},
    )
    d = diff_radar(baseline, current)
    assert "freshness" in d["regressions"]
    assert d["composite_delta"] < 0


def test_diff_no_change():
    base = compute_radar(
        fresh=10, edited=0, stale=0,
        broken_links=0, orphan_count=0,
        embedded_sections=10, section_count=10,
        role_distribution={"tutorial": 10},
    )
    d = diff_radar(base, base)
    assert d["composite_delta"] == 0.0
    assert d["verdict"] == "no meaningful change"


def test_diff_doc_health_radar_rejects_bad_input():
    assert "error" in diff_doc_health_radar("not a dict", {"axes": {}})
    assert "error" in diff_doc_health_radar({"composite": 80}, {"composite": 90})


# ── Integration ───────────────────────────────────────────────────────────────

@pytest.fixture
def simple_wiki(tmp_path):
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "intro.md").write_text(
        "# Intro\n\nWelcome.\n\nSee [reference](reference.md).\n"
    )
    (docs / "reference.md").write_text(
        "# Reference\n\n## API\n\nDetails here.\n"
    )
    (docs / "guide.md").write_text(
        "# Guide\n\n## Setup\n\nSteps.\n"
    )
    storage = str(tmp_path / "store")
    res = index_local(path=str(docs), use_ai_summaries=False, storage_path=storage)
    assert res["success"]
    return res["repo"], storage


def test_unknown_repo(tmp_path):
    r = doc_health_radar(repo="not-a-real-repo", storage_path=str(tmp_path / "store"))
    assert "error" in r


def test_radar_shape(simple_wiki):
    repo, storage = simple_wiki
    r = doc_health_radar(repo=repo, storage_path=storage)
    assert "result" in r
    radar = r["result"]["radar"]
    assert "axes" in radar
    assert "composite" in radar
    assert "grade" in radar
    assert "omitted_axes" in radar
    # Core axes always present (section_count > 0)
    assert "link_integrity" in radar["axes"]
    assert "orphan_health" in radar["axes"]
    assert "embedding_coverage" in radar["axes"]
    assert "role_coverage" in radar["axes"]


def test_radar_composite_in_range(simple_wiki):
    repo, storage = simple_wiki
    r = doc_health_radar(repo=repo, storage_path=storage)
    composite = r["result"]["radar"]["composite"]
    assert 0.0 <= composite <= 100.0
    assert r["result"]["radar"]["grade"] in ("A", "B", "C", "D", "F")
