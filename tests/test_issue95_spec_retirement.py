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
            "The cleanup fields report durable state; a fresh pending read "
            "self-heals or returns the record."
        ),
    },
}

ABSENT_MONOLITH_CONTRADICTIONS = (
    "a non-retired result may have an absent retiring monolith",
    "non-retired result with an absent monolith",
)

CLEANUP_INCOMPLETE_PATTERN = re.compile(
    r"cleanup[-_]incomplete",
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

CANONICAL_CLEANUP_INCOMPLETE_TABLES = {
    "reconciliation.reason_code": (
        (
            "supersession_cleanup_incomplete",
            (
                "Supersession was proven, but a pre-commit publication or "
                "cleanup failure prevented the primary unlink; the provisional "
                "remains discoverable and retry is idempotent."
            ),
        ),
        (
            "graduation_cleanup_incomplete",
            (
                "Exact-duplicate graduation was proven, but a pre-commit "
                "publication or cleanup failure prevented the primary unlink; "
                "nothing was reconciled, the provisional remains discoverable, "
                "and retry is idempotent."
            ),
        ),
    ),
    "legacy_reconciliation.reason_code": (
        (
            "legacy_reconcile_cleanup_incomplete",
            (
                "Retirement was proven, but a pre-commit publication or cleanup "
                "failure prevented the primary unlink; the legacy index remains "
                "discoverable and retry is idempotent."
            ),
        ),
    ),
}

MARKDOWN_ESCAPE_PATTERN = re.compile(r"\\([\\`*_{}\[\]()#+.!|<>-])")
FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})")


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


def _assert_no_contradictions(document_text):
    normalized = " ".join(document_text.split()).lower()
    contradictions = [
        claim
        for claim in ABSENT_MONOLITH_CONTRADICTIONS
        if claim in normalized
    ]
    assert not contradictions, (
        "SPEC.md retirement coordination contains contradictions: "
        + ", ".join(contradictions)
    )
    actual_inventory = _cleanup_incomplete_prose_inventory(document_text)
    assert actual_inventory == CLEANUP_INCOMPLETE_PROSE_INVENTORY, (
        "SPEC.md contains contradictions: "
        "cleanup-incomplete prose inventory differs from the canonical "
        f"contract: {actual_inventory!r}"
    )


def _normalize_markdown_surface(text):
    without_escapes = MARKDOWN_ESCAPE_PATTERN.sub(r"\1", text)
    return " ".join(without_escapes.replace("`", "").split()).lower()


def _normalize_cleanup_incomplete_sentence(sentence):
    without_rendering = _normalize_markdown_surface(sentence)
    canonical_spelling = CLEANUP_INCOMPLETE_PATTERN.sub(
        "cleanup-incomplete",
        without_rendering,
    )
    return canonical_spelling


def _visible_markdown_lines(text):
    in_fence = False
    fence_character = None
    fence_length = 0
    for line in text.splitlines():
        marker_match = FENCE_PATTERN.match(line)
        if marker_match:
            marker = marker_match.group(1)
            if not in_fence:
                in_fence = True
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                in_fence = False
                fence_character = None
                fence_length = 0
            continue
        if not in_fence:
            yield line


def _cleanup_incomplete_prose_inventory(document_text):
    prose = "\n".join(
        line
        for line in _visible_markdown_lines(document_text)
        if not line.lstrip().startswith("|")
    )
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(prose.split()))
    return tuple(
        normalized
        for sentence in sentences
        if CLEANUP_INCOMPLETE_PATTERN.search(
            normalized := _normalize_cleanup_incomplete_sentence(sentence)
        )
    )


def _normalize_reason_code(cell):
    normalized = _normalize_markdown_surface(cell)
    return CLEANUP_INCOMPLETE_PATTERN.sub(
        "cleanup_incomplete",
        normalized,
    )


def _markdown_table_rows(text):
    rows = []
    for line in _visible_markdown_lines(text):
        if not line.lstrip().startswith("|"):
            continue
        cells = tuple(line.strip().strip("|").split("|"))
        rows.append(cells)
    return tuple(rows)


def _cleanup_incomplete_reason_code_rows(spec_path=SPEC_PATH):
    rows = []
    text = spec_path.read_text(encoding="utf-8")
    for cells in _markdown_table_rows(text):
        normalized_cells = tuple(
            _normalize_cleanup_incomplete_sentence(cell) for cell in cells
        )
        if not any(
            CLEANUP_INCOMPLETE_PATTERN.search(cell)
            for cell in normalized_cells
        ):
            continue
        rows.append(
            (
                _normalize_reason_code(cells[0]),
                _normalize_cleanup_incomplete_sentence(cells[-1]),
            )
        )
    return tuple(rows)


