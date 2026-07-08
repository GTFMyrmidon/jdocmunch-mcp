"""v1.94.0 — floke75 large-corpus stability cluster (#75, #76, #77).

#75  embedding vectors live only in the .embeddings.jsonl sidecar, never inline
     in the monolith; the monolith is written with compact separators; vectors
     rehydrate lazily (as array('f')) on first semantic use.
#76  the PostToolUse auto-reindex is throttled: per-file debounce + a global
     concurrency cap enforced by the spawned hook-reindex worker.
#77  list_repos reads a tiny per-index summary sidecar instead of json-parsing
     the whole monolith for two len()s.
"""

import io
import json
import os
from array import array
from unittest import mock

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.storage.doc_store import DocStore, _load_sidecar_vectors


SAMPLE_MD = """# Guide

## Authentication

To sign in users, configure OAuth 2.0 with your provider.

## Payments

We use Stripe for credit card processing.

## Notifications

Email alerts go through SendGrid.
"""

_VECS = {
    "Authentication": [1.0, 0.0, 0.0],
    "Payments": [0.0, 1.0, 0.0],
    "Notifications": [0.0, 0.0, 1.0],
}


def _make_index(tmp_path, with_embeddings=True, name="corpus"):
    store = DocStore(base_path=str(tmp_path))
    sections = parse_file(SAMPLE_MD, "README.md", "test/repo")
    if with_embeddings:
        for sec in sections:
            sec.embedding = _VECS.get(sec.title, [0.33, 0.33, 0.33])
    store.save_index(
        owner="local", name=name, sections=sections,
        raw_files={"README.md": SAMPLE_MD}, doc_types={".md": 1},
    )
    return store


# ---------------------------------------------------------------------------
# #75 — vectors out of the monolith
# ---------------------------------------------------------------------------

def test_monolith_has_no_inline_vectors(tmp_path):
    _make_index(tmp_path)
    raw = (tmp_path / "local" / "corpus.json").read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["sections"], "sections should be present"
    assert all("embedding" not in s for s in data["sections"])
    assert '"embedding"' not in raw


def test_monolith_is_compact(tmp_path):
    _make_index(tmp_path)
    raw = (tmp_path / "local" / "corpus.json").read_text(encoding="utf-8")
    # indent=2 would produce newline+space runs; compact separators do not.
    assert "\n  " not in raw
    assert ", " not in raw  # separators=(",", ":")


def test_sidecar_written_as_safety_net(tmp_path):
    # Embeddings set outside embed_sections (no sidecar yet) must still be
    # recoverable: save writes one so the strip is lossless.
    _make_index(tmp_path)
    assert (tmp_path / "local" / "corpus.embeddings.jsonl").exists()


def test_has_embeddings_after_load_from_stripped_monolith(tmp_path):
    store = _make_index(tmp_path)
    index = store.load_index("local", "corpus")
    # Monolith carries no inline vectors, but the sidecar means they exist.
    assert not any(s.get("embedding") for s in index.sections)
    assert index._has_embeddings() is True


def test_semantic_search_rehydrates_from_sidecar(tmp_path):
    store = _make_index(tmp_path)
    index = store.load_index("local", "corpus")
    with mock.patch("jdocmunch_mcp.storage.doc_store.embed_query", return_value=[0.0, 1.0, 0.0]):
        results = index.search("anything", max_results=3, semantic_only=True)
    assert results
    assert results[0]["title"] == "Payments"


def test_rehydrated_vectors_are_float_array(tmp_path):
    store = _make_index(tmp_path)
    index = store.load_index("local", "corpus")
    index._rehydrate_embeddings()
    embs = [s.get("embedding") for s in index.sections if s.get("embedding")]
    assert embs
    assert all(isinstance(e, array) and e.typecode == "f" for e in embs)


def test_no_embeddings_means_no_sidecar_and_no_strip(tmp_path):
    store = _make_index(tmp_path, with_embeddings=False, name="plain")
    assert not (tmp_path / "local" / "plain.embeddings.jsonl").exists()
    index = store.load_index("local", "plain")
    assert index._has_embeddings() is False


def test_load_sidecar_vectors_strips_embed_version_salt(tmp_path):
    sidecar = tmp_path / "s.jsonl"
    sidecar.write_text(
        json.dumps({"_header": True, "provider": "p", "model": "m", "dim": 3}) + "\n"
        + json.dumps({"hash": "abc123#pv1", "vector": [1.0, 2.0, 3.0]}) + "\n",
        encoding="utf-8",
    )
    out = _load_sidecar_vectors(str(sidecar))
    assert "abc123" in out  # salt stripped
    assert "abc123#pv1" not in out
    assert isinstance(out["abc123"], array)


def test_incremental_save_keeps_vectors_out_of_monolith(tmp_path):
    store = _make_index(tmp_path)
    # Add a new file incrementally.
    new_secs = parse_file("# Extra\n\nMore docs here.\n", "EXTRA.md", "test/repo")
    for sec in new_secs:
        sec.embedding = [0.5, 0.5, 0.5]
    store.incremental_save(
        owner="local", name="corpus",
        changed_files=[], new_files=["EXTRA.md"], deleted_files=[],
        new_sections=new_secs, raw_files={"EXTRA.md": "# Extra\n\nMore docs here.\n"},
        doc_types={".md": 2},
    )
    data = json.loads((tmp_path / "local" / "corpus.json").read_text(encoding="utf-8"))
    assert all("embedding" not in s for s in data["sections"])


