"""Tests for v1.33.0: answerability + quotability scoring."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.retrieval.scoring import (
    ANSWERABILITY_WEIGHTS,
    QUOTABILITY_WEIGHTS,
    attach_scores,
    compute_answerability,
    compute_quotability,
)
from jdocmunch_mcp.tools.index_local import index_local


# ---------------------------------------------------------------------------
# compute_answerability
# ---------------------------------------------------------------------------

class TestAnswerability:
    def test_empty_text_zero(self):
        out = compute_answerability("")
        assert out["value"] == 0.0
        assert set(out["components"].keys()) == set(ANSWERABILITY_WEIGHTS.keys())

    def test_definition_drives_score(self):
        text = (
            "**Section** — A unit of documentation between two headings.\n\n"
            "**Token** — A short-lived API credential.\n\n"
            "These are core concepts.\n"
        )
        out = compute_answerability(text)
        assert out["components"]["definition"] > 0.5
        assert out["value"] > 0.15

    def test_imperative_lifts_score(self):
        text = (
            "Install the package.\n"
            "Configure the env var.\n"
            "Run the CLI.\n"
            "Verify the output.\n"
            "Open the dashboard.\n"
        )
        out = compute_answerability(text)
        assert out["components"]["imperative"] > 0.5
        assert out["value"] > 0.10

    def test_code_block_present_score(self):
        text = "Some prose.\n\n```python\nprint('hi')\n```\n"
        out = compute_answerability(text)
        assert out["components"]["code_block"] == 1.0

    def test_numbered_steps(self):
        text = "1. Install\n2. Configure\n3. Run\n4. Verify\n"
        out = compute_answerability(text)
        assert out["components"]["numbered_steps"] > 0.5

    def test_faq_questions(self):
        text = "Why is it slow?\nBecause caching is off.\nHow to fix?\nEnable caching.\n"
        out = compute_answerability(text)
        assert out["components"]["faq_question"] > 0.0

    def test_value_bounded_zero_one(self):
        # Even with all signals firing, the score caps at 1.0.
        text = (
            "**X** — definition.\n"
            "**Y** — definition.\n"
            "**Z** — definition.\n"
            "Install the X.\nConfigure the Y.\nRun the Z.\nVerify.\nOpen.\n"
            "1. step\n2. step\n3. step\n4. step\n"
            "Why?\nWhy?\nWhy?\n"
            "```\ncode\n```\n"
        )
        out = compute_answerability(text)
        assert 0.0 <= out["value"] <= 1.0


# ---------------------------------------------------------------------------
# compute_quotability
# ---------------------------------------------------------------------------

class TestQuotability:
    def test_empty_text_zero(self):
        out = compute_quotability("")
        assert out["value"] == 0.0

    def test_high_prose_high_score(self):
        text = textwrap.dedent("""
            A bearer token is a short-lived credential issued by the auth service.
            It identifies the caller for the lifetime of a single API call. The
            token is opaque to the client and must be sent in the Authorization
            header on every request.

            **Token** — opaque API credential.

            Tokens expire after one hour by default; clients must refresh
            proactively at 80% of the lifetime.
        """).lstrip()
        out = compute_quotability(text)
        assert out["value"] > 0.50

    def test_see_above_penalty(self):
        # Same prose-heavy section but riddled with back-references.
        text = (
            "As mentioned above, the configuration is similar.\n"
            "See above for the schema.\n"
            "As discussed earlier, the format applies.\n"
            "See the previous section for examples.\n"
            "As noted previously, this is critical.\n"
        )
        out = compute_quotability(text)
        # self_contained should be reduced.
        assert out["components"]["self_contained"] < 0.5

    def test_pure_code_low_intro(self):
        # All code, no prose — low intro_density.
        text = (
            "```python\n"
            "def foo():\n"
            "    return 1\n"
            "```\n"
            "```python\n"
            "def bar():\n"
            "    return 2\n"
            "```\n"
        )
        out = compute_quotability(text)
        # Low value overall — pure code blocks are not quotable as prose.
        assert out["value"] < 0.45

    def test_short_section_lower_length_score(self):
        short = "Hi.\n"
        # Build a multi-line long fixture so line_count differs.
        long = "\n".join(f"Line {i} explains a thing." for i in range(15)) + "\n"
        out_short = compute_quotability(short)
        out_long = compute_quotability(long)
        assert out_short["components"]["length"] < out_long["components"]["length"]


# ---------------------------------------------------------------------------
# attach_scores
# ---------------------------------------------------------------------------

class TestAttachScores:
    def test_attaches_scalar_scores_by_default(self):
        row = {"id": "x", "content": "**Term** — an example definition.\nMore text.\n"}
        attach_scores(row)
        assert "_answerability" in row
        assert "_quotability" in row
        assert "_answerability_components" not in row
        assert "_quotability_components" not in row

    def test_include_components_attaches_dicts(self):
        row = {"id": "x", "content": "Install the package.\n"}
        attach_scores(row, include_components=True)
        assert "_answerability_components" in row
        assert "_quotability_components" in row
        assert set(row["_answerability_components"].keys()) == set(ANSWERABILITY_WEIGHTS.keys())
        assert set(row["_quotability_components"].keys()) == set(QUOTABILITY_WEIGHTS.keys())

    def test_text_loader_used_when_content_missing(self):
        row = {"id": "x"}  # no content
        called = {}

        def _loader(r):
            called["yes"] = True
            # Use a multi-char term — the bold-term regex requires the
            # term name to be 2-81 chars (single-letter terms ignored).
            return "**Token** — opaque API credential.\n"

        attach_scores(row, text_loader=_loader)
        assert called.get("yes") is True
        assert row["_answerability"] > 0.0


# ---------------------------------------------------------------------------
# End-to-end through search_sections
# ---------------------------------------------------------------------------

class TestSearchSectionsEndToEnd:
    def test_results_carry_scores(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections

        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "g.md").write_text(textwrap.dedent("""
            # Title

            ## Auth

            **Token** — short-lived credential. Tokens are opaque and
            must be sent in the Authorization header. They expire after
            one hour by default.

            ## How to install

            1. Install the package.
            2. Configure the env var.
            3. Run the CLI.
            4. Verify the output.
        """).lstrip(), encoding="utf-8")
        index_local(
            path=str(repo), name="ans",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )

        out = search_sections(repo="ans", query="token", semantic=False, storage_path=str(tmp_path))
        assert out["results"]
        for r in out["results"]:
            assert "_answerability" in r
            assert "_quotability" in r
            assert 0.0 <= r["_answerability"] <= 1.0
            assert 0.0 <= r["_quotability"] <= 1.0

    def test_install_section_has_imperative_signal(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections

        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "g.md").write_text(textwrap.dedent("""
            # Top

            ## How to set up the package

            Install the package via pip.
            Configure the environment variable.
            Run the CLI to verify.
            Open the dashboard.
            Connect to the cluster.
        """).lstrip(), encoding="utf-8")
        index_local(
            path=str(repo), name="imp",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        out = search_sections(repo="imp", query="set up package", semantic=False, storage_path=str(tmp_path))
        # The how-to section's answerability should reflect the imperative density.
        assert out["results"]
        top = out["results"][0]
        assert top["_answerability"] > 0.05
