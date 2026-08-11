"""jdoc#117: the derived sidecars refresh on the INCREMENTAL path too.

All four sidecars (glossary / related-graph / boilerplate / near-duplicate) were
written on the full-index path only, three of them behind a bare
``except Exception: pass``. Two invisible consequences:

1. an incrementally-refreshed index kept whatever sidecars the last FULL index
   produced, so ``get_related_sections`` answered from an arbitrarily old
   corpus. Staleness was unbounded and nothing reported it, because the
   incremental path never attempted the write;
2. a genuine failure on the full path was indistinguishable from a clean
   result -- #103's argument for the dedup sidecar, which applies to all four.

⚠ The hazard in the FIX is content. Persisted section dicts carry no body text
(``Section.to_dict`` drops it), so rebuilding from them naively would write
EMPTY sidecars -- a silent wipe, strictly worse than the staleness. The
glossary assertions below exist to catch exactly that: they check that terms
from UNTOUCHED documents survive an incremental refresh, which is only possible
if the body text was re-read through the byte-range loader.

⚠ Corpus size matters. Under ~5 documents the incremental path re-materializes
everything anyway and the bug hides (the jdoc#107 lesson), so these fixtures are
sized so the refresh genuinely skips untouched files.
"""

import json
import os
import time

import pytest

from jdocmunch_mcp.tools.index_local import (
    _sidecar_view,
    _write_sidecars,
    index_local,
)

_SIDECARS = ("terms", "related", "boilerplate", "duplicates")


def _write_corpus(root, n=12):
    for i in range(n):
        with open(os.path.join(root, f"doc{i}.md"), "w", encoding="utf-8") as fh:
            fh.write(
                f"# Doc {i}\n\n**Alpha{i}** - a defined term for the glossary.\n\n"
                + "Shared boilerplate footer line.\n" * 3
            )


def _sidecar_stat(store, name):
    out = {}
    for kind in _SIDECARS:
        path = os.path.join(store, "local", f"{name}.{kind}.json")
        out[kind] = os.path.getmtime(path) if os.path.exists(path) else None
    return out


def _glossary_terms(store, name):
    path = os.path.join(store, "local", f"{name}.terms.json")
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    return {e["term"] for e in payload.get("entries", [])}


@pytest.fixture()
def corpus(tmp_path):
    root = tmp_path / "corpus"
    store = tmp_path / "store"
    root.mkdir()
    store.mkdir()
    _write_corpus(str(root))
    return str(root), str(store)


class TestIncrementalRefreshesSidecars:
    def test_every_sidecar_is_rewritten_on_an_incremental_refresh(self, corpus):
        root, store = corpus
        index_local(path=root, name="s117", use_ai_summaries=False,
                    use_embeddings=False, storage_path=store)
        before = _sidecar_stat(store, "s117")
        assert all(before[k] is not None for k in _SIDECARS), before

        time.sleep(1.1)  # coarse mtime granularity on some filesystems
        with open(os.path.join(root, "doc0.md"), "a", encoding="utf-8") as fh:
            fh.write("\n**Zeta0** - a new term added by the edit.\n")

        result = index_local(path=root, name="s117", use_ai_summaries=False,
                             use_embeddings=False, storage_path=store)
        assert result.get("incremental") is True, "fixture must take the incremental path"

        after = _sidecar_stat(store, "s117")
        stale = [k for k in _SIDECARS if after[k] <= before[k]]
        assert not stale, f"sidecars not refreshed on the incremental path: {stale}"

    def test_untouched_documents_keep_their_glossary_terms(self, corpus):
        """The content-hydration guard. An empty rebuild would pass a naive
        mtime check and destroy the sidecar; this is what actually pins it."""
        root, store = corpus
        index_local(path=root, name="s117", use_ai_summaries=False,
                    use_embeddings=False, storage_path=store)
        before = _glossary_terms(store, "s117")
        assert "Alpha7" in before

        with open(os.path.join(root, "doc0.md"), "a", encoding="utf-8") as fh:
            fh.write("\n**Zeta0** - a new term added by the edit.\n")
        index_local(path=root, name="s117", use_ai_summaries=False,
                    use_embeddings=False, storage_path=store)

        after = _glossary_terms(store, "s117")
        assert "Zeta0" in after, "the edited document's new term is missing"
        assert "Alpha7" in after, (
            "an untouched document's term was dropped - the rebuild ran on "
            "contentless section dicts"
        )
        assert before - {"Zeta0"} <= after

    def test_related_graph_covers_the_whole_corpus_not_just_changed_files(self, corpus):
        root, store = corpus
        index_local(path=root, name="s117", use_ai_summaries=False,
                    use_embeddings=False, storage_path=store)
        with open(os.path.join(root, "doc0.md"), "a", encoding="utf-8") as fh:
            fh.write("\nAn edit.\n")
        index_local(path=root, name="s117", use_ai_summaries=False,
                    use_embeddings=False, storage_path=store)

        with open(os.path.join(store, "local", "s117.related.json"), encoding="utf-8") as fh:
            payload = json.load(fh)
        # One changed file must not collapse the graph to that file's sections.
        assert payload["section_count"] > 12, payload["section_count"]


class TestSidecarFailuresAreReported:
    def test_a_failing_sidecar_is_named_rather_than_swallowed(self, tmp_path, monkeypatch):
        import jdocmunch_mcp.retrieval.boilerplate as bp

        def _boom(*a, **k):
            raise RuntimeError("disk on fire")

        monkeypatch.setattr(bp, "write", _boom)
        skips = _write_sidecars(str(tmp_path), "local", "s117", [])
        assert "boilerplate" in skips
        assert skips["boilerplate"]["reason"] == "error"
        assert "disk on fire" in skips["boilerplate"]["detail"]

    def test_a_clean_run_reports_no_skips(self, tmp_path):
        assert _write_sidecars(str(tmp_path), "local", "s117", []) == {}

    def test_index_response_discloses_a_skipped_sidecar(self, corpus, monkeypatch):
        import jdocmunch_mcp.retrieval.boilerplate as bp

        root, store = corpus
        monkeypatch.setattr(bp, "write", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("nope")))
        result = index_local(path=root, name="s117", use_ai_summaries=False,
                             use_embeddings=False, storage_path=store)
        assert "boilerplate" in result.get("sidecars_skipped", {})


class TestSidecarView:
    def test_section_objects_carry_their_content_through(self):
        class _S:
            content = "body text"
            embedding = [0.1, 0.2]

            def to_dict(self):
                return {"id": "a", "title": "A"}

        rows = _sidecar_view([_S()])
        assert rows[0]["content"] == "body text"
        assert rows[0]["embedding"] == [0.1, 0.2]

    def test_contentless_dicts_are_hydrated_through_the_loader(self):
        rows = _sidecar_view(
            [{"id": "a", "doc_path": "d.md", "byte_start": 0, "byte_end": 4}],
            content_for=lambda sec: "hello",
        )
        assert rows[0]["content"] == "hello"

    def test_existing_content_is_not_re_read(self):
        rows = _sidecar_view(
            [{"id": "a", "content": "already here"}],
            content_for=lambda sec: "SHOULD NOT BE USED",
        )
        assert rows[0]["content"] == "already here"

    def test_a_loader_failure_degrades_to_empty_not_an_exception(self):
        rows = _sidecar_view(
            [{"id": "a"}],
            content_for=lambda sec: None,
        )
        assert rows[0]["content"] == ""
