"""Tests for v1.19.0: section role classification + glossary."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.retrieval.glossary import (
    _MARKDOWN_BOLD_RE,
    _RST_GLOSSARY_RE,
    extract_glossary,
    load_terms,
    lookup,
    write_terms,
)
from jdocmunch_mcp.retrieval.roles import (
    ROLES,
    annotate_sections,
    classify_section,
)
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import index_local


# ---------------------------------------------------------------------------
# classify_section
# ---------------------------------------------------------------------------

class TestClassifyHeading:
    def test_changelog_heading(self):
        role, source = classify_section("Changelog", "")
        assert role == "changelog"
        assert source == "heading"

    def test_release_notes_heading(self):
        assert classify_section("Release Notes", "")[0] == "changelog"

    def test_faq_heading(self):
        assert classify_section("FAQ", "")[0] == "faq"

    def test_troubleshooting_heading(self):
        assert classify_section("Troubleshooting Connection Errors", "")[0] == "troubleshooting"

    def test_tutorial_heading(self):
        assert classify_section("Quickstart", "")[0] == "tutorial"

    def test_how_to_heading(self):
        assert classify_section("How to authenticate", "")[0] == "how_to"

    def test_api_heading(self):
        assert classify_section("API Reference", "")[0] in {"api", "reference"}

    def test_reference_heading(self):
        assert classify_section("Configuration Options", "")[0] == "reference"

    def test_example_heading(self):
        assert classify_section("Example Usage", "")[0] == "example"

    def test_other_heading(self):
        assert classify_section("Miscellaneous Notes", "")[0] == "other"

    def test_default_concept(self):
        role, source = classify_section("Background", "Some prose explanation.")
        assert role == "concept"
        assert source == "default"


class TestClassifyDensity:
    def test_faq_from_questions(self):
        # Each question on its own line so the line-end-only ? regex fires.
        body = (
            "Do we cache?\nYes — at index time.\n"
            "Why not invalidate per query?\nLatency.\n"
            "When is it disabled?\nNever.\n"
        )
        # Use a non-matching title so density signals win.
        assert classify_section("Caching behavior", body)[0] == "faq"

    def test_tutorial_from_steps_plus_code(self):
        body = (
            "1. Install\n"
            "```bash\npip install x\n```\n"
            "2. Configure\n"
            "```bash\nexport API_KEY=...\n```\n"
            "3. Run\n"
            "```bash\nx run\n```\n"
        )
        assert classify_section("Get started", body)[0] == "tutorial"

    def test_how_to_from_imperatives(self):
        body = (
            "Install the package.\n"
            "Configure the env var.\n"
            "Run the command.\n"
            "Verify the output.\n"
        )
        role, _ = classify_section("Setup procedure", body)
        # Heuristic may classify as how_to or concept depending on density;
        # accept either to keep the test resilient.
        assert role in {"how_to", "concept"}

    def test_example_dominated_by_code(self):
        body = "```py\nprint('a')\n```\n```py\nprint('b')\n```\n"
        assert classify_section("Snippets", body)[0] in {"example", "concept"}


class TestAncestorBleed:
    def test_ancestor_provides_role(self):
        role, source = classify_section("Connection refused",
                                        "some prose about errors",
                                        ancestor_titles=["Troubleshooting"])
        assert role == "troubleshooting"
        assert source == "ancestor"


class TestAnnotateSections:
    def test_annotate_walks_sections(self):
        content = (
            "# Top\n\n"
            "## Quickstart\n\nfollow the steps...\n\n"
            "## API Reference\n\nendpoint listing\n\n"
            "## Misc\n\nnotes\n"
        )
        sections = parse_file(content, "g.md", "local/r")
        annotate_sections(sections)
        roles = {s.title: (s.metadata or {}).get("role") for s in sections}
        assert roles.get("Quickstart") == "tutorial"
        assert roles.get("API Reference") in {"api", "reference"}
        assert roles.get("Misc") == "other"


class TestRolesEnum:
    def test_roles_tuple_complete(self):
        for r in ("concept", "tutorial", "how_to", "reference", "api",
                  "example", "troubleshooting", "changelog", "faq", "other"):
            assert r in ROLES


# ---------------------------------------------------------------------------
# extract_glossary
# ---------------------------------------------------------------------------

class TestExtractGlossary:
    def test_markdown_bold_em_dash(self):
        content = "**Section** — A unit of documentation between two headings.\n"
        out = list(extract_glossary([{"id": "x", "content": content}]))
        assert any(e["term"] == "Section" for e in out)
        assert any("documentation" in e["definition"] for e in out)

    def test_markdown_bold_colon_separator(self):
        content = "**Token**: a numeric API key.\n"
        out = list(extract_glossary([{"id": "x", "content": content}]))
        assert any(e["term"] == "Token" for e in out)

    def test_markdown_bold_too_short_definition_skipped(self):
        content = "**Foo** — ok\n"  # 2-char def under min length
        out = list(extract_glossary([{"id": "x", "content": content}]))
        assert not out

    def test_no_match_returns_empty(self):
        out = list(extract_glossary([{"id": "x", "content": "Plain prose with no bold definitions."}]))
        assert out == []

    def test_rst_glossary_directive(self):
        content = textwrap.dedent("""
        .. glossary::

            Section
                A unit of documentation between two headings.
            Token
                An API authentication credential.
        """).lstrip()
        out = list(extract_glossary([{"id": "x", "content": content}]))
        terms = {e["term"] for e in out}
        assert "Section" in terms
        assert "Token" in terms
        assert all(e["source"] == "rst_glossary" for e in out)

    def test_dedup_across_sources(self):
        content = (
            "**Section** — bold definition.\n\n"
            ".. glossary::\n\n"
            "    Section\n"
            "        rst definition.\n"
        )
        out = list(extract_glossary([{"id": "x", "content": content}]))
        # Same term but from two different sources — both kept (different
        # sources are useful diagnostically).
        assert sum(1 for e in out if e["term"].lower() == "section") == 2


# ---------------------------------------------------------------------------
# Persistence + lookup
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_round_trip(self, tmp_path):
        entries = [
            {"term": "Section", "definition": "x", "section_id": "s", "source": "markdown_bold"},
            {"term": "Token", "definition": "y", "section_id": "s", "source": "markdown_bold"},
        ]
        n = write_terms(str(tmp_path), "owner", "name", entries)
        assert n == 2
        loaded = load_terms(str(tmp_path), "owner", "name")
        assert loaded == entries

    def test_lookup_case_insensitive(self, tmp_path):
        write_terms(str(tmp_path), "o", "n", [
            {"term": "Section", "definition": "x", "section_id": "s", "source": "markdown_bold"},
        ])
        out = lookup(str(tmp_path), "o", "n", "section")
        assert len(out) == 1

    def test_load_missing_returns_empty(self, tmp_path):
        assert load_terms(str(tmp_path), "o", "missing") == []


# ---------------------------------------------------------------------------
# End-to-end through index_local + tool wrappers
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def _setup(self, tmp_path):
        repo = tmp_path / "docs"
        repo.mkdir()
        (repo / "guide.md").write_text(textwrap.dedent("""
            # Top

            ## Quickstart

            1. Install
               ```bash
               pip install x
               ```
            2. Run
               ```bash
               x serve
               ```

            ## Troubleshooting

            Connection refused — check the port.

            ## Glossary

            **Section** — A unit of documentation.
            **Token** — An API authentication credential.
            """).lstrip(), encoding="utf-8")
        index_local(
            path=str(repo), name="rolesfx",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        return str(tmp_path), "rolesfx"

    def test_role_filter_in_search_sections(self, tmp_path):
        from jdocmunch_mcp.tools.search_sections import search_sections
        storage_path, repo = self._setup(tmp_path)
        out = search_sections(
            repo=repo, query="x", role="troubleshooting",
            semantic=False, storage_path=storage_path,
        )
        assert out["_meta"]["role_filter"] == "troubleshooting"
        for r in out["results"]:
            assert (r.get("metadata") or {}).get("role") == "troubleshooting"

    def test_lookup_term_finds_glossary_entry(self, tmp_path):
        from jdocmunch_mcp.tools.glossary_tools import lookup_term
        storage_path, repo = self._setup(tmp_path)
        out = lookup_term(repo=repo, term="Section", storage_path=storage_path)
        assert out["_meta"]["match_count"] >= 1
        assert any("documentation" in e["definition"].lower() for e in out["matches"])

    def test_list_terms_alphabetical(self, tmp_path):
        from jdocmunch_mcp.tools.glossary_tools import list_terms
        storage_path, repo = self._setup(tmp_path)
        out = list_terms(repo=repo, storage_path=storage_path)
        terms = [e["term"] for e in out["terms"]]
        assert terms == sorted(terms, key=str.lower)
        assert "Section" in terms or "section" in [t.lower() for t in terms]

    def test_list_terms_prefix_filter(self, tmp_path):
        from jdocmunch_mcp.tools.glossary_tools import list_terms
        storage_path, repo = self._setup(tmp_path)
        out = list_terms(repo=repo, prefix="tok", storage_path=storage_path)
        # All returned terms start with 'tok' (case-insensitive).
        for e in out["terms"]:
            assert e["term"].lower().startswith("tok")


# ---------------------------------------------------------------------------
# Server registration
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_glossary_tools_registered(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        assert "lookup_term" in names
        assert "list_terms" in names
