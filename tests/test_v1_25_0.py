"""Tests for v1.25.0: notebook output preservation."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.parser.notebook_parser import (
    _render_output,
    _render_outputs,
    _strip_html,
    _truncate,
    convert_notebook,
)
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


def _nb(cells: list, language: str = "python") -> str:
    """Build an .ipynb-shaped JSON string for testing."""
    return json.dumps({
        "cells": cells,
        "metadata": {"language_info": {"name": language}},
        "nbformat": 4,
        "nbformat_minor": 5,
    })


# ---------------------------------------------------------------------------
# Output renderers
# ---------------------------------------------------------------------------

class TestRenderOutput:
    def test_stream_output(self):
        out = {"output_type": "stream", "name": "stdout", "text": ["hello\n", "world\n"]}
        rendered = _render_output(out)
        assert "hello" in rendered
        assert "world" in rendered

    def test_execute_result_text_plain(self):
        out = {
            "output_type": "execute_result",
            "data": {"text/plain": "42"},
        }
        assert "42" in _render_output(out)

    def test_execute_result_text_html_stripped(self):
        out = {
            "output_type": "execute_result",
            "data": {"text/html": "<table><tr><td>cell</td></tr></table>"},
        }
        rendered = _render_output(out)
        assert "<table>" not in rendered
        assert "cell" in rendered

    def test_image_marker(self):
        out = {
            "output_type": "display_data",
            "data": {"image/png": "iVBORw0KGgoAAAANSUhEUg..."},
        }
        rendered = _render_output(out)
        assert "image/png" in rendered
        # Truncated, never the full base64.
        assert "iVBORw0K" not in rendered

    def test_error_traceback(self):
        out = {
            "output_type": "error",
            "ename": "NameError",
            "evalue": "x is not defined",
            "traceback": ["Traceback (most recent call last):\n", "  File ..."],
        }
        rendered = _render_output(out)
        assert "Traceback" in rendered

    def test_application_json(self):
        out = {"output_type": "execute_result", "data": {"application/json": {"k": [1, 2, 3]}}}
        rendered = _render_output(out)
        assert "json" in rendered
        assert '"k"' in rendered

    def test_unknown_output_returns_empty(self):
        assert _render_output({"output_type": "weird"}) == ""

    def test_render_outputs_aggregates(self):
        outputs = [
            {"output_type": "stream", "text": "first\n"},
            {"output_type": "stream", "text": "second\n"},
        ]
        rendered = _render_outputs(outputs)
        assert "Output:" in rendered
        assert "first" in rendered
        assert "second" in rendered

    def test_render_outputs_empty_returns_empty(self):
        assert _render_outputs([]) == ""

    def test_truncate_caps_long_text(self):
        long = "x" * 5000
        out = _truncate(long, limit=100)
        assert "[truncated]" in out
        assert len(out) < 200

    def test_strip_html_collapses_whitespace(self):
        out = _strip_html("<div> a   b\n c </div>")
        assert out == "a b c"


# ---------------------------------------------------------------------------
# convert_notebook end-to-end
# ---------------------------------------------------------------------------

class TestConvertNotebook:
    def test_code_cell_with_stream_output(self):
        nb_json = _nb([
            {
                "cell_type": "markdown",
                "source": "# Title\n",
            },
            {
                "cell_type": "code",
                "source": "print('hi')\n",
                "outputs": [{"output_type": "stream", "text": "hi\n"}],
            },
        ])
        md = convert_notebook(nb_json)
        # The source code is in a fenced block.
        assert "print('hi')" in md
        # The output is preserved.
        assert "**Output:**" in md
        # And the streamed text appears in the body.
        assert "hi" in md

    def test_code_cell_without_outputs_unchanged(self):
        nb_json = _nb([{"cell_type": "code", "source": "1 + 1\n", "outputs": []}])
        md = convert_notebook(nb_json)
        assert "1 + 1" in md
        assert "**Output:**" not in md  # no outputs to render

    def test_invalid_json_returns_empty(self):
        assert convert_notebook("{not json") == ""

    def test_kernel_language_detection(self):
        nb_json = _nb(
            [{"cell_type": "code", "source": "x = 1\n", "outputs": []}],
            language="javascript",
        )
        md = convert_notebook(nb_json)
        assert "```javascript" in md


# ---------------------------------------------------------------------------
# Indexed retrieval — outputs reach search_sections
# ---------------------------------------------------------------------------

class TestEndToEndOutputsRetrievable:
    def test_output_text_retrievable_via_search(self, tmp_path):
        nb = _nb([
            {"cell_type": "markdown", "source": "# Notebook\n## Filtering\n"},
            {
                "cell_type": "code",
                "source": "print(len(events))\n",
                "outputs": [{"output_type": "stream", "text": "42 events found\n"}],
            },
        ])
        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "demo.ipynb").write_text(nb, encoding="utf-8")
        index_local(
            path=str(repo_dir), name="nb",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        # The phrase from the output should be searchable.
        out = search_sections(
            repo="nb", query="42 events found",
            semantic=False, storage_path=str(tmp_path),
        )
        # Some result must exist — the output text is now indexed.
        assert out["results"], out

    def test_error_traceback_retrievable(self, tmp_path):
        nb = _nb([
            {
                "cell_type": "code",
                "source": "x\n",
                "outputs": [{
                    "output_type": "error",
                    "ename": "NameError",
                    "evalue": "name 'x' is not defined",
                    "traceback": ["NameError: name 'x' is not defined"],
                }],
            },
        ])
        repo_dir = tmp_path / "docs"
        repo_dir.mkdir()
        (repo_dir / "demo.ipynb").write_text(nb, encoding="utf-8")
        index_local(
            path=str(repo_dir), name="nbe",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        out = search_sections(
            repo="nbe", query="NameError defined",
            semantic=False, storage_path=str(tmp_path),
        )
        assert out["results"]
