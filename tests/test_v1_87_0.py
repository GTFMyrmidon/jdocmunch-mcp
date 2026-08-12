"""Tests for v1.87.0 — get_doc_pr_risk_profile backlink/tutorial signal fix (#69).

Two correctness bugs in the aggregate doc-PR risk tool, both swallowed by
broad exception handling so the composite silently *understated* risk:

  1. ``backlink_burden`` called ``get_backlinks(section_id=...)`` but the
     tool's signature requires ``doc_path`` — every call raised TypeError
     and was caught, so the signal scored 0 even with real inbound links.
  2. ``tutorial_disruption`` guarded on ``tp["result"]["chain"]`` but
     ``get_tutorial_path`` returns ``chain`` at the TOP level — the guard
     never matched, so the signal scored 0 even on a real tutorial chain.

These tests use a fixture with genuine backlinks AND a genuine ordered
tutorial chain, then assert both signals are now nonzero. A forced-failure
case asserts unresolvable signals surface in ``result.diagnostics`` instead
of being indistinguishable from a true zero-risk verdict.
"""

from __future__ import annotations

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.get_doc_pr_risk_profile import get_doc_pr_risk_profile
from jdocmunch_mcp.tools.index_local import index_local


@pytest.fixture
def tutorial_wiki(tmp_path):
    """3 ordered docs forming a tutorial chain, with inbound links to the first.

    ``01-intro.md`` is linked from both ``02-setup.md`` and ``03-deploy.md``
    (so it carries inbound backlinks), and the ``NN-`` numeric prefixes make
    the three a single ordered-filename tutorial chain.
    """
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "01-intro.md").write_text(
        "# Intro\n\nStart here. Then read [setup](02-setup.md).\n"
    )
    (docs / "02-setup.md").write_text(
        "# Setup\n\nBack to [intro](01-intro.md). Next [deploy](03-deploy.md).\n"
    )
    (docs / "03-deploy.md").write_text(
        "# Deploy\n\nReturn to [intro](01-intro.md) when done.\n"
    )
    storage = str(tmp_path / "store")
    res = index_local(path=str(docs), use_ai_summaries=False, storage_path=storage)
    assert res["success"]
    return res["repo"], storage


def _section_id_for(repo, storage, doc_path):
    owner, name = DocStore(base_path=storage)._resolve_repo(repo)
    idx = DocStore(base_path=storage).load_index(owner, name)
    for s in idx.sections:
        if s.get("doc_path") == doc_path:
            return s["id"]
    raise AssertionError(f"No section in {doc_path}")


def test_backlink_burden_nonzero_when_inbound_links_exist(tutorial_wiki):
    """Regression: get_backlinks must be called with doc_path, not section_id."""
    repo, storage = tutorial_wiki
    sid = _section_id_for(repo, storage, "01-intro.md")
    r = get_doc_pr_risk_profile(
        repo=repo,
        changed_sections=[{"section_id": sid, "kind": "modified"}],
        storage_path=storage,
    )
    res = r["result"]
    assert res["signals"]["backlink_burden"] > 0.0, res["signals"]
    # 01-intro.md is linked from 02 and 03 -> 2 inbound references.
    assert res["signal_details"]["total_backlinks_on_changed"] >= 2


def test_tutorial_disruption_nonzero_on_chain(tutorial_wiki):
    """Regression: get_tutorial_path returns `chain` at the top level."""
    repo, storage = tutorial_wiki
    sid = _section_id_for(repo, storage, "01-intro.md")
    r = get_doc_pr_risk_profile(
        repo=repo,
        changed_sections=[{"section_id": sid, "kind": "modified"}],
        storage_path=storage,
    )
    res = r["result"]
    assert res["signals"]["tutorial_disruption"] > 0.0, res["signals"]
    assert res["signal_details"]["tutorial_chain_sections"] >= 1


def test_blockers_surface_backlink_and_tutorial(tutorial_wiki):
    """top_blockers must include the backlink/tutorial signals once they fire."""
    repo, storage = tutorial_wiki
    sid = _section_id_for(repo, storage, "01-intro.md")
    r = get_doc_pr_risk_profile(
        repo=repo,
        changed_sections=[{"section_id": sid, "kind": "modified"}],
        storage_path=storage,
    )
    kinds = {b["kind"] for b in r["result"]["top_blockers"]}
    # The intro has 2 inbound refs (>=3 threshold not met) but sits on a
    # tutorial chain, so the tutorial blocker must appear.
    assert "tutorial_path" in kinds, r["result"]["top_blockers"]


def test_clean_signals_report_no_failures(tutorial_wiki):
    """All-valid sections produce an empty diagnostics.signal_failures list."""
    repo, storage = tutorial_wiki
    sid = _section_id_for(repo, storage, "01-intro.md")
    r = get_doc_pr_risk_profile(
        repo=repo,
        changed_sections=[{"section_id": sid, "kind": "modified"}],
        storage_path=storage,
    )
    diag = r["result"]["diagnostics"]
    assert diag["signal_failures"] == [], diag
    assert r["_meta"]["signal_failure_count"] == 0


def test_unresolvable_section_surfaces_in_diagnostics(tutorial_wiki):
    """A bogus section_id is recorded in diagnostics, not silently scored 0."""
    repo, storage = tutorial_wiki
    r = get_doc_pr_risk_profile(
        repo=repo,
        changed_sections=[{"section_id": "ghost::not::a::section", "kind": "modified"}],
        storage_path=storage,
    )
    failures = r["result"]["diagnostics"]["signal_failures"]
    assert failures, "expected at least one recorded signal failure"
    assert r["_meta"]["signal_failure_count"] == len(failures)
    signals_with_failures = {f["signal"] for f in failures}
    # The backlink path can't resolve the section to a doc_path, and the
    # tutorial path returns an error for the missing section.
    assert "backlink_burden" in signals_with_failures
    assert "tutorial_disruption" in signals_with_failures


def test_co_located_changes_do_not_inflate_backlinks(tmp_path):
    """Several changed sections in one doc count that doc's backlinks once."""
    docs = tmp_path / "wiki"
    docs.mkdir()
    # target.md has two headings (two sections), both inbound-linked once.
    (docs / "target.md").write_text(
        "# Target\n\nIntro prose.\n\n## Details\n\nMore prose.\n"
    )
    (docs / "a.md").write_text("# A\n\nSee [target](target.md).\n")
    (docs / "b.md").write_text("# B\n\nSee [target](target.md).\n")
    storage = str(tmp_path / "store")
    res = index_local(path=str(docs), use_ai_summaries=False, storage_path=storage)
    assert res["success"]
    repo = res["repo"]

    owner, name = DocStore(base_path=storage)._resolve_repo(repo)
    idx = DocStore(base_path=storage).load_index(owner, name)
    target_sids = [s["id"] for s in idx.sections if s.get("doc_path") == "target.md"]
    assert len(target_sids) >= 2

    r = get_doc_pr_risk_profile(
        repo=repo,
        changed_sections=[{"section_id": s, "kind": "modified"} for s in target_sids],
        storage_path=storage,
    )
    # target.md has 2 inbound links (from a.md + b.md). Even though 2+
    # sections in target.md changed, the document is counted once.
    assert r["result"]["signal_details"]["total_backlinks_on_changed"] == 2
