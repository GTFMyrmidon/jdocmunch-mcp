"""jdoc#107 — a partial embed pass must not destroy vectors it wasn't handed.

Reported by @faxik: an incremental `index_local` refresh took a real corpus's
sidecar from **5,316 vectors to 21**, then 224, then 48 on a second index.
Exit 0, no warning, `search_sections` still "worked".

⚠⚠ **It is not a cache. Since jdoc#75 the sidecar is the AUTHORITATIVE vector
store** — `_index_to_dict` strips `embedding` from the monolith and
`_rehydrate_embeddings` reads the vectors back from
`<name>.embeddings.jsonl`. So this is data loss, not a cost regression, and
the reporter (following our own "cache" naming) under-stated it.

`embed_sections` rewrote the sidecar from the `sections` it was handed, and
`cache.write` is documented as an atomic REWRITE. Correct on the full-index
path, where that is the whole corpus. On an incremental refresh it is only the
changed documents' sections.

⚠ It does not reproduce on a toy corpus: with a handful of documents the
incremental path re-materializes everything anyway, so the rewrite happens to
contain the corpus and looks right. The fixtures here are sized so the
incremental pass genuinely skips untouched documents.

Two more sites in the same family, found while confirming the report and
neither of them filed:

  - `index_file` called `embed_sections` with NO owner/name, so the cache was
    disabled and its vectors reached the sidecar only via the safety net —
    which bails when a sidecar already exists. ⚠⚠ That is the PostToolUse
    auto-reindex path, so it fired on every single doc edit.
  - `_ensure_sidecar_from_sections` returned early whenever the sidecar
    existed, so it could never repair or extend one.
"""

import json

import pytest

from jdocmunch_mcp.embeddings import cache as emb_cache
from jdocmunch_mcp.embeddings import provider as emb_provider


DIM = 8


class _FakeProvider:
    """Deterministic embeddings; counts how many texts it was asked for."""

    def __init__(self):
        self.calls = 0
        self.texts = []

    def embed_texts(self, texts, task_type=None):
        self.calls += 1
        self.texts.extend(texts)
        out = []
        for t in texts:
            seed = float(len(t) % 7) + 1.0
            out.append([seed] * DIM)
        return out


class _Section:
    """Minimal stand-in for parser.Section as embed_sections sees it."""

    def __init__(self, hash_, title="T", content="body", summary=""):
        self.content_hash = hash_
        self.title = title
        self.content = content
        self.summary = summary
        self.embedding = None


@pytest.fixture
def fake_provider(monkeypatch):
    prov = _FakeProvider()
    monkeypatch.setattr(emb_provider, "_get_provider", lambda: prov)
    monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake")
    monkeypatch.setattr(emb_provider, "_provider_identity", lambda n: ("fake-model", DIM))
    return prov


def _read_sidecar(tmp_path, owner="local", name="corpus"):
    """Return {bare_hash: vector} plus the header, straight off disk."""
    path = emb_cache._cache_path(str(tmp_path), owner, name)
    header = None
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        entry = json.loads(raw)
        if entry.get("_header") is True:
            header = entry
            continue
        out[entry["hash"].rsplit("#", 1)[0]] = entry["vector"]
    return header, out


def _embed(sections, tmp_path, **kwargs):
    return emb_provider.embed_sections(
        sections, owner="local", name="corpus", storage_path=str(tmp_path), **kwargs
    )


# ---------------------------------------------------------------------------
# The reported collapse
# ---------------------------------------------------------------------------

def test_incremental_pass_preserves_untouched_vectors(tmp_path, fake_provider):
    """The #107 repro, scaled down: 50 sections, then a 2-section refresh."""
    corpus = [_Section(f"h{i:03d}") for i in range(50)]
    _embed(corpus, tmp_path)
    _, before = _read_sidecar(tmp_path)
    assert len(before) == 50

    # Incremental refresh: only two documents changed.
    _embed([_Section("h007"), _Section("h042")], tmp_path)

    _, after = _read_sidecar(tmp_path)
    assert len(after) == 50, (
        f"sidecar collapsed to {len(after)} vectors — this is jdoc#107"
    )
    assert set(after) == set(before)


