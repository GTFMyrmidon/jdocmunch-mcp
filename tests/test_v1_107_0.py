"""jdoc#80 Part C (v1.107.0) — graduation of a provisional index.

When a provisional index (created under failed Git verification, Part B) is
FULLY refreshed while Git lineage is now CONFIRMED, it graduates:

- graduate in place when no established index shares its identity;
- reconcile (auto-cleanup, delete the provisional loser) when one does, but
  ONLY when the provisional's documents are a subset of the established one
  (no document loss) — otherwise it stays provisional (diverged, fail closed);
- ambiguous (more than one established match) stays provisional (fail closed).

Security invariants (PRD §4.1 I1-I6) are exercised directly in the adversarial
section: graduation needs confirmed lineage (never weaker / never accretion),
provisionals never vouch for each other, it is event-driven not time-driven,
provisionals are authority-free until proven, conflict never touches the
established index, and no gameable tiebreak is used.
"""

from __future__ import annotations

import jdocmunch_mcp.tools._worktree_corpus as wc
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools._worktree_corpus import (
    REASON_GRADUATED,
    REASON_RECONCILED,
    REASON_GRADUATION_AMBIGUOUS,
    REASON_GRADUATION_DIVERGED,
    RECONCILIATION_PROVISIONAL,
    GitEvidence,
    classify_graduation,
)
from jdocmunch_mcp.tools.index_local import index_local

_LINEAGE = "lineagekey00001"


class _Evidence:
    """Switchable git-evidence source: 'fail' quarantines a create; 'confirm'
    verifies lineage (drives graduation on the next refresh)."""

    def __init__(self):
        self.mode = "fail"
        self.key = _LINEAGE
        self.rel = ""

    def __call__(self, root):
        if self.mode == "fail":
            return GitEvidence(in_git=False, verification_failed=True)
        return GitEvidence(
            in_git=True, lineage_state="confirmed", lineage_key=self.key,
            relative_root=self.rel, common_dir="/c/.git", head_sha="a" * 40,
            corpus_dirty=False,
        )


def _docdir(tmp_path, name="docs", files=("a.md",)):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(files):
        (d / f).write_text(f"# H{i}\n\nbody {i}\n", encoding="utf-8")
    return d


def _index(path, storage, **kw):
    return index_local(
        path=str(path), storage_path=str(storage),
        use_ai_summaries=False, use_embeddings=False, **kw,
    )


def _plant_established(store, name, lineage_key, rel="", selection="full", docs=("a.md",)):
    """A non-provisional established index with worktree identity fields."""
    store.save_index(
        owner="local", name=name, sections=[],
        raw_files={d: f"# {d}\n\nx\n" for d in docs}, doc_types={},
        source_root=f"/planted/{name}",
        corpus_selection=selection,
        worktree_lineage_key=lineage_key, repo_relative_root=rel,
        corpus_identity_version=1,
    )


# ── pure decision helper (classify_graduation) ────────────────────────────

def test_classify_graduation_no_match_graduates():
    assert classify_graduation([], "full") == ("graduate", "")


def test_classify_graduation_one_match_reconciles():
    rows = [{"repo": "local/est", "corpus_selection": "full"}]
    assert classify_graduation(rows, "full") == ("reconcile", "local/est")


def test_classify_graduation_multiple_matches_ambiguous():
    rows = [
        {"repo": "local/e1", "corpus_selection": "full"},
        {"repo": "local/e2", "corpus_selection": "full"},
    ]
    assert classify_graduation(rows, "full") == ("ambiguous", "")


def test_classify_graduation_selection_mismatch_is_not_a_match():
    rows = [{"repo": "local/est", "corpus_selection": "subset:sha:1"}]
    # Different selection => not the same corpus => graduate in place.
    assert classify_graduation(rows, "full") == ("graduate", "")


