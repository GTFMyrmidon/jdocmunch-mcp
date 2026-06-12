"""v1.70.1 — jdoc#31: `paths` subset refresh must not prune the rest of the index.

`index_local(paths=[...])` (and CLI `--paths-from`) on an existing incremental
index used to treat every indexed file NOT in the list as deleted, collapsing a
whole corpus on a 1-file refresh. The diff is now scoped to the listed subset:
listed files are added/updated, a listed file missing from disk is removed, and
unlisted files are never touched.
"""

from pathlib import Path

import pytest

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.index_local import index_local


@pytest.fixture
def doc_tree(tmp_path: Path) -> Path:
    root = tmp_path / "docs"  # sibling of the storage dir, never walked into it
    root.mkdir()
    (root / "intro.md").write_text("# Intro\n\nWelcome.\n", encoding="utf-8")
    (root / "install.md").write_text("# Install\n\npip install foo\n", encoding="utf-8")
    (root / "advanced.md").write_text("# Advanced\n\nDetails.\n", encoding="utf-8")
    sub = root / "guides"
    sub.mkdir()
    (sub / "auth.md").write_text("# Auth\n\nOauth flow.\n", encoding="utf-8")
    return root


def _index_full(doc_tree: Path, storage: Path, name: str) -> dict:
    result = index_local(
        path=str(doc_tree),
        storage_path=str(storage),
        use_ai_summaries=False,
        use_embeddings=False,
        name=name,
    )
    assert result.get("success") is True, result
    return result


def _doc_paths(storage: Path, name: str) -> set:
    index = DocStore(base_path=str(storage)).load_index("local", name)
    assert index is not None
    return set(index.file_hashes)


class TestSubsetRefreshPreservesIndex:
    def test_refresh_one_file_keeps_the_rest(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        _index_full(doc_tree, storage, "subset")
        before = _doc_paths(storage, "subset")
        assert len(before) == 4

        (doc_tree / "intro.md").write_text("# Intro\n\nUpdated.\n", encoding="utf-8")
        result = index_local(
            path=str(doc_tree),
            paths=["intro.md"],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="subset",
        )
        assert result.get("success") is True, result
        assert result.get("deleted") == 0
        assert result.get("changed") == 1
        assert _doc_paths(storage, "subset") == before

    def test_unchanged_subset_reports_no_changes(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        _index_full(doc_tree, storage, "nochange")
        result = index_local(
            path=str(doc_tree),
            paths=["intro.md", "install.md"],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="nochange",
        )
        assert result.get("success") is True, result
        assert result.get("deleted") == 0
        assert _doc_paths(storage, "nochange") == {
            "intro.md", "install.md", "advanced.md", "guides/auth.md",
        }

    def test_listed_missing_file_is_pruned(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        _index_full(doc_tree, storage, "prune-one")
        (doc_tree / "install.md").unlink()

        result = index_local(
            path=str(doc_tree),
            paths=["install.md"],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="prune-one",
        )
        assert result.get("success") is True, result
        assert result.get("deleted") == 1
        assert _doc_paths(storage, "prune-one") == {
            "intro.md", "advanced.md", "guides/auth.md",
        }

    def test_listed_dir_scopes_deletion_to_subtree(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        _index_full(doc_tree, storage, "dir-scope")
        (doc_tree / "guides" / "auth.md").unlink()

        result = index_local(
            path=str(doc_tree),
            paths=["guides"],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="dir-scope",
        )
        assert result.get("success") is True, result
        assert result.get("deleted") == 1
        assert _doc_paths(storage, "dir-scope") == {
            "intro.md", "install.md", "advanced.md",
        }

    def test_listing_root_keeps_full_corpus_diff(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        _index_full(doc_tree, storage, "root-listed")
        (doc_tree / "advanced.md").unlink()

        result = index_local(
            path=str(doc_tree),
            paths=["."],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="root-listed",
        )
        assert result.get("success") is True, result
        assert result.get("deleted") == 1
        assert _doc_paths(storage, "root-listed") == {
            "intro.md", "install.md", "guides/auth.md",
        }

    def test_full_walk_deletion_semantics_unchanged(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        _index_full(doc_tree, storage, "walk")
        (doc_tree / "advanced.md").unlink()

        result = index_local(
            path=str(doc_tree),
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="walk",
        )
        assert result.get("success") is True, result
        assert result.get("deleted") == 1
        assert _doc_paths(storage, "walk") == {
            "intro.md", "install.md", "guides/auth.md",
        }

    def test_no_index_and_no_files_still_errors(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        result = index_local(
            path=str(doc_tree),
            paths=["does-not-exist.md"],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="fresh-empty",
        )
        assert result.get("success") is False
        assert "No documentation files found" in (result.get("error") or "")