def _cleanup_incomplete_rows_for_table(text, label):
    lines = tuple(_visible_markdown_lines(text))
    label_marker = f"**`{label}`**"
    label_indexes = [
        index
        for index, line in enumerate(lines)
        if line.startswith(label_marker)
    ]
    assert len(label_indexes) == 1, (
        f"SPEC.md must contain exactly one {label_marker} table label"
    )
    start = next(
        index
        for index in range(label_indexes[0] + 1, len(lines))
        if lines[index].lstrip().startswith("|")
    )
    table_lines = []
    for line in lines[start:]:
        if not line.lstrip().startswith("|"):
            break
        table_lines.append(line)
    table_rows = _markdown_table_rows("\n".join(table_lines))
    normalized_headers = tuple(
        _normalize_markdown_surface(cell) for cell in table_rows[0]
    )
    assert normalized_headers.count("outcome") == 1
    outcome_index = normalized_headers.index("outcome")
    expected_width = len(normalized_headers)
    assert all(len(cells) == expected_width for cells in table_rows)
    rows = []
    for cells in table_rows[2:]:
        reason_code = _normalize_reason_code(cells[0])
        if reason_code.endswith("_cleanup_incomplete"):
            rows.append(
                (
                    reason_code,
                    _normalize_cleanup_incomplete_sentence(
                        cells[outcome_index]
                    ),
                )
            )
    return tuple(rows)


def _assert_cleanup_incomplete_table_inventory(spec_path=SPEC_PATH):
    text = spec_path.read_text(encoding="utf-8")
    normalized_expected = {
        label: tuple(
            (
                reason_code,
                _normalize_cleanup_incomplete_sentence(outcome),
            )
            for reason_code, outcome in rows
        )
        for label, rows in CANONICAL_CLEANUP_INCOMPLETE_TABLES.items()
    }
    actual_by_table = {
        label: _cleanup_incomplete_rows_for_table(text, label)
        for label in CANONICAL_CLEANUP_INCOMPLETE_TABLES
    }
    assert actual_by_table == normalized_expected

    rows = _cleanup_incomplete_reason_code_rows(spec_path)
    expected_rows = tuple(
        row
        for table_rows in normalized_expected.values()
        for row in table_rows
    )
    assert rows == expected_rows


def _assert_retirement_contract(spec_path=SPEC_PATH):
    text = spec_path.read_text(encoding="utf-8")
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
    _assert_no_contradictions(text)
    _assert_cleanup_incomplete_table_inventory(spec_path)


def test_retirement_coordination_spec_matches_commit_contract():
    _assert_retirement_contract()


def test_cleanup_incomplete_inventories_are_canonical():
    assert _cleanup_incomplete_prose_inventory(
        SPEC_PATH.read_text(encoding="utf-8")
    ) == CLEANUP_INCOMPLETE_PROSE_INVENTORY
    rows = _cleanup_incomplete_reason_code_rows()
    assert tuple(reason_code for reason_code, _ in rows) == (
        CLEANUP_INCOMPLETE_REASON_CODE_INVENTORY
    )
    text = SPEC_PATH.read_text(encoding="utf-8")
    assert {
        label: tuple(reason_code for reason_code, _ in rows)
        for label in CANONICAL_CLEANUP_INCOMPLETE_TABLES
        for rows in (_cleanup_incomplete_rows_for_table(text, label),)
    } == {
        "reconciliation.reason_code": (
            "supersession_cleanup_incomplete",
            "graduation_cleanup_incomplete",
        ),
        "legacy_reconciliation.reason_code": (
            "legacy_reconcile_cleanup_incomplete",
        ),
    }
    _assert_cleanup_incomplete_table_inventory()


NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
}
COUNTED_FIELDS_PATTERN = re.compile(
    r"\b(one|two|three|four|five|six)\b\s+(?:[^\s]*\s+)?fields",
    re.IGNORECASE,
)


