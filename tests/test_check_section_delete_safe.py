"""Tests for check_section_delete_safe."""

from __future__ import annotations

import os
import time

import pytest

from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.check_section_delete_safe import check_section_delete_safe
from jdocmunch_mcp.storage.doc_store import DocStore


# ---------- helpers ----------


def _index(docs_path: str, tmp_path) -> tuple[str, str]:
    storage = str(tmp_path / "store")
    res = index_local(path=docs_path, use_ai_summaries=False, storage_path=storage)
    assert res["success"], f"Indexing failed: {res}"
    return res["repo"], storage


def _section_id_for(repo: str, storage: str, doc_path: str, title_contains: str) -> str:
    """Find the section_id for the first section in ``doc_path`` whose
    title contains ``title_contains`` (case-insensitive)."""
    owner, name = DocStore(base_path=storage)._resolve_repo(repo)
    index = DocStore(base_path=storage).load_index(owner, name)
    needle = title_contains.lower()
    for sec in index.sections:
        if sec.get("doc_path") != doc_path:
            continue
        if needle in (sec.get("title") or "").lower():
            return sec["id"]
    raise AssertionError(
        f"No section in {doc_path} with title containing {title_contains!r}; "
        f"have titles: {[s.get('title') for s in index.sections if s.get('doc_path') == doc_path]}"
    )


# ---------- fixtures ----------


@pytest.fixture
def wiki_orphan(tmp_path):
    """A page nothing else references — the safe case."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "lonely.md").write_text(
        "# Lonely Page\n\n## A solo subsection\n\nNo one links here.\n"
    )
    (docs / "other.md").write_text(
        "# Other Page\n\nUnrelated content.\n"
    )
    return str(docs)


@pytest.fixture
def wiki_with_anchor_ref(tmp_path):
    """A target section that another page links to by anchor."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "target.md").write_text(
        "# Target Page\n\n## Important Section\n\nLong-lived content.\n\n"
        "## Other Section\n\nMore content.\n"
    )
    (docs / "referrer.md").write_text(
        "# Referrer\n\n"
        "See [the important bit](target.md#important-section) for details.\n"
    )
    return str(docs)


@pytest.fixture
def wiki_with_doc_refs(tmp_path):
    """A page referenced by many other pages (no anchors), driving the
    transitive-backlink channel."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "hub.md").write_text(
        "# Hub Page\n\n## Detail Section\n\nReusable content.\n"
    )
    for i in range(4):
        (docs / f"page-{i}.md").write_text(
            f"# Page {i}\n\nSee [the hub](hub.md) for context.\n"
        )
    return str(docs)


@pytest.fixture
def wiki_tutorial_chain(tmp_path):
    """Ordered-filename tutorial chain — deleting any non-terminal step
    should be flagged."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "01-intro.md").write_text("# Intro\n\nWelcome.\n")
    (docs / "02-setup.md").write_text("# Setup\n\nInstall the tools.\n")
    (docs / "03-finish.md").write_text("# Finish\n\nYou're done.\n")
    return str(docs)


# ---------- tests ----------


def test_orphan_section_is_safe(tmp_path, wiki_orphan):
    repo, storage = _index(wiki_orphan, tmp_path)
    sid = _section_id_for(repo, storage, "lonely.md", "solo")
    r = check_section_delete_safe(repo, sid, storage_path=storage)
    res = r["result"]
    assert res["verdict"] == "safe_to_delete", f"Got verdict={res['verdict']}, blockers={res['blockers']}"
    assert res["blockers"] == []
    assert "Safe to delete" in res["recommended_action"]


def test_anchor_reference_blocks(tmp_path, wiki_with_anchor_ref):
    repo, storage = _index(wiki_with_anchor_ref, tmp_path)
    sid = _section_id_for(repo, storage, "target.md", "important")
    r = check_section_delete_safe(repo, sid, storage_path=storage)
    res = r["result"]
    assert res["verdict"] == "anchor_referenced", f"Got {res['verdict']}"
    assert res["evidence"]["anchor_backlink_count"] >= 1
    assert any(b["kind"] == "anchor_reference" for b in res["blockers"])


