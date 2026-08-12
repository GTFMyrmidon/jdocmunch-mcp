"""Tests for v1.16.0: section freshness probe + retrieval confidence."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.retrieval.confidence import (
    WEIGHTS,
    _freshness as _conf_freshness,
    _gap,
    _identity,
    _strength,
    attach_confidence,
    compute_confidence,
)
from jdocmunch_mcp.retrieval.freshness import FreshnessProbe
from jdocmunch_mcp.storage import DocStore


# ---------------------------------------------------------------------------
# Confidence sub-signals
# ---------------------------------------------------------------------------

class TestConfidenceComponents:
    def test_gap_decisive_top1(self):
        assert _gap(10.0, 0.0) == 1.0

    def test_gap_tied(self):
        assert _gap(5.0, 5.0) == 0.0

    def test_gap_zero_top1(self):
        assert _gap(0.0, 0.0) == 0.0

    def test_strength_zero_at_zero(self):
        assert _strength(0.0) == 0.0

    def test_strength_saturates_high(self):
        assert _strength(100.0) > 0.99

    def test_identity_exact_title_match(self):
        results = [{"title": "Search Sections"}, {"title": "Other"}]
        assert _identity("search sections", results) == 1.0

    def test_identity_no_match_default_07(self):
        assert _identity("xyz", [{"title": "abc"}]) == 0.7

    def test_identity_empty_query(self):
        assert _identity("", [{"title": "abc"}]) == 0.7

    def test_freshness_all_fresh(self):
        results = [{"_freshness": "fresh"}, {"_freshness": "fresh"}]
        assert _conf_freshness(results) == 1.0

    def test_freshness_one_stale_drops(self):
        results = [{"_freshness": "fresh"}, {"_freshness": "stale_index"}]
        assert _conf_freshness(results) == 0.6

    def test_freshness_uncommitted_treated_as_non_fresh(self):
        assert _conf_freshness([{"_freshness": "edited_uncommitted"}]) == 0.6


class TestComputeConfidence:
    def test_empty_results(self):
        out = compute_confidence("q", [])
        assert out["value"] == 0.0
        assert "components" in out

    def test_high_score_high_gap_high_confidence(self):
        results = [
            {"title": "Hit", "_score": 12.0, "_freshness": "fresh"},
            {"title": "Other", "_score": 1.0, "_freshness": "fresh"},
        ]
        out = compute_confidence("q", results)
        assert out["value"] > 0.7

    def test_zero_scores_low_confidence(self):
        results = [{"title": "x", "_score": 0.0}, {"title": "y", "_score": 0.0}]
        out = compute_confidence("q", results)
        assert out["value"] < 0.5

    def test_components_returned(self):
        results = [{"title": "Hit", "_score": 8.0}, {"title": "Other", "_score": 4.0}]
        out = compute_confidence("hit", results)
        assert set(out["components"].keys()) == set(WEIGHTS.keys())
        assert out["components"]["identity"] == 1.0  # exact title match for "Hit"

    def test_identity_does_not_punish_paraphrase(self):
        # "embedding cache" doesn't equal title — but identity floor is 0.7.
        results = [{"title": "Embedding sidecar", "_score": 5.0, "_freshness": "fresh"}]
        out = compute_confidence("embedding cache", results)
        assert out["components"]["identity"] == 0.7
        # Confidence should still be usable.
        assert out["value"] > 0.3

    def test_attach_confidence_mutates_meta(self):
        meta = {"latency_ms": 5}
        results = [{"title": "x", "_score": 5.0, "_freshness": "fresh"}]
        attach_confidence("q", results, meta)
        assert "confidence" in meta
        assert "confidence_components" not in meta  # off by default

    def test_attach_confidence_with_components(self):
        meta = {}
        results = [{"title": "x", "_score": 5.0, "_freshness": "fresh"}]
        attach_confidence("q", results, meta, include_components=True)
        assert "confidence_components" in meta


# ---------------------------------------------------------------------------
# FreshnessProbe
# ---------------------------------------------------------------------------

class TestFreshnessProbe:
    def _build(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        content = (
            "# Top\n\n"
            "## Alpha\n\nbody alpha here\n\n"
            "## Beta\n\nbody beta there\n"
        )
        sections = parse_file(content, "g.md", "local/r")
        store.save_index(
            owner="local",
            name="r",
            sections=sections,
            raw_files={"g.md": content},
            doc_types={".md": 1},
        )
        index = store.load_index("local", "r")
        return store, index, content

    def test_fresh_index_marks_all_fresh(self, tmp_path):
        store, index, _ = self._build(tmp_path)
        probe = FreshnessProbe(store, "local", "r", index)
        results = [dict(s) for s in index.sections]
        for sec in results:
            probe.annotate(sec)
        for sec in results:
            assert sec["_freshness"] == "fresh", sec.get("title")

    def test_byte_range_drift_marks_stale(self, tmp_path):
        store, index, original_content = self._build(tmp_path)

        # Mutate the on-disk file so byte_range hashes won't match.
        # Write bytes directly so Windows newline translation can't drift the offsets.
        cached_path = store._safe_content_path(store._content_dir("local", "r"), "g.md")
        new_text = original_content.replace("body alpha", "BODY ALPHA REWRITTEN")
        cached_path.write_bytes(new_text.encode("utf-8"))

        probe = FreshnessProbe(store, "local", "r", index)
        # Pick the Alpha section.
        alpha = next(s for s in index.sections if s.get("title") == "Alpha")
        bucket = probe.annotate(dict(alpha))
        assert bucket == "stale_index"

    def test_full_file_change_marks_edited_uncommitted(self, tmp_path):
        store, index, original_content = self._build(tmp_path)

        # Append a new line at the END so existing byte_ranges still hash
        # the same but the full-file hash changes.
        # Binary write — bypass Windows CRLF translation that would drift
        # offsets and false-trip the byte-range comparison.
        cached_path = store._safe_content_path(store._content_dir("local", "r"), "g.md")
        cached_path.write_bytes(
            (original_content + "\n## Gamma added later\n\nnew body\n").encode("utf-8")
        )

        probe = FreshnessProbe(store, "local", "r", index)
        # Pick a section whose byte_range is unaffected (Alpha — early in file).
        alpha = next(s for s in index.sections if s.get("title") == "Alpha")
        bucket = probe.annotate(dict(alpha))
        assert bucket == "edited_uncommitted"

    def test_missing_file_marks_stale(self, tmp_path):
        store, index, _ = self._build(tmp_path)
        cached_path = store._safe_content_path(store._content_dir("local", "r"), "g.md")
        cached_path.unlink()

        probe = FreshnessProbe(store, "local", "r", index)
        alpha = next(s for s in index.sections if s.get("title") == "Alpha")
        bucket = probe.annotate(dict(alpha))
        assert bucket == "stale_index"

    def test_summary_aggregates(self, tmp_path):
        store, index, _ = self._build(tmp_path)
        probe = FreshnessProbe(store, "local", "r", index)
        results = [dict(s) for s in index.sections]
        for sec in results:
            probe.annotate(sec)
        # All fresh — summary's fresh count equals len(results).
        summary = probe.summary(results)
        assert summary["fresh"] == len(results)
        assert summary["edited_uncommitted"] == 0
        assert summary["stale_index"] == 0

    def test_per_file_cache_hits(self, tmp_path):
        store, index, _ = self._build(tmp_path)
        probe = FreshnessProbe(store, "local", "r", index)
        # Annotating multiple sections from the same file should populate
        # _file_state once.
        results = [dict(s) for s in index.sections if s.get("doc_path") == "g.md"]
        for sec in results:
            probe.annotate(sec)
        # File hash cache should have one entry for g.md.
        assert "g.md" in probe._file_state


# ---------------------------------------------------------------------------
# End-to-end via search_sections
# ---------------------------------------------------------------------------

class TestSearchSectionsIntegration:
    def test_meta_carries_confidence_and_freshness(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections

        store = DocStore(base_path=str(tmp_path))
        content = "# Top\n\n## Auth\n\ntoken refresh details here\n\n## Misc\n\nunrelated\n"
        sections = parse_file(content, "g.md", "local/r")
        store.save_index(
            owner="local",
            name="r",
            sections=sections,
            raw_files={"g.md": content},
            doc_types={".md": 1},
        )

        out = search_sections(repo="local/r", query="token refresh", semantic=False, storage_path=str(tmp_path))
        meta = out["_meta"]
        assert "confidence" in meta
        assert isinstance(meta["confidence"], float)
        assert 0.0 <= meta["confidence"] <= 1.0
        assert "freshness" in meta
        for sec in out["results"]:
            assert "_freshness" in sec

    def test_stale_index_lowers_confidence(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections

        store = DocStore(base_path=str(tmp_path))
        content = "# Top\n\n## Auth\n\ntoken refresh body\n\n## Misc\n\nfiller\n"
        sections = parse_file(content, "g.md", "local/r")
        store.save_index(
            owner="local",
            name="r",
            sections=sections,
            raw_files={"g.md": content},
            doc_types={".md": 1},
        )

        # Capture fresh-state confidence.
        fresh_out = search_sections(
            repo="local/r", query="token refresh", semantic=False, storage_path=str(tmp_path)
        )
        fresh_conf = fresh_out["_meta"]["confidence"]

        # Append-only mutation preserves the matching tokens AND the byte
        # ranges of pre-existing sections. Full-file hash drifts → freshness
        # marker should drop to edited_uncommitted on existing sections.
        cached = store._safe_content_path(store._content_dir("local", "r"), "g.md")
        cached.write_bytes(
            (content + "\n## Notes\n\nappended later\n").encode("utf-8")
        )

        # Force a fresh load (clear in-memory cache).
        from jdocmunch_mcp.storage.doc_store import _INDEX_CACHE
        _INDEX_CACHE.clear()

        stale_out = search_sections(
            repo="local/r", query="token refresh", semantic=False, storage_path=str(tmp_path)
        )
        # Freshness summary should report the file's edits.
        non_fresh = (
            stale_out["_meta"]["freshness"].get("edited_uncommitted", 0)
            + stale_out["_meta"]["freshness"].get("stale_index", 0)
        )
        assert non_fresh > 0
        # Confidence cannot exceed the fresh-state confidence.
        stale_conf = stale_out["_meta"]["confidence"]
        assert stale_conf <= fresh_conf
