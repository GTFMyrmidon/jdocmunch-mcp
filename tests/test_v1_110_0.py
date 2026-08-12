"""jdoc#87 — Part C.2: explicit-intent reconciliation of genuine pre-1.102
fieldless legacy indexes (v1.110.0).

Real temporary Git repositories + linked worktrees (rknighton's fixture
pattern). Covers his minimal adversarial plan: no-intent backfill-only
control, zero-peer and one-peer proof rows, positive retirement with the
peer verified byte-for-byte, content/certification/ambiguity/provisional
negatives, proof-to-mutation drift conflict, and cleanup-failure retry.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools import _worktree_corpus as wc
from jdocmunch_mcp.tools.index_local import index_local


GUIDE_BYTES = b"# Guide\n\nshared snapshot for jdoc87\n"


def _git(cwd: Path, *args: str):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _index(path: Path, name: str, store: Path, **kw) -> dict:
    return index_local(
        path=str(path), name=name, storage_path=str(store),
        use_ai_summaries=False, use_embeddings=False, **kw,
    )


def _twin_repo(tmp_path: Path):
    """One commit; a linked worktree detached at the same commit — the two
    checkouts are byte-identical, clean, and certified at one SHA."""
    if shutil.which("git") is None:
        pytest.skip("git is required")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "JDocMunch test")
    _git(repo, "config", "core.autocrlf", "false")
    guide = repo / "docs" / "guide.md"
    guide.parent.mkdir(parents=True)
    guide.write_bytes(GUIDE_BYTES)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "snapshot")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "--detach", str(wt), sha)
    return repo, wt, sha


def _plant_legacy(monkeypatch, docs: Path, store: Path, name: str) -> None:
    """Create a genuine fieldless index: git evidence unavailable-as-non-git
    at creation, so no identity fields and no provisional stamp are written."""
    real_collect = wc.collect_git_evidence
    monkeypatch.setattr(wc, "collect_git_evidence", lambda _root: wc.GitEvidence())
    created = _index(docs, name, store)
    assert created["success"], created
    monkeypatch.setattr(wc, "collect_git_evidence", real_collect)
    stored = DocStore(base_path=str(store)).load_index("local", name)
    assert stored is not None
    assert int(getattr(stored, "corpus_identity_version", 0) or 0) == 0
    assert (getattr(stored, "worktree_lineage_key", "") or "") == ""
    assert (getattr(stored, "reconciliation_state", "") or "") == ""


def _standard_pair(tmp_path, monkeypatch):
    """Modern established peer at repo/docs + fieldless legacy at wt/docs."""
    repo, wt, sha = _twin_repo(tmp_path)
    store = tmp_path / "store"
    peer = _index(repo / "docs", "modern", store)
    assert peer["success"], peer
    _plant_legacy(monkeypatch, wt / "docs", store, "old")
    return repo, wt, sha, store


def _peer_monolith(store: Path) -> Path:
    return DocStore(base_path=str(store))._index_path("local", "modern")


# ── LC2-01 control: no intent -> backfill only, never delete ──────────────

def test_no_intent_full_refresh_backfills_and_keeps_both(tmp_path, monkeypatch):
    _, wt, _, store = _standard_pair(tmp_path, monkeypatch)
    out = _index(wt / "docs", "old", store)
    assert out["success"], out
    assert "legacy_reconciliation" not in out
    ds = DocStore(base_path=str(store))
    refreshed = ds.load_index("local", "old")
    assert int(refreshed.corpus_identity_version) == 1  # backfilled
    assert ds.load_index("local", "modern") is not None  # both kept


# ── precondition fail-closed rows ─────────────────────────────────────────

def test_invalid_mode_value_is_rejected(tmp_path, monkeypatch):
    _, wt, _, store = _standard_pair(tmp_path, monkeypatch)
    out = _index(wt / "docs", "old", store, legacy_reconcile="yes")
    assert out["success"] is False
    assert "Invalid legacy_reconcile" in out["error"]


def test_not_applicable_on_modern_target_and_no_write(tmp_path, monkeypatch):
    _, wt, _, store = _standard_pair(tmp_path, monkeypatch)
    before = _peer_monolith(store).read_bytes()
    out = _index(tmp_path / "repo" / "docs", "modern", store, legacy_reconcile="apply")
    assert out["success"] is False
    assert out["error"] == wc.REASON_LEGACY_NOT_APPLICABLE
    assert _peer_monolith(store).read_bytes() == before
    assert DocStore(base_path=str(store)).load_index("local", "old") is not None


def test_not_applicable_on_subset_paths(tmp_path, monkeypatch):
    _, wt, _, store = _standard_pair(tmp_path, monkeypatch)
    out = _index(
        wt / "docs", "old", store,
        legacy_reconcile="report", paths=["guide.md"],
    )
    assert out["success"] is False
    assert out["error"] == wc.REASON_LEGACY_NOT_APPLICABLE


# ── zero-peer and one-peer proof rows (report changes nothing) ────────────

def test_report_zero_peer_backfills_without_retirement(tmp_path, monkeypatch):
    repo, wt, sha = _twin_repo(tmp_path)
    store = tmp_path / "store"
    _plant_legacy(monkeypatch, wt / "docs", store, "old")
    out = _index(wt / "docs", "old", store, legacy_reconcile="report")
    assert out["success"], out
    block = out["legacy_reconciliation"]
    assert block["state"] == "kept"
    assert block["reason_code"] == wc.REASON_LEGACY_NO_MODERN_PEER
    refreshed = DocStore(base_path=str(store)).load_index("local", "old")
    assert refreshed is not None
    # Under C.2 intent the handle stays fieldless (re-runnable); an ordinary
    # refresh is the backfill path (LC2-01).
    assert int(getattr(refreshed, "corpus_identity_version", 0) or 0) == 0


def test_report_ready_changes_neither_handle(tmp_path, monkeypatch):
    _, wt, sha, store = _standard_pair(tmp_path, monkeypatch)
    peer_before = _peer_monolith(store).read_bytes()
    out = _index(wt / "docs", "old", store, legacy_reconcile="report")
    assert out["success"], out
    block = out["legacy_reconciliation"]
    assert block["state"] == "report"
    assert block["reason_code"] == wc.REASON_LEGACY_READY
    assert block["established_handle"] == "local/modern"
    assert block["would_remove_handle"] == "local/old"
    assert block["certified_sha"] == sha
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "old") is not None
    assert _peer_monolith(store).read_bytes() == peer_before


# ── positive retirement + idempotence-adjacent rerun ──────────────────────

def test_apply_retires_legacy_and_peer_is_byte_identical(tmp_path, monkeypatch):
    _, wt, sha, store = _standard_pair(tmp_path, monkeypatch)
    peer_before = _peer_monolith(store).read_bytes()
    out = _index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert out["success"], out
    assert out["repo"] == "local/modern"
    block = out["legacy_reconciliation"]
    assert block["state"] == "reconciled"
    assert block["reason_code"] == wc.REASON_LEGACY_RECONCILED
    assert block["removed_handle"] == "local/old"
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "old") is None
    assert _peer_monolith(store).read_bytes() == peer_before
    # A rerun with the retired name is a fail-closed refusal, never a delete.
    again = _index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert again["success"] is False
    assert again["error"] == wc.REASON_LEGACY_NOT_APPLICABLE
    assert _peer_monolith(store).read_bytes() == peer_before


# ── proof negatives: never delete ─────────────────────────────────────────

def test_content_differs_keeps_both(tmp_path, monkeypatch):
    _, wt, _, store = _standard_pair(tmp_path, monkeypatch)
    # Corrupt one stored hash on the peer — equality is now unprovable.
    monolith = _peer_monolith(store)
    data = json.loads(monolith.read_text(encoding="utf-8"))
    assert data["file_hashes"], data.keys()
    key = sorted(data["file_hashes"])[0]
    data["file_hashes"][key] = "0" * 64
    monolith.write_text(json.dumps(data), encoding="utf-8")
    out = _index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert out["success"], out
    block = out["legacy_reconciliation"]
    assert block["state"] == "kept"
    assert block["reason_code"] == wc.REASON_LEGACY_CONTENT_DIFFERS
    assert block["differing_file_count"] >= 1
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "old") is not None
    assert ds.load_index("local", "modern") is not None


def test_dirty_checkout_is_uncertified_and_keeps_both(tmp_path, monkeypatch):
    _, wt, _, store = _standard_pair(tmp_path, monkeypatch)
    (wt / "docs" / "guide.md").write_bytes(GUIDE_BYTES + b"\nuncommitted edit\n")
    out = _index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert out["success"], out
    block = out["legacy_reconciliation"]
    assert block["state"] == "kept"
    assert block["reason_code"] == wc.REASON_LEGACY_UNCERTIFIED
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "old") is not None
    assert ds.load_index("local", "modern") is not None


def test_multiple_peers_is_ambiguous(tmp_path, monkeypatch):
    repo, wt, sha, store = _standard_pair(tmp_path, monkeypatch)
    wt2 = tmp_path / "wt2"
    _git(repo, "worktree", "add", "--detach", str(wt2), sha)
    second = _index(wt2 / "docs", "modern2", store, worktree_mode="branch_local")
    assert second["success"], second
    out = _index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert out["success"], out
    block = out["legacy_reconciliation"]
    assert block["state"] == "kept"
    assert block["reason_code"] == wc.REASON_LEGACY_AMBIGUOUS
    ds = DocStore(base_path=str(store))
    for name in ("old", "modern", "modern2"):
        assert ds.load_index("local", name) is not None


def test_provisional_peer_never_vouches(tmp_path, monkeypatch):
    repo, wt, sha = _twin_repo(tmp_path)
    store = tmp_path / "store"
    _plant_legacy(monkeypatch, wt / "docs", store, "old")
    # The only other index is PROVISIONAL — authority-free, never a peer.
    real_collect = wc.collect_git_evidence
    monkeypatch.setattr(
        wc, "collect_git_evidence",
        lambda _root: wc.GitEvidence(verification_failed=True),
    )
    created = _index(repo / "docs", "prov", store)
    assert created["success"], created
    monkeypatch.setattr(wc, "collect_git_evidence", real_collect)
    out = _index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert out["success"], out
    assert (
        out["legacy_reconciliation"]["reason_code"]
        == wc.REASON_LEGACY_NO_MODERN_PEER
    )
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "prov") is not None
    assert ds.load_index("local", "old") is not None


# ── drift between proof and mutation ──────────────────────────────────────

def test_candidate_drift_between_proof_and_retirement(tmp_path, monkeypatch):
    _, wt, _, store = _standard_pair(tmp_path, monkeypatch)
    real_filter = wc.filter_lineage_candidates
    calls = {"n": 0}

    def flaky_filter(rows, ev, allow_containment=False):
        calls["n"] += 1
        if calls["n"] >= 2:  # the pre-retirement recheck sees a changed world
            return []
        return real_filter(rows, ev, allow_containment=allow_containment)

    monkeypatch.setattr(wc, "filter_lineage_candidates", flaky_filter)
    out = _index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert out["success"], out
    block = out["legacy_reconciliation"]
    assert block["state"] == "kept"
    assert block["reason_code"] == wc.REASON_LEGACY_CONFLICT
    ds = DocStore(base_path=str(store))
    assert ds.load_index("local", "old") is not None
    assert ds.load_index("local", "modern") is not None


# ── cleanup failure: loud, retryable ──────────────────────────────────────

def test_cleanup_failure_visible_then_retry_completes(tmp_path, monkeypatch):
    _, wt, _, store = _standard_pair(tmp_path, monkeypatch)
    monkeypatch.setattr(DocStore, "delete_index", lambda self, o, n: False)
    blocked = _index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert blocked["success"], blocked
    block = blocked["legacy_reconciliation"]
    assert block["reason_code"] == wc.REASON_LEGACY_CLEANUP_INCOMPLETE
    assert DocStore(base_path=str(store)).load_index("local", "old") is not None
    monkeypatch.undo()
    retried = _index(wt / "docs", "old", store, legacy_reconcile="apply")
    assert retried["success"], retried
    assert (
        retried["legacy_reconciliation"]["reason_code"]
        == wc.REASON_LEGACY_RECONCILED
    )
    assert DocStore(base_path=str(store)).load_index("local", "old") is None
