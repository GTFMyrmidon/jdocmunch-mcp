"""verify_index says WHICH bytes it verified, and can verify the live source (jdoc#105).

The reported behaviour is real: the default reads the cached raw mirror under
`store._content_dir()`, so a source file that was edited, truncated or deleted
after indexing still verifies clean. Both sides of the comparison come from the
index.

That default is kept, because it is a real check (corruption of ~/.doc-index is
what B1/B2 of the v1.10 audit were about) and flipping it would silently change
what existing CI gates on. What was wrong was the documentation, which promised
"its current on-disk content", and the absence of any way to ask the other
question.

The fixture below is the reporter's: four documents, one left alone, one
deleted, one modified at IDENTICAL byte length, one truncated. The same-length
modification matters -- it rules out a size check passing for the wrong reason.
"""

import pytest

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.verify_index import verify_index


CANARY = "ORIGINAL-CANARY-0001"
CHANGED = "MODIFIED-CANARY-9999"  # same length as CANARY, deliberately


@pytest.fixture
def indexed(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for stem in ("healthy", "deleted", "modified", "truncated"):
        (src / f"{stem}.md").write_text(
            f"# {stem.title()}\n\nbody text for {stem} with {CANARY} inside it\n",
            encoding="utf-8",
        )
    store = tmp_path / "store"
    out = index_local(
        path=str(src), name="fixture",
        storage_path=str(store), use_ai_summaries=False, use_embeddings=False,
    )
    assert out.get("success"), out
    return {"src": src, "storage": str(store), "repo": out["repo"]}


def _mutate(src):
    """Reporter's four cases, applied without reindexing."""
    (src / "deleted.md").unlink()
    m = src / "modified.md"
    m.write_text(m.read_text(encoding="utf-8").replace(CANARY, CHANGED), encoding="utf-8")
    assert len(CANARY) == len(CHANGED), "same-length modification is the point"
    (src / "truncated.md").write_text("# Trunc\n", encoding="utf-8")


class TestDefaultIsUnchangedAndDisclosed:
    def test_default_stays_clean_after_source_mutation(self, indexed):
        """Documented behaviour, not a silent one. The 1.x contract is why the
        default did not flip."""
        _mutate(indexed["src"])
        r = verify_index(indexed["repo"], storage_path=indexed["storage"])
        assert r["drift_count"] == 0
        assert r["missing_count"] == 0

    def test_default_names_its_layer(self, indexed):
        r = verify_index(indexed["repo"], storage_path=indexed["storage"])
        assert r["_meta"]["verify_layer"] == "cache"

    def test_default_states_it_is_not_proof_the_source_is_current(self, indexed):
        """The reported harm was a caller reading `clean` as source freshness."""
        r = verify_index(indexed["repo"], storage_path=indexed["storage"])
        assert "NOT proof the source is current" in r["_meta"]["verifies"]


class TestLiveLayerCatchesWhatCacheCannot:
    def test_deleted_source_is_missing(self, indexed):
        _mutate(indexed["src"])
        r = verify_index(indexed["repo"], storage_path=indexed["storage"], source="live")
        paths = {m["doc_path"] for m in r["missing_sections"]}
        assert any("deleted.md" in p for p in paths), r

    def test_same_length_modification_is_drift(self, indexed):
        """A size check would pass here; a hash must not."""
        _mutate(indexed["src"])
        r = verify_index(indexed["repo"], storage_path=indexed["storage"], source="live")
        paths = {d["doc_path"] for d in r["drift_sections"]}
        assert any("modified.md" in p for p in paths), r

    def test_truncated_source_is_reported(self, indexed):
        _mutate(indexed["src"])
        r = verify_index(indexed["repo"], storage_path=indexed["storage"], source="live")
        touched = {d["doc_path"] for d in r["drift_sections"]}
        touched |= {m["doc_path"] for m in r["missing_sections"]}
        assert any("truncated.md" in p for p in touched), r

    def test_untouched_source_stays_clean(self, indexed):
        """Control: live must not flag everything."""
        _mutate(indexed["src"])
        r = verify_index(indexed["repo"], storage_path=indexed["storage"], source="live")
        flagged = {d["doc_path"] for d in r["drift_sections"]}
        flagged |= {m["doc_path"] for m in r["missing_sections"]}
        assert not any("healthy.md" in p for p in flagged), r

    def test_live_on_an_unmutated_tree_is_clean(self, indexed):
        """Stronger control: before any mutation, live and cache must agree."""
        r = verify_index(indexed["repo"], storage_path=indexed["storage"], source="live")
        assert r["drift_count"] == 0 and r["missing_count"] == 0, r

    def test_live_names_its_layer(self, indexed):
        r = verify_index(indexed["repo"], storage_path=indexed["storage"], source="live")
        assert r["_meta"]["verify_layer"] == "live"
        assert r["_meta"]["source_root"]


class TestLiveRefusesRatherThanFallingBack:
    def test_no_source_root_reports_a_reason(self, indexed, monkeypatch):
        """Falling back to the cache would answer the cache question under the
        live label, which is exactly the confusion being fixed."""
        import jdocmunch_mcp.tools.verify_index as vi
        real = vi.DocStore.load_index

        def _no_root(self, owner, name):
            idx = real(self, owner, name)
            if idx is not None:
                idx.source_root = ""
            return idx

        monkeypatch.setattr(vi.DocStore, "load_index", _no_root)
        r = verify_index(indexed["repo"], storage_path=indexed["storage"], source="live")
        assert r["drift_count"] == 0
        assert r["missing_count"] == r["section_count"]
        assert {m["reason"] for m in r["missing_sections"]} == {"no_source_root"}


class TestInvalidSource:
    @pytest.mark.parametrize("bad", ["git", "workspace", "CACHE!", "1"])
    def test_rejected_explicitly(self, indexed, bad):
        r = verify_index(indexed["repo"], storage_path=indexed["storage"], source=bad)
        assert "error" in r and "source must be" in r["error"]

    @pytest.mark.parametrize("ok", ["cache", "CACHE", " live ", "Live"])
    def test_case_and_whitespace_tolerated(self, indexed, ok):
        r = verify_index(indexed["repo"], storage_path=indexed["storage"], source=ok)
        assert "error" not in r
        assert r["_meta"]["verify_layer"] == ok.strip().lower()


def test_counters_still_sum(indexed):
    """The stated invariant, on both layers."""
    for src in ("cache", "live"):
        r = verify_index(indexed["repo"], storage_path=indexed["storage"], source=src)
        total = (r["clean_count"] + r["drift_count"] + r["missing_count"]
                 + r["error_count"] + r["skipped_count"])
        assert total == r["section_count"], (src, r)
