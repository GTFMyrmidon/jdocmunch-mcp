"""jdoc#82 — Item A hardening: the four adversarial-QA reproductions as
focused regressions. Each mirrors the supplied harness's failing check."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.storage.corpus_claims import (
    claim_key,
    claims_dir,
    read_claim,
    try_claim,
)
from jdocmunch_mcp.tools._corpus_identity import (
    corpus_norm_root,
    selection_covers,
    selection_descriptor,
    selection_identical,
)
from jdocmunch_mcp.tools.index_local import index_local


@pytest.fixture()
def corpus(tmp_path):
    src = tmp_path / "project" / "docs"
    src.mkdir(parents=True)
    (src / "guide.md").write_text("# Guide\n\nHello.\n", encoding="utf-8")
    (src / "api.md").write_text("# API\n\nCalls.\n", encoding="utf-8")
    storage = tmp_path / "storage"
    return src, str(storage)


def _index(src, storage, **kw):
    return index_local(
        path=str(src), storage_path=storage,
        use_ai_summaries=False, use_embeddings=False, **kw,
    )


def _rows(storage):
    return DocStore(base_path=storage).list_repos()


def _tree_digest(storage):
    root = Path(storage)
    digest = hashlib.sha256()
    if not root.exists():
        return digest.hexdigest()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        digest.update(p.relative_to(root).as_posix().encode("utf-8"))
        digest.update(p.read_bytes())
    return digest.hexdigest()


class TestSingleWinnerInvariant:
    """Harness check 1: claim payload visibility."""

    def test_claim_payload_is_atomically_visible(self, corpus):
        # The hardlink publication means a claim file, once observable,
        # always carries its full ownership payload.
        src, storage = corpus
        root = corpus_norm_root(src.resolve())
        key = claim_key(root, "full")
        acquired, _ = try_claim(storage, key, "local/docs-w", root, "full")
        assert acquired
        claim = read_claim(storage, key)
        assert claim and claim["repo"] == "local/docs-w"

    def test_unreadable_claim_blocks_creation_with_no_write(self, corpus):
        # Fallback-path window: a claim file that exists but has no readable
        # payload is a winner mid-write — the second creator must refuse to
        # create rather than race to a second physical index.
        src, storage = corpus
        root = corpus_norm_root(src.resolve())
        key = claim_key(root, "full")
        d = claims_dir(storage)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{key}.json").write_bytes(b"")  # present but unreadable
        before = _tree_digest(storage)
        r = _index(src, storage, name="docs-b")
        assert r["success"] is False
        assert r["error"] == "corpus_creation_in_progress"
        assert _tree_digest(storage) == before
        assert _rows(storage) == []


class TestTrueAmbiguity:
    """Harness check 2: several legacy matches never promote a winner."""

    def test_two_legacy_matches_explicit_name_no_established_handle(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        store = DocStore(base_path=storage)
        first = store.load_index("local", "docs-a")
        store.save_index(
            owner="local", name="docs-b",
            sections=[], raw_files={}, doc_types={},
            source_root=first.source_root,
        )
        for n in ("docs-a", "docs-b"):
            for suffix in (".json", ".summary.json"):
                p = Path(storage) / "local" / f"{n}{suffix}"
                if p.exists():
                    data = json.loads(p.read_text(encoding="utf-8"))
                    data.pop("corpus_selection", None)
                    p.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        before = _tree_digest(storage)
        r = _index(src, storage, name="docs-c")
        assert r["success"] is False
        assert r["error"] == "ambiguous_corpus_identity"
        assert "established_handle" not in r
        assert r["total_matches"] == 2
        assert _tree_digest(storage) == before


class TestSelectionOrderIndependence:
    """Harness check 3: durable-selection identity is symmetric."""

    def test_full_then_subset_and_subset_then_full_both_yield_two(self, tmp_path):
        src = tmp_path / "docs"
        src.mkdir()
        (src / "guide.md").write_text("# Guide\n\nBody.\n", encoding="utf-8")
        (src / "api.md").write_text("# API\n\nBody.\n", encoding="utf-8")

        s1 = str(tmp_path / "full-first")
        assert _index(src, s1, name="docs-all")["success"]
        r = _index(src, s1, name="docs-guide", paths=["guide.md"])
        assert r["success"], r  # intentional durable subset — not a conflict
        assert len(_rows(s1)) == 2

        s2 = str(tmp_path / "subset-first")
        assert _index(src, s2, name="docs-guide", paths=["guide.md"])["success"]
        r = _index(src, s2, name="docs-all")
        assert r["success"], r
        assert len(_rows(s2)) == 2

    def test_relation_properties(self):
        sub = selection_descriptor(["a.md"])
        # Identity: symmetric, never full==subset in either direction.
        assert selection_identical("full", "full")
        assert selection_identical("", "full")  # legacy normalizes
        assert not selection_identical("full", sub)
        assert not selection_identical(sub, "full")
        # Refresh coverage stays directional: full absorbs a temporary
        # subset call; a subset never absorbs full.
        assert selection_covers("full", sub)
        assert not selection_covers(sub, "full")

    def test_omitted_name_subset_refresh_still_routes_to_full(self, corpus):
        src, storage = corpus
        assert _index(src, storage, name="docs-a")["success"]
        r = _index(src, storage, paths=["guide.md"])
        assert r["success"] and r["repo"] == "local/docs-a"
        assert len(_rows(storage)) == 1


class TestNoSilentRetargeting:
    """Harness check 4: corpus-shaping inputs are part of durable identity."""

    def test_ignore_patterns_shape_the_descriptor(self):
        plain = selection_descriptor(None)
        shaped = selection_descriptor(None, extra_ignore_patterns=["drafts/**"])
        assert plain == "full"
        assert shaped.startswith("full+shape:") and shaped != plain
        assert selection_descriptor(
            None, extra_ignore_patterns=["drafts/**"]
        ) == shaped  # stable
        assert not selection_identical(plain, shaped)

    def test_changed_ignore_selection_reconciles_and_discloses(self, tmp_path):
        src = tmp_path / "docs"
        src.mkdir()
        (src / "guide.md").write_text("# Guide\n\nBody.\n", encoding="utf-8")
        drafts = src / "drafts"
        drafts.mkdir()
        (drafts / "future.md").write_text("# Future\n\nDraft.\n", encoding="utf-8")
        storage = str(tmp_path / "store")

        first = _index(
            src, storage, name="published-docs",
            extra_ignore_patterns=["drafts/**"],
        )
        assert first["success"]
        store = DocStore(base_path=storage)
        idx = store.load_index("local", "published-docs")
        assert idx.corpus_selection.startswith("full+shape:")
        assert list(idx.doc_paths) == ["guide.md"]

        # jdoc#116 CHANGED THE INSTANCE, NOT THE INVARIANT. Read this carefully
        # before "restoring" the old assertion.
        #
        # The rule is: stored coverage never shifts under an unchanged identity.
        # This test used to pin one INSTANCE of it — that a refresh saying
        # NOTHING recomputes the selection as `full`, widens, and discloses. That
        # instance was the defect in jdoc#116: the index-local CLI cannot express
        # the patterns, so every documented CLI refresh silently destroyed the
        # operator's exclusion. Disclosure does not help when the entry point has
        # no way to avoid triggering it.
        #
        # Now: None means "said nothing" and INHERITS, so coverage and identity
        # both hold still and there is nothing to disclose. `[]` means
        # "explicitly none" and still widens WITH disclosure, asserted below.
        # The invariant is satisfied in both cases; it is satisfied more
        # strongly by inheritance, because neither side moves at all.
        refresh = _index(src, storage, name="published-docs")
        assert refresh["success"]
        after = store.load_index("local", "published-docs")
        assert list(after.doc_paths) == ["guide.md"], "silent refresh must not widen"
        assert after.corpus_selection == idx.corpus_selection
        assert refresh.get("corpus_selection_changed") is None

        # The explicit clear is what now carries the disclosure.
        cleared = _index(src, storage, name="published-docs", extra_ignore_patterns=[])
        assert cleared["success"]
        after = store.load_index("local", "published-docs")
        assert list(after.doc_paths) == ["drafts/future.md", "guide.md"]
        assert after.corpus_selection == "full"
        assert cleared["corpus_selection_changed"] == {
            "from": idx.corpus_selection,
            "to": "full",
        }

    def test_follow_symlinks_shapes_the_descriptor(self):
        assert selection_descriptor(None, follow_symlinks=True).startswith(
            "full+shape:"
        )