def test_classify_graduation_legacy_empty_selection_is_full():
    rows = [{"repo": "local/est"}]  # no corpus_selection => "full"
    assert classify_graduation(rows, "full") == ("reconcile", "local/est")


# ── graduation integration ────────────────────────────────────────────────

def test_graduate_in_place_when_no_established_peer(tmp_path, monkeypatch):
    ev = _Evidence()
    monkeypatch.setattr(wc, "collect_git_evidence", ev)
    store_dir = tmp_path / "store"
    src = _docdir(tmp_path)
    # Create provisional (git unavailable).
    out1 = _index(src, store_dir, name="proj")
    assert out1["reconciliation"]["state"] == RECONCILIATION_PROVISIONAL

    # Git comes back; full refresh graduates in place.
    ev.mode = "confirm"
    (src / "b.md").write_text("# More\n\ny\n", encoding="utf-8")
    out2 = _index(src, store_dir, name="proj")
    assert out2["success"] is True
    assert out2["reconciliation"]["state"] == "graduated"
    assert out2["reconciliation"]["reason_code"] == REASON_GRADUATED

    loaded = DocStore(base_path=str(store_dir)).load_index("local", "proj")
    assert loaded.reconciliation_state == ""            # provisional cleared
    assert loaded.worktree_lineage_key == _LINEAGE      # identity written


def test_reconcile_auto_cleanup_to_established(tmp_path, monkeypatch):
    ev = _Evidence()
    monkeypatch.setattr(wc, "collect_git_evidence", ev)
    store_dir = tmp_path / "store"
    store = DocStore(base_path=str(store_dir))
    src = _docdir(tmp_path, files=("a.md",))
    _index(src, store_dir, name="prov")                 # provisional
    # An established index for the SAME identity already exists (docs superset).
    _plant_established(store, "established", _LINEAGE, docs=("a.md",))

    ev.mode = "confirm"
    out = _index(src, store_dir, name="prov")
    assert out["success"] is True
    assert out["reconciliation"]["reason_code"] == REASON_RECONCILED
    assert out["repo"] == "local/established"
    # I5: provisional (loser) auto-cleaned; established untouched.
    assert store.load_index("local", "prov") is None
    assert store.load_index("local", "established") is not None


def test_diverged_stays_provisional_no_deletion(tmp_path, monkeypatch):
    ev = _Evidence()
    monkeypatch.setattr(wc, "collect_git_evidence", ev)
    store_dir = tmp_path / "store"
    store = DocStore(base_path=str(store_dir))
    src = _docdir(tmp_path, files=("a.md", "unique.md"))
    _index(src, store_dir, name="prov")
    # Established lacks unique.md -> provisional is NOT a subset -> diverged.
    _plant_established(store, "established", _LINEAGE, docs=("a.md",))

    ev.mode = "confirm"
    out = _index(src, store_dir, name="prov")
    assert out["reconciliation"]["reason_code"] == REASON_GRADUATION_DIVERGED
    # Fail closed: provisional kept (no document loss), established untouched.
    assert store.load_index("local", "prov").reconciliation_state == RECONCILIATION_PROVISIONAL
    assert store.load_index("local", "established") is not None


def test_ambiguous_stays_provisional(tmp_path, monkeypatch):
    ev = _Evidence()
    monkeypatch.setattr(wc, "collect_git_evidence", ev)
    store_dir = tmp_path / "store"
    store = DocStore(base_path=str(store_dir))
    src = _docdir(tmp_path)
    _index(src, store_dir, name="prov")
    _plant_established(store, "est1", _LINEAGE, docs=("a.md",))
    _plant_established(store, "est2", _LINEAGE, docs=("a.md",))

    ev.mode = "confirm"
    out = _index(src, store_dir, name="prov")
    assert out["reconciliation"]["reason_code"] == REASON_GRADUATION_AMBIGUOUS
    assert store.load_index("local", "prov").reconciliation_state == RECONCILIATION_PROVISIONAL


