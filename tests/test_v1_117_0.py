"""Absence evidence: cite a zero-result scan as proof (handoff/v2 phase 3).

Suite parity with jcodemunch-mcp v1.108.166 (jcodemunch-mcp#377 phase 3).

A zero-result search could not be cited under v1/v2: nothing was served, so
there was no id to reference. But "we searched the complete, fresh,
non-truncated index and it is not there" is exactly the claim an audit most
needs attested, and the one agents most often assert with no proof at all.

The refusal rules carry the weight here. Getting them wrong lets a handoff
attest something false, so each one is pinned separately:
  - only `absent` proves absence;
  - `low_confidence` and `degraded` do not;
  - a stale index does not;
  - a truncated index does not;
  - unknown coverage is disclosed as unknown, not rendered as complete.
"""

from __future__ import annotations

import pytest

from jdocmunch_mcp import handoff


@pytest.fixture(autouse=True)
def _clear():
    handoff.clear_handoffs()
    handoff.clear_absences()
    yield
    handoff.clear_handoffs()
    handoff.clear_absences()


def _verdict(state="absent", index="fresh", sections=9412, documents=2148, coverage=None):
    v = {
        "state": state,
        "scanned": {"sections": sections, "documents": documents},
        "channels": {"lexical": "ok", "semantic": "off", "index": index},
        "scorer": 1,
        "note": "n/a",
    }
    if coverage:
        v["coverage"] = coverage
    return v


COVERAGE = {
    "generation": {"git_head": "c2e53092ac68", "index_version": 17},
    "files_indexed": 2148,
    "excluded": {"gitignore": 4033, "skip_dir": 28},
}


def _record(**kw):
    """Record a scan and return (ref, refusal).

    Pop every non-verdict kwarg BEFORE building the verdict: the remainder is
    forwarded to `_verdict`, and Python evaluates call arguments left to right.
    """
    tool = kw.pop("tool", "search_sections")
    repo = kw.pop("repo", "owner/name")
    query = kw.pop("query", "MigrationGuarantee")
    verdict = kw.pop("verdict", None)
    arguments = kw.pop("arguments", None)
    truncated = kw.pop("truncated", False)
    return handoff.note_absence(
        tool,
        repo,
        query,
        verdict or _verdict(**kw),
        arguments=arguments,
        truncated=truncated,
    )


def _finalize(refs, claims=None):
    sections = [{"heading": "Migration surface", "content": "Body."}]
    if claims:
        sections = [{"heading": "Migration surface", "claims": claims}]
    return handoff.finalize_handoff(
        repo="owner/name",
        task="Audit the migration docs",
        sections=sections,
        evidence_refs=refs,
        served=(frozenset({"docs/architecture.md::storage-model"}), frozenset({"docs/architecture.md"})),
    )


class TestOnlyAbsentProves:
    def test_absent_over_fresh_index_yields_a_citable_ref(self):
        ref, refusal = _record(coverage=COVERAGE)
        assert refusal is None
        assert ref and ref.startswith(handoff.ABSENCE_REF_PREFIX)

    def test_low_confidence_is_refused(self):
        ref, refusal = _record(state="low_confidence")
        assert ref is None
        assert "low_confidence" in refusal

    def test_degraded_is_refused(self):
        ref, refusal = _record(state="degraded")
        assert ref is None
        assert "degraded" in refusal

    def test_ok_is_never_recorded_at_all(self):
        ref, refusal = _record(state="ok")
        assert (ref, refusal) == (None, None)


class TestIndexIntegrityGates:
    def test_stale_index_cannot_prove_absence(self):
        ref, refusal = _record(index="stale")
        assert ref is None
        assert "stale" in refusal

    def test_truncated_index_cannot_prove_absence(self):
        ref, refusal = _record(truncated=True)
        assert ref is None
        assert "truncated" in refusal

    def test_a_refused_scan_is_still_recorded_so_the_reason_survives(self):
        # The record must persist even when no token is handed out, so a caller
        # that constructs the ref gets the reason and not a bare unknown-ref.
        _record(index="stale")
        good_ref, _ = _record(index="fresh")
        stale_ref, _ = handoff.note_absence(
            "search_sections", "owner/name", "MigrationGuarantee",
            _verdict(index="stale"),
        )
        assert stale_ref is None
        assert good_ref is not None


