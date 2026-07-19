"""v1.103.0 — coverage contract on absence claims (suite parity with jcm).

An ``absent`` verdict backed only by scan counts lies by omission when files
were excluded at index time. A full discovery walk now persists a coverage
block (``DocIndex.coverage``); ``absent``/``degraded`` verdicts disclose it;
``ok`` stays lean; a legacy index (no recorded coverage) carries no block.
"""

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections
from jdocmunch_mcp.retrieval.verdict import (
    SCORER_VERSION,
    build_verdict,
    filter_verdict,
    index_coverage_meta,
)


SAMPLE_MD = """# Guide

## Sessions

Configure the session store before enabling sign-in flows.
"""


def _build_corpus(tmp_path):
    """Corpus with one good doc, one unsupported-extension trip-wire, and one
    file that parses to zero sections (plain YAML is read through the OpenAPI
    branch and yields nothing when it isn't a spec)."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(SAMPLE_MD, encoding="utf-8")
    (docs / "vendor.xyz").write_text("not a doc format", encoding="utf-8")
    (docs / "settings.yaml").write_text("alpha: 1\nbeta: 2\n", encoding="utf-8")
    return docs


def _index(tmp_path, docs):
    store_dir = tmp_path / "store"
    res = index_local(
        path=str(docs),
        name="covtest",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(store_dir),
    )
    assert res.get("success"), res
    return str(store_dir)


class TestCoveragePersisted:
    def test_full_walk_records_coverage(self, tmp_path):
        docs = _build_corpus(tmp_path)
        storage = _index(tmp_path, docs)
        index = DocStore(base_path=storage).load_index("local", "covtest")
        cov = index.coverage
        assert cov["walk"] == "full"
        assert cov["files_indexed"] == 1
        assert cov["skip_counts"]["unsupported_extension"] >= 1
        assert cov["no_sections_count"] >= 1
        assert cov["recorded_at"]

    def test_coverage_survives_subset_refresh(self, tmp_path):
        docs = _build_corpus(tmp_path)
        storage = _index(tmp_path, docs)
        (docs / "guide.md").write_text(
            SAMPLE_MD + "\n## Extras\n\nMore prose here.\n", encoding="utf-8"
        )
        res = index_local(
            path=str(docs),
            name="covtest",
            paths=["guide.md"],
            use_ai_summaries=False,
            use_embeddings=False,
            storage_path=storage,
        )
        assert res.get("success"), res
        index = DocStore(base_path=storage).load_index("local", "covtest")
        # Incremental subset save carries the recorded block forward.
        assert index.coverage.get("walk") == "full"
        assert index.coverage.get("skip_counts", {}).get("unsupported_extension", 0) >= 1


class TestVerdictDisclosure:
    def test_absent_verdict_discloses_coverage(self, tmp_path):
        docs = _build_corpus(tmp_path)
        storage = _index(tmp_path, docs)
        res = search_sections(
            repo="local/covtest",
            query="zzz_nonexistent_topic_qqq",
            storage_path=storage,
        )
        v = res["_meta"]["verdict"]
        assert v["state"] == "absent"
        cov = v["coverage"]
        assert cov["files_indexed"] == 1
        assert cov["excluded"]["unsupported_extension"] >= 1
        assert cov["no_sections_files"] >= 1
        assert cov["generation"]["indexed_at"]

    def test_degraded_verdict_discloses_coverage(self, tmp_path):
        docs = _build_corpus(tmp_path)
        storage = _index(tmp_path, docs)
        res = search_sections(
            repo="local/covtest",
            query="sessions",
            semantic=True,  # no embeddings on this index -> degraded
            storage_path=storage,
        )
        v = res["_meta"]["verdict"]
        assert v["state"] == "degraded"
        assert "coverage" in v

    def test_ok_verdict_stays_lean(self, tmp_path):
        docs = _build_corpus(tmp_path)
        storage = _index(tmp_path, docs)
        res = search_sections(
            repo="local/covtest", query="session store", storage_path=storage
        )
        assert res["result_count"] > 0
        v = res["_meta"]["verdict"]
        assert v["state"] in ("ok", "low_confidence")
        assert "coverage" not in v


class TestLegacyIndexNoBlock:
    def _legacy_index(self, tmp_path):
        """Index saved without a coverage block (pre-contract shape)."""
        store = DocStore(base_path=str(tmp_path))
        sections = parse_file(SAMPLE_MD, "README.md", "test/repo")
        store.save_index(
            owner="local",
            name="legacy",
            sections=sections,
            raw_files={"README.md": SAMPLE_MD},
            doc_types={".md": 1},
        )
        return store

    def test_index_coverage_meta_is_none(self, tmp_path):
        store = self._legacy_index(tmp_path)
        index = store.load_index("local", "legacy")
        assert index.coverage == {}
        assert index_coverage_meta(index) is None

    def test_absent_verdict_has_no_coverage_block(self, tmp_path):
        self._legacy_index(tmp_path)
        res = search_sections(
            repo="local/legacy",
            query="zzz_nonexistent_topic_qqq",
            storage_path=str(tmp_path),
        )
        v = res["_meta"]["verdict"]
        assert v["state"] == "absent"
        assert "coverage" not in v


class TestScorerPin:
    def test_build_verdict_carries_scorer_version(self):
        v = build_verdict(result_count=1, confidence=0.9)
        assert v["scorer"] == SCORER_VERSION == 1

    def test_filter_verdict_has_no_scorer(self):
        # Structured lookups emit no scores/thresholds, so no pin.
        assert "scorer" not in filter_verdict(0)

    def test_filter_verdict_attaches_coverage_on_absent_only(self):
        cov = {"generation": {"indexed_at": "t"}, "files_indexed": 3}
        assert filter_verdict(0, coverage=cov)["coverage"] == cov
        assert "coverage" not in filter_verdict(2, coverage=cov)
