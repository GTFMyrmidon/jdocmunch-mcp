"""jdoc#86 — modern verified-snapshot supersession (v1.109.0).

Real temporary Git repositories + linked worktrees, byte-preserving fixtures
(rknighton's #86 fixture pattern). Covers:

- MS-02 positive: certified strict-ancestor provisional retires to the
  established descendant; target byte-for-byte unchanged.
- MS-01 negative: descendant provisional never supersedes; explicit
  next_action reported.
- MS-03: explicit established-refresh-then-exact-dedup completion path.
- MS-04 negatives: unrelated branches, dirty checkout, uncertified target.
- Conflict: candidate set changes between classification and retirement.
- Cleanup failure: visible, idempotent on retry.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools import _worktree_corpus as wc
from jdocmunch_mcp.tools import _git as git_mod
from jdocmunch_mcp.tools.index_local import index_local


ANCESTOR_BYTES = b"# Guide\n\nancestor snapshot\n"
DESCENDANT_BYTES = b"# Guide\n\ndescendant snapshot\n"


def _git(cwd: Path, *args: str, check: bool = True):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=check, text=True, encoding="utf-8",
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def _write_exact(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _index(path: Path, name: str, store: Path) -> dict:
    return index_local(
        path=str(path), name=name, storage_path=str(store),
        use_ai_summaries=False, use_embeddings=False,
    )


def _linear_repo(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("git is required")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "JDocMunch test")
    _git(repo, "config", "core.autocrlf", "false")
    _write_exact(repo / "docs" / "guide.md", ANCESTOR_BYTES)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "ancestor")
    ancestor_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _write_exact(repo / "docs" / "guide.md", DESCENDANT_BYTES)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "descendant")
    descendant_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    ancestor_worktree = tmp_path / "ancestor-worktree"
    _git(repo, "worktree", "add", "--detach", str(ancestor_worktree), ancestor_sha)
    return repo, ancestor_worktree, ancestor_sha, descendant_sha


def _create_provisional(monkeypatch, docs: Path, store: Path, name: str) -> None:
    real_collect = wc.collect_git_evidence
    monkeypatch.setattr(
        wc, "collect_git_evidence",
        lambda _root: wc.GitEvidence(verification_failed=True),
    )
    created = _index(docs, name, store)
    assert created["success"], created
    monkeypatch.setattr(wc, "collect_git_evidence", real_collect)
    stored = DocStore(base_path=str(store)).load_index("local", name)
    assert stored is not None
    assert stored.reconciliation_state == wc.RECONCILIATION_PROVISIONAL


# ── commit_ancestry unit coverage ─────────────────────────────────────────

def test_commit_ancestry_classifies_linear_and_garbage(tmp_path: Path):
    repo, _, a_sha, d_sha = _linear_repo(tmp_path)
    docs = repo / "docs"
    assert git_mod.commit_ancestry(docs, a_sha, d_sha) == git_mod.ANCESTRY_ANCESTOR
    assert git_mod.commit_ancestry(docs, d_sha, a_sha) == git_mod.ANCESTRY_DESCENDANT
    assert git_mod.commit_ancestry(docs, a_sha, a_sha) == git_mod.ANCESTRY_UNPROVEN
    assert git_mod.commit_ancestry(docs, a_sha, "f" * 40) == git_mod.ANCESTRY_UNPROVEN
    assert git_mod.commit_ancestry(docs, "", d_sha) == git_mod.ANCESTRY_UNPROVEN
    assert git_mod.commit_ancestry(docs, "zz" * 20, d_sha) == git_mod.ANCESTRY_UNPROVEN


def test_commit_ancestry_unrelated_branches(tmp_path: Path):
    repo, _, a_sha, _ = _linear_repo(tmp_path)
    _git(repo, "checkout", "-b", "side", a_sha)
    _write_exact(repo / "docs" / "guide.md", b"# Guide\n\nside branch\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "side")
    side_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-")
    main_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert (
        git_mod.commit_ancestry(repo / "docs", side_sha, main_sha)
        == git_mod.ANCESTRY_UNRELATED
    )


# ── MS-02 positive: ancestor provisional retires ──────────────────────────

def test_ms02_ancestor_provisional_is_superseded(tmp_path: Path, monkeypatch):
    repo, ancestor_worktree, ancestor_sha, descendant_sha = _linear_repo(tmp_path)
    store = tmp_path / "store"

    established = _index(repo / "docs", "established", store)
    assert established["success"], established
    _create_provisional(monkeypatch, ancestor_worktree / "docs", store, "provisional")

    target_monolith = store / "local" / "established.json"
    before_bytes = target_monolith.read_bytes()

    result = _index(ancestor_worktree / "docs", "provisional", store)
    rec = result.get("reconciliation", {})

    assert rec.get("reason_code") == wc.REASON_SUPERSEDED
    assert result.get("repo") == "local/established"
    assert rec.get("provisional_sha") == ancestor_sha
    assert rec.get("established_sha") == descendant_sha
    assert rec.get("relationship") == "provisional_is_strict_ancestor_of_established"
    assert rec.get("removed_handle") == "local/provisional"
    assert rec.get("removed_file_count") == 1
    assert "cleanup_incomplete" not in rec
    # Loser fully retired; target byte-for-byte unchanged.
    assert DocStore(base_path=str(store)).load_index("local", "provisional") is None
    assert target_monolith.read_bytes() == before_bytes


# ── MS-01 negative: descendant provisional keeps both + next action ───────

def test_ms01_descendant_provisional_kept_with_next_action(tmp_path: Path, monkeypatch):
    repo, ancestor_worktree, ancestor_sha, descendant_sha = _linear_repo(tmp_path)
    store = tmp_path / "store"

    established = _index(ancestor_worktree / "docs", "established", store)
    assert established["success"], established
    _create_provisional(monkeypatch, repo / "docs", store, "provisional")

    result = _index(repo / "docs", "provisional", store)
    rec = result.get("reconciliation", {})

    assert rec.get("reason_code") == wc.REASON_PROVISIONAL_NEWER
    assert rec.get("relationship") == "established_is_strict_ancestor_of_provisional"
    assert rec.get("provisional_sha") == descendant_sha
    assert rec.get("established_sha") == ancestor_sha
    assert "established" in rec.get("next_action", "")
    kept = DocStore(base_path=str(store)).load_index("local", "provisional")
    target = DocStore(base_path=str(store)).load_index("local", "established")
    assert kept is not None
    assert kept.reconciliation_state == wc.RECONCILIATION_PROVISIONAL
    assert target is not None
    assert target.head_sha == ancestor_sha


# ── MS-03: explicit completion path still works ───────────────────────────

def test_ms03_explicit_refresh_then_exact_dedup_completes(tmp_path: Path, monkeypatch):
    repo, ancestor_worktree, ancestor_sha, descendant_sha = _linear_repo(tmp_path)
    store = tmp_path / "store"

    established = _index(ancestor_worktree / "docs", "established", store)
    assert established["success"], established
    _create_provisional(monkeypatch, repo / "docs", store, "provisional")

    blocked = _index(repo / "docs", "provisional", store)
    assert (
        blocked.get("reconciliation", {}).get("reason_code")
        == wc.REASON_PROVISIONAL_NEWER
    )

    refreshed = _index(repo / "docs", "established", store)
    assert refreshed["success"], refreshed

    completed = _index(repo / "docs", "provisional", store)
    assert (
        completed.get("reconciliation", {}).get("reason_code") == wc.REASON_RECONCILED
    )
    assert completed.get("repo") == "local/established"
    assert DocStore(base_path=str(store)).load_index("local", "provisional") is None


# ── MS-04 negatives ───────────────────────────────────────────────────────

def test_unrelated_branches_stay_content_differs(tmp_path: Path, monkeypatch):
    repo, _, a_sha, _ = _linear_repo(tmp_path)
    store = tmp_path / "store"
    # Side branch from the ancestor: unrelated ordering vs main HEAD.
    side_worktree = tmp_path / "side-worktree"
    _git(repo, "branch", "side", a_sha)
    _git(repo, "worktree", "add", str(side_worktree), "side")
    _write_exact(side_worktree / "docs" / "guide.md", b"# Guide\n\nside branch\n")
    _git(side_worktree, "add", "-A")
    _git(side_worktree, "commit", "-m", "side")

    established = _index(repo / "docs", "established", store)
    assert established["success"], established
    _create_provisional(monkeypatch, side_worktree / "docs", store, "provisional")

    result = _index(side_worktree / "docs", "provisional", store)
    rec = result.get("reconciliation", {})
    assert rec.get("reason_code") == wc.REASON_GRADUATION_CONTENT_DIFFERS
    assert rec.get("ancestry") == git_mod.ANCESTRY_UNRELATED
    assert DocStore(base_path=str(store)).load_index("local", "provisional") is not None
    assert DocStore(base_path=str(store)).load_index("local", "established") is not None


def test_dirty_checkout_never_supersedes(tmp_path: Path, monkeypatch):
    repo, ancestor_worktree, ancestor_sha, descendant_sha = _linear_repo(tmp_path)
    store = tmp_path / "store"

    established = _index(repo / "docs", "established", store)
    assert established["success"], established
    _create_provisional(monkeypatch, ancestor_worktree / "docs", store, "provisional")

    # Dirty the ancestor checkout with an untracked doc — ancestry can't
    # certify uncommitted content, so supersession must not fire.
    _write_exact(ancestor_worktree / "docs" / "extra.md", b"# Extra\n")

    result = _index(ancestor_worktree / "docs", "provisional", store)
    rec = result.get("reconciliation", {})
    assert rec.get("reason_code") != wc.REASON_SUPERSEDED
    assert DocStore(base_path=str(store)).load_index("local", "provisional") is not None


def test_uncertified_target_never_supersedes(tmp_path: Path, monkeypatch):
    repo, ancestor_worktree, ancestor_sha, descendant_sha = _linear_repo(tmp_path)
    store = tmp_path / "store"

    established = _index(repo / "docs", "established", store)
    assert established["success"], established
    # Strip certification from the established target by editing the stored
    # monolith directly (mtime change evicts the index cache).
    import json as _json

    monolith = store / "local" / "established.json"
    data = _json.loads(monolith.read_text(encoding="utf-8"))
    data.pop("sha_certified", None)
    monolith.write_text(_json.dumps(data, separators=(",", ":")), encoding="utf-8")
    summary = store / "local" / "established.summary.json"
    if summary.exists():
        sdata = _json.loads(summary.read_text(encoding="utf-8"))
        sdata["sha_certified"] = False
        summary.write_text(_json.dumps(sdata, separators=(",", ":")), encoding="utf-8")
    _create_provisional(monkeypatch, ancestor_worktree / "docs", store, "provisional")

    result = _index(ancestor_worktree / "docs", "provisional", store)
    rec = result.get("reconciliation", {})
    assert rec.get("reason_code") == wc.REASON_GRADUATION_CONTENT_DIFFERS
    assert "ancestry" not in rec  # prerequisites failed — never probed
    assert DocStore(base_path=str(store)).load_index("local", "provisional") is not None


# ── conflict + cleanup-failure hardening ──────────────────────────────────

def test_candidate_change_after_classification_is_conflict(tmp_path: Path, monkeypatch):
    repo, ancestor_worktree, ancestor_sha, descendant_sha = _linear_repo(tmp_path)
    store = tmp_path / "store"

    established = _index(repo / "docs", "established", store)
    assert established["success"], established
    _create_provisional(monkeypatch, ancestor_worktree / "docs", store, "provisional")

    real_filter = wc.filter_lineage_candidates
    calls = {"n": 0}

    def flaky_filter(rows, ev, allow_containment=False):
        calls["n"] += 1
        if calls["n"] >= 2:  # the pre-retirement recheck sees a changed world
            return []
        return real_filter(rows, ev, allow_containment=allow_containment)

    monkeypatch.setattr(wc, "filter_lineage_candidates", flaky_filter)
    result = _index(ancestor_worktree / "docs", "provisional", store)
    rec = result.get("reconciliation", {})
    assert rec.get("reason_code") == wc.REASON_SUPERSESSION_CONFLICT
    assert DocStore(base_path=str(store)).load_index("local", "provisional") is not None
    assert DocStore(base_path=str(store)).load_index("local", "established") is not None


def test_published_vocabulary_table_is_complete():
    """Every runtime STATUS_*/REASON_* value must appear in SPEC.md's published
    table (the #84 item-4 contract; #86 acceptance criterion)."""
    spec = (Path(__file__).parent.parent / "SPEC.md").read_text(encoding="utf-8")
    live = {
        v for k, v in vars(wc).items()
        if (k.startswith("STATUS_") or k.startswith("REASON_"))
        and isinstance(v, str)
    }
    missing = sorted(v for v in live if f"`{v}`" not in spec)
    assert not missing, f"SPEC.md vocabulary table is missing: {missing}"


def test_cleanup_failure_is_visible_and_retryable(tmp_path: Path, monkeypatch):
    repo, ancestor_worktree, ancestor_sha, descendant_sha = _linear_repo(tmp_path)
    store = tmp_path / "store"

    established = _index(repo / "docs", "established", store)
    assert established["success"], established
    _create_provisional(monkeypatch, ancestor_worktree / "docs", store, "provisional")

    monkeypatch.setattr(DocStore, "delete_index", lambda self, o, n: False)
    blocked = _index(ancestor_worktree / "docs", "provisional", store)
    rec = blocked.get("reconciliation", {})
    assert rec.get("reason_code") == wc.REASON_SUPERSESSION_CLEANUP_INCOMPLETE
    assert DocStore(base_path=str(store)).load_index("local", "provisional") is not None

    monkeypatch.undo()
    # monkeypatch.undo() also restored collect_git_evidence — that's fine,
    # the retry runs with fully real behavior and must complete (idempotent).
    retried = _index(ancestor_worktree / "docs", "provisional", store)
    assert (
        retried.get("reconciliation", {}).get("reason_code") == wc.REASON_SUPERSEDED
    )
    assert DocStore(base_path=str(store)).load_index("local", "provisional") is None
    assert DocStore(base_path=str(store)).load_index("local", "established") is not None