def test_incremental_pass_adds_new_sections_without_dropping_old(tmp_path, fake_provider):
    _embed([_Section(f"h{i:03d}") for i in range(20)], tmp_path)
    _embed([_Section("new001"), _Section("new002")], tmp_path)
    _, after = _read_sidecar(tmp_path)
    assert len(after) == 22
    assert "new001" in after and "h000" in after


def test_re_embedding_the_same_hash_does_not_duplicate_the_entry(tmp_path, fake_provider):
    """The merge is keyed, not appended — a repeat pass keeps ONE row.

    ⚠ The vector is unchanged on purpose: the key IS the content hash, so an
    identical hash is a cache hit by definition. Edited content produces a
    different hash and therefore a different key (covered above).
    """
    _embed([_Section("h1", content="short")], tmp_path)
    _, before = _read_sidecar(tmp_path)
    _embed([_Section("h1", content="short")], tmp_path)
    _, after = _read_sidecar(tmp_path)
    assert len(after) == 1
    assert after["h1"] == before["h1"]


def test_untouched_sections_are_not_re_embedded(tmp_path, fake_provider):
    """The merge must not cost a re-embed — cache hits still short-circuit."""
    corpus = [_Section(f"h{i:03d}") for i in range(20)]
    _embed(corpus, tmp_path)
    calls_after_first = fake_provider.calls

    _embed([_Section("h005")], tmp_path)
    assert fake_provider.calls == calls_after_first, "cache hit should skip the provider"


# ---------------------------------------------------------------------------
# prune=True — the full-index path stays authoritative
# ---------------------------------------------------------------------------

def test_full_index_prunes_vectors_for_deleted_sections(tmp_path, fake_provider):
    _embed([_Section(f"h{i:03d}") for i in range(10)], tmp_path)
    survivors = [_Section("h000"), _Section("h001")]
    _embed(survivors, tmp_path, prune=True)
    _, after = _read_sidecar(tmp_path)
    assert set(after) == {"h000", "h001"}


def test_merge_is_the_default(tmp_path, fake_provider):
    """⚠ Defaulting to prune would make every unconverted caller lose data."""
    import inspect

    sig = inspect.signature(emb_provider.embed_sections)
    assert sig.parameters["prune"].default is False


# ---------------------------------------------------------------------------
# Identity rotation must still purge — merging cannot resurrect stale vectors
# ---------------------------------------------------------------------------

def test_provider_rotation_still_purges(tmp_path, monkeypatch):
    prov = _FakeProvider()
    monkeypatch.setattr(emb_provider, "_get_provider", lambda: prov)
    monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake")
    monkeypatch.setattr(emb_provider, "_provider_identity", lambda n: ("model-a", DIM))
    _embed([_Section(f"h{i}") for i in range(5)], tmp_path)

    # Rotate the model: cache.load returns {} on identity mismatch, so the
    # merge has nothing to carry forward and the sidecar is rebuilt clean.
    monkeypatch.setattr(emb_provider, "_provider_identity", lambda n: ("model-b", DIM))
    _embed([_Section("h0")], tmp_path)

    header, after = _read_sidecar(tmp_path)
    assert header["model"] == "model-b"
    assert set(after) == {"h0"}, "stale-identity vectors must not survive rotation"


# ---------------------------------------------------------------------------
# cache.append_entries — the safety net can extend an existing sidecar
# ---------------------------------------------------------------------------

def test_append_entries_creates_with_the_given_identity(tmp_path):
    emb_cache.append_entries(
        str(tmp_path), "local", "corpus",
        entries=[("a#pv1", [1.0] * DIM)],
        identity_if_new=("prov", "model", DIM),
    )
    header, out = _read_sidecar(tmp_path)
    assert header["provider"] == "prov"
    assert set(out) == {"a"}


