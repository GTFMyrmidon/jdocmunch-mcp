# -*- coding: utf-8 -*-
"""v1.114.2 — canonical handoff contract (jdocmunch.handoff/v1).

Suite parity with jcodemunch-mcp v1.108.162 (#374 there): finalize_handoff
assembles a deterministic Markdown handoff from caller-authored sections,
attests evidence_refs against the session retrieval record (section ids /
doc paths served by search_sections / search_titles / get_section /
get_sections), and serves the immutable body via munch://handoff/<id>.
"""

import asyncio
import hashlib
import json

import pytest

from jdocmunch_mcp import handoff


SERVED = (
    frozenset({"guide.md::setup#1", "guide.md::install#2"}),
    frozenset({"docs/guide.md"}),
)


def _finalize(**over):
    args = dict(
        repo="my-docs",
        task="Audit the setup docs",
        sections=[{"heading": "Findings", "content": "Setup docs are current."}],
        evidence_refs=["guide.md::setup#1"],
        served=SERVED,
    )
    args.update(over)
    return handoff.finalize_handoff(**args)


@pytest.fixture(autouse=True)
def _clean():
    handoff.clear_handoffs()
    handoff.clear_session_record()
    yield
    handoff.clear_handoffs()
    handoff.clear_session_record()


class TestFinalize:
    def test_receipt_shape(self):
        r = _finalize()
        assert r["schema"] == "jdocmunch.handoff/v1"
        assert r["canonical"] is True
        assert r["content_type"] == "text/markdown"
        assert r["resource_uri"] == f"munch://handoff/{r['handoff_id']}"
        assert len(r["sha256"]) == 64 and r["length"] > 0

    def test_deterministic(self):
        a, b = _finalize(), _finalize()
        assert (a["handoff_id"], a["sha256"]) == (b["handoff_id"], b["sha256"])
        c = _finalize(task="Different task")
        assert c["handoff_id"] != a["handoff_id"]

    def test_sha_matches_body(self):
        r = _finalize()
        body = handoff.get_handoff(r["handoff_id"])["body"]
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == r["sha256"]
        assert len(body.encode("utf-8")) == r["length"]

    def test_doc_path_ref_attests(self):
        # A served doc path, and the doc-path component of a served id.
        assert _finalize(evidence_refs=["docs/guide.md"])["evidence_attested"] is True
        assert _finalize(evidence_refs=["guide.md"])["evidence_attested"] is True

    def test_unknown_ref_fails_closed(self):
        r = _finalize(evidence_refs=["ghost.md::x#1"])
        assert "error" in r and r["unknown_refs"] == ["ghost.md::x#1"]

    def test_empty_evidence_and_sections_rejected(self):
        assert "error" in _finalize(evidence_refs=[])
        assert "error" in _finalize(sections=[])

    def test_duplicate_appendix_rejected_and_exactly_once(self):
        assert "error" in _finalize(appendices=[
            {"name": "R", "content": "a"}, {"name": "R", "content": "b"},
        ])
        r = _finalize(appendices=[{"name": "Diag report", "content": "raw"}])
        body = handoff.get_handoff(r["handoff_id"])["body"]
        assert body.count("## Appendix: Diag report") == 1

    def test_no_char_limit(self):
        big = "x" * 300_000
        r = _finalize(sections=[{"heading": "Big", "content": big}])
        assert "error" not in r


class TestSessionRecord:
    def test_note_served_rows_feeds_attestation(self):
        handoff.note_served_rows([{"id": "a.md::intro#1", "doc_path": "docs/a.md"}])
        ids, paths = handoff.served_refs()
        assert "a.md::intro#1" in ids and "docs/a.md" in paths
        r = _finalize(evidence_refs=["a.md::intro#1"], served=None)
        assert r["evidence_attested"] is True

    def test_search_chokepoint_records(self, tmp_path):
        # End-to-end: index a doc, search it via call_tool, finalize with its path.
        (tmp_path / "readme_v1142.md").write_text(
            "# Handoff Probe v1142\n\nUnique corpus anchor text.\n", encoding="utf-8"
        )
        store = tmp_path / "store"
        from jdocmunch_mcp.tools.index_local import index_local
        res = index_local(path=str(tmp_path), name="handoff-probe-v1142",
                          storage_path=str(store))
        assert "error" not in res, res
        import jdocmunch_mcp.server as srv
        # Drive search through call_tool with explicit storage via env
        import os
        old_env = os.environ.get("DOC_INDEX_PATH")
        os.environ["DOC_INDEX_PATH"] = str(store)
        try:
            out = asyncio.run(srv.call_tool("search_titles", {
                "repo": "local/handoff-probe-v1142", "query": "Handoff Probe v1142",
            }))
            payload = json.loads(out[0].text)
            assert payload.get("results"), payload
        finally:
            if old_env is None:
                os.environ.pop("DOC_INDEX_PATH", None)
            else:
                os.environ["DOC_INDEX_PATH"] = old_env
        ids, paths = handoff.served_refs()
        assert ids or paths, "search_titles response did not feed the session record"


class TestResource:
    def test_repeated_reads_byte_identical(self):
        r = _finalize()
        from jdocmunch_mcp import server
        a = asyncio.run(server.read_resource(r["resource_uri"]))
        b = asyncio.run(server.read_resource(r["resource_uri"]))
        assert a[0].content == b[0].content
        assert a[0].mime_type == "text/markdown"

    def test_advertised_and_identity_unaffected(self):
        r = _finalize()
        from jdocmunch_mcp import server
        uris = [str(x.uri) for x in asyncio.run(server.list_resources())]
        assert r["resource_uri"] in uris
        assert "munch://runtime/identity" in uris

    def test_unknown_id_raises(self):
        from jdocmunch_mcp import server
        with pytest.raises(ValueError):
            asyncio.run(server.read_resource("munch://handoff/0000000000000000"))


class TestRegistration:
    def test_tool_registered_and_write_annotated(self):
        import jdocmunch_mcp.server as srv
        tools = asyncio.run(srv.list_tools())
        t = next((x for x in tools if x.name == "finalize_handoff"), None)
        assert t is not None
        assert t.annotations is not None and t.annotations.readOnlyHint is False
        assert "finalize_handoff" in srv._TOOL_TIER_STANDARD
        assert "finalize_handoff" in srv._NON_READONLY_TOOLS

    def test_dispatch_error_shape(self):
        from jdocmunch_mcp import server
        res = asyncio.run(server.call_tool("finalize_handoff", {
            "repo": "r", "task": "t",
            "sections": [{"heading": "H", "content": "C"}],
            "evidence_refs": ["never-served.md"],
        }))
        body = json.loads(res[0].text)
        assert body["unknown_refs"] == ["never-served.md"]
