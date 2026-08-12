"""v1.95.0 — suite-parity _meta.verdict on search_sections + find_endpoint."""

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.search_sections import search_sections
from jdocmunch_mcp.retrieval.verdict import (
    build_verdict,
    filter_verdict,
    suggest_docs,
    suggest_endpoints,
)


SAMPLE_MD = """# Guide

## Authentication

To sign in users, configure OAuth 2.0 with your provider.
Tokens are returned as JWTs and stored in the session.

## Payments

We use Stripe for credit card processing.
"""


def _make_index(tmp_path):
    store = DocStore(base_path=str(tmp_path))
    sections = parse_file(SAMPLE_MD, "README.md", "test/repo")
    store.save_index(
        owner="local",
        name="verdict_test",
        sections=sections,
        raw_files={"README.md": SAMPLE_MD},
        doc_types={".md": 1},
    )
    return "local/verdict_test"


class TestVerdictUnit:
    def test_ok(self):
        v = build_verdict(result_count=3, confidence=0.9)
        assert v["state"] == "ok"
        assert v["channels"] == {"lexical": "ok", "semantic": "off", "index": "fresh"}

    def test_low_confidence(self):
        v = build_verdict(result_count=2, confidence=0.2)
        assert v["state"] == "low_confidence"

    def test_absent(self):
        v = build_verdict(result_count=0, confidence=0.0, did_you_mean=["docs/a.md"])
        assert v["state"] == "absent"
        assert v["did_you_mean"] == ["docs/a.md"]

    def test_degraded_takes_precedence(self):
        # semantic asked for on an index with no embeddings — even with hits.
        v = build_verdict(
            result_count=5, confidence=0.9,
            semantic_requested=True, semantic_available=False,
        )
        assert v["state"] == "degraded"
        assert v["channels"]["semantic"] == "unavailable"

    def test_index_stale_channel(self):
        v = build_verdict(result_count=1, confidence=0.9, index_stale=True)
        assert v["channels"]["index"] == "stale"

    def test_suggest_docs(self):
        secs = [{"doc_path": "guide/auth.md", "title": "Authentication"},
                {"doc_path": "guide/pay.md", "title": "Payments"}]
        assert suggest_docs("auth", secs) == ["guide/auth.md"]
        assert suggest_docs("nomatchzzz", secs) == []

    def test_filter_verdict_and_suggest_endpoints(self):
        assert filter_verdict(2)["state"] == "ok"
        absent = filter_verdict(0, did_you_mean=["/pets/{id}"])
        assert absent["state"] == "absent"
        assert absent["did_you_mean"] == ["/pets/{id}"]
        # no channels on a structured filter verdict
        assert "channels" not in absent
        paths = ["/pets/{id}", "/pets", "/stores/{id}"]
        assert "/pets/{id}" in suggest_endpoints("/pets/*", paths)


class TestVerdictOnSearchSections:
    def test_ok_on_hit(self, tmp_path):
        repo = _make_index(tmp_path)
        res = search_sections(repo=repo, query="authentication", storage_path=str(tmp_path))
        assert res["result_count"] > 0
        assert res["_meta"]["verdict"]["state"] in ("ok", "low_confidence")

    def test_absent_on_miss(self, tmp_path):
        repo = _make_index(tmp_path)
        res = search_sections(
            repo=repo, query="kubernetes_helm_nonexistent", storage_path=str(tmp_path)
        )
        assert res["result_count"] == 0
        assert res["_meta"]["verdict"]["state"] == "absent"

    def test_degraded_when_semantic_on_unembedded_index(self, tmp_path):
        repo = _make_index(tmp_path)  # no embeddings attached
        res = search_sections(
            repo=repo, query="authentication", semantic=True, storage_path=str(tmp_path)
        )
        v = res["_meta"]["verdict"]
        assert v["state"] == "degraded"
        assert v["channels"]["semantic"] == "unavailable"
