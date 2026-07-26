"""Issue #95 QA-20 public delete-result vocabulary regressions."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


delete_tool = importlib.import_module("jdocmunch_mcp.tools.delete_index")

SPEC_PATH = Path(__file__).parents[1] / "SPEC.md"
TABLE_HEADING = "##### Public result vocabulary"
TABLE_COLUMNS = ("Outcome", "success", "reason_code", "retryable")

EXPECTED_VOCABULARY = {
    "index_deleted": {
        "outcome": "Deleted",
        "success": True,
        "retryable": False,
    },
    "index_not_found": {
        "outcome": "Missing",
        "success": False,
        "retryable": False,
    },
    "index_lifecycle_busy": {
        "outcome": "Lifecycle contention",
        "success": False,
        "retryable": True,
    },
}

EXPECTED_MESSAGES = {
    "index_deleted": "Index deleted.",
    "index_not_found": "Index not found.",
    "index_lifecycle_busy": (
        "Index is busy completing a retirement. The index still exists; "
        "retry shortly."
    ),
}


def _table_cells(line):
    return tuple(
        cell.strip().strip("`")
        for cell in line.strip().strip("|").split("|")
    )


def _published_vocabulary():
    lines = SPEC_PATH.read_text(encoding="utf-8").splitlines()
    assert TABLE_HEADING in lines, (
        f"SPEC.md must contain the deliberate {TABLE_HEADING!r} schema"
    )
    heading_index = lines.index(TABLE_HEADING)
    table_lines = []
    for line in lines[heading_index + 1 :]:
        if line.startswith("|"):
            table_lines.append(line)
        elif table_lines:
            break

    assert len(table_lines) == 5
    assert _table_cells(table_lines[0]) == TABLE_COLUMNS
    rows = {}
    for line in table_lines[2:]:
        outcome, success, reason_code, retryable = _table_cells(line)
        rows[reason_code] = {
            "outcome": outcome,
            "success": {"true": True, "false": False}[success],
            "retryable": {"true": True, "false": False}[retryable],
        }
    return rows


def test_runtime_vocabulary_is_complete_and_exact():
    assert delete_tool.DELETE_RESULT_VOCABULARY == EXPECTED_VOCABULARY


def test_spec_table_exactly_matches_runtime_vocabulary():
    assert _published_vocabulary() == delete_tool.DELETE_RESULT_VOCABULARY


@pytest.mark.parametrize(
    ("reason_code", "success"),
    [
        ("index_deleted", True),
        ("index_not_found", False),
        ("index_lifecycle_busy", False),
    ],
)
def test_public_delete_emits_each_authoritative_result(
    tmp_path, monkeypatch, reason_code, success
):
    def resolve_repo(self, repo):
        return "local", "docs"

    def delete_index(self, owner, name, *, outcome, lock_wait):
        assert (owner, name) == ("local", "docs")
        assert lock_wait is False
        outcome["reason_code"] = reason_code
        return success

    monkeypatch.setattr(delete_tool.DocStore, "_resolve_repo", resolve_repo)
    monkeypatch.setattr(delete_tool.DocStore, "delete_index", delete_index)

    result = delete_tool.delete_index(
        "local/docs",
        storage_path=str(tmp_path),
    )

    assert set(result) == {
        "success",
        "repo",
        "reason_code",
        "retryable",
        "message",
        "_meta",
    }
    assert {
        "success": result["success"],
        "reason_code": result["reason_code"],
        "retryable": result["retryable"],
    } == {
        "success": EXPECTED_VOCABULARY[reason_code]["success"],
        "reason_code": reason_code,
        "retryable": EXPECTED_VOCABULARY[reason_code]["retryable"],
    }
    assert result["repo"] == "local/docs"
    assert result["message"] == EXPECTED_MESSAGES[reason_code]
