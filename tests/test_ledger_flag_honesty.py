"""The ranking ledger must not claim a channel that did not run.

Written after jcodemunch 1.108.186-.188 fixed three defects in its own ledger. All
three were checked here first and jdoc already satisfies every one, so **nothing was
ported** — a rule whose predicate can never match reads enforced and is not. What
jdoc lacked was anything pinning the correct behaviour in place, which is what this
file is: a drift guard, not a fix.

The three invariants, in the order jcm broke them:

1. **The flag is DERIVED, never asserted.** jcm's `get_ranked_context` fusion exit
   recorded `semantic_used=True` while building no similarity channel, and that column
   feeds a learned per-repo weight. jdoc derives it from `mode`, and `mode` is gated on
   `has_emb = index._has_embeddings()`, so it cannot claim a channel the index has no
   data for. ⚠ A ledger is APPEND-ONLY: once a wrong value is in it, fixing the
   producer does not fix the history, and every consumer reading that column keeps
   reading the lie until the recency window ages it out. That asymmetry is why this is
   worth a test rather than a comment.

2. **Features are MEASURED, never defaulted.** jcm's fusion exit passed no ledger
   features at all, so `top1_score`/`top2_score` were recorded as `None` on every row
   it wrote — indistinguishable from a genuinely absent score once stored, and read by
   three of its regret signals.

3. **Rows land in the store the CALLER named.** jcm's writers passed no base path
   while every reader took one, so rows went to the default store whatever
   `storage_path` said, and its tuner learned from a ledger the queries had never
   written to. jdoc threads `base_path=storage_path` through; this pins that it stays.

Helper names are prefixed ``_lfh``. jdoc's replay fixtures anchor on prose phrases
("hybrid search", "search sections", "section retrieval"), so those phrases are kept
out of the fixture documents below — a fixture that matches a golden query pollutes
the corpus it is scored against.
"""

from __future__ import annotations

import pytest


def _lfh_docs(tmp_path, name: str, *, body: str = "widget calibration procedure"):
    """Index a tiny doc folder with NO embeddings and return its repo id."""
    from jdocmunch_mcp.tools.index_local import index_local

    repo_dir = tmp_path / f"docs_{name}"
    repo_dir.mkdir()
    (repo_dir / "guide.md").write_text(f"# Overview\n\n{body}\n", encoding="utf-8")
    index_local(
        path=str(repo_dir), name=name,
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )
    return name


@pytest.fixture()
def _lfh_spy(monkeypatch):
    """Capture what search_sections hands the ledger, without a real db.

    The tool imports `record_ranking_event` from the module inside its own function
    body, so patching the module attribute reaches the real call.
    """
    from jdocmunch_mcp.storage import token_tracker as _tt

    seen: list = []
    monkeypatch.setattr(_tt, "record_ranking_event", lambda **kw: seen.append(kw))
    return seen


def _lfh_only(seen: list) -> dict:
    assert len(seen) == 1, f"expected exactly one ranking event, got {len(seen)}"
    return seen[0]


