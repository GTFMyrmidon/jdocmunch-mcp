"""Issue #95 retirement coordination specification contract."""

import inspect
from pathlib import Path

import pytest

from jdocmunch_mcp.storage import doc_store as storage_module
from jdocmunch_mcp.storage.doc_store import RetirementConflict


SPEC_PATH = Path(__file__).parents[1] / "SPEC.md"
SECTION_START = "**Retirement coordination**"
SECTION_END = '`legacy_reconcile="report"`'
SCHEMA_HEADING = "##### Retirement cleanup disclosure schema"
MATRIX_HEADING = "##### Retirement commit outcome matrix"

REQUIRED_CONTRACTS = {
    "exact publication authority": (
        "requires the explicit publication receipt created by its caller and "
        "a readable current retirement record with that exact current "
        "publication identity"
    ),
    "post-retained-gate proof": (
        "after the retained-handle gate is acquired and immediately before "
        "the primary `<name>.json` commit"
    ),
    "conditional cleanup": (
        "stale or older cleanup cannot remove a newer publication"
    ),
    "truthful completion recovery": (
        "after the primary unlink commits, the retirement result is retired"
    ),
    "additive cleanup disclosure": (
        "record cleanup failure is disclosed additively on that retired result"
    ),
    "truthful self-healing": (
        "a fresh pending-state read returns the durable record when self-healing "
        "cannot remove it"
    ),
    "non-retired availability": (
        "every non-retired outcome occurs before the primary unlink and leaves "
        "the retiring monolith loadable"
    ),
    "commit-scoped availability": (
        "at the protected A-to-B commit, B is loadable"
    ),
    "sequential retirement": (
        "a later separately authorized B-to-C retirement may legitimately "
        "remove B"
    ),
    "wait policy": (
        "public deletion uses zero-wait coordination, while internal "
        "retirement uses its bounded wait"
    ),
}

EXPECTED_OUTCOME_MATRIX = {
    "Pre-commit refusal": {
        "Primary unlink committed": "false",
        "Returned retirement status": "non-retired",
        "Retiring monolith": "loadable",
        "Cleanup fields": "absent",
        "Durable recovery": (
            "Retry starts from the still-loadable retiring monolith."
        ),
    },
    "Committed with complete record cleanup": {
        "Primary unlink committed": "true",
        "Returned retirement status": "retired",
        "Retiring monolith": "absent",
        "Cleanup fields": "absent",
        "Durable recovery": (
            "No retirement cleanup remains for that exact publication."
        ),
    },
    "Committed with incomplete record cleanup": {
        "Primary unlink committed": "true",
        "Returned retirement status": "retired",
        "Retiring monolith": "absent",
        "Cleanup fields": "present",
        "Durable recovery": (
            "The four fields report durable state; a fresh pending read "
            "self-heals or returns the record."
        ),
    },
}

FORBIDDEN_CONTRADICTIONS = (
    "cleanup-incomplete may follow a committed primary unlink",
    "cleanup-incomplete after committed unlink",
    "a non-retired result may have an absent retiring monolith",
    "non-retired result with an absent monolith",
)


def _retirement_section_raw(spec_path=SPEC_PATH):
    text = spec_path.read_text(encoding="utf-8")
    start = text.index(SECTION_START)
    end = text.index(SECTION_END, start)
    return text[start:end]


def _retirement_section(spec_path=SPEC_PATH):
    return " ".join(_retirement_section_raw(spec_path).split()).lower()


def _parse_table(section, heading):
    lines = section.splitlines()
    assert heading in lines, f"SPEC.md must contain {heading!r}"
    start = next(
        index
        for index in range(lines.index(heading) + 1, len(lines))
        if lines[index].startswith("|")
    )
    headers = [
        value.strip() for value in lines[start].strip("|").split("|")
    ]
    rows = {}
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        values = [
            value.strip().replace("`", "")
            for value in line.strip("|").split("|")
        ]
        row = dict(zip(headers, values, strict=True))
        rows[values[0]] = {
            header: row[header] for header in headers[1:]
        }
    return rows


def _published_cleanup_schema():
    rows = _parse_table(_retirement_section_raw(), SCHEMA_HEADING)
    return {
        field: {
            "json_type": row["JSON type"],
            "allowed_values": tuple(
                value.strip()
                for value in row["Allowed values"].split(",")
            ),
            "meaning": row["Meaning"],
        }
        for field, row in rows.items()
    }


def _assert_no_contradictions(section):
    normalized = " ".join(section.split()).lower()
    contradictions = [
        claim for claim in FORBIDDEN_CONTRADICTIONS if claim in normalized
    ]
    assert not contradictions, (
        "SPEC.md retirement coordination contains contradictions: "
        + ", ".join(contradictions)
    )


def _assert_retirement_contract(spec_path=SPEC_PATH):
    section = _retirement_section(spec_path)
    missing = [
        name
        for name, statement in REQUIRED_CONTRACTS.items()
        if statement.lower() not in section
    ]
    assert not missing, (
        "SPEC.md retirement coordination is missing contracts: "
        + ", ".join(missing)
    )
    _assert_no_contradictions(section)


def test_retirement_coordination_spec_matches_commit_contract():
    _assert_retirement_contract()


def test_cleanup_incomplete_vocabulary_is_precommit_only():
    section = _retirement_section()

    assert (
        "cleanup-incomplete reason codes describe only a pre-commit failure"
        in section
    )
    assert (
        "a cleanup-incomplete outcome never follows a committed primary unlink"
        in section
    )


def test_cleanup_disclosure_schema_matches_storage_emitter():
    runtime_schema = getattr(
        storage_module, "RETIREMENT_CLEANUP_OUTCOME_SCHEMA", None
    )

    assert runtime_schema is not None
    assert _published_cleanup_schema() == runtime_schema


def test_retirement_commit_outcome_matrix_is_complete_and_consistent():
    rows = _parse_table(_retirement_section_raw(), MATRIX_HEADING)

    assert rows == EXPECTED_OUTCOME_MATRIX
    for row in rows.values():
        committed = row["Primary unlink committed"] == "true"
        if committed:
            assert row["Returned retirement status"] == "retired"
            assert row["Retiring monolith"] == "absent"
        else:
            assert row["Returned retirement status"] == "non-retired"
            assert row["Retiring monolith"] == "loadable"


@pytest.mark.parametrize("claim", FORBIDDEN_CONTRADICTIONS)
def test_retirement_spec_rejects_explicit_contradictions(claim):
    with pytest.raises(AssertionError, match="contains contradictions"):
        _assert_no_contradictions(_retirement_section_raw() + "\n" + claim)


def test_retirement_conflict_docstring_scopes_availability_to_retiring_primary():
    docstring = " ".join(inspect.getdoc(RetirementConflict).split()).lower()

    assert "the retiring primary remains loadable" in docstring
    assert "every participating index remains loadable" not in docstring
