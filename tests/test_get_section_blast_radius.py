"""Tests for get_section_blast_radius."""

from __future__ import annotations

import pytest

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.get_section_blast_radius import get_section_blast_radius
from jdocmunch_mcp.storage.doc_store import DocStore


def _index(docs_path: str, tmp_path) -> tuple[str, str]:
    storage = str(tmp_path / "store")
    res = index_local(path=docs_path, use_ai_summaries=False, storage_path=storage)
    assert res["success"], f"Indexing failed: {res}"
    return res["repo"], storage


def _section_id_for(repo: str, storage: str, doc_path: str, title_contains: str) -> str:
    owner, name = DocStore(base_path=storage)._resolve_repo(repo)
    index = DocStore(base_path=storage).load_index(owner, name)
    needle = title_contains.lower()
    for sec in index.sections:
        if sec.get("doc_path") != doc_path:
            continue
        if needle in (sec.get("title") or "").lower():
            return sec["id"]
    raise AssertionError(f"No section in {doc_path} matching {title_contains!r}")


# ---------- fixtures ----------


@pytest.fixture
def wiki_orphan(tmp_path):
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "lonely.md").write_text("# Lonely\n\n## Solo\n\nNothing here.\n")
    (docs / "other.md").write_text("# Other\n\nUnrelated.\n")
    return str(docs)


@pytest.fixture
def wiki_chain(tmp_path):
    """A → B → C linear reference chain — exercises BFS depth."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "target.md").write_text("# Target\n\n## Hub\n\nDestination.\n")
    (docs / "ref1.md").write_text("# Ref1\n\nSee [target](target.md).\n")
    (docs / "ref2.md").write_text("# Ref2\n\nSee [ref1](ref1.md).\n")
    (docs / "ref3.md").write_text("# Ref3\n\nSee [ref2](ref2.md).\n")
    return str(docs)


@pytest.fixture
def wiki_anchor(tmp_path):
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "target.md").write_text(
        "# Target\n\n## Important Section\n\nContent.\n\n## Other\n\nMore.\n"
    )
    (docs / "ref_anchor.md").write_text(
        "# Ref\n\nSee [important](target.md#important-section).\n"
    )
    (docs / "ref_doc.md").write_text(
        "# RefDoc\n\nSee the [page](target.md).\n"
    )
    return str(docs)


# ---------- tests ----------


def test_orphan_zero_impact(tmp_path, wiki_orphan):
    repo, storage = _index(wiki_orphan, tmp_path)
    sid = _section_id_for(repo, storage, "lonely.md", "solo")
    r = get_section_blast_radius(repo, sid, storage_path=storage)
    res = r["result"]
    assert res["direct_impact"] == []
    assert res["transitive_impact"] == []
    assert res["summary"]["docs_affected"] == 0
    assert res["blast_score"] == 0.0


def test_direct_hits_at_depth_1(tmp_path, wiki_chain):
    repo, storage = _index(wiki_chain, tmp_path)
    sid = _section_id_for(repo, storage, "target.md", "hub")
    r = get_section_blast_radius(repo, sid, max_depth=1, storage_path=storage)
    res = r["result"]
    # ref1.md links directly at depth 1
    assert res["summary"]["direct_count"] >= 1
    assert any(d["doc_path"] == "ref1.md" for d in res["direct_impact"])
    # depth=1 means no transitive
    assert res["summary"]["transitive_count"] == 0


def test_transitive_walk_depth_3(tmp_path, wiki_chain):
    repo, storage = _index(wiki_chain, tmp_path)
    sid = _section_id_for(repo, storage, "target.md", "hub")
    r = get_section_blast_radius(repo, sid, max_depth=3, storage_path=storage)
    res = r["result"]
    # Chain: target ← ref1 ← ref2 ← ref3
    # depth=3 reaches ref3
    paths_at_each_depth = {
        d["doc_path"]: d["depth"]
        for d in res["direct_impact"] + res["transitive_impact"]
    }
    assert "ref1.md" in paths_at_each_depth
    assert "ref2.md" in paths_at_each_depth
    assert "ref3.md" in paths_at_each_depth
    assert paths_at_each_depth["ref1.md"] == 1
    assert paths_at_each_depth["ref2.md"] == 2
    assert paths_at_each_depth["ref3.md"] == 3


def test_max_depth_bounds_walk(tmp_path, wiki_chain):
    repo, storage = _index(wiki_chain, tmp_path)
    sid = _section_id_for(repo, storage, "target.md", "hub")
    r = get_section_blast_radius(repo, sid, max_depth=2, storage_path=storage)
    res = r["result"]
    paths = {d["doc_path"] for d in res["direct_impact"] + res["transitive_impact"]}
    assert "ref1.md" in paths
    assert "ref2.md" in paths
    assert "ref3.md" not in paths


def test_anchor_classification(tmp_path, wiki_anchor):
    repo, storage = _index(wiki_anchor, tmp_path)
    sid = _section_id_for(repo, storage, "target.md", "important")
    r = get_section_blast_radius(repo, sid, storage_path=storage)
    res = r["result"]
    kinds = {d["link_kind"] for d in res["direct_impact"]}
    assert "anchor" in kinds
    assert res["summary"]["anchor_refs"] >= 1


def test_blast_score_normalized(tmp_path, wiki_chain):
    repo, storage = _index(wiki_chain, tmp_path)
    sid = _section_id_for(repo, storage, "target.md", "hub")
    r = get_section_blast_radius(repo, sid, storage_path=storage)
    score = r["result"]["blast_score"]
    assert 0.0 <= score <= 1.0
    # Three referers in a 4-doc wiki should score meaningfully.
    assert score > 0.0


def test_no_self_reference(tmp_path):
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "selfref.md").write_text(
        "# Self\n\n## A\n\nSee [itself](selfref.md#a).\n## B\n\nUnrelated.\n"
    )
    repo, storage = _index(str(docs), tmp_path)
    sid = _section_id_for(repo, storage, "selfref.md", "A")
    r = get_section_blast_radius(repo, sid, storage_path=storage)
    # Same-doc self-references are excluded.
    assert all(d["doc_path"] != "selfref.md" for d in r["result"]["direct_impact"])


def test_unknown_section(tmp_path, wiki_orphan):
    repo, storage = _index(wiki_orphan, tmp_path)
    r = get_section_blast_radius(repo, f"{repo}::nope.md::x#1", storage_path=storage)
    assert "error" in r


def test_max_depth_zero_treated_as_one(tmp_path, wiki_chain):
    """max_depth < 1 is clamped to 1 — the tool always returns at least
    direct impact rather than refusing."""
    repo, storage = _index(wiki_chain, tmp_path)
    sid = _section_id_for(repo, storage, "target.md", "hub")
    r = get_section_blast_radius(repo, sid, max_depth=0, storage_path=storage)
    assert "result" in r
    assert r["_meta"]["max_depth"] == 1


def test_impact_items_capped(tmp_path):
    """A pathological referer farm shouldn't blow the response size."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "target.md").write_text("# Target\n\n## Hub\n\nContent.\n")
    for i in range(80):
        (docs / f"r{i}.md").write_text(f"# R{i}\n\nSee [hub](target.md).\n")
    repo, storage = _index(str(docs), tmp_path)
    sid = _section_id_for(repo, storage, "target.md", "hub")
    r = get_section_blast_radius(repo, sid, storage_path=storage)
    assert len(r["result"]["direct_impact"]) <= 50
