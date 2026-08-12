"""Issue #95 QA-20 public delete-result vocabulary regressions."""

from __future__ import annotations

import importlib
import multiprocessing
from pathlib import Path


delete_tool = importlib.import_module("jdocmunch_mcp.tools.delete_index")
storage_module = importlib.import_module(
    "jdocmunch_mcp.storage.doc_store"
)
DocStore = storage_module.DocStore

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

EXPECTED_STORAGE_CODES = {
    "deleted": "index_deleted",
    "not_found": "index_not_found",
    "lifecycle_busy": "index_lifecycle_busy",
}


def _store_with_index(storage_path):
    store = DocStore(base_path=str(storage_path))
    store.save_index(
        "local",
        "docs",
        [],
        {"docs.md": "# Docs\n"},
        {".md": 1},
    )
    return store


def _hold_index_lock(storage_path, locked, release):
    store = DocStore(base_path=storage_path)
    with store._index_write_lock("local", "docs"):
        locked.set()
        if not release.wait(15):
            raise TimeoutError("index-lock holder was not released")


def _join(process, label):
    process.join(15)
    if process.is_alive():
        process.terminate()
        process.join(5)
        raise AssertionError(f"{label} did not exit")
    assert process.exitcode == 0, f"{label} exit code: {process.exitcode}"


def _contended_index(storage_path):
    context = multiprocessing.get_context("spawn")
    locked = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_index_lock,
        args=(str(storage_path), locked, release),
    )
    holder.start()
    assert locked.wait(15)
    return holder, release


def _release_holder(holder, release):
    release.set()
    _join(holder, "index-lock holder")


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
    assert (
        getattr(delete_tool, "DELETE_RESULT_VOCABULARY", None)
        == EXPECTED_VOCABULARY
    )


def test_storage_reason_codes_are_the_public_source_of_truth():
    storage_codes = getattr(storage_module, "DELETE_REASON_CODES", None)
    tool_codes = getattr(delete_tool, "DELETE_REASON_CODES", None)
    vocabulary = getattr(delete_tool, "DELETE_RESULT_VOCABULARY", None)

    assert storage_codes == EXPECTED_STORAGE_CODES
    assert tool_codes is storage_codes
    assert set(vocabulary) == set(
        storage_codes.values()
    )


def test_spec_table_exactly_matches_runtime_vocabulary():
    assert _published_vocabulary() == delete_tool.DELETE_RESULT_VOCABULARY


def test_real_storage_emitter_exercises_every_authoritative_result(tmp_path):
    store = _store_with_index(tmp_path)
    holder, release = _contended_index(tmp_path)
    busy_outcome = {}
    try:
        assert store.delete_index(
            "local",
            "docs",
            outcome=busy_outcome,
            lock_wait=False,
        ) is False
    finally:
        _release_holder(holder, release)

    deleted_outcome = {}
    assert store.delete_index(
        "local",
        "docs",
        outcome=deleted_outcome,
        lock_wait=False,
    ) is True
    missing_outcome = {}
    assert store.delete_index(
        "local",
        "docs",
        outcome=missing_outcome,
        lock_wait=False,
    ) is False

    assert {
        busy_outcome["reason_code"],
        deleted_outcome["reason_code"],
        missing_outcome["reason_code"],
    } == set(EXPECTED_VOCABULARY)
    assert busy_outcome["reason_code"] == EXPECTED_STORAGE_CODES[
        "lifecycle_busy"
    ]
    assert deleted_outcome["reason_code"] == EXPECTED_STORAGE_CODES["deleted"]
    assert missing_outcome["reason_code"] == EXPECTED_STORAGE_CODES["not_found"]


def test_public_delete_maps_every_real_storage_result(tmp_path):
    _store_with_index(tmp_path)
    holder, release = _contended_index(tmp_path)
    try:
        busy = delete_tool.delete_index(
            "local/docs", storage_path=str(tmp_path)
        )
    finally:
        _release_holder(holder, release)
    deleted = delete_tool.delete_index(
        "local/docs", storage_path=str(tmp_path)
    )
    missing = delete_tool.delete_index(
        "local/docs", storage_path=str(tmp_path)
    )

    results = {
        result["reason_code"]: result
        for result in (busy, deleted, missing)
    }
    assert set(results) == set(EXPECTED_VOCABULARY)
    for reason_code, expected in EXPECTED_VOCABULARY.items():
        result = results[reason_code]
        assert set(result) == {
            "success",
            "repo",
            "reason_code",
            "retryable",
            "message",
            "_meta",
        }
        assert result["success"] is expected["success"]
        assert result["retryable"] is expected["retryable"]
        assert result["repo"] == "local/docs"
        assert result["message"] == EXPECTED_MESSAGES[reason_code]
