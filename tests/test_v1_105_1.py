"""jdoc#84 item 1 — the two public candidate lists share the MAX_CANDIDATES bound.

QA follow-up on the 1.102.0 (#83) worktree work (@rknighton): on the
`doc_resolve_repo` not-found worktree path the nested
`worktree_resolution.candidates` correctly capped at MAX_CANDIDATES (=5) with
`total_candidates` reporting the true count, but the top-level
`canonical_candidates` returned the full unbounded list. Both public lists must
cap at MAX_CANDIDATES; the full count stays reported via
`worktree_resolution.total_candidates`.

The resolver is driven with a controlled decision so the boundary at 5/6 is
exercised deterministically (the bug is in resolve_repo.py's response
assembly, not in the pure resolver).
"""

from __future__ import annotations

import jdocmunch_mcp.tools._worktree_corpus as wc
from jdocmunch_mcp.tools._worktree_corpus import (
    MAX_CANDIDATES,
    GitEvidence,
    ResolutionDecision,
)
from jdocmunch_mcp.tools.resolve_repo import doc_resolve_repo


def _candidates(n: int) -> list:
    return [{"repo": f"local/c{i}", "source_root": f"/root/c{i}"} for i in range(n)]


def _drive(monkeypatch, tmp_path, n: int) -> dict:
    """Run doc_resolve_repo on an unindexed path with a resolver that returns
    exactly n candidates, so the not-found worktree branch assembles the two
    public lists from a known decision."""
    ev = GitEvidence(in_git=True, lineage_state="confirmed")
    monkeypatch.setattr(wc, "collect_git_evidence", lambda root: ev)
    monkeypatch.setattr(
        wc, "filter_lineage_candidates", lambda repos, evidence, allow_containment=True: []
    )

    decision = ResolutionDecision(
        status="ambiguous",
        reason_code="multiple_equivalent_candidates",
        candidates=_candidates(n),
        total_candidates=n,
        next_action="Pick an explicit index handle.",
    )
    monkeypatch.setattr(wc, "resolve_worktree_corpus", lambda request, candidates: decision)

    probe = tmp_path / "worktree"
    probe.mkdir()
    return doc_resolve_repo(path=str(probe), storage_path=str(tmp_path / "store"))


def test_five_candidates_returned_in_full(tmp_path, monkeypatch):
    out = _drive(monkeypatch, tmp_path, 5)
    assert len(out["canonical_candidates"]) == 5
    assert len(out["worktree_resolution"]["candidates"]) == 5
    assert out["worktree_resolution"]["total_candidates"] == 5


def test_six_candidates_capped_at_five_but_total_reports_six(tmp_path, monkeypatch):
    out = _drive(monkeypatch, tmp_path, 6)
    assert len(out["canonical_candidates"]) == MAX_CANDIDATES == 5
    assert len(out["worktree_resolution"]["candidates"]) == 5
    assert out["worktree_resolution"]["total_candidates"] == 6
    # Both public lists agree, and neither leaks the sixth record.
    assert out["canonical_candidates"] == out["worktree_resolution"]["candidates"]


def test_eight_candidates_both_lists_capped(tmp_path, monkeypatch):
    # The originally-reported reproduction (8 found).
    out = _drive(monkeypatch, tmp_path, 8)
    assert len(out["canonical_candidates"]) == 5
    assert len(out["worktree_resolution"]["candidates"]) == 5
    assert out["worktree_resolution"]["total_candidates"] == 8


def test_zero_candidates_well_formed(tmp_path, monkeypatch):
    # no_match style: resolver returns a decision with no candidates → the
    # not-found response omits both candidate lists and stays well formed.
    ev = GitEvidence(in_git=True, lineage_state="confirmed")
    monkeypatch.setattr(wc, "collect_git_evidence", lambda root: ev)
    monkeypatch.setattr(
        wc, "filter_lineage_candidates", lambda repos, evidence, allow_containment=True: []
    )
    decision = ResolutionDecision(status="no_match", reason_code="no_equivalent_candidate")
    monkeypatch.setattr(wc, "resolve_worktree_corpus", lambda request, candidates: decision)
    probe = tmp_path / "worktree"
    probe.mkdir()
    out = doc_resolve_repo(path=str(probe), storage_path=str(tmp_path / "store"))
    assert out["found"] is False
    assert "canonical_candidates" not in out
    assert "worktree_resolution" not in out  # no_match is not attached


def test_one_candidate_unchanged(tmp_path, monkeypatch):
    out = _drive(monkeypatch, tmp_path, 1)
    assert len(out["canonical_candidates"]) == 1
    assert out["canonical_candidates"] == out["worktree_resolution"]["candidates"]
    assert out["worktree_resolution"]["total_candidates"] == 1
