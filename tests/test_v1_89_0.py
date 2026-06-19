"""Tests for v1.89.0 — index_local zero-config safe default name (#72).

When ``name`` was omitted, ``index_local`` returned the raw folder basename as
the storage name, so a folder whose basename contained characters invalid for
jDocMunch storage (e.g. a space) failed downstream with
``Invalid name: '<folder label>'``. The omitted-name path now derives a
deterministic, storage-safe handle (slug + short hash of the absolute path)
when the basename isn't already valid, preserving a valid basename exactly.
"""

from __future__ import annotations

import re

import pytest

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import (
    _default_local_name,
    index_local,
    normalize_local_index_name,
)

_STORAGE_OK = re.compile(r"[A-Za-z0-9._-]+")


# ── _default_local_name unit ────────────────────────────────────────────────

def test_preserves_valid_basename_exactly():
    assert _default_local_name("example-docs", "/x/example-docs") == "example-docs"


def test_slugifies_spaced_basename_to_storage_safe():
    out = _default_local_name("My Docs", "/x/My Docs")
    assert out.startswith("my-docs-")
    assert _STORAGE_OK.fullmatch(out)


def test_derivation_is_deterministic():
    a = _default_local_name("My Docs", "/x/My Docs")
    b = _default_local_name("My Docs", "/x/My Docs")
    assert a == b


def test_same_slug_different_path_does_not_collide():
    a = _default_local_name("My Docs", "/a/My Docs")
    b = _default_local_name("My Docs", "/b/My Docs")
    assert a != b
    assert a.startswith("my-docs-") and b.startswith("my-docs-")


def test_all_invalid_chars_falls_back_to_local_docs():
    out = _default_local_name("日本語", "/x/日本語")
    assert out.startswith("local-docs-")
    assert _STORAGE_OK.fullmatch(out)


# ── normalize_local_index_name routing ──────────────────────────────────────

def test_omitted_name_uses_safe_derivation():
    out = normalize_local_index_name(None, "My Docs", "/x/My Docs")
    assert out.startswith("my-docs-")
    assert _STORAGE_OK.fullmatch(out)


def test_omitted_name_valid_basename_unchanged():
    assert normalize_local_index_name(None, "good-name", "/x/good-name") == "good-name"


def test_explicit_local_prefix_still_round_trips():
    assert normalize_local_index_name("local/foo", "folder") == "foo"


def test_invalid_explicit_names_still_fail_closed():
    with pytest.raises(ValueError):
        normalize_local_index_name("a/b", "folder")
    with pytest.raises(ValueError):
        normalize_local_index_name("local/", "folder")
    with pytest.raises(ValueError):
        normalize_local_index_name("github/foo", "folder")


# ── index_local integration ─────────────────────────────────────────────────

def _make_corpus(parent, label):
    docs = parent / label
    docs.mkdir(parents=True)
    (docs / "README.md").write_text("# Title\n\nSome body text.\n", encoding="utf-8")
    return docs


def test_spaced_folder_indexes_with_derived_name(tmp_path):
    docs = _make_corpus(tmp_path, "My Docs")
    store = str(tmp_path / "store")
    res = index_local(path=str(docs), use_ai_summaries=False, storage_path=store)
    assert res["success"] is True, res
    assert res["repo"].startswith("local/my-docs-")
    assert res["original_folder_label"] == "My Docs"
    assert res["derived_local_name"] == res["repo"].split("/", 1)[1]
    assert any("not a valid storage name" in w for w in res.get("warnings", []))
    # The derived index is actually loadable under owner=local.
    owner, name = DocStore(base_path=store)._resolve_repo(res["repo"])
    assert DocStore(base_path=store).load_index(owner, name) is not None


def test_spaced_folder_refresh_is_stable(tmp_path):
    """A second refresh resolves to the same derived handle (deterministic)."""
    docs = _make_corpus(tmp_path, "My Docs")
    store = str(tmp_path / "store")
    first = index_local(path=str(docs), use_ai_summaries=False, storage_path=store)
    second = index_local(
        path=str(docs), use_ai_summaries=False, storage_path=store, incremental=True
    )
    assert second["success"] is True
    assert second["repo"] == first["repo"]


def test_valid_folder_name_has_no_derivation_fields(tmp_path):
    docs = _make_corpus(tmp_path, "valid-docs")
    store = str(tmp_path / "store")
    res = index_local(path=str(docs), use_ai_summaries=False, storage_path=store)
    assert res["repo"] == "local/valid-docs"
    assert "original_folder_label" not in res
    assert "derived_local_name" not in res


def test_two_spaced_folders_get_distinct_indexes(tmp_path):
    """Two same-named spaced folders in different parents don't collide."""
    a = _make_corpus(tmp_path / "a", "My Docs")
    b = _make_corpus(tmp_path / "b", "My Docs")
    store = str(tmp_path / "store")
    ra = index_local(path=str(a), use_ai_summaries=False, storage_path=store)
    rb = index_local(path=str(b), use_ai_summaries=False, storage_path=store)
    assert ra["success"] and rb["success"]
    assert ra["repo"] != rb["repo"]
