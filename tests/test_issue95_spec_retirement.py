"""Issue #95 retirement coordination specification contract."""

import inspect
import re
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
    "pre-commit pending signal": (
        "durable pre-commit recovery signal"
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

ABSENT_MONOLITH_CONTRADICTIONS = (
    "a non-retired result may have an absent retiring monolith",
    "non-retired result with an absent monolith",
)

CLEANUP_INCOMPLETE_CONTRADICTIONS = (
    (
        "Following a successful primary unlink, the operation can still "
        "return cleanup_incomplete."
    ),
    (
        "The primary index can be unlinked before a cleanup-incomplete "
        "response is returned."
    ),
)

CLEANUP_INCOMPLETE_PATTERN = re.compile(
    r"\bcleanup[-_]incomplete\b",
    re.IGNORECASE,
)

CLEANUP_INCOMPLETE_PRECOMMIT_ONLY_PATTERN = re.compile(
    r"""
    \bcleanup[-_]incomplete\b
    [^.!?;:]{0,160}?
    (?:
        \bonly\s+(?:a\s+)?pre-commit\b
        |
        \bpre-commit[- ]only\b
        |
        \bonly\s+before\s+(?:the\s+)?primary\s+unlink\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

CLEANUP_INCOMPLETE_COMMITTED_UNLINK_PROHIBITION_PATTERN = re.compile(
    r"""
    \bcleanup[-_]incomplete\b
    [^.!?;:]{0,160}?
    \b(?:never|cannot|can't|must\s+not|may\s+not|does\s+not|do\s+not)\b
    \s+(?:be\s+)?
    (?:
        follow(?:s|ed|ing)?\s+(?:a\s+)?committed\s+primary\s+unlink
        |
        (?:occur(?:s|red|ring)?|return(?:s|ed|ing)?)
        [^.!?;:]{0,80}?
        \b(?:after|following)\s+(?:a\s+)?committed\s+primary\s+unlink
    )
    """,
    re.IGNORECASE | re.VERBOSE,
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
        claim
        for claim in ABSENT_MONOLITH_CONTRADICTIONS
        if claim in normalized
    ]
    assert not contradictions, (
        "SPEC.md retirement coordination contains contradictions: "
        + ", ".join(contradictions)
    )
    try:
        _assert_cleanup_incomplete_accounted(section)
    except AssertionError as exc:
        raise AssertionError(
            "SPEC.md retirement coordination contains contradictions: "
            f"{exc}"
        ) from exc


def _assert_cleanup_incomplete_accounted(section):
    matching_lines = [
        line
        for line in section.splitlines()
        if CLEANUP_INCOMPLETE_PATTERN.search(line)
    ]
    assert matching_lines, "SPEC.md must publish cleanup-incomplete semantics"
    for row in (line for line in matching_lines if line.startswith("|")):
        cells = [
            cell.strip().replace("`", "").lower()
            for cell in row.strip("|").split("|")
        ]
        assert cells[0].endswith("cleanup_incomplete"), (
            "cleanup-incomplete table occurrence is not a reason code"
        )
        assert "pre-commit" in cells[-1], (
            "cleanup-incomplete table row is not pre-commit"
        )
        assert "prevented the primary unlink" in cells[-1], (
            "cleanup-incomplete table row does not prevent unlink"
        )
    prose = "\n".join(
        line for line in section.splitlines() if not line.startswith("|")
    )
    matching_units = []
    for paragraph in re.split(r"\n\s*\n", prose):
        normalized_paragraph = " ".join(paragraph.split())
        sentences = re.split(r"(?<=[.!?])\s+", normalized_paragraph)
        for sentence in sentences:
            clauses = re.split(
                r";\s+|:\s+|,\s+(?=(?:and|but|yet|or)\b)",
                sentence,
                flags=re.IGNORECASE,
            )
            matching_units.extend(
                clause.strip()
                for clause in clauses
                if CLEANUP_INCOMPLETE_PATTERN.search(clause)
            )
    assert matching_units, "SPEC.md must publish cleanup-incomplete prose"
    for unit in matching_units:
        relationship_is_bound = bool(
            CLEANUP_INCOMPLETE_PRECOMMIT_ONLY_PATTERN.search(unit)
            or CLEANUP_INCOMPLETE_COMMITTED_UNLINK_PROHIBITION_PATTERN.search(
                unit
            )
        )
        assert relationship_is_bound, (
            "cleanup-incomplete prose occurrence is not independently "
            f"accounted: {unit!r}"
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
    _assert_cleanup_incomplete_accounted(
        spec_path.read_text(encoding="utf-8")
    )


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


@pytest.mark.parametrize(
    "claim",
    (
        *ABSENT_MONOLITH_CONTRADICTIONS,
        *CLEANUP_INCOMPLETE_CONTRADICTIONS,
    ),
)
def test_retirement_spec_rejects_explicit_contradictions(claim):
    _assert_retirement_contract()
    with pytest.raises(AssertionError, match="contains contradictions"):
        _assert_no_contradictions(_retirement_section_raw() + "\n" + claim)


def test_retirement_spec_rejects_same_paragraph_cleanup_contradiction():
    section = _retirement_section_raw()
    paragraph = next(
        value
        for value in re.split(r"\n\s*\n", section)
        if "cleanup-incomplete reason codes"
        in " ".join(value.split()).lower()
        and "never follows a committed primary unlink"
        in " ".join(value.split()).lower()
    )
    contradiction = (
        "Nevertheless, after a committed primary unlink, the operation may "
        "return cleanup_incomplete."
    )
    mutated = section.replace(paragraph, f"{paragraph} {contradiction}")

    with pytest.raises(AssertionError, match="contains contradictions"):
        _assert_no_contradictions(mutated)


@pytest.mark.parametrize(
    "contradiction",
    (
        (
            "Cleanup_incomplete may follow a committed primary unlink and "
            "never requires manual repair."
        ),
        (
            "Cleanup_incomplete may follow a committed primary unlink, "
            "because a pre-commit check only records diagnostics."
        ),
    ),
)
def test_retirement_spec_rejects_unrelated_cleanup_qualifiers(
    contradiction,
):
    with pytest.raises(AssertionError, match="contains contradictions"):
        _assert_no_contradictions(
            _retirement_section_raw() + "\n" + contradiction
        )


def test_retirement_spec_accepts_only_before_primary_unlink():
    _assert_cleanup_incomplete_accounted(
        "Cleanup-incomplete occurs only before the primary unlink."
    )


def test_retirement_conflict_docstring_scopes_availability_to_retiring_primary():
    docstring = " ".join(inspect.getdoc(RetirementConflict).split()).lower()

    assert "the retiring primary remains loadable" in docstring
    assert "every participating index remains loadable" not in docstring