class TestTheFlagIsDerivedNotAsserted:
    def test_semantic_requested_without_embeddings_records_false(self, tmp_path, _lfh_spy):
        """The invariant jcm violated. Asking for semantic on an index that has no
        embeddings must NOT put a `1` in the ledger — the channel cannot have run."""
        from jdocmunch_mcp.tools.search_sections import search_sections

        repo = _lfh_docs(tmp_path, "lfh1")
        search_sections(
            repo=repo, query="calibration", semantic=True, storage_path=str(tmp_path),
        )
        row = _lfh_only(_lfh_spy)
        assert row["mode"] == "lexical"
        assert row["semantic_used"] is False

    def test_semantic_only_without_embeddings_records_false(self, tmp_path, _lfh_spy):
        """`semantic_only=True` is the strongest possible request for the channel and
        still must not manufacture the claim."""
        from jdocmunch_mcp.tools.search_sections import search_sections

        repo = _lfh_docs(tmp_path, "lfh2")
        search_sections(
            repo=repo, query="calibration", semantic_only=True, storage_path=str(tmp_path),
        )
        row = _lfh_only(_lfh_spy)
        assert row["mode"] == "lexical"
        assert row["semantic_used"] is False

    def test_the_flag_follows_the_embeddings_probe(self, tmp_path, _lfh_spy, monkeypatch):
        """Non-vacuity. A hardcoded `False` would pass both tests above and re-arm the
        trap in the opposite direction, so prove the flag can still reach True: when
        the probe reports embeddings, the row says the channel ran."""
        from jdocmunch_mcp.storage import doc_store as _ds
        from jdocmunch_mcp.tools.search_sections import search_sections

        repo = _lfh_docs(tmp_path, "lfh3")
        monkeypatch.setattr(
            _ds.DocIndex, "_has_embeddings", lambda self: True, raising=False
        )
        search_sections(
            repo=repo, query="calibration", semantic=True, storage_path=str(tmp_path),
        )
        row = _lfh_only(_lfh_spy)
        assert row["mode"] == "hybrid"
        assert row["semantic_used"] is True

    def test_the_ledger_and_the_verdict_agree(self, tmp_path, _lfh_spy):
        """jcm's two accounts of one call contradicted each other for three releases
        because nothing compared them. The verdict's semantic availability and the
        ledger's flag are answers to the same question."""
        from jdocmunch_mcp.tools.search_sections import search_sections

        repo = _lfh_docs(tmp_path, "lfh4")
        out = search_sections(
            repo=repo, query="calibration", semantic=True, storage_path=str(tmp_path),
        )
        row = _lfh_only(_lfh_spy)
        verdict = out["_meta"]["verdict"]
        assert verdict["channels"]["semantic"] != "ok"
        assert row["semantic_used"] is False


class TestFeaturesAreMeasuredNotDefaulted:
    def test_a_hit_records_its_real_scores(self, tmp_path, _lfh_spy):
        """jcm recorded None for every top score on one exit. A default and a
        genuinely absent measurement are indistinguishable once stored."""
        from jdocmunch_mcp.tools.search_sections import search_sections

        repo = _lfh_docs(tmp_path, "lfh5")
        out = search_sections(repo=repo, query="calibration", storage_path=str(tmp_path))
        assert out["results"], "need a hit for there to be a score to record"
        row = _lfh_only(_lfh_spy)
        assert row["top1_score"] is not None
        assert row["result_count"] == len(out["results"])
        assert row["confidence"] == out["_meta"].get("confidence")

    def test_a_miss_records_a_zero_count_not_a_phantom_score(self, tmp_path, _lfh_spy):
        from jdocmunch_mcp.tools.search_sections import search_sections

        repo = _lfh_docs(tmp_path, "lfh6")
        search_sections(
            repo=repo, query="zzz_lfh_absent_phrase", storage_path=str(tmp_path),
        )
        row = _lfh_only(_lfh_spy)
        assert row["result_count"] == 0
        assert row["top1_score"] is None


class TestRowsFollowTheNamedStore:
    def test_the_recorded_base_path_is_the_one_the_caller_passed(self, tmp_path, _lfh_spy):
        """Every reader here takes a base path. A writer that passes none sends rows
        to the default store while the tuner reads the named one."""
        from jdocmunch_mcp.tools.search_sections import search_sections

        repo = _lfh_docs(tmp_path, "lfh7")
        search_sections(repo=repo, query="calibration", storage_path=str(tmp_path))
        assert _lfh_only(_lfh_spy)["base_path"] == str(tmp_path)

    def test_a_row_is_readable_from_the_store_it_was_written_to(self, tmp_path, monkeypatch):
        """End to end against the real ledger, not the spy: what a search writes is
        what a reader given the same storage_path finds."""
        from jdocmunch_mcp.storage import token_tracker as _tt
        from jdocmunch_mcp.tools.search_sections import search_sections

        # jdoc gates the SQLite sink on an env var, not a config key.
        monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
        repo = _lfh_docs(tmp_path, "lfh8")
        search_sections(repo=repo, query="calibration", storage_path=str(tmp_path))

        rows = _tt.ranking_db_query(base_path=str(tmp_path))
        assert len(rows) == 1
        assert rows[0]["tool"] == "search_sections"
        assert rows[0]["semantic_used"] == 0
