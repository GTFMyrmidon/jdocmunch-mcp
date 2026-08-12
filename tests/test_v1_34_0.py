"""Tests for v1.34.0: section dedup detector + dedupe flag + wiki-stats integration."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.retrieval import dedup
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import index_local


# ---------------------------------------------------------------------------
# detect_clusters
# ---------------------------------------------------------------------------

class TestDetectClusters:
    def _section(self, sid: str, content: str) -> dict:
        return {"id": sid, "content": content}

    def test_no_dupes_returns_empty(self):
        secs = [
            self._section("a", "alpha beta gamma delta echo foxtrot golf hotel india juliet"),
            self._section("b", "kilo lima mike november oscar papa quebec romeo sierra tango"),
        ]
        assert dedup.detect_clusters(secs) == []

    def test_identical_sections_cluster(self):
        body = "the quick brown fox jumps over the lazy dog and runs away fast"
        secs = [self._section("a", body), self._section("b", body)]
        clusters = dedup.detect_clusters(secs, min_jaccard=0.85)
        assert len(clusters) == 1
        c = clusters[0]
        assert set(c["member_ids"]) == {"a", "b"}

    def test_near_dupe_above_threshold(self):
        # 5-shingle Jaccard needs lots of context for a single-word edit
        # to register as near-dupe. Build a 40-word body and mutate one.
        prefix = "alpha beta gamma delta epsilon zeta eta theta iota kappa "
        a = prefix + "the quick brown fox jumps over the lazy dog and runs away fast " + \
            "lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega"
        b = a.replace("lazy dog", "lazy hound")
        secs = [self._section("a", a), self._section("b", b)]
        clusters = dedup.detect_clusters(secs, min_jaccard=0.5)
        assert len(clusters) == 1

    def test_below_threshold_not_clustered(self):
        a = "alpha beta gamma delta echo foxtrot golf hotel india juliet"
        b = "kilo lima mike november oscar papa quebec romeo sierra tango"
        secs = [self._section("a", a), self._section("b", b)]
        clusters = dedup.detect_clusters(secs, min_jaccard=0.85)
        assert clusters == []

    def test_short_section_skipped(self):
        # Both under _MIN_TOKENS — neither qualifies even though they're identical.
        secs = [self._section("a", "tiny one"), self._section("b", "tiny one")]
        assert dedup.detect_clusters(secs) == []

    def test_representative_is_longest(self):
        a = "alpha beta gamma delta echo foxtrot golf hotel india juliet"
        b = a + " EXTRA tokens that make this section longer than its peer"
        secs = [self._section("a", a), self._section("b", b)]
        clusters = dedup.detect_clusters(secs, min_jaccard=0.4)
        assert len(clusters) == 1
        # b is longer, so it's the representative.
        assert clusters[0]["representative_id"] == "b"

    def test_three_way_cluster(self):
        body = "the quick brown fox jumps over the lazy dog and runs away fast"
        secs = [
            self._section("a", body),
            self._section("b", body + " slightly different"),
            self._section("c", body + " another variant"),
        ]
        clusters = dedup.detect_clusters(secs, min_jaccard=0.5)
        assert len(clusters) == 1
        assert set(clusters[0]["member_ids"]) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestDedupPersistence:
    def test_round_trip(self, tmp_path):
        body = "the quick brown fox jumps over the lazy dog and runs away fast"
        secs = [{"id": "a", "content": body}, {"id": "b", "content": body}]
        n = dedup.write(str(tmp_path), "owner", "name", secs)
        assert n == 1
        clusters = dedup.load(str(tmp_path), "owner", "name")
        assert len(clusters) == 1

    def test_load_missing_empty(self, tmp_path):
        assert dedup.load(str(tmp_path), "owner", "missing") == []

    def test_purge(self, tmp_path):
        body = "the quick brown fox jumps over the lazy dog and runs away fast"
        secs = [{"id": "a", "content": body}, {"id": "b", "content": body}]
        dedup.write(str(tmp_path), "o", "n", secs)
        assert dedup.purge(str(tmp_path), "o", "n") is True
        assert dedup.purge(str(tmp_path), "o", "n") is False

    def test_build_member_to_rep(self):
        clusters = [
            {"representative_id": "rep1", "member_ids": ["rep1", "m1", "m2"]},
            {"representative_id": "rep2", "member_ids": ["rep2", "m3"]},
        ]
        out = dedup.build_member_to_rep(clusters)
        assert out == {"m1": "rep1", "m2": "rep1", "m3": "rep2"}


# ---------------------------------------------------------------------------
# index_local writes the sidecar
# ---------------------------------------------------------------------------

class TestIndexLocalWritesDedupSidecar:
    def test_dupes_detected_at_index_time(self, tmp_path):
        body_template = textwrap.dedent("""
            The configuration file lives at config.toml.
            The loader searches in three locations and picks the first match.
            All other locations are ignored after the first match is found.
            Required fields include api_key and endpoint with sensible defaults.
            Optional fields include timeout_seconds retry_count and log_level.
        """).strip()
        repo = tmp_path / "docs"
        repo.mkdir()
        # Two near-identical config pages, one different.
        (repo / "configA.md").write_text(f"# Config A\n\n{body_template}\n", encoding="utf-8")
        (repo / "configB.md").write_text(f"# Config B\n\n{body_template} (slight change)\n", encoding="utf-8")
        (repo / "different.md").write_text(
            "# Different\n\nUnrelated section about authentication tokens and API keys for the service.\n",
            encoding="utf-8",
        )
        index_local(
            path=str(repo), name="dup",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        clusters = dedup.load(str(tmp_path), "local", "dup")
        # At least one cluster involving the two near-identical pages.
        assert clusters
        # Verify the cluster contains both A and B's level-1 sections.
        all_member_ids = {m for c in clusters for m in c["member_ids"]}
        assert any("configA.md" in m for m in all_member_ids)
        assert any("configB.md" in m for m in all_member_ids)


# ---------------------------------------------------------------------------
# search_sections dedupe flag
# ---------------------------------------------------------------------------

class TestSearchSectionsDedupe:
    def _setup(self, tmp_path):
        body = textwrap.dedent("""
            Configure your environment by setting EXAMPLE_API_KEY in the shell.
            The loader searches the local config file then the user config file.
            Optional fields include timeout_seconds retry_count and log_level.
            Default values apply when the field is omitted from configuration.
        """).strip()
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "guideA.md").write_text(f"# Configuration\n\n{body}\n", encoding="utf-8")
        (repo / "guideB.md").write_text(f"# Configuration\n\n{body}\n", encoding="utf-8")
        index_local(
            path=str(repo), name="dedup_e2e",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

    def test_default_returns_dupes(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections

        self._setup(tmp_path)
        out = search_sections(
            repo="dedup_e2e", query="configuration loader",
            semantic=False, storage_path=str(tmp_path),
        )
        ids = [r["id"] for r in out["results"]]
        # Both guides' Configuration sections should appear.
        assert any("guideA.md" in i for i in ids)
        assert any("guideB.md" in i for i in ids)

    def test_dedupe_collapses_dupes(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections

        self._setup(tmp_path)
        out = search_sections(
            repo="dedup_e2e", query="configuration loader",
            semantic=False, dedupe=True, storage_path=str(tmp_path),
        )
        meta = out["_meta"]
        assert meta.get("dedupe") is True
        ids = [r["id"] for r in out["results"]]
        # Only one of the duplicate sections should remain.
        guides_in_results = sum(1 for i in ids if "guideA.md" in i or "guideB.md" in i)
        assert guides_in_results <= 1, ids
        # Suppressed members listed in _meta.deduped.
        if guides_in_results == 1:
            assert meta.get("deduped"), meta


# ---------------------------------------------------------------------------
# get_wiki_stats integration
# ---------------------------------------------------------------------------

class TestWikiStatsDuplicates:
    def test_clusters_surfaced(self, tmp_path):
        from jdocmunch_mcp.tools.get_wiki_stats import get_wiki_stats

        body = textwrap.dedent("""
            Configure your environment by setting EXAMPLE_API_KEY in the shell.
            The loader searches the local config file then the user config file.
            Optional fields include timeout_seconds retry_count and log_level.
            Default values apply when the field is omitted from configuration.
        """).strip()
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "a.md").write_text(f"# A\n\n{body}\n", encoding="utf-8")
        (repo / "b.md").write_text(f"# B\n\n{body}\n", encoding="utf-8")
        index_local(
            path=str(repo), name="dup_wiki",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        out = get_wiki_stats(repo="dup_wiki", storage_path=str(tmp_path))
        result = out["result"]
        assert "duplicate_cluster_count" in result
        assert "duplicate_clusters" in result
        assert result["duplicate_cluster_count"] >= 1
        assert result["duplicate_section_count"] >= 1
