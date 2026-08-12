"""Near-duplicate sidecar is bounded and discloses a skip (jdoc#103).

`detect_clusters` is all-pairs Jaccard with a length pre-filter. The pre-filter
prunes to ~30% of the space, which is a constant, not a change of asymptote:
measured wall clock grows ~O(n^2.3), and a 187,985-section corpus extrapolates
to several hours with no progress output.

The code comment always said "fine up to a few thousand sections" and was right.
Nothing enforced or surfaced that ceiling, and the caller's bare
`except Exception: pass` meant a skip would have been silent too -- so a skipped
sidecar was indistinguishable from one that found no duplicates.
"""

import pytest

from jdocmunch_mcp.retrieval import dedup


def _sections(n: int, *, distinct: bool = True) -> list[dict]:
    return [
        {
            "id": f"o/r::doc{i}.md::sec#{i}",
            "content": (
                # Must clear dedup._MIN_TOKENS or every section is dropped
                # before clustering and the fixture proves nothing.
                f"section {i} " + " ".join(f"unique{i}word{w}" for w in range(60))
                if distinct
                else "identical " + " ".join(f"shared word {w}" for w in range(60))
            ),
        }
        for i in range(n)
    ]


class TestCeilingResolver:
    def test_default(self):
        assert dedup.section_ceiling() == dedup._DEFAULT_SECTION_CEILING

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, "5000")
        assert dedup.section_ceiling() == 5000

    def test_zero_disables_the_guard(self, monkeypatch):
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, "0")
        assert dedup.section_ceiling() == 0

    @pytest.mark.parametrize("bad", ["", "abc", "1e5", "-3", "  "])
    def test_garbage_falls_back_to_default_not_uncapped(self, bad, monkeypatch):
        """A typo must not silently uncap an O(n^2.3) loop."""
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, bad)
        assert dedup.section_ceiling() == dedup._DEFAULT_SECTION_CEILING

    def test_default_sits_above_every_published_benchmark_corpus(self):
        """Largest published corpus is 10.4k sections; the guard must not fire there."""
        assert dedup.section_ceiling() > 10_400


class TestWriteGuard:
    def test_runs_and_reports_no_skip_under_the_ceiling(self, tmp_path, monkeypatch):
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, "100")
        dedup.write(str(tmp_path), "o", "r", _sections(10))
        assert dedup.last_skip_reason() is None

    def test_skips_and_reports_over_the_ceiling(self, tmp_path, monkeypatch):
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, "5")
        assert dedup.write(str(tmp_path), "o", "r", _sections(50)) == 0
        skip = dedup.last_skip_reason()
        assert skip["reason"] == "section_count"
        assert skip["sections"] == 50
        assert skip["ceiling"] == 5
        assert dedup.SECTION_CEILING_ENV_VAR in skip["detail"], (
            "the disclosure must name the knob that moves it"
        )

    def test_opt_out(self, tmp_path):
        assert dedup.write(str(tmp_path), "o", "r", _sections(10), enabled=False) == 0
        assert dedup.last_skip_reason() == {"reason": "disabled"}

    def test_explicit_max_sections_beats_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, "100000")
        assert dedup.write(str(tmp_path), "o", "r", _sections(20), max_sections=5) == 0
        assert dedup.last_skip_reason()["reason"] == "section_count"

    def test_ceiling_zero_runs_regardless_of_size(self, tmp_path, monkeypatch):
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, "0")
        dedup.write(str(tmp_path), "o", "r", _sections(30))
        assert dedup.last_skip_reason() is None

    def test_skip_state_resets_between_calls(self, tmp_path, monkeypatch):
        """A stale skip reason would mislabel a later healthy run."""
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, "5")
        dedup.write(str(tmp_path), "o", "r", _sections(50))
        assert dedup.last_skip_reason() is not None
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, "1000")
        dedup.write(str(tmp_path), "o", "r", _sections(10))
        assert dedup.last_skip_reason() is None

    def test_a_skip_writes_no_sidecar_masquerading_as_empty(self, tmp_path, monkeypatch):
        """Control: the guard must not persist an empty cluster file that would
        read as 'ran, found nothing'."""
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, "5")
        dedup.write(str(tmp_path), "o", "skipped", _sections(50))
        written = list(tmp_path.rglob("*.json"))
        assert not written, f"a skipped run persisted {written}"


class TestStillDetectsDuplicates:
    """Control: the guard must not break what the sidecar is for."""

    def test_near_duplicates_still_cluster(self, tmp_path, monkeypatch):
        monkeypatch.setenv(dedup.SECTION_CEILING_ENV_VAR, "1000")
        count = dedup.write(str(tmp_path), "o", "r", _sections(6, distinct=False))
        assert count >= 1, "identical sections no longer cluster"
        assert dedup.last_skip_reason() is None
