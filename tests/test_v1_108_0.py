"""jdoc#85 — C.1 hardening (v1.108.0).

Confirmed-gap regressions adapted from rknighton's focused QA harness
(test_c1_confirmed_repros.py, run against 1.107.0), plus the decided
C1-05/C1-06 negative cases:

- C1-01/C1-02: exact-duplicate cleanup requires Git-verified identity AND
  per-file stored-hash equality; a mismatch keeps both indexes.
- C1-05: dirty state does not block exact-duplicate cleanup when hashes match.
- C1-06: hash equality without verified lineage never reconciles.
- C1-07/C1-08: reconciliation and direct deletion remove every index-owned
  auxiliary sidecar.
- C1-09: corpus_identity_version survives the summary/list projection.
"""

from __future__ import annotations

from pathlib import Path

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools import _worktree_corpus as wc
from jdocmunch_mcp.tools.index_local import index_local


HEAD_A = "a" * 40
SIDE_SUFFIXES = (
    ".embeddings.jsonl",
    ".terms.json",
    ".related.json",
    ".boilerplate.json",
    ".duplicates.json",
)


class EvidenceSwitch:
    """Callable monkeypatch for collect_git_evidence."""

    def __init__(self):
        self.mode = "fail"
        self.dirty = False
        self.head = HEAD_A

    def __call__(self, root: Path):
        if self.mode == "fail":
            return wc.GitEvidence(verification_failed=True)
        return wc.GitEvidence(
            in_git=True,
            lineage_state=wc.LINEAGE_CONFIRMED,
            lineage_key="lineage-k",
            common_dir="/git/common",
            toplevel=str(Path(root).parent),
            relative_root=Path(root).name,
            head_sha=self.head,
            corpus_dirty=self.dirty,
        )


def _source(tmp_path: Path, text: str = "# Guide\n\nprovisional body\n") -> Path:
    src = tmp_path / "docs"
    src.mkdir()
    (src / "guide.md").write_text(text, encoding="utf-8")
    return src


def _index(src: Path, storage: Path, name: str) -> dict:
    return index_local(
        path=str(src),
        name=name,
        storage_path=str(storage),
        use_ai_summaries=False,
        use_embeddings=False,
    )


def _plant_established(
    storage: Path,
    *,
    name: str = "established",
    content: str = "# Guide\n\nestablished body\n",
) -> None:
    DocStore(base_path=str(storage)).save_index(
        owner="local",
        name=name,
        sections=[],
        raw_files={"guide.md": content},
        doc_types={".md": 1},
        source_root="/different/worktree/docs",
        corpus_selection="full",
        worktree_lineage_key="lineage-k",
        repo_relative_root="docs",
        corpus_identity_version=wc.CORPUS_IDENTITY_VERSION,
        head_sha=HEAD_A,
        source_dirty=False,
        sha_certified=True,
    )


def _create_provisional(monkeypatch, tmp_path: Path):
    src = _source(tmp_path)
    storage = tmp_path / "store"
    evidence = EvidenceSwitch()
    monkeypatch.setattr(wc, "collect_git_evidence", evidence)
    created = _index(src, storage, "provisional")
    assert created["success"], created
    idx = DocStore(base_path=str(storage)).load_index("local", "provisional")
    assert idx is not None
    assert idx.reconciliation_state == wc.RECONCILIATION_PROVISIONAL
    return src, storage, evidence