def test_append_entries_keeps_the_existing_header_and_rows(tmp_path):
    emb_cache.write(
        str(tmp_path), "local", "corpus",
        provider="real", model="real-model", dim=DIM,
        entries=[("a#pv1", [1.0] * DIM)],
    )
    emb_cache.append_entries(
        str(tmp_path), "local", "corpus",
        entries=[("b#pv1", [2.0] * DIM)],
        identity_if_new=("__inline__", "__inline__", None),
    )
    header, out = _read_sidecar(tmp_path)
    assert header["provider"] == "real", "an existing identity must not be clobbered"
    assert set(out) == {"a", "b"}


def test_append_entries_skips_keys_already_present(tmp_path):
    emb_cache.write(
        str(tmp_path), "local", "corpus",
        provider="real", model="m", dim=DIM,
        entries=[("a#pv1", [1.0] * DIM)],
    )
    emb_cache.append_entries(
        str(tmp_path), "local", "corpus",
        entries=[("a#pv1", [9.0] * DIM)],
        identity_if_new=("x", "y", None),
    )
    _, out = _read_sidecar(tmp_path)
    assert out["a"] == [1.0] * DIM


# ---------------------------------------------------------------------------
# stored_hashes — the coverage signal the reporter had to build externally
# ---------------------------------------------------------------------------

def test_stored_hashes_returns_bare_hashes(tmp_path):
    emb_cache.write(
        str(tmp_path), "local", "corpus",
        provider="p", model="m", dim=DIM,
        entries=[("a#pv1", [1.0] * DIM), ("b#pv1", [2.0] * DIM)],
    )
    assert emb_cache.stored_hashes(str(tmp_path), "local", "corpus") == {"a", "b"}


def test_stored_hashes_is_empty_for_a_missing_sidecar(tmp_path):
    assert emb_cache.stored_hashes(str(tmp_path), "local", "nope") == set()


def test_stored_hashes_ignores_the_header_and_corrupt_lines(tmp_path):
    path = emb_cache._cache_path(str(tmp_path), "local", "corpus")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"_header": True, "provider": "p", "model": "m", "dim": DIM}) + "\n"
        + json.dumps({"hash": "a#pv1", "vector": [1.0]}) + "\n"
        + "{not json\n"
        + json.dumps({"hash": "b#pv1", "vector": [2.0]}) + "\n",
        encoding="utf-8",
    )
    assert emb_cache.stored_hashes(str(tmp_path), "local", "corpus") == {"a", "b"}


# ---------------------------------------------------------------------------
# End to end, through the real tools
# ---------------------------------------------------------------------------

def _write_corpus(root, n_docs, marker="original"):
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_docs):
        (root / f"doc{i:02d}.md").write_text(
            f"# Doc {i}\n\n## Alpha {i}\n\n{marker} body for alpha {i}.\n"
            f"\n## Beta {i}\n\n{marker} body for beta {i}.\n",
            encoding="utf-8",
        )


@pytest.fixture
def indexing_env(tmp_path, monkeypatch):
    """A real folder plus a fake embedding provider wired into index_local."""
    prov = _FakeProvider()
    monkeypatch.setattr(emb_provider, "_get_provider", lambda: prov)
    monkeypatch.setattr(emb_provider, "get_provider_name", lambda: "fake")
    monkeypatch.setattr(emb_provider, "_provider_identity", lambda n: ("fake-model", DIM))
    src = tmp_path / "corpus"
    store = tmp_path / "store"
    return prov, src, store


def test_index_local_incremental_keeps_the_sidecar(indexing_env):
    """The reported failure, driven through `index_local` end to end."""
    from jdocmunch_mcp.tools.index_local import index_local

    prov, src, store = indexing_env
    _write_corpus(src, 30)

    full = index_local(
        path=str(src), name="corpus", storage_path=str(store),
        use_embeddings=True, use_ai_summaries=False, incremental=False,
    )
    assert full["success"]
    before = emb_cache.stored_hashes(str(store), "local", "corpus")
    # ⚠ Not section_count: sections are keyed by CONTENT hash, and the 30
    # empty `# Doc i` parents share one. Coverage is the honest measure.
    assert full["embedding_coverage"] == 1.0
    assert len(before) > 30

    # Touch exactly one document, then refresh incrementally.
    (src / "doc07.md").write_text(
        "# Doc 7\n\n## Alpha 7\n\nEDITED body.\n", encoding="utf-8"
    )
    inc = index_local(
        path=str(src), name="corpus", storage_path=str(store),
        use_embeddings=True, use_ai_summaries=False, incremental=True,
    )
    assert inc["success"] and inc["incremental"] is True

    after = emb_cache.stored_hashes(str(store), "local", "corpus")
    assert len(after) >= len(before) - 2, (
        f"sidecar went {len(before)} -> {len(after)} vectors — jdoc#107"
    )
    assert inc["embedding_coverage"] == 1.0
    assert inc["embedded_sections"] == inc["section_count"]
    assert "warnings" not in inc or not any(
        "coverage" in w for w in inc["warnings"]
    )


