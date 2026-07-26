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

CLEANUP_INCOMPLETE_PROSE_INVENTORY = (
    (
        "cleanup-incomplete reason codes describe only a pre-commit failure, "
        "and a cleanup-incomplete outcome never follows a committed primary "
        "unlink."
    ),
)

CLEANUP_INCOMPLETE_REASON_CODE_INVENTORY = (
    "supersession_cleanup_incomplete",
    "graduation_cleanup_incomplete",
    "legacy_reconcile_cleanup_incomplete",
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
    actual_inventory = _cleanup_incomplete_prose_inventory(section)
    assert actual_inventory == CLEANUP_INCOMPLETE_PROSE_INVENTORY, (
        "SPEC.md retirement coordination contains contradictions: "
        "cleanup-incomplete prose inventory differs from the canonical "
        f"contract: {actual_inventory!r}"
    )


def _normalize_cleanup_incomplete_sentence(sentence):
    without_backticks = sentence.replace("`", "")
    canonical_spelling = CLEANUP_INCOMPLETE_PATTERN.sub(
        "cleanup-incomplete",
        without_backticks,
    )
    return " ".join(canonical_spelling.split()).lower()


def _cleanup_incomplete_prose_inventory(section):
    prose = "\n".join(
        line for line in section.splitlines() if not line.startswith("|")
    )
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(prose.split()))
    return tuple(
        _normalize_cleanup_incomplete_sentence(sentence)
        for sentence in sentences
        if CLEANUP_INCOMPLETE_PATTERN.search(sentence)
    )


def _cleanup_incomplete_reason_code_rows(spec_path=SPEC_PATH):
    rows = []
    for line in spec_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [
            cell.strip().replace("`", "").lower()
            for cell in line.strip("|").split("|")
        ]
        if cells[0].endswith("_cleanup_incomplete"):
            rows.append((cells[0], cells[-1]))
    return tuple(rows)


def _assert_cleanup_incomplete_table_inventory(spec_path=SPEC_PATH):
    rows = _cleanup_incomplete_reason_code_rows(spec_path)
    assert tuple(reason_code for reason_code, _ in rows) == (
        CLEANUP_INCOMPLETE_REASON_CODE_INVENTORY
    )
    for reason_code, meaning in rows:
        assert "pre-commit" in meaning, (
            f"{reason_code} table row is not pre-commit"
        )
        assert "prevented the primary unlink" in meaning, (
            f"{reason_code} table row does not prevent unlink"
        )


def _assert_retirement_contract(spec_path=SPEC_PATH):
    raw_section = _retirement_section_raw(spec_path)
    section = " ".join(raw_section.split()).lower()
    missing = [
        name
        for name, statement in REQUIRED_CONTRACTS.items()
        if statement.lower() not in section
    ]
    assert not missing, (
        "SPEC.md retirement coordination is missing contracts: "
        + ", ".join(missing)
    )
    _assert_no_contradictions(raw_section)
    _assert_cleanup_incomplete_table_inventory(spec_path)


def test_retirement_coordination_spec_matches_commit_contract():
    _assert_retirement_contract()


def test_cleanup_incomplete_inventories_are_canonical():
    assert _cleanup_incomplete_prose_inventory(
        _retirement_section_raw()
    ) == CLEANUP_INCOMPLETE_PROSE_INVENTORY
    rows = _cleanup_incomplete_reason_code_rows()
    assert tuple(reason_code for reason_code, _ in rows) == (
        CLEANUP_INCOMPLETE_REASON_CODE_INVENTORY
    )
    _assert_cleanup_incomplete_table_inventory()


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


@pytest.mark.parametrize(
    "contradiction",
    (
        (
            "Cleanup_incomplete never follows a committed primary unlink but "
            "cleanup_incomplete may return after a committed primary unlink."
        ),
        (
            "Cleanup_incomplete may follow a committed primary unlink while "
            "monitoring runs only before the primary unlink."
        ),
    ),
)
def test_retirement_spec_rejects_cross_occurrence_qualifiers(
    contradiction,
):
    with pytest.raises(AssertionError, match="contains contradictions"):
        _assert_no_contradictions(
            _retirement_section_raw() + "\n" + contradiction
        )


@pytest.mark.parametrize(
    "contradiction",
    (
        (
            "Cleanup_incomplete never follows a committed primary unlink and "
            "may return after a committed primary unlink."
        ),
        (
            "Cleanup_incomplete occurs only before the primary unlink and may "
            "return after a committed primary unlink."
        ),
        (
            "Only before the primary unlink may cleanup-incomplete occur after "
            "a committed primary unlink."
        ),
        (
            "After a committed primary unlink, cleanup_incomplete cannot occur "
            "but may return after a committed primary unlink."
        ),
    ),
)
def test_retirement_spec_rejects_noncanonical_cleanup_claims(
    contradiction,
):
    with pytest.raises(AssertionError, match="contains contradictions"):
        _assert_no_contradictions(
            _retirement_section_raw() + "\n" + contradiction
        )


def test_retirement_conflict_docstring_scopes_availability_to_retiring_primary():
    docstring = " ".join(inspect.getdoc(RetirementConflict).split()).lower()

    assert "the retiring primary remains loadable" in docstring
    assert "every participating index remains loadable" not in docstring
