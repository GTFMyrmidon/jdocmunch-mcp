"""Tests for v1.91.0 — get_recent_changes cached-mirror vs live-source (#71).

get_recent_changes is documented as returning sections whose "source" drifted,
but FreshnessProbe reads the cached raw-content mirror under the doc index, not
the live workspace files. An empty result therefore proves "stored mirror and
index agree", not "live workspace files match the index".

This adds:
  * `_meta.drift_layer` naming which layer was actually read; and
  * an opt-in `live_source=True` mode that reads the live files under the
    index's source_root, with a clean fallback to the cached mirror when no
    usable source_root is recorded.
The default (cached-mirror) behavior other consumers rely on is unchanged.
"""

from __future__ import annotations

import shutil

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_recent_changes import get_recent_changes
from jdocmunch_mcp.tools.index_local import index_local


_DOC = "# Title\n\nIntro paragraph here.\n\n## Section Two\n\nSecond body text.\n"


def _setup(tmp_path):
    docs = tmp_path / "wiki"
    docs.mkdir()
    f = docs / "guide.md"
    f.write_text(_DOC, encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_local(path=str(docs), use_ai_summaries=False, storage_path=storage)
    assert res["success"], res
    return res["repo"], storage, docs, f


def test_default_layer_is_cached_mirror(tmp_path):
    repo, storage, _docs, _f = _setup(tmp_path)
    r = get_recent_changes(repo=repo, storage_path=storage)
    assert r["_meta"]["drift_layer"] == "cached_mirror"
    assert r["_meta"]["live_source_requested"] is False
    assert r["change_count"] == 0


def test_live_unedited_reports_fresh(tmp_path):
    """Live mode on an unedited file must not produce false drift (CRLF-safe)."""
    repo, storage, _docs, _f = _setup(tmp_path)
    r = get_recent_changes(repo=repo, live_source=True, storage_path=storage)
    assert r["_meta"]["drift_layer"] == "live_source"
    assert r["_meta"]["live_source_available"] is True
    assert r["change_count"] == 0


def test_live_edit_invisible_to_mirror_visible_to_live(tmp_path):
    repo, storage, _docs, f = _setup(tmp_path)
    # Edit the LIVE workspace file without reindexing.
    f.write_text(
        "# Title\n\nCOMPLETELY DIFFERENT intro now.\n\n## Section Two\n\nChanged.\n",
        encoding="utf-8",
    )
    mirror = get_recent_changes(repo=repo, storage_path=storage)
    assert mirror["_meta"]["drift_layer"] == "cached_mirror"
    assert mirror["change_count"] == 0  # cached mirror still agrees with the index

    live = get_recent_changes(repo=repo, live_source=True, storage_path=storage)
    assert live["_meta"]["drift_layer"] == "live_source"
    assert live["_meta"]["live_source_available"] is True
    assert live["change_count"] > 0


def test_live_source_falls_back_when_root_missing(tmp_path):
    repo, storage, docs, _f = _setup(tmp_path)
    shutil.rmtree(docs)  # source root gone; the cached mirror under storage remains
    r = get_recent_changes(repo=repo, live_source=True, storage_path=storage)
    assert r["_meta"]["live_source_requested"] is True
    assert r["_meta"]["live_source_available"] is False
    assert r["_meta"]["drift_layer"] == "cached_mirror"
    # Mirror is intact, so the fallback still returns a clean result.
    assert r["change_count"] == 0


def test_mirror_mode_unchanged_detects_mirror_drift(tmp_path):
    """Default cached-mirror behavior is preserved: mutating the mirror is seen."""
    repo, storage, _docs, _f = _setup(tmp_path)
    store = DocStore(base_path=storage)
    owner, name = store._resolve_repo(repo)
    mirror_file = store._content_dir(owner, name) / "guide.md"
    mirror_file.write_text(
        "# Title\n\nMIRROR MUTATED.\n\n## Section Two\n\nx.\n", encoding="utf-8"
    )
    r = get_recent_changes(repo=repo, storage_path=storage)
    assert r["_meta"]["drift_layer"] == "cached_mirror"
    assert r["change_count"] > 0