def test_index_local_full_reindex_reports_coverage(indexing_env):
    from jdocmunch_mcp.tools.index_local import index_local

    prov, src, store = indexing_env
    _write_corpus(src, 10)
    full = index_local(
        path=str(src), name="corpus", storage_path=str(store),
        use_embeddings=True, use_ai_summaries=False, incremental=False,
    )
    assert full["embedded_sections"] == full["section_count"]
    assert full["embedding_coverage"] == 1.0


def test_lexical_index_grows_no_coverage_fields(tmp_path):
    """An index with no sidecar must not report 0.0 — that reads as a loss."""
    from jdocmunch_mcp.tools.index_local import index_local

    src = tmp_path / "corpus"
    _write_corpus(src, 3)
    out = index_local(
        path=str(src), name="lexonly", storage_path=str(tmp_path / "store"),
        use_embeddings=False, use_ai_summaries=False, incremental=False,
    )
    assert out["success"]
    assert "embedding_coverage" not in out
    assert "embedded_sections" not in out


def test_index_file_persists_its_vectors(indexing_env):
    """index_file embedded sections whose vectors were then thrown away."""
    from jdocmunch_mcp.tools.index_file import index_file
    from jdocmunch_mcp.tools.index_local import index_local

    prov, src, store = indexing_env
    _write_corpus(src, 12)
    index_local(
        path=str(src), name="corpus", storage_path=str(store),
        use_embeddings=True, use_ai_summaries=False, incremental=False,
    )
    before = emb_cache.stored_hashes(str(store), "local", "corpus")

    target = src / "doc03.md"
    target.write_text(
        "# Doc 3\n\n## Gamma 3\n\nBrand new section body.\n", encoding="utf-8"
    )
    out = index_file(file_path=str(target), storage_path=str(store))
    assert out["success"], out

    after = emb_cache.stored_hashes(str(store), "local", "corpus")
    new_keys = after - before
    assert new_keys, "index_file's new vectors never reached the sidecar — jdoc#107"
    assert out["embedded_sections"] == out["total_sections"]


def test_search_still_ranks_after_an_incremental_refresh(indexing_env):
    """The user-visible symptom: semantic ranking must survive a refresh.

    ⚠ Verified at the ENTRY POINT, not at the sidecar. The sidecar count is
    the mechanism; what the reporter actually lost was retrieval.
    """
    from jdocmunch_mcp.tools.index_local import index_local
    from jdocmunch_mcp.tools.search_sections import search_sections

    prov, src, store = indexing_env
    _write_corpus(src, 30)
    index_local(
        path=str(src), name="corpus", storage_path=str(store),
        use_embeddings=True, use_ai_summaries=False, incremental=False,
    )
    (src / "doc07.md").write_text(
        "# Doc 7\n\n## Alpha 7\n\nEDITED body.\n", encoding="utf-8"
    )
    index_local(
        path=str(src), name="corpus", storage_path=str(store),
        use_embeddings=True, use_ai_summaries=False, incremental=True,
    )

    store_obj = __import__(
        "jdocmunch_mcp.storage", fromlist=["DocStore"]
    ).DocStore(base_path=str(store))
    index = store_obj.load_index("local", "corpus")
    covered = index._embedded_section_count()
    assert covered == len(index.sections), (
        f"only {covered}/{len(index.sections)} sections can rank semantically"
    )

    res = search_sections(
        repo="local/corpus", query="alpha body",
        storage_path=str(store),
    )
    assert res["_meta"]["search_mode"] == "hybrid"