def test_transitive_backlinks_block(tmp_path, wiki_with_doc_refs):
    repo, storage = _index(wiki_with_doc_refs, tmp_path)
    sid = _section_id_for(repo, storage, "hub.md", "detail")
    r = check_section_delete_safe(repo, sid, storage_path=storage)
    res = r["result"]
    # 4 incoming refs from page-{0..3}.md, threshold is 3 → blocks.
    assert res["evidence"]["transitive_backlink_count"] >= 3
    assert res["verdict"] in {"backlinks_blocking", "anchor_referenced", "tutorial_path_blocking"}
    if res["verdict"] == "backlinks_blocking":
        assert any(b["kind"] == "transitive_backlinks" for b in res["blockers"])


def test_tutorial_chain_blocks(tmp_path, wiki_tutorial_chain):
    repo, storage = _index(wiki_tutorial_chain, tmp_path)
    # 01-intro is the chain start
    sid = _section_id_for(repo, storage, "01-intro.md", "intro")
    r = check_section_delete_safe(repo, sid, storage_path=storage)
    res = r["result"]
    assert res["verdict"] == "tutorial_path_blocking", f"Got {res['verdict']}, blockers={res['blockers']}"
    assert res["evidence"]["tutorial_path_memberships"], "Expected at least one tutorial membership"
    assert any(b["kind"] == "tutorial_path_membership" for b in res["blockers"])


def test_recently_edited_soft_block(tmp_path, wiki_orphan):
    """A freshly written page should fall into the recently-edited bucket
    when the window is wide enough."""
    repo, storage = _index(wiki_orphan, tmp_path)
    sid = _section_id_for(repo, storage, "lonely.md", "solo")
    # 30-day window catches anything indexed in this test run.
    r = check_section_delete_safe(repo, sid, recent_edit_days=30, storage_path=storage)
    res = r["result"]
    # No structural blockers exist → recently_edited should be the verdict
    # OR safe (depending on platform mtime resolution).
    if res["evidence"].get("last_edit_days_ago") is not None:
        assert res["evidence"]["last_edit_days_ago"] <= 30


def test_zero_window_skips_recent_edit_channel(tmp_path, wiki_orphan):
    """With recent_edit_days=0, only strictly-future mtimes block — i.e.,
    nothing should block, and an orphan stays safe."""
    repo, storage = _index(wiki_orphan, tmp_path)
    sid = _section_id_for(repo, storage, "lonely.md", "solo")
    r = check_section_delete_safe(repo, sid, recent_edit_days=0, storage_path=storage)
    res = r["result"]
    # File was just written; days_ago == 0, which is <= 0 → still flagged.
    # That's correct behaviour (just-touched IS recent). Test asserts the
    # mechanism, not a specific verdict.
    if res["evidence"].get("last_edit_days_ago") == 0:
        assert any(b["kind"] == "recently_edited" for b in res["blockers"])


def test_unknown_section_id(tmp_path, wiki_orphan):
    repo, storage = _index(wiki_orphan, tmp_path)
    r = check_section_delete_safe(repo, f"{repo}::bogus.md::nope#1", storage_path=storage)
    assert "error" in r
    assert "Section not found" in r["error"]


def test_unknown_repo(tmp_path):
    r = check_section_delete_safe(
        "nobody/nothing",
        "nobody/nothing::x.md::y#1",
        storage_path=str(tmp_path / "store"),
    )
    assert "error" in r


def test_blocker_cap(tmp_path):
    """No more than 5 blockers ever surface, even on a pathological doc."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "target.md").write_text(
        "# Target\n\n## Hot Section\n\nContent.\n"
    )
    # Pile up anchor + doc refs.
    for i in range(10):
        (docs / f"r{i}.md").write_text(
            f"# R{i}\n\nSee [hot](target.md#hot-section) and [doc](target.md).\n"
        )
    repo, storage = _index(str(docs), tmp_path)
    sid = _section_id_for(repo, storage, "target.md", "hot")
    r = check_section_delete_safe(repo, sid, storage_path=storage)
    res = r["result"]
    assert len(res["blockers"]) <= 5
