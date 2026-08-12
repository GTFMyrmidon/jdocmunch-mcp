"""Tests for get_doc_pr_risk_profile (v1.63.0)."""

from __future__ import annotations

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.get_doc_pr_risk_profile import (
    _normalise_changes,
    _risk_level,
    get_doc_pr_risk_profile,
)
from jdocmunch_mcp.tools.index_local import index_local


# ── Pure-function unit tests ──────────────────────────────────────────────────

def test_normalise_bare_strings():
    out = _normalise_changes(["a", "b"])
    assert out == [
        {"section_id": "a", "kind": "modified"},
        {"section_id": "b", "kind": "modified"},
    ]


def test_normalise_dicts():
    out = _normalise_changes([
        {"section_id": "a", "kind": "added"},
        {"section_id": "b", "kind": "deleted"},
    ])
    assert out[0]["kind"] == "added"
    assert out[1]["kind"] == "deleted"


def test_normalise_invalid_kind_defaults_to_modified():
    out = _normalise_changes([{"section_id": "a", "kind": "ghost"}])
    assert out[0]["kind"] == "modified"


def test_normalise_drops_malformed():
    out = _normalise_changes([{"kind": "added"}, 42, {"section_id": "valid"}])
    assert len(out) == 1
    assert out[0]["section_id"] == "valid"


def test_risk_level_thresholds():
    assert _risk_level(0.1) == "low"
    assert _risk_level(0.25) == "low"
    assert _risk_level(0.4) == "medium"
    assert _risk_level(0.6) == "high"
    assert _risk_level(0.9) == "critical"


# ── Integration ───────────────────────────────────────────────────────────────

@pytest.fixture
def wiki(tmp_path):
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "intro.md").write_text(
        "# Intro\n\nSee [reference](reference.md#api) and [guide](guide.md).\n"
    )
    (docs / "reference.md").write_text(
        "# Reference\n\n## API\n\nDetails. See also [intro](intro.md).\n"
    )
    (docs / "guide.md").write_text(
        "# Guide\n\n## Setup\n\nSteps. See [intro](intro.md) and "
        "[API](reference.md#api).\n"
    )
    storage = str(tmp_path / "store")
    res = index_local(path=str(docs), use_ai_summaries=False, storage_path=storage)
    assert res["success"]
    return res["repo"], storage


def _first_section_id(repo, storage, doc_path):
    owner, name = DocStore(base_path=storage)._resolve_repo(repo)
    idx = DocStore(base_path=storage).load_index(owner, name)
    for s in idx.sections:
        if s.get("doc_path") == doc_path:
            return s["id"]
    raise AssertionError(f"No section in {doc_path}")


def test_empty_changes_refused(wiki):
    repo, storage = wiki
    r = get_doc_pr_risk_profile(repo=repo, changed_sections=[], storage_path=storage)
    assert "error" in r
    assert r["reason"] == "no_changes"


def test_unknown_repo(tmp_path):
    r = get_doc_pr_risk_profile(
        repo="not/real",
        changed_sections=["section_id"],
        storage_path=str(tmp_path / "store"),
    )
    assert "error" in r


def test_response_shape_single_change(wiki):
    repo, storage = wiki
    sid = _first_section_id(repo, storage, "guide.md")
    r = get_doc_pr_risk_profile(
        repo=repo,
        changed_sections=[{"section_id": sid, "kind": "modified"}],
        storage_path=storage,
    )
    assert "result" in r, f"unexpected: {r}"
    res = r["result"]
    assert res["risk_level"] in ("low", "medium", "high", "critical")
    assert 0.0 <= res["risk_score"] <= 1.0
    assert set(res["signals"].keys()) == {
        "volume", "blast_radius", "backlink_burden",
        "tutorial_disruption", "role_weight",
    }
    # In a tiny 3-doc fixture, a single change is non-trivial by volume —
    # we just verify the signal calibration is in the right ballpark.
    assert res["signals"]["volume"] > 0.0


def test_added_kind_skips_backlink_lookup(wiki):
    """A newly added section has no inbound refs by definition."""
    repo, storage = wiki
    r = get_doc_pr_risk_profile(
        repo=repo,
        changed_sections=[{"section_id": "ghost::added::section", "kind": "added"}],
        storage_path=storage,
    )
    assert "result" in r
    # Ghost section_id won't resolve in any delegate, but the call still
    # returns cleanly with a low-risk verdict.
    assert r["result"]["risk_level"] in ("low", "medium")


def test_bare_string_input_works(wiki):
    repo, storage = wiki
    sid = _first_section_id(repo, storage, "intro.md")
    r = get_doc_pr_risk_profile(
        repo=repo,
        changed_sections=[sid],  # bare string, defaults to modified
        storage_path=storage,
    )
    assert "result" in r
    assert r["result"]["signal_details"]["changes_evaluated"] == 1


def test_recommended_action_per_level(wiki):
    repo, storage = wiki
    sid = _first_section_id(repo, storage, "intro.md")
    r = get_doc_pr_risk_profile(
        repo=repo, changed_sections=[sid], storage_path=storage,
    )
    action = r["result"]["recommended_action"]
    level = r["result"]["risk_level"]
    if level == "low":
        assert "Low-risk" in action
    elif level == "medium":
        assert "Moderate" in action
    elif level == "high":
        assert "HIGH" in action
    else:
        assert "CRITICAL" in action


def test_signal_shape_includes_breakdown(wiki):
    repo, storage = wiki
    sid = _first_section_id(repo, storage, "reference.md")
    r = get_doc_pr_risk_profile(
        repo=repo,
        changed_sections=[{"section_id": sid, "kind": "modified"}],
        storage_path=storage,
    )
    details = r["result"]["signal_details"]
    assert details["changes_evaluated"] == 1
    assert details["total_sections_in_repo"] >= 1
    assert "role_breakdown" in details
