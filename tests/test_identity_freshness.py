"""`get_section` / `get_sections` disclose freshness, and `fresh` means proven.

Two defects, one theme: a reading that was never taken must not render as
proof of currency.

1. **The identity content tools disclosed nothing.** `search_sections` has
   carried per-section freshness since v1.16.0, but `get_section` and
   `get_sections` — the tools that hand a caller actual bytes — emitted neither
   freshness nor a verdict. A content read is a claim about what the file holds
   right now, so the caller had no way to learn the bytes were stale.

2. **`FreshnessProbe._classify` answered `fresh` when it had compared
   nothing.** A section with no `doc_path`, and a file that exists but whose
   bytes could not be read (`_file_hash` returns `(None, True)` on `OSError`),
   both fell through every comparison to the closing `return "fresh"`.
   `summary()` compounded it by tallying three buckets and silently dropping
   anything else, so such a section vanished from the aggregate entirely.

⚠ The load-bearing tests are the ones asserting a NON-`fresh` value on a
changed or unreadable source. A test suite that only ever sees a clean tree
cannot tell this fix from the bug.
"""

from __future__ import annotations

import os

import pytest

from jdocmunch_mcp.retrieval.freshness import FreshnessProbe
from jdocmunch_mcp.retrieval.verdict import index_channel, section_verdict_for_index
from jdocmunch_mcp.tools.get_section import get_section
from jdocmunch_mcp.tools.get_sections import get_sections
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.storage import DocStore