def test_spec_never_hardcodes_a_stale_cleanup_field_count():
    """Prose that counts the cleanup fields must agree with the schema.

    Removing the write-only completion marker left SPEC.md saying "four" in
    two places while three fields remained, and the guard did not notice
    because it pinned the sentence verbatim -- a pinned string is only as
    current as the last person who remembered to update both copies. This
    derives the count from the runtime schema instead.
    """
    section = _retirement_section_raw()
    expected = len(storage_module.RETIREMENT_CLEANUP_OUTCOME_SCHEMA)

    for match in COUNTED_FIELDS_PATTERN.finditer(section):
        assert NUMBER_WORDS[match.group(1).lower()] == expected, (
            f"SPEC.md says {match.group(0)!r} but the runtime schema "
            f"publishes {expected} cleanup fields"
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


@pytest.mark.parametrize("claim", ABSENT_MONOLITH_CONTRADICTIONS)
def test_retirement_spec_rejects_absent_monolith_claims(claim):
    """Exact-phrase tripwire for the one claim family with no inventory pin.

    Unlike cleanup-incomplete prose, "absent monolith" claims have no
    canonical sentence set to pin against, so this is a named-phrase check
    that cannot catch a reworded equivalent. A tripwire, not a proof.
    """
    _assert_retirement_contract()
    with pytest.raises(AssertionError, match="contains contradictions"):
        _assert_no_contradictions(_retirement_section_raw() + "\n" + claim)


@pytest.mark.parametrize(
    "sentence",
    (
        "Cleanup_incomplete may return after a committed primary unlink.",
        (
            "Cleanup_incomplete may follow a committed primary unlink and "
            "never requires manual repair."
        ),
        (
            "Cleanup_incomplete never follows a committed primary unlink but "
            "cleanup_incomplete may return after a committed primary unlink."
        ),
        (
            "Only before the primary unlink may cleanup-incomplete occur "
            "after a committed primary unlink."
        ),
    ),
)
def test_any_added_cleanup_incomplete_sentence_fails_the_inventory(sentence):
    """The guard pins the inventory; it does not judge meaning.

    Cleanup-incomplete prose is protected by pinning the COMPLETE set of
    sentences that mention it, so ANY addition fails — contradictory,
    redundant, or merely new. That is what makes the guard sound: scanning for
    specific contradictory phrasings could never be complete, because a
    contradiction can always be reworded. These cases sample the property;
    they are not the list of phrasings it covers.
    """
    with pytest.raises(AssertionError, match="contains contradictions"):
        _assert_no_contradictions(
            _retirement_section_raw() + "\n" + sentence
        )


def _write_mutated_spec(tmp_path, text):
    spec_path = tmp_path / "SPEC.md"
    spec_path.write_text(text, encoding="utf-8")
    return spec_path


CONTRADICTION = (
    "Cleanup_incomplete may return after a committed primary unlink."
)
GIT_TABLE_END = "\n\n**`reconciliation.reason_code`**"


def _authoritative_row(text):
    return next(
        line
        for line in text.splitlines()
        if line.startswith("| `supersession_cleanup_incomplete` |")
    )


def _after_section_boundary(text):
    """Drift parked just outside the section, still inside the document."""
    return text.replace(SECTION_END, f"{SECTION_END}\n\n{CONTRADICTION}", 1)


def _markdown_escaped(text):
    """An escaped underscore renders identically to a reader."""
    escaped = (
        "Cleanup\\_incomplete may return after a committed primary unlink."
    )
    return text.replace(SECTION_START, f"{SECTION_START}\n\n{escaped}", 1)


def _indented_fence(text):
    """Four spaces open an indented code block, not a fence — still visible."""
    return text.replace(
        SECTION_START,
        f"{SECTION_START}\n\n    ```\n{CONTRADICTION}\n    ```",
        1,
    )


def _reason_code_in_wrong_table(text):
    """An authoritative row relocated under a different table label."""
    row = _authoritative_row(text)
    return text.replace(f"{row}\n", "", 1).replace(
        GIT_TABLE_END, f"\n{row}{GIT_TABLE_END}", 1
    )


def _contradiction_appended_to_row(text):
    """Drift smuggled onto the end of an otherwise correct row."""
    row = _authoritative_row(text)
    appended = (
        " Nevertheless, after a committed primary unlink, the operation may "
        "return cleanup_incomplete."
    )
    return text.replace(row, row[:-1].rstrip() + appended + " |", 1)


def _escaped_shadow_reason_code(text):
    """A fourth reason code that renders like one of the real three."""
    shadow = "| `shadow_cleanup\\_incomplete` | A visible shadow outcome. |"
    return text.replace(GIT_TABLE_END, f"\n{shadow}{GIT_TABLE_END}", 1)


def _extra_cell_hiding_the_outcome(text):
    """An inserted column, so the last cell is no longer the outcome."""
    row = _authoritative_row(text)
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    return text.replace(
        row, f"| {cells[0]} | {CONTRADICTION} | {cells[1]} |", 1
    )


@pytest.mark.parametrize(
    "mutate",
    (
        _after_section_boundary,
        _markdown_escaped,
        _indented_fence,
        _reason_code_in_wrong_table,
        _contradiction_appended_to_row,
        _escaped_shadow_reason_code,
        _extra_cell_hiding_the_outcome,
    ),
    ids=lambda mutate: mutate.__name__.lstrip("_"),
)
def test_retirement_spec_guard_reads_what_a_reader_sees(tmp_path, mutate):
    """Each case defeats a different part of the guard's reader model.

    The guard has to decide what a reader actually SEES: which lines sit
    inside a real fence, how markdown escapes render, and which column carries
    the authoritative outcome. A mutation slipping past any of those would let
    SPEC.md drift while this suite stayed green, so each model is pinned once
    here rather than as its own near-identical test.
    """
    mutated = mutate(SPEC_PATH.read_text(encoding="utf-8"))

    with pytest.raises(AssertionError):
        _assert_retirement_contract(_write_mutated_spec(tmp_path, mutated))


def test_retirement_conflict_docstring_scopes_availability_to_retiring_primary():
    docstring = " ".join(inspect.getdoc(RetirementConflict).split()).lower()

    assert "the retiring primary remains loadable" in docstring
    assert "every participating index remains loadable" not in docstring