def _exact_text(path: Path) -> str:
    """Read preserving on-disk newlines — file_hashes address real disk bytes
    (jdoc#52), and write_text minted CRLF on Windows."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        return f.read()


def _plant_sidecars(storage: Path, name: str) -> list:
    owner_dir = storage / "local"
    owner_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix in SIDE_SUFFIXES:
        path = owner_dir / f"{name}{suffix}"
        path.write_text("{}\n", encoding="utf-8")
        paths.append(path)
    return paths


# ── C1-09: identity version survives listing ──────────────────────────────

def test_summary_row_preserves_identity_version(tmp_path: Path):
    storage = tmp_path / "store"
    store = DocStore(base_path=str(storage))
    store.save_index(
        owner="local",
        name="modern",
        sections=[],
        raw_files={"guide.md": "# Guide\n"},
        doc_types={".md": 1},
        source_root="/one/docs",
        corpus_selection="full",
        worktree_lineage_key="lineage-k",
        repo_relative_root="docs",
        corpus_identity_version=wc.CORPUS_IDENTITY_VERSION,
    )

    row = next(r for r in store.list_repos() if r["repo"] == "local/modern")
    assert row.get("corpus_identity_version") == wc.CORPUS_IDENTITY_VERSION
    assert wc.legacy_sibling_handles([row], "/other/docs") == []


def test_prefix_summary_without_identity_key_falls_back_to_monolith(tmp_path: Path):
    """A summary written before the key existed can't claim legacy status —
    list_repos must consult the monolith instead of mispresenting the index."""
    import json

    storage = tmp_path / "store"
    store = DocStore(base_path=str(storage))
    store.save_index(
        owner="local",
        name="modern",
        sections=[],
        raw_files={"guide.md": "# Guide\n"},
        doc_types={".md": 1},
        source_root="/one/docs",
        corpus_identity_version=wc.CORPUS_IDENTITY_VERSION,
    )
    summary_path = storage / "local" / "modern.summary.json"
    assert summary_path.exists()
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    s.pop("corpus_identity_version", None)
    summary_path.write_text(json.dumps(s), encoding="utf-8")

    row = next(r for r in store.list_repos() if r["repo"] == "local/modern")
    assert row.get("corpus_identity_version") == wc.CORPUS_IDENTITY_VERSION


# ── C1-07/C1-08: complete cleanup of a retired index ──────────────────────

def test_delete_index_removes_all_named_sidecars(tmp_path: Path):
    storage = tmp_path / "store"
    store = DocStore(base_path=str(storage))
    store.save_index(
        owner="local",
        name="victim",
        sections=[],
        raw_files={"guide.md": "# Guide\n"},
        doc_types={".md": 1},
    )
    sidecars = _plant_sidecars(storage, "victim")

    assert store.delete_index("local", "victim") is True
    assert not [p for p in sidecars if p.exists()]


def test_successful_reconciliation_removes_loser_sidecars(monkeypatch, tmp_path: Path):
    src, storage, evidence = _create_provisional(monkeypatch, tmp_path)
    raw = _exact_text(storage / "local" / "provisional" / "guide.md")
    _plant_established(storage, content=raw)
    sidecars = _plant_sidecars(storage, "provisional")

    evidence.mode = "confirmed"
    result = _index(src, storage, "provisional")

    assert result.get("reconciliation", {}).get("reason_code") == wc.REASON_RECONCILED
    assert DocStore(base_path=str(storage)).load_index("local", "provisional") is None
    assert not [p for p in sidecars if p.exists()]


# ── C1-01/C1-02: duplicate proof requires matching stored hashes ──────────

def test_reconcile_rejects_same_path_different_content(monkeypatch, tmp_path: Path):
    src, storage, evidence = _create_provisional(monkeypatch, tmp_path)
    provisional = DocStore(base_path=str(storage)).load_index("local", "provisional")
    assert provisional is not None
    provisional_hash = provisional.file_hashes["guide.md"]

    _plant_established(storage, content="# Guide\n\nDIFFERENT target body\n")
    target = DocStore(base_path=str(storage)).load_index("local", "established")
    assert target is not None
    assert target.file_hashes["guide.md"] != provisional_hash

    evidence.mode = "confirmed"
    result = _index(src, storage, "provisional")

    kept = DocStore(base_path=str(storage)).load_index("local", "provisional")
    assert kept is not None
    assert kept.reconciliation_state == wc.RECONCILIATION_PROVISIONAL
    rec = result.get("reconciliation", {})
    assert rec.get("reason_code") == wc.REASON_GRADUATION_CONTENT_DIFFERS
    assert "guide.md" in rec.get("differing_files", [])
    assert rec.get("established_handle") == "local/established"
    # The established index is untouched.
    still = DocStore(base_path=str(storage)).load_index("local", "established")
    assert still is not None
    assert still.file_hashes["guide.md"] == target.file_hashes["guide.md"]


# ── C1-05: dirty state doesn't block a hash-proven exact duplicate ────────

def test_dirty_exact_duplicate_still_reconciles(monkeypatch, tmp_path: Path):
    src, storage, evidence = _create_provisional(monkeypatch, tmp_path)
    raw = _exact_text(storage / "local" / "provisional" / "guide.md")
    _plant_established(storage, content=raw)

    evidence.mode = "confirmed"
    evidence.dirty = True
    result = _index(src, storage, "provisional")

    assert result.get("reconciliation", {}).get("reason_code") == wc.REASON_RECONCILED
    assert DocStore(base_path=str(storage)).load_index("local", "provisional") is None


# ── C1-06: hash equality without verified lineage never reconciles ────────

def test_hash_equality_without_verified_lineage_never_reconciles(
    monkeypatch, tmp_path: Path
):
    src, storage, evidence = _create_provisional(monkeypatch, tmp_path)
    raw = _exact_text(storage / "local" / "provisional" / "guide.md")
    _plant_established(storage, content=raw)

    # Verification keeps failing — identical content must NOT authorize cleanup.
    assert evidence.mode == "fail"
    result = _index(src, storage, "provisional")

    kept = DocStore(base_path=str(storage)).load_index("local", "provisional")
    assert kept is not None
    assert kept.reconciliation_state == wc.RECONCILIATION_PROVISIONAL
    assert result.get("reconciliation", {}).get("reason_code") != wc.REASON_RECONCILED
    assert DocStore(base_path=str(storage)).load_index("local", "established") is not None
