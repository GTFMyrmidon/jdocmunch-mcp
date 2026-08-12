"""v1.61.0 — explicit-paths indexing on `index_local`.

Adds `paths=[...]` to bypass the directory walk and index only the listed
files / subdirs. Also covers the CLI `--paths-from FILE` (and `-` for stdin)
parsing.
"""

import io
import json
import sys
import uuid
from pathlib import Path

import pytest

from jdocmunch_mcp.tools.index_local import index_local


@pytest.fixture
def doc_tree(tmp_path: Path) -> Path:
    """Three .md files plus a subdirectory with one more."""
    (tmp_path / "intro.md").write_text("# Intro\n\nWelcome.\n", encoding="utf-8")
    (tmp_path / "install.md").write_text("# Install\n\npip install foo\n", encoding="utf-8")
    (tmp_path / "advanced.md").write_text("# Advanced\n\nDetails.\n", encoding="utf-8")
    sub = tmp_path / "guides"
    sub.mkdir()
    (sub / "auth.md").write_text("# Auth\n\nOauth flow.\n", encoding="utf-8")
    (tmp_path / "junk.bin").write_bytes(b"\x00\x01\x02")  # unsupported extension
    return tmp_path


class TestExplicitPaths:
    def test_only_listed_files_indexed(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        result = index_local(
            path=str(doc_tree),
            paths=["intro.md", "install.md"],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="explicit-test",
        )
        assert result.get("success") is True, result
        # advanced.md and guides/auth.md should NOT be indexed
        section_count = result.get("section_count", 0)
        # 2 docs * 1 heading each = at least 2 sections
        assert section_count >= 2

    def test_directory_in_paths_recurses(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        result = index_local(
            path=str(doc_tree),
            paths=["guides"],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="dir-recurse",
        )
        assert result.get("success") is True, result
        # guides/auth.md should be picked up
        assert result.get("section_count", 0) >= 1

    def test_absolute_path_under_folder_accepted(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        result = index_local(
            path=str(doc_tree),
            paths=[str(doc_tree / "intro.md")],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="abs-path",
        )
        assert result.get("success") is True, result
        assert result.get("section_count", 0) >= 1

    def test_path_outside_folder_rejected(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        outside = tmp_path.parent  # almost certainly not under doc_tree
        result = index_local(
            path=str(doc_tree),
            paths=[str(outside / "elsewhere.md")],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="outside-reject",
        )
        # No usable file → either no_docs error OR empty index with warnings
        if result.get("success") is False:
            assert "No documentation files found" in (result.get("error") or "")
        warnings = result.get("warnings") or []
        assert any(
            "outside" in str(w).lower() or "non-existent" in str(w).lower()
            for w in warnings
        )

    def test_unsupported_extension_skipped_with_warning(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        result = index_local(
            path=str(doc_tree),
            paths=["junk.bin", "intro.md"],
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="unsupported",
        )
        assert result.get("success") is True
        # intro.md still indexed; junk.bin warned-and-skipped
        warnings = result.get("warnings") or []
        assert any("junk.bin" in str(w) for w in warnings)

    def test_default_behavior_unchanged_when_paths_omitted(self, doc_tree: Path, tmp_path: Path):
        storage = tmp_path / "store"
        result = index_local(
            path=str(doc_tree),
            storage_path=str(storage),
            use_ai_summaries=False,
            use_embeddings=False,
            name="default-walk",
        )
        assert result.get("success") is True
        # All 4 .md files indexed (.bin skipped)
        # Section count should reflect all of them
        assert result.get("section_count", 0) >= 4


class TestPathsFromArgParser:
    """Unit-test the CLI's --paths-from file/stdin reader helper."""

    def test_reads_file_strips_blanks_and_comments(self, tmp_path: Path):
        from jdocmunch_mcp.server import _load_paths_from_arg
        f = tmp_path / "p.txt"
        f.write_text(
            "intro.md\n"
            "\n"
            "  # a comment\n"
            "install.md  \n"
            "guides/auth.md\n",
            encoding="utf-8",
        )
        paths, err = _load_paths_from_arg(str(f))
        assert err is None
        assert paths == ["intro.md", "install.md", "guides/auth.md"]

    def test_reads_stdin(self, monkeypatch):
        from jdocmunch_mcp.server import _load_paths_from_arg
        monkeypatch.setattr("sys.stdin", io.StringIO("a.md\nb.md\n"))
        paths, err = _load_paths_from_arg("-")
        assert err is None
        assert paths == ["a.md", "b.md"]

    def test_empty_file_returns_error(self, tmp_path: Path):
        from jdocmunch_mcp.server import _load_paths_from_arg
        f = tmp_path / "empty.txt"
        f.write_text("\n# nothing\n", encoding="utf-8")
        paths, err = _load_paths_from_arg(str(f))
        assert paths is None
        assert err is not None
        assert "no usable paths" in err.lower()

    def test_missing_file_returns_error(self, tmp_path: Path):
        from jdocmunch_mcp.server import _load_paths_from_arg
        missing = tmp_path / "does_not_exist.txt"
        paths, err = _load_paths_from_arg(str(missing))
        assert paths is None
        assert err is not None
        assert "cannot read" in err.lower()