class TestCitation:
    def test_absence_ref_attests_in_a_handoff(self):
        ref, _ = _record(coverage=COVERAGE)
        receipt = _finalize([ref])
        assert "error" not in receipt
        assert receipt["absence_attested"] == 1

    def test_unrecorded_absence_ref_is_unknown_not_refused(self):
        result = _finalize([handoff.ABSENCE_REF_PREFIX + "deadbeefcafe"])
        assert "error" in result
        assert "unknown_refs" in result

    def test_refused_scan_cited_directly_reports_the_reason(self):
        handoff.note_absence(
            "search_sections", "owner/name", "Ghost", _verdict(index="stale")
        )
        ref = handoff._absence_ref("search_sections", "owner/name", "Ghost", {})
        result = _finalize([ref])
        assert "error" in result
        assert "absence evidence refused" in result["error"]
        assert result["refused_absence"][0]["ref"] == ref
        assert "stale" in result["refused_absence"][0]["reason"]

    def test_refused_scan_cited_by_a_claim_names_the_claim(self):
        handoff.note_absence(
            "search_sections", "owner/name", "Ghost", _verdict(index="stale")
        )
        ref = handoff._absence_ref("search_sections", "owner/name", "Ghost", {})
        result = _finalize(
            [],
            claims=[{"id": "no.impl", "statement": "No implementation exists.",
                     "evidence_refs": [ref]}],
        )
        assert "error" in result
        assert result["refused_absence_claims"][0]["claim_id"] == "no.impl"

    def test_claim_can_cite_absence_alongside_a_served_symbol(self):
        ref, _ = _record(coverage=COVERAGE)
        receipt = _finalize(
            [],
            claims=[{
                "id": "no.impl",
                "statement": "No implementation with this identity exists.",
                "evidence_refs": [ref, "docs/architecture.md::storage-model"],
            }],
        )
        assert "error" not in receipt
        assert receipt["absence_attested"] == 1
        assert receipt["claims_attested"] == 1


class TestRenderedProofIsAuditable:
    def test_body_carries_the_scan_scope_and_counts(self):
        ref, _ = _record(coverage=COVERAGE, arguments={"path_glob": "docs/**"})
        receipt = _finalize([ref])
        body = handoff.get_handoff(receipt["handoff_id"])["body"]
        assert "absence proof: `search_sections` query 'MigrationGuarantee'" in body
        assert "scope: path_glob='docs/**'" in body
        assert "scanned: documents=2148, sections=9412" in body
        assert "index=fresh" in body
        assert "coverage: 2148 files indexed" in body
        assert "excluded: gitignore=4033, skip_dir=28" in body
        assert "scorer: 1" in body

    def test_unknown_coverage_is_disclosed_not_implied_complete(self):
        ref, _ = _record(coverage=None)
        receipt = _finalize([ref])
        body = handoff.get_handoff(receipt["handoff_id"])["body"]
        assert "coverage: not recorded for this index (scope unknown)" in body
        assert "files indexed" not in body

    def test_unscoped_search_says_whole_repository(self):
        ref, _ = _record(coverage=COVERAGE)
        receipt = _finalize([ref])
        body = handoff.get_handoff(receipt["handoff_id"])["body"]
        assert "scope: whole indexed corpus" in body


class TestRefIdentity:
    def test_same_scan_same_scope_is_the_same_ref(self):
        first, _ = _record(arguments={"path_glob": "docs/**"})
        second, _ = _record(arguments={"path_glob": "docs/**"})
        assert first == second

    def test_a_different_scope_is_a_different_proof(self):
        broad, _ = _record()
        narrow, _ = _record(arguments={"path_glob": "docs/**"})
        assert broad != narrow

    def test_a_different_query_is_a_different_proof(self):
        first, _ = _record(query="Alpha")
        second, _ = _record(query="Beta")
        assert first != second

    def test_scope_arg_order_does_not_change_the_ref(self):
        a, _ = _record(arguments={"path_glob": "docs/**", "role": "guide"})
        handoff.clear_absences()
        b, _ = _record(arguments={"role": "guide", "path_glob": "docs/**"})
        assert a == b


class TestV1AndV2Unaffected:
    def test_handoff_with_no_absence_omits_the_receipt_key(self):
        receipt = _finalize(["docs/architecture.md::storage-model"])
        assert "absence_attested" not in receipt
        assert receipt["schema"] == "jdocmunch.handoff/v1"

    def test_absence_alone_still_renders_a_v1_body_when_no_claims(self):
        ref, _ = _record(coverage=COVERAGE)
        receipt = _finalize([ref])
        assert receipt["schema"] == "jdocmunch.handoff/v1"
        assert receipt["absence_attested"] == 1

    def test_scan_detail_renders_once_when_a_claim_owns_it(self):
        # Repeating the whole scan block under the claim AND in the global
        # index is noise in the artifact meant to be read.
        ref, _ = _record(coverage=COVERAGE)
        receipt = _finalize(
            [],
            claims=[{"id": "no.impl", "statement": "Nothing implements it.",
                     "evidence_refs": [ref]}],
        )
        body = handoff.get_handoff(receipt["handoff_id"])["body"]
        assert body.count("absence proof:") == 1
        assert body.count(f"`{ref}`") == 2  # cited under the claim + indexed

    def test_scan_detail_still_renders_in_the_index_without_claims(self):
        ref, _ = _record(coverage=COVERAGE)
        receipt = _finalize([ref])
        body = handoff.get_handoff(receipt["handoff_id"])["body"]
        assert body.count("absence proof:") == 1
