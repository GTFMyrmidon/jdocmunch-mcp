"""Tests for v1.59.1: strip raw embedding vectors from per-section responses
(issue #11).

The 384-dim float vector serves the internal semantic-search pipeline only;
returning it through get_section / get_sections / describe_section /
get_section_summary / get_section_summaries inflates responses by ~2,000
tokens per section with no consumer value.
"""

from __future__ import annotations

import textwrap

from jdocmunch_mcp.tools.describe_section import describe_section
from jdocmunch_mcp.tools.get_section import get_section
from jdocmunch_mcp.tools.get_section_summary import get_section_summary
from jdocmunch_mcp.tools.get_section_summaries import get_section_summaries
from jdocmunch_mcp.tools.get_sections import get_sections
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.get_toc import get_toc


def _index_with_fake_embedding(tmp_path):
    """Index a tiny doc, then inject a fake embedding into every section so
    we can verify the response strips it without depending on a live
    embedding provider."""
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "guide.md").write_text(textwrap.dedent("""
        # Guide

        Intro.

        ## Section A

        Body of A.

        ## Section B

        Body of B.
    """).lstrip("\n"), encoding="utf-8")
    index_local(
        path=str(repo), name="emb",
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )

    # Inject a fake 384-dim embedding into every section, persist, and reload.
    from jdocmunch_mcp.storage import DocStore
    store = DocStore(base_path=str(tmp_path))
    owner, name = store._resolve_repo("emb")
    index = store.load_index(owner, name)
    fake = [0.001 * (i % 7) for i in range(384)]
    for sec in index.sections:
        sec["embedding"] = list(fake)
    # Persist via the same path the indexer uses.
    import json
    p = store._index_path(owner, name)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = store._index_to_dict(index)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return owner, name


def _all_section_ids(tmp_path):
    """Pull every section id from the loaded index — robust to TOC shape."""
    from jdocmunch_mcp.storage import DocStore
    store = DocStore(base_path=str(tmp_path))
    owner, name = store._resolve_repo("emb")
    index = store.load_index(owner, name)
    return [s.get("id") for s in index.sections if s.get("id")]


class TestEmbeddingStripping:
    def test_get_section_strips_embedding(self, tmp_path):
        _index_with_fake_embedding(tmp_path)
        sid = _all_section_ids(tmp_path)[0]
        out = get_section(repo="emb", section_id=sid, storage_path=str(tmp_path))
        assert "embedding" not in out["section"], \
            "raw embedding vector must not leak into get_section response"

    def test_get_sections_strips_embedding(self, tmp_path):
        _index_with_fake_embedding(tmp_path)
        sids = _all_section_ids(tmp_path)
        out = get_sections(repo="emb", section_ids=sids, storage_path=str(tmp_path))
        for entry in out["sections"]:
            sec = entry.get("section")
            if sec is not None:
                assert "embedding" not in sec, \
                    "raw embedding vector must not leak into get_sections response"

    def test_describe_section_strips_embedding(self, tmp_path):
        _index_with_fake_embedding(tmp_path)
        sid = _all_section_ids(tmp_path)[0]
        out = describe_section(repo="emb", section_id=sid, storage_path=str(tmp_path))
        assert "embedding" not in out["section"], \
            "raw embedding vector must not leak into describe_section response"
        # Ancestor / sibling chains must not leak it either.
        for chain_key in ("ancestors", "siblings", "children"):
            for node in out.get(chain_key) or []:
                assert "embedding" not in node, \
                    f"embedding leaked into describe_section.{chain_key}"

    def test_get_section_summary_strips_embedding(self, tmp_path):
        _index_with_fake_embedding(tmp_path)
        sid = _all_section_ids(tmp_path)[0]
        out = get_section_summary(repo="emb", section_id=sid,
                                  storage_path=str(tmp_path))
        assert "embedding" not in out["section"], \
            "raw embedding vector must not leak into get_section_summary"

    def test_get_section_summaries_strips_embedding(self, tmp_path):
        _index_with_fake_embedding(tmp_path)
        sids = _all_section_ids(tmp_path)
        out = get_section_summaries(repo="emb", section_ids=sids,
                                    storage_path=str(tmp_path))
        for entry in out["sections"]:
            sec = entry.get("section")
            if sec is not None:
                assert "embedding" not in sec, \
                    "raw embedding vector must not leak into get_section_summaries"
