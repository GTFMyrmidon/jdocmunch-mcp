"""Tests for v1.92.0 — live-source freshness compares in the indexed domain (#74).

v1.91.0's live_source mode read RAW workspace bytes, but the index stores hashes
and byte offsets over PREPROCESSED content (transformed formats — .json, .jsonc,
.svg, .xml, .html, .mdx, .ipynb, .tscn, .tres — are converted by
preprocess_content before storage). So a clean index of a transformed format
false-flagged every section as stale_index under live_source=True.

The probe now reproduces index_local's pipeline in live mode (read with
newline="" then preprocess_content) and hashes/slices the preprocessed bytes, so
an unchanged transformed file is fresh, while a real edit to the preprocessed
representation still surfaces as drift.
"""

from __future__ import annotations

from jdocmunch_mcp.tools.get_recent_changes import get_recent_changes
from jdocmunch_mcp.tools.index_local import index_local


_MD = "# Sample\n\nPlain Markdown body.\n"
_JSONC = (
    "{\n"
    '  // stripped by JSONC preprocessing\n'
    '  "hooks": {\n'
    '    "PreCompact": [\n'
    '      {"matcher": "*", "cmd": "echo ok"}\n'
    "    ]\n"
    "  }\n"
    "}\n"
)
_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">\n'
    "  <!-- a comment -->\n"
    '  <rect width="10" height="10" fill="#abc"/>\n'
    "  <title>Box</title>\n"
    "</svg>\n"
)


def _setup(tmp_path, files):
    docs = tmp_path / "wiki"
    docs.mkdir()
    for fname, body in files.items():
        (docs / fname).write_text(body, encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_local(path=str(docs), use_ai_summaries=False, storage_path=storage)
    assert res["success"], res
    return res["repo"], storage, docs


def test_clean_index_markdown_no_drift_either_layer(tmp_path):
    repo, storage, _ = _setup(tmp_path, {"sample.md": _MD})
    cached = get_recent_changes(repo=repo, storage_path=storage)
    live = get_recent_changes(repo=repo, live_source=True, storage_path=storage)
    assert cached["change_count"] == 0
    assert live["change_count"] == 0
    assert live["_meta"]["drift_layer"] == "live_source"


def test_clean_index_transformed_formats_no_live_drift(tmp_path):
    """Regression: unchanged .jsonc / .svg must not false-flag under live_source."""
    repo, storage, _ = _setup(
        tmp_path, {"sample.md": _MD, "sample.jsonc": _JSONC, "diagram.svg": _SVG}
    )
    cached = get_recent_changes(repo=repo, storage_path=storage)
    live = get_recent_changes(repo=repo, live_source=True, storage_path=storage)
    assert cached["change_count"] == 0, cached
    assert live["change_count"] == 0, live["changes"]


def test_reindex_does_not_leave_transformed_files_stale(tmp_path):
    """A second incremental index must not leave transformed files permanently stale."""
    repo, storage, docs = _setup(tmp_path, {"sample.jsonc": _JSONC, "diagram.svg": _SVG})
    second = index_local(
        path=str(docs), use_ai_summaries=False, storage_path=storage, incremental=True
    )
    assert second["success"]
    live = get_recent_changes(repo=repo, live_source=True, storage_path=storage)
    assert live["change_count"] == 0, live["changes"]


def test_real_preprocessed_edit_to_transformed_still_drifts(tmp_path):
    """Editing structure that survives preprocessing must still surface as drift.

    convert_json renders JSON *structure* (keys become headings), so a key
    rename changes the preprocessed/indexed representation and must drift.
    """
    repo, storage, docs = _setup(tmp_path, {"sample.jsonc": _JSONC})
    # Rename a key — the rendered structure (and thus the indexed bytes) changes.
    (docs / "sample.jsonc").write_text(
        _JSONC.replace("PreCompact", "PreCompactRenamed"), encoding="utf-8"
    )
    live = get_recent_changes(repo=repo, live_source=True, storage_path=storage)
    assert live["change_count"] > 0, live


def test_comment_only_edit_to_transformed_does_not_drift(tmp_path):
    """Editing only bytes that preprocessing removes (a JSONC comment) is not drift."""
    repo, storage, docs = _setup(tmp_path, {"sample.jsonc": _JSONC})
    # Change only the comment text — preprocess_content strips it, so the
    # indexed representation is unchanged and this must NOT be stale_index.
    (docs / "sample.jsonc").write_text(
        "{\n"
        '  // a totally different comment that gets stripped anyway\n'
        '  "hooks": {\n'
        '    "PreCompact": [\n'
        '      {"matcher": "*", "cmd": "echo ok"}\n'
        "    ]\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    live = get_recent_changes(repo=repo, live_source=True, storage_path=storage)
    assert live["change_count"] == 0, live["changes"]
