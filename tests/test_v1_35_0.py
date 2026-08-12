"""Tests for v1.35.0: CHANGELOG generator + code-block compression."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.retrieval.code_compress import (
    compress_fenced_code,
    _markers_for,
    _is_comment_line,
)
from jdocmunch_mcp.tools.get_section import get_section
from jdocmunch_mcp.tools.get_sections import get_sections
from jdocmunch_mcp.tools.index_local import index_local


# ---------------------------------------------------------------------------
# Code-block compression — pure function
# ---------------------------------------------------------------------------


class TestCompressFencedCode:
    def test_empty_input(self):
        out, saved = compress_fenced_code("")
        assert out == ""
        assert saved == 0

    def test_no_fence_passes_through(self):
        text = "hello\n\nworld\n# this looks like a comment\n"
        out, saved = compress_fenced_code(text)
        assert out == text
        assert saved == 0

    def test_python_strips_comments_and_blanks(self):
        text = textwrap.dedent("""
            ```python
            # license header
            import os

            def f():
                # explanation
                return os.getcwd()
            ```
        """).lstrip("\n")
        out, saved = compress_fenced_code(text)
        assert "license header" not in out
        assert "explanation" not in out
        assert "import os" in out
        assert "return os.getcwd()" in out
        # Blank line between import and def is dropped.
        assert "\n\n" not in out.split("```python\n", 1)[1].split("```")[0]
        assert saved > 0

    def test_javascript_double_slash(self):
        text = textwrap.dedent("""
            ```js
            // top comment
            const x = 1;
            // mid

            const y = 2;
            ```
        """).lstrip("\n")
        out, _ = compress_fenced_code(text)
        assert "top comment" not in out
        assert "mid" not in out
        assert "const x = 1;" in out
        assert "const y = 2;" in out

    def test_sql_dash_dash(self):
        text = "```sql\n-- create the table\nSELECT 1;\n\n-- final\n```\n"
        out, _ = compress_fenced_code(text)
        assert "create the table" not in out
        assert "final" not in out
        assert "SELECT 1;" in out

    def test_unknown_language_keeps_comments(self):
        text = "```mystery\n# not a known language\nfoo\n```\n"
        out, _ = compress_fenced_code(text)
        assert "not a known language" in out

    def test_partial_line_comment_preserved(self):
        text = "```python\nx = 1  # inline retained\n```\n"
        out, _ = compress_fenced_code(text)
        assert "inline retained" in out

    def test_outside_fence_unchanged(self):
        text = "Prose paragraph.\n\n```python\n# strip me\nx = 1\n```\n\n# Heading after\n"
        out, _ = compress_fenced_code(text)
        assert "# Heading after" in out
        assert "strip me" not in out

    def test_tilde_fence_supported(self):
        text = "~~~python\n# strip me\nx = 1\n~~~\n"
        out, _ = compress_fenced_code(text)
        assert "strip me" not in out
        assert "x = 1" in out

    def test_long_fence_must_match(self):
        # Open with 4 backticks; close requires at least 4.
        text = "````python\n# strip me\nx = 1\n````\n"
        out, _ = compress_fenced_code(text)
        assert "strip me" not in out
        assert "x = 1" in out

    def test_multiple_fences(self):
        text = textwrap.dedent("""
            ```python
            # A
            a = 1
            ```

            prose

            ```js
            // B
            const b = 1;
            ```
        """).lstrip("\n")
        out, _ = compress_fenced_code(text)
        assert "# A" not in out
        assert "// B" not in out
        assert "a = 1" in out
        assert "const b = 1;" in out
        assert "prose" in out

    def test_unclosed_fence_does_not_explode(self):
        text = "```python\n# unclosed\nx = 1\n"
        out, _ = compress_fenced_code(text)
        # Lines past open should still be processed as fenced.
        assert "unclosed" not in out
        assert "x = 1" in out


class TestMarkersFor:
    def test_known_languages(self):
        assert _markers_for("python") == ("#",)
        assert _markers_for("PY") == ("#",)
        assert _markers_for("js") == ("//",)
        assert _markers_for("typescript") == ("//",)
        assert _markers_for("sql") == ("--",)
        assert _markers_for("clojure") == (";",)
        assert _markers_for("erlang") == ("%",)

    def test_unknown_returns_empty(self):
        assert _markers_for("") == ()
        assert _markers_for("mystery") == ()
        assert _markers_for(None) == ()


class TestIsCommentLine:
    def test_full_comment(self):
        assert _is_comment_line("  # foo", ("#",)) is True
        assert _is_comment_line("// foo", ("//",)) is True

    def test_partial_not_comment(self):
        assert _is_comment_line("x = 1  # tail", ("#",)) is False

    def test_blank_not_comment(self):
        assert _is_comment_line("   ", ("#",)) is False


# ---------------------------------------------------------------------------
# get_section integration
# ---------------------------------------------------------------------------


class TestGetSectionCompressCode:
    def _index_with_code(self, tmp_path):
        repo = tmp_path / "docs"
        repo.mkdir()
        body = textwrap.dedent("""
            # Page

            Some prose.

            ```python
            # license header that costs tokens
            import os

            def f():
                # docstring stand-in
                return os.getcwd()
            ```

            More prose.
        """).lstrip("\n")
        (repo / "page.md").write_text(body, encoding="utf-8")
        index_local(
            path=str(repo), name="ccomp",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

    def test_default_keeps_comments(self, tmp_path):
        self._index_with_code(tmp_path)
        from jdocmunch_mcp.storage import DocStore
        idx = DocStore(base_path=str(tmp_path)).load_index("local", "ccomp")
        sec = next(s for s in idx.sections if s.get("title") == "Page")
        out = get_section(repo="ccomp", section_id=sec["id"], storage_path=str(tmp_path))
        content = out["section"]["content"]
        assert "license header" in content
        assert out["_meta"].get("code_compressed_bytes") is None

    def test_compress_strips_in_response(self, tmp_path):
        self._index_with_code(tmp_path)
        from jdocmunch_mcp.storage import DocStore
        idx = DocStore(base_path=str(tmp_path)).load_index("local", "ccomp")
        sec = next(s for s in idx.sections if s.get("title") == "Page")
        out = get_section(
            repo="ccomp", section_id=sec["id"],
            compress_code=True, storage_path=str(tmp_path),
        )
        content = out["section"]["content"]
        assert "license header" not in content
        assert "docstring stand-in" not in content
        assert "import os" in content
        assert "return os.getcwd()" in content
        assert out["_meta"]["code_compressed_bytes"] > 0

    def test_disk_unchanged_after_compress(self, tmp_path):
        self._index_with_code(tmp_path)
        from jdocmunch_mcp.storage import DocStore
        idx = DocStore(base_path=str(tmp_path)).load_index("local", "ccomp")
        sec = next(s for s in idx.sections if s.get("title") == "Page")
        # Compress once.
        get_section(
            repo="ccomp", section_id=sec["id"],
            compress_code=True, storage_path=str(tmp_path),
        )
        # Re-fetch without compression — original bytes survive.
        out = get_section(repo="ccomp", section_id=sec["id"], storage_path=str(tmp_path))
        assert "license header" in out["section"]["content"]


class TestGetSectionsCompressCode:
    def test_batch_aggregates_savings(self, tmp_path):
        body = textwrap.dedent("""
            # Top

            ```python
            # comment
            x = 1
            ```
        """).lstrip("\n")
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "a.md").write_text(body, encoding="utf-8")
        (repo / "b.md").write_text(body, encoding="utf-8")
        index_local(
            path=str(repo), name="bccomp",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        from jdocmunch_mcp.storage import DocStore
        idx = DocStore(base_path=str(tmp_path)).load_index("local", "bccomp")
        ids = [s["id"] for s in idx.sections if s.get("title") == "Top"]
        assert len(ids) == 2
        out = get_sections(
            repo="bccomp", section_ids=ids,
            compress_code=True, storage_path=str(tmp_path),
        )
        assert out["_meta"]["code_compressed_bytes"] > 0


# ---------------------------------------------------------------------------
# CHANGELOG generator
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


class TestChangelogGenerator:
    def test_script_exists(self):
        assert (_repo_root() / "scripts" / "generate_changelog.py").exists()

    def test_module_loads(self):
        mod = self._load_module()
        assert hasattr(mod, "render")
        assert hasattr(mod, "_RELEASE_RE")

    def test_generated_file_present(self):
        path = _repo_root() / "CHANGELOG.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert text.startswith("# Changelog")

    def _load_module(self):
        import importlib.util
        path = _repo_root() / "scripts" / "generate_changelog.py"
        spec = importlib.util.spec_from_file_location("_gen_changelog", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_release_entries_have_required_shape(self):
        mod = self._load_module()
        render = mod.render

        # Synthetic commits exercise the renderer without touching git.
        commits = [
            {
                "hash": "deadbeef",
                "date": "2026-04-26",
                "subject": "release: v1.99.0 — synthetic example",
                "body": "First paragraph summary.\n\nSecond paragraph not used.\n\nCo-Authored-By: x <x@y>",
            },
            {"hash": "f00", "date": "2026-04-26",
             "subject": "chore: not a release", "body": "ignored"},
        ]
        text = render(commits)
        assert "## v1.99.0 — 2026-04-26" in text
        assert "**synthetic example**" in text
        assert "First paragraph summary." in text
        assert "Co-Authored-By" not in text
        # Non-release commits filtered.
        assert "not a release" not in text

    def test_subject_regex_accepts_em_dash_and_hyphen(self):
        mod = self._load_module()
        _RELEASE_RE = mod._RELEASE_RE
        for sep in ("—", "–", "-"):
            m = _RELEASE_RE.match(f"release: v1.0.0 {sep} title here")
            assert m is not None
            assert m.group("ver") == "1.0.0"
            assert m.group("title").strip() == "title here"

    def test_runs_against_real_repo(self, tmp_path):
        # Smoke test: invoking the script with --out under tmp succeeds and
        # produces a non-empty file with at least one release entry.
        out = tmp_path / "CL.md"
        proc = subprocess.run(
            [sys.executable, str(_repo_root() / "scripts" / "generate_changelog.py"),
             "--out", str(out), "--repo", str(_repo_root())],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        assert proc.returncode == 0, proc.stderr
        text = out.read_text(encoding="utf-8")
        assert "# Changelog" in text
        assert "## v1." in text


# ---------------------------------------------------------------------------
# Schema parity
# ---------------------------------------------------------------------------


class TestSchema:
    def test_get_section_schema_has_compress_code(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gs = next(t for t in tools if t.name == "get_section")
        assert "compress_code" in gs.inputSchema["properties"]
        assert gs.inputSchema["properties"]["compress_code"]["default"] is False

    def test_get_sections_schema_has_compress_code(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        gs = next(t for t in tools if t.name == "get_sections")
        assert "compress_code" in gs.inputSchema["properties"]