@pytest.fixture()
def doc_repo(tmp_path):
    """A real indexed folder whose source files we can then edit."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "doc.md").write_text(
        "# Title\n\nAlpha body.\n\n## Second\n\nBeta body.\n", encoding="utf-8"
    )
    store_path = str(tmp_path / "store")
    res = index_local(
        path=str(src), name="identity-freshness", use_ai_summaries=False,
        use_embeddings=False, storage_path=store_path,
    )
    store = DocStore(base_path=store_path)
    owner, name = store._resolve_repo(res["repo"])
    index = store.load_index(owner, name)
    return {
        "repo": res["repo"],
        "storage_path": store_path,
        "file": src / "doc.md",
        "section_ids": [s["id"] for s in index.sections],
    }


# --- the tools now disclose ------------------------------------------------


def test_get_section_reports_freshness_and_a_verdict(doc_repo):
    r = get_section(
        repo=doc_repo["repo"], section_id=doc_repo["section_ids"][-1],
        storage_path=doc_repo["storage_path"],
    )
    meta = r["_meta"]
    assert meta["freshness"]["fresh"] == 1
    assert meta["verdict"]["state"] == "ok"
    assert meta["verdict"]["channels"]["index"] == "fresh"
    assert r["section"]["_freshness"] == "fresh"


def test_get_sections_reports_freshness_and_a_verdict(doc_repo):
    r = get_sections(
        repo=doc_repo["repo"], section_ids=doc_repo["section_ids"],
        storage_path=doc_repo["storage_path"],
    )
    meta = r["_meta"]
    assert meta["freshness"]["fresh"] == len(doc_repo["section_ids"])
    assert meta["verdict"]["channels"]["index"] == "fresh"


def test_the_content_itself_is_unchanged(doc_repo):
    """Disclosure is additive. The answer must not move."""
    r = get_section(
        repo=doc_repo["repo"], section_id=doc_repo["section_ids"][-1],
        storage_path=doc_repo["storage_path"],
    )
    assert "Beta body." in r["section"]["content"]
    assert set(r) == {"section", "_meta"}


# --- the readings that must NOT say fresh ---------------------------------


def test_an_edited_source_is_not_reported_fresh(doc_repo):
    doc_repo["file"].write_text(
        "# Title\n\nAlpha body.\n\n## Second\n\nBeta body.\n\nAppended.\n",
        encoding="utf-8",
    )
    meta = get_section(
        repo=doc_repo["repo"], section_id=doc_repo["section_ids"][-1],
        storage_path=doc_repo["storage_path"],
    )["_meta"]
    assert meta["freshness"]["fresh"] == 0
    assert meta["freshness"]["edited_uncommitted"] == 1
    assert meta["verdict"]["channels"]["index"] == "edited_uncommitted"


def test_a_deleted_source_reads_stale_and_degrades_the_verdict(doc_repo):
    """⚠ The channel regressed to `fresh` here on the first implementation.

    `stale_index` was missing from `index_channel`'s accepted set, so it fell
    through to the closing `return "fresh"` — the exact failure the function
    exists to prevent, reintroduced by its own membership test.
    """
    os.remove(doc_repo["file"])
    meta = get_section(
        repo=doc_repo["repo"], section_id=doc_repo["section_ids"][-1],
        storage_path=doc_repo["storage_path"],
    )["_meta"]
    assert meta["freshness"]["stale_index"] == 1
    assert meta["verdict"]["channels"]["index"] == "stale"
    assert meta["verdict"]["state"] == "degraded"


def test_the_batch_verdict_takes_the_WORST_section_reading(doc_repo):
    """Averaging, or taking the first, lets one stale section ride out under an
    `ok` covering the others — and the caller may never see the per-section
    flag."""
    doc_repo["file"].write_text("# Title\n\nTotally different.\n", encoding="utf-8")
    meta = get_sections(
        repo=doc_repo["repo"], section_ids=doc_repo["section_ids"],
        storage_path=doc_repo["storage_path"],
    )["_meta"]
    assert meta["verdict"]["channels"]["index"] != "fresh"


def test_the_drift_layer_is_disclosed(doc_repo):
    """⚠ The DEFAULT probe compares against jdoc's cached mirror, which does
    not change when the workspace file does — so it answered `fresh` for a file
    that had been edited AND for one that had been deleted. Which layer
    answered is part of the answer."""
    meta = get_section(
        repo=doc_repo["repo"], section_id=doc_repo["section_ids"][-1],
        storage_path=doc_repo["storage_path"],
    )["_meta"]
    assert meta["drift_layer"] in ("live_source", "cached_mirror")


# --- the probe fails closed ------------------------------------------------


def test_a_section_with_no_doc_path_is_unknown_not_fresh(doc_repo):
    store = DocStore(base_path=doc_repo["storage_path"])
    owner, name = store._resolve_repo(doc_repo["repo"])
    index = store.load_index(owner, name)
    probe = FreshnessProbe(store, owner, name, index)
    assert probe.annotate({"doc_path": ""}) == "unknown"


def test_an_unreadable_file_is_unknown_not_fresh(doc_repo):
    """`_file_hash` returns (None, True) on OSError — the file is there and we
    failed to read it. That is our capability failing, not evidence.

    Seeded through the probe's own `_file_state` cache, which is exactly the
    tuple an OSError leaves behind, so the real `_classify` path runs. The
    class uses `__slots__`, so the method itself cannot be monkeypatched.
    """
    store = DocStore(base_path=doc_repo["storage_path"])
    owner, name = store._resolve_repo(doc_repo["repo"])
    index = store.load_index(owner, name)
    probe = FreshnessProbe(store, owner, name, index)
    probe._file_state["ghost.md"] = (None, True)
    assert probe.annotate({"doc_path": "ghost.md"}) == "unknown"


def test_summary_counts_unknown_instead_of_dropping_it():
    """⚠ The old summary tallied three buckets and dropped everything else, so
    the counts could sum to fewer than the sections they described."""
    probe = FreshnessProbe.__new__(FreshnessProbe)
    out = FreshnessProbe.summary(probe, [
        {"_freshness": "fresh"},
        {"_freshness": "unknown"},
        {},                       # no reading at all
        {"_freshness": "wat"},    # a bucket from the future
    ])
    assert out["fresh"] == 1
    assert out["unknown"] == 3
    assert sum(out.values()) == 4


# --- index_channel ---------------------------------------------------------


@pytest.mark.parametrize(
    "reading,expected",
    [
        ("fresh", "fresh"),
        ("unknown", "unknown"),
        ("edited_uncommitted", "edited_uncommitted"),
        ("stale_index", "stale"),
        ("stale", "stale"),
    ],
)
def test_index_channel_maps_every_reading(reading, expected):
    assert index_channel(freshness=reading) == expected


def test_index_channel_precedence():
    assert index_channel(index_changed=True, freshness="fresh") == "rebuilding"
    assert index_channel(index_stale=True, freshness="fresh") == "stale"


def test_index_channel_without_a_reading_is_unchanged():
    """Callers passing only the Boolean keep their previous behaviour."""
    assert index_channel() == "fresh"
    assert index_channel(index_stale=True) == "stale"


def test_an_unrecognised_reading_does_not_leak_into_the_channel():
    assert index_channel(freshness="probably_fine") == "fresh"


def test_section_verdict_is_absent_when_nothing_was_found(doc_repo):
    store = DocStore(base_path=doc_repo["storage_path"])
    owner, name = store._resolve_repo(doc_repo["repo"])
    index = store.load_index(owner, name)
    assert section_verdict_for_index(index, found_count=0)["state"] == "absent"


# --- non-vacuity -----------------------------------------------------------


def test_the_old_two_state_expression_would_fail_these():
    """Prove the assertions above are not vacuous: the expression this replaced
    flattened every non-stale reading to `fresh`."""
    for reading in ("unknown", "edited_uncommitted"):
        old = "stale" if False else "fresh"
        assert index_channel(freshness=reading) != old