# ---------------------------------------------------------------------------
# #77 — list_repos summary sidecar
# ---------------------------------------------------------------------------

def test_summary_sidecar_written(tmp_path):
    _make_index(tmp_path)
    summary_path = tmp_path / "local" / "corpus.summary.json"
    assert summary_path.exists()
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    assert s["repo"] == "local/corpus"
    assert s["section_count"] >= 1
    assert s["doc_count"] == 1


def test_list_repos_reads_summary_not_monolith(tmp_path):
    store = _make_index(tmp_path)
    # Corrupt the monolith but keep a valid summary: list_repos must still work,
    # proving it read the summary and never parsed the monolith.
    (tmp_path / "local" / "corpus.json").write_text("{ this is not json", encoding="utf-8")
    rows = store.list_repos()
    assert len(rows) == 1
    assert rows[0]["repo"] == "local/corpus"
    assert rows[0]["doc_count"] == 1


def test_list_repos_falls_back_to_full_parse(tmp_path):
    store = _make_index(tmp_path)
    (tmp_path / "local" / "corpus.summary.json").unlink()
    rows = store.list_repos()
    assert len(rows) == 1
    assert rows[0]["repo"] == "local/corpus"


def test_list_repos_skips_summary_files(tmp_path):
    store = _make_index(tmp_path)
    rows = store.list_repos()
    # Exactly one row — the .summary.json sidecar is not mistaken for an index.
    assert [r["repo"] for r in rows] == ["local/corpus"]


def test_delete_index_removes_summary(tmp_path):
    store = _make_index(tmp_path)
    store.delete_index("local", "corpus")
    assert not (tmp_path / "local" / "corpus.summary.json").exists()


# ---------------------------------------------------------------------------
# #76 — PostToolUse reindex throttle
# ---------------------------------------------------------------------------

def test_debounce_leading_edge(tmp_path, monkeypatch):
    from jdocmunch_mcp.cli import hooks
    monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path))
    monkeypatch.setenv("JDOCMUNCH_HOOK_DEBOUNCE_SECONDS", "60")
    p = str(tmp_path / "a.md")
    assert hooks._should_reindex(p) is True   # first: stamps + proceeds
    assert hooks._should_reindex(p) is False  # within window: coalesced


def test_debounce_disabled_when_zero(tmp_path, monkeypatch):
    from jdocmunch_mcp.cli import hooks
    monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path))
    monkeypatch.setenv("JDOCMUNCH_HOOK_DEBOUNCE_SECONDS", "0")
    p = str(tmp_path / "b.md")
    assert hooks._should_reindex(p) is True
    assert hooks._should_reindex(p) is True


def test_concurrency_cap_slots(tmp_path, monkeypatch):
    from jdocmunch_mcp.cli import hooks
    monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path))
    monkeypatch.setenv("JDOCMUNCH_HOOK_MAX_REINDEX", "1")
    fd1 = hooks._acquire_reindex_slot()
    try:
        # With a single slot held, the next worker is over cap.
        assert fd1 not in (None,)
        if fd1 != -1:  # locking primitive available on this platform
            assert hooks._acquire_reindex_slot() is None
    finally:
        hooks._release_reindex_slot(fd1)


def test_hook_reindex_over_cap_skips_load(tmp_path, monkeypatch):
    from jdocmunch_mcp.cli import hooks
    monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path))
    monkeypatch.setenv("JDOCMUNCH_HOOK_MAX_REINDEX", "1")
    p = tmp_path / "doc.md"
    p.write_text("hello", encoding="utf-8")
    fd = hooks._acquire_reindex_slot()
    try:
        if fd == -1:
            return  # no locking primitive; cap not enforceable here
        with mock.patch("jdocmunch_mcp.tools.index_file.index_file_cli") as m:
            assert hooks.run_hook_reindex(str(p)) == 0
            m.assert_not_called()  # over cap: never loaded the index
    finally:
        hooks._release_reindex_slot(fd)


def test_hook_reindex_runs_when_slot_free(tmp_path, monkeypatch):
    from jdocmunch_mcp.cli import hooks
    monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path))
    p = tmp_path / "doc.md"
    p.write_text("hello", encoding="utf-8")
    with mock.patch("jdocmunch_mcp.tools.index_file.index_file_cli", return_value={"success": True}) as m:
        assert hooks.run_hook_reindex(str(p)) == 0
        m.assert_called_once()


def test_hook_reindex_ignores_non_doc(tmp_path, monkeypatch):
    from jdocmunch_mcp.cli import hooks
    monkeypatch.setenv("DOC_INDEX_PATH", str(tmp_path))
    with mock.patch("jdocmunch_mcp.tools.index_file.index_file_cli") as m:
        assert hooks.run_hook_reindex(str(tmp_path / "app.py")) == 0
        m.assert_not_called()


def test_hook_reindex_dispatch(tmp_path):
    from jdocmunch_mcp.server import main
    with mock.patch("jdocmunch_mcp.cli.hooks.run_hook_reindex", return_value=0) as m:
        try:
            main(["hook-reindex", str(tmp_path / "doc.md")])
        except SystemExit:
            pass
        m.assert_called_once()