# ── adversarial invariants (PRD §6.6) ─────────────────────────────────────

def test_still_unverifiable_never_graduates(tmp_path, monkeypatch):
    # I1 + I3: no amount of repeated failed-verification refreshes graduates.
    ev = _Evidence()  # stays in "fail" mode
    monkeypatch.setattr(wc, "collect_git_evidence", ev)
    store_dir = tmp_path / "store"
    src = _docdir(tmp_path)
    _index(src, store_dir, name="proj")
    for _ in range(5):
        (src / "more.md").write_text("# x\n\ny\n", encoding="utf-8")
        out = _index(src, store_dir, name="proj")
        # Never a graduated/reconciled outcome without confirmed git.
        assert out.get("reconciliation", {}).get("state") != "graduated"
    assert DocStore(base_path=str(store_dir)).load_index(
        "local", "proj"
    ).reconciliation_state == RECONCILIATION_PROVISIONAL


def test_subset_refresh_never_graduates(tmp_path, monkeypatch):
    # I1: partial (subset) proof does not graduate; only a full refresh can.
    ev = _Evidence()
    monkeypatch.setattr(wc, "collect_git_evidence", ev)
    store_dir = tmp_path / "store"
    src = _docdir(tmp_path, files=("a.md", "b.md"))
    _index(src, store_dir, name="proj")
    ev.mode = "confirm"
    out = _index(src, store_dir, name="proj", paths=["a.md"])
    assert out.get("reconciliation", {}).get("state") != "graduated"
    assert DocStore(base_path=str(store_dir)).load_index(
        "local", "proj"
    ).reconciliation_state == RECONCILIATION_PROVISIONAL


def test_provisionals_never_vouch_for_each_other(tmp_path, monkeypatch):
    # I2: two provisional indexes with the same would-be identity. Refreshing
    # one must NOT reconcile it to the OTHER provisional (a provisional is never
    # a reconcile target). It graduates in place on its own git proof; the other
    # stays provisional, unconsumed.
    ev = _Evidence()
    monkeypatch.setattr(wc, "collect_git_evidence", ev)
    store_dir = tmp_path / "store"
    store = DocStore(base_path=str(store_dir))
    src = _docdir(tmp_path)
    _index(src, store_dir, name="p1")
    _index(src, store_dir, name="p2", paths=["a.md"])  # 2nd provisional, distinct handle
    # Both provisional.
    assert store.load_index("local", "p1").reconciliation_state == RECONCILIATION_PROVISIONAL
    assert store.load_index("local", "p2").reconciliation_state == RECONCILIATION_PROVISIONAL

    ev.mode = "confirm"
    out = _index(src, store_dir, name="p1")
    # p1 graduates in place (NOT reconcile-to-p2); p2 untouched, still provisional.
    assert out["reconciliation"]["state"] == "graduated"
    assert store.load_index("local", "p1").reconciliation_state == ""
    assert store.load_index("local", "p2").reconciliation_state == RECONCILIATION_PROVISIONAL


def test_provisional_authority_free_until_graduated(tmp_path, monkeypatch):
    # I4: while provisional, a would-be peer must not resolve to it. classify
    # only ever reconciles to a non-provisional row (its input is pre-filtered);
    # here a provisional row carrying a lineage key is never a reconcile target.
    prov_row = {
        "repo": "local/prov", "corpus_selection": "full",
        "worktree_lineage_key": _LINEAGE, "repo_relative_root": "",
        "reconciliation_state": RECONCILIATION_PROVISIONAL,
    }
    ev = GitEvidence(in_git=True, lineage_state="confirmed", lineage_key=_LINEAGE, relative_root="")
    from jdocmunch_mcp.tools._worktree_corpus import filter_lineage_candidates
    got = filter_lineage_candidates([prov_row], ev, allow_containment=False)
    assert got == []  # provisional never a candidate
    assert classify_graduation(got, "full") == ("graduate", "")
