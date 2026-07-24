# -*- coding: utf-8 -*-
"""v1.118.0 — lexical search no longer lowercases the query before tokenizing.

Follow-up to #91, reported by @tetiz123 while validating the CJK tokenizer fix
on a real Korean corpus. ``DocIndex._lexical_search`` passed ``query.lower()``
to the scorer, but ``bm25.tokenize`` inserts CamelCase boundaries BEFORE it
lowercases. So the two sides of the match disagreed for any identifier that
carries case information:

    document side  tokenize("OvertimeService")  -> ['overtime', 'service']
    query side     tokenize("overtimeservice")  -> ['overtimeservice']

Code-identifier queries — the one thing BM25 should be unbeatable at — scored
0.0 and returned a silent empty list (Stage-A prune tokenizes the ORIGINAL
query, so candidates survive the prune and then every one scores 0). Fix: feed
the scorer the raw query; ``tokenize`` lowercases internally after de-camel, so
it is both correct and free. Underscore identifiers were never affected (the
delimiter is case-independent) and stay working.
"""

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.retrieval.bm25 import compute_corpus_stats, score_section
from jdocmunch_mcp.storage.doc_store import DocStore


SAMPLE_MD = """# API Reference

## Overtime rules

The OvertimeService computes the monthly cap. The AttendanceRepository
persists it. Error code HCA060T is raised when the cap is exceeded, and the
SPM_NOTIFICATION flag controls the alert.

## Deployment

How to deploy the cluster and roll back a release.
"""


def _make_index(tmp_path):
    store = DocStore(base_path=str(tmp_path))
    sections = parse_file(SAMPLE_MD, "api.md", "test/repo")
    store.save_index(
        owner="local",
        name="ident_test",
        sections=sections,
        raw_files={"api.md": SAMPLE_MD},
        doc_types={".md": 1},
    )
    return store.load_index("local", "ident_test")


class TestRootCause:
    """The tokenizer asymmetry that the pre-lowercasing exposed."""

    def test_camelcase_query_side_disagrees_when_lowercased(self):
        sec = {
            "id": "r::api.md::overtime#1",
            "title": "Overtime",
            "summary": "",
            "content": "The OvertimeService computes the cap. Error HCA060T.",
        }
        stats = compute_corpus_stats([sec])
        # Original-case query tokenizes the same way the document did -> hit.
        assert score_section(sec, "OvertimeService", stats=stats) > 0.0
        assert score_section(sec, "HCA060T", stats=stats) > 0.0
        # Pre-lowercased query is what the buggy caller passed -> miss.
        assert score_section(sec, "overtimeservice", stats=stats) == 0.0
        assert score_section(sec, "hca060t", stats=stats) == 0.0


class TestLexicalSearchEndToEnd:
    def test_camelcase_identifier_query_finds_its_section(self, tmp_path):
        index = _make_index(tmp_path)
        results = index.search("OvertimeService", max_results=5, semantic=False)
        assert results, "CamelCase identifier query must return its section (#91 follow-up)"
        assert results[0]["title"] == "Overtime rules"

    def test_acronym_suffix_identifier_query_finds_its_section(self, tmp_path):
        index = _make_index(tmp_path)
        results = index.search("HCA060T", max_results=5, semantic=False)
        assert results
        assert results[0]["title"] == "Overtime rules"

    def test_repository_identifier_query_finds_its_section(self, tmp_path):
        index = _make_index(tmp_path)
        results = index.search("AttendanceRepository", max_results=5, semantic=False)
        assert results
        assert results[0]["title"] == "Overtime rules"

    def test_underscore_identifier_still_works(self, tmp_path):
        # The delimiter is case-independent, so this survived the bug — it must
        # keep working after the fix (control).
        index = _make_index(tmp_path)
        results = index.search("SPM_NOTIFICATION", max_results=5, semantic=False)
        assert results
        assert results[0]["title"] == "Overtime rules"

    def test_lowercase_prose_query_unaffected(self, tmp_path):
        # An ordinary lowercase query has no case information to lose; the fix
        # must not regress it (tokenize lowercases internally either way).
        index = _make_index(tmp_path)
        results = index.search("deploy cluster", max_results=5, semantic=False)
        assert results
        assert results[0]["title"] == "Deployment"

    def test_tag_kicker_still_case_folds(self, tmp_path):
        # query_words stays lowercased, so a differently-cased query still
        # matches a lowercased tag. Exercised implicitly: an all-caps query
        # against lowercase content must not error and must rank sanely.
        index = _make_index(tmp_path)
        results = index.search("OVERTIME", max_results=5, semantic=False)
        assert results
        assert results[0]["title"] == "Overtime rules"
