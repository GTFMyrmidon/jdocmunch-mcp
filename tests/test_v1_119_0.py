"""v1.119.0 — a rebuild underneath a scan cannot prove absence (5th refusal rule).

Suite parity with jcodemunch-mcp v1.108.168. The absence contract shipped four
refusal rules (only `absent`; never low_confidence/degraded; never stale; never
truncated) and none covered an index being REWRITTEN while the scan reads it.

Index staleness here is `source_dirty`, which says the SOURCE moved — it is
blind to a reindex that rewrites sections under an unchanged tree. That matters
more in jdoc than elsewhere: sections score through a lazy content loader that
reads body text from disk at scan time.
"""

import pytest

from jdocmunch_mcp import handoff
from jdocmunch_mcp.retrieval.verdict import build_verdict, index_changed_since_load


class _FakeIndex:
    """Stand-in for a DocIndex (which already accepts stamped attributes)."""


@pytest.fixture()
def clean_absences():
    handoff.clear_absences()
    yield
    handoff.clear_absences()


def _verdict(state, index_channel="fresh"):
    return {
        "state": state,
        "scanned": {"sections": 10},
        "channels": {"lexical": "ok", "semantic": "off", "index": index_channel},
        "scorer": 1,
    }


class TestChangeDetection:
    def test_unstamped_index_is_not_changed(self):
        """Unknown must never mean changed, or every verdict degrades."""
        assert index_changed_since_load(_FakeIndex()) is False

    def test_unchanged_monolith_reports_false(self, tmp_path):
        f = tmp_path / "index.json"
        f.write_text("{}")
        idx = _FakeIndex()
        idx._index_path = str(f)
        idx._loaded_mtime_ns = f.stat().st_mtime_ns
        assert index_changed_since_load(idx) is False

    def test_rewritten_monolith_reports_true(self, tmp_path):
        f = tmp_path / "index.json"
        f.write_text("{}")
        idx = _FakeIndex()
        idx._index_path = str(f)
        idx._loaded_mtime_ns = 1  # a mtime the file cannot have
        assert index_changed_since_load(idx) is True

    def test_missing_monolith_never_raises(self, tmp_path):
        idx = _FakeIndex()
        idx._index_path = str(tmp_path / "gone.json")
        idx._loaded_mtime_ns = 12345
        assert index_changed_since_load(idx) is False


class TestVerdictGate:
    def test_zero_results_mid_rebuild_is_degraded_not_absent(self):
        v = build_verdict(result_count=0, index_changed=True)
        assert v["state"] == "degraded"
        assert v["channels"]["index"] == "rebuilding"

    def test_zero_results_on_a_settled_index_still_proves_absence(self):
        v = build_verdict(result_count=0, index_changed=False)
        assert v["state"] == "absent"
        assert v["channels"]["index"] == "fresh"

    def test_results_are_still_returned_mid_rebuild(self):
        """Only the absence CLAIM is refused; a real hit is still a real hit."""
        v = build_verdict(result_count=5, confidence=0.9, index_changed=True)
        assert v["state"] == "ok"
        assert v["channels"]["index"] == "rebuilding"

    def test_rebuilding_outranks_stale_in_the_channel(self):
        v = build_verdict(result_count=0, index_stale=True, index_changed=True)
        assert v["channels"]["index"] == "rebuilding"

    def test_default_is_byte_identical_to_pre_1_119_0(self):
        assert build_verdict(result_count=0) == build_verdict(
            result_count=0, index_changed=False
        )


class TestAbsenceRefusal:
    def test_rebuild_refusal_names_the_cause(self):
        reason = handoff.absence_refusal(
            {"state": "degraded", "channels": {"index": "rebuilding"}}
        )
        assert reason is not None
        assert "rewritten" in reason

    def test_rebuild_scan_yields_no_citable_ref(self, clean_absences):
        ref, refusal = handoff.note_absence(
            "search_sections", "o/r", "widget",
            _verdict("degraded", index_channel="rebuilding"),
        )
        assert ref is None
        assert "rewritten" in refusal

    def test_settled_index_still_mints_a_ref(self, clean_absences):
        ref, refusal = handoff.note_absence(
            "search_sections", "o/r", "widget", _verdict("absent")
        )
        assert refusal is None
        assert ref and ref.startswith("absent:")

    def test_prior_rules_unaffected(self):
        assert handoff.absence_refusal(
            {"state": "absent", "channels": {"index": "stale"}}
        ) is not None
        assert handoff.absence_refusal(
            {"state": "low_confidence", "channels": {}}
        ) is not None
        assert handoff.absence_refusal(
            {"state": "absent", "channels": {}, "truncated": True}
        ) is not None
        assert handoff.absence_refusal(
            {"state": "absent", "channels": {"index": "fresh"}}
        ) is None
