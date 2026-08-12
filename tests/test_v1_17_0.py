"""Tests for v1.17.0: code-block-aware indexing + jcodemunch bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.parser.markdown_parser import parse_markdown
from jdocmunch_mcp.parser.sections import Section
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.storage.doc_store import INDEX_VERSION


# ---------------------------------------------------------------------------
# Index version bumped to 3 (full re-index migration)
# ---------------------------------------------------------------------------

class TestIndexVersion:
    def test_index_version_is_3(self):
        assert INDEX_VERSION == 3


# ---------------------------------------------------------------------------
# Parser code-block extraction
# ---------------------------------------------------------------------------

class TestCodeBlockExtraction:
    def test_basic_fenced_block(self):
        content = "# Top\n\n## Install\n\n```bash\npip install x\n```\n"
        secs = parse_markdown(content, "g.md", "local/r")
        install = next(s for s in secs if s.title == "Install")
        assert len(install.code_blocks) == 1
        blk = install.code_blocks[0]
        assert blk["lang"] == "bash"
        assert "pip install x" in blk["content"]
        assert blk["block_id"].endswith("::code#0")
        assert blk["byte_end"] > blk["byte_start"]

    def test_lang_extraction_default_blank(self):
        content = "# T\n\n## S\n\n```\nplain\n```\n"
        secs = parse_markdown(content, "g.md", "local/r")
        s = next(s for s in secs if s.title == "S")
        assert s.code_blocks[0]["lang"] == ""

    def test_tilde_fence_supported(self):
        content = "# T\n\n## S\n\n~~~python\nx = 1\n~~~\n"
        secs = parse_markdown(content, "g.md", "local/r")
        s = next(s for s in secs if s.title == "S")
        assert s.code_blocks[0]["lang"] == "python"
        assert "x = 1" in s.code_blocks[0]["content"]

    def test_multiple_blocks_in_one_section(self):
        content = (
            "# T\n\n## S\n\n"
            "```bash\ncmd1\n```\n\n"
            "Some text.\n\n"
            "```python\ny = 2\n```\n"
        )
        secs = parse_markdown(content, "g.md", "local/r")
        s = next(s for s in secs if s.title == "S")
        assert len(s.code_blocks) == 2
        ids = [b["block_id"] for b in s.code_blocks]
        assert ids[0].endswith("::code#0")
        assert ids[1].endswith("::code#1")
        assert s.code_blocks[0]["lang"] == "bash"
        assert s.code_blocks[1]["lang"] == "python"

    def test_blocks_isolated_per_section(self):
        content = (
            "# Top\n\n## A\n\n```\nfoo\n```\n\n## B\n\n```\nbar\n```\n"
        )
        secs = parse_markdown(content, "g.md", "local/r")
        a = next(s for s in secs if s.title == "A")
        b = next(s for s in secs if s.title == "B")
        assert len(a.code_blocks) == 1
        assert len(b.code_blocks) == 1
        # Block IDs must include the parent section_id, so they're distinct.
        assert a.code_blocks[0]["block_id"] != b.code_blocks[0]["block_id"]

    def test_byte_range_matches_body_only(self):
        # Extract the cached file bytes via the byte_start/byte_end and
        # verify they exactly match the block content.
        content = "# T\n\n## S\n\n```python\nprint('hi')\nprint('bye')\n```\n"
        secs = parse_markdown(content, "g.md", "local/r")
        s = next(sec for sec in secs if sec.title == "S")
        blk = s.code_blocks[0]
        # The body byte range should NOT include the fence delimiters.
        body_bytes = content.encode("utf-8")[blk["byte_start"]:blk["byte_end"]]
        assert body_bytes.decode("utf-8") == blk["content"]
        assert "```" not in blk["content"]

    def test_block_id_encodes_parent_section_id(self):
        # block_ids are stamped at _finalize_section once the section_id is
        # known, even though the block parses mid-section.
        content = "# Top\n\n```\nfoo bar\n```\n\n## S1\n\nbody\n"
        secs = parse_markdown(content, "g.md", "local/r")
        # The fenced block lives under "Top" (level-1).
        top = next(s for s in secs if s.title == "Top")
        assert len(top.code_blocks) == 1
        block_id = top.code_blocks[0]["block_id"]
        assert block_id.startswith(top.id + "::code#")


# ---------------------------------------------------------------------------
# Section.to_dict / from_dict round-trip
# ---------------------------------------------------------------------------

class TestSectionRoundtrip:
    def test_code_blocks_persist_through_to_from_dict(self):
        s = Section(
            id="r::d::s#1",
            repo="r",
            doc_path="d.md",
            title="T",
            content="body",
            level=1,
            parent_id="",
            children=[],
            code_blocks=[
                {"block_id": "r::d::s#1::code#0", "lang": "py", "content": "x=1", "byte_start": 0, "byte_end": 3}
            ],
        )
        d = s.to_dict()
        assert "code_blocks" in d
        s2 = Section.from_dict(d)
        assert s2.code_blocks == s.code_blocks

    def test_omitted_when_empty(self):
        s = Section(
            id="r::d::s#1", repo="r", doc_path="d.md", title="T", content="",
            level=1, parent_id="", children=[],
        )
        d = s.to_dict()
        assert "code_blocks" not in d


# ---------------------------------------------------------------------------
# DocStore round-trip preserves code_blocks
# ---------------------------------------------------------------------------

class TestDocStoreRoundtrip:
    def test_code_blocks_survive_save_and_load(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        content = (
            "# Top\n\n"
            "## Install\n\n"
            "```bash\npip install jdocmunch-mcp\n```\n\n"
            "## Run\n\n"
            "```python\nfrom jdocmunch_mcp import server\n```\n"
        )
        sections = parse_file(content, "guide.md", "local/r")
        store.save_index(
            owner="local", name="r", sections=sections,
            raw_files={"guide.md": content},
            doc_types={".md": 1},
        )
        index = store.load_index("local", "r")
        loaded_blocks = []
        for sec in index.sections:
            for blk in sec.get("code_blocks", []) or []:
                loaded_blocks.append(blk)
        assert len(loaded_blocks) == 2
        langs = {b["lang"] for b in loaded_blocks}
        assert langs == {"bash", "python"}


# ---------------------------------------------------------------------------
# find_code_examples MCP tool
# ---------------------------------------------------------------------------

class TestFindCodeExamples:
    def _setup(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        content = (
            "# Doc\n\n"
            "## Install\n\n"
            "```bash\npip install jdocmunch-mcp\necho ok\n```\n\n"
            "## Run\n\n"
            "```python\nfrom jdocmunch_mcp import server\nserver.main()\n```\n\n"
            "## Test\n\n"
            "```bash\npytest tests/\n```\n"
        )
        sections = parse_file(content, "guide.md", "local/r")
        store.save_index(
            owner="local", name="r", sections=sections,
            raw_files={"guide.md": content},
            doc_types={".md": 1},
        )

    def test_finds_block_by_content_token(self, tmp_path):
        from jdocmunch_mcp.tools.find_code_examples import find_code_examples
        self._setup(tmp_path)
        out = find_code_examples(repo="local/r", query="pytest", storage_path=str(tmp_path))
        # The Test section's slug is lowercase ('test'), so check the slug
        # portion of the block id.
        ids = [r["block_id"] for r in out["results"]]
        assert any("/test#" in i for i in ids), out

    def test_lang_filter(self, tmp_path):
        from jdocmunch_mcp.tools.find_code_examples import find_code_examples
        self._setup(tmp_path)
        out = find_code_examples(
            repo="local/r", query="install", lang="python", storage_path=str(tmp_path)
        )
        # No python block has 'install' — filter should yield empty results.
        for r in out["results"]:
            assert r["lang"] == "python"
        assert out["_meta"]["lang_filter"] == "python"

    def test_lang_filter_case_insensitive(self, tmp_path):
        from jdocmunch_mcp.tools.find_code_examples import find_code_examples
        self._setup(tmp_path)
        out = find_code_examples(
            repo="local/r", query="server", lang="PYTHON", storage_path=str(tmp_path)
        )
        for r in out["results"]:
            assert r["lang"].lower() == "python"

    def test_empty_query_returns_zero(self, tmp_path):
        from jdocmunch_mcp.tools.find_code_examples import find_code_examples
        self._setup(tmp_path)
        out = find_code_examples(repo="local/r", query="    ", storage_path=str(tmp_path))
        assert out["results"] == []

    def test_unknown_repo_returns_error(self, tmp_path):
        from jdocmunch_mcp.tools.find_code_examples import find_code_examples
        out = find_code_examples(repo="nope/missing", query="x", storage_path=str(tmp_path))
        assert "error" in out

    def test_no_blocks_after_filter_returns_zero_with_reason(self, tmp_path):
        from jdocmunch_mcp.tools.find_code_examples import find_code_examples
        self._setup(tmp_path)
        out = find_code_examples(
            repo="local/r", query="anything", lang="rust", storage_path=str(tmp_path)
        )
        assert out["results"] == []
        assert out["_meta"]["reason"] == "no_code_blocks_for_filter"


# ---------------------------------------------------------------------------
# link_code_to_symbols (jcodemunch bridge)
# ---------------------------------------------------------------------------

class TestLinkCodeToSymbols:
    def _setup(self, tmp_path):
        store = DocStore(base_path=str(tmp_path))
        content = (
            "# Doc\n\n"
            "## Run\n\n"
            "```python\n"
            "from jdocmunch_mcp import server\n"
            "server.main()\n"
            "```\n"
        )
        sections = parse_file(content, "g.md", "local/r")
        store.save_index(
            owner="local", name="r", sections=sections,
            raw_files={"g.md": content},
            doc_types={".md": 1},
        )

    def test_bridge_unavailable_returns_empty_with_meta(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.link_code_to_symbols import link_code_to_symbols
        self._setup(tmp_path)

        # Force the import to fail.
        import jdocmunch_mcp.tools.link_code_to_symbols as lcs
        monkeypatch.setattr(lcs, "import_search_symbols", lambda: None)

        out = link_code_to_symbols(repo="local/r", code_repo="x/y", storage_path=str(tmp_path))
        assert out["_meta"]["bridge_available"] is False
        assert out["by_block"] == {}
        assert out["by_symbol"] == {}
        assert "hint" in out["_meta"]

    def test_bridge_available_aggregates_results(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.link_code_to_symbols import link_code_to_symbols
        self._setup(tmp_path)

        # Stub a search_symbols that returns a deterministic id per identifier.
        def _stub(repo, query, max_results=3):
            return {"results": [{"id": f"sym::{query}#1"}]}

        import jdocmunch_mcp.tools.link_code_to_symbols as lcs
        monkeypatch.setattr(lcs, "import_search_symbols", lambda: _stub)

        out = link_code_to_symbols(repo="local/r", code_repo="code/y", storage_path=str(tmp_path))
        assert out["_meta"]["bridge_available"] is True
        assert out["by_block"]
        # Each block resolves to at least one symbol.
        for block_id, sids in out["by_block"].items():
            assert sids
        # Reverse mapping consistent.
        for sid, blocks in out["by_symbol"].items():
            assert blocks

    def test_max_examples_caps_input(self, tmp_path, monkeypatch):
        from jdocmunch_mcp.tools.link_code_to_symbols import link_code_to_symbols

        store = DocStore(base_path=str(tmp_path))
        # 5 blocks in 5 sections.
        body = "# Top\n\n"
        for i in range(5):
            body += f"## S{i}\n\n```py\nident_{i}()\n```\n\n"
        sections = parse_file(body, "g.md", "local/r")
        store.save_index(
            owner="local", name="r", sections=sections,
            raw_files={"g.md": body},
            doc_types={".md": 1},
        )

        calls = {"n": 0}

        def _stub(repo, query, max_results=3):
            calls["n"] += 1
            return {"results": [{"id": f"sym::{query}"}]}

        import jdocmunch_mcp.tools.link_code_to_symbols as lcs
        monkeypatch.setattr(lcs, "import_search_symbols", lambda: _stub)

        out = link_code_to_symbols(
            repo="local/r", code_repo="x/y", max_examples=2, storage_path=str(tmp_path)
        )
        assert out["_meta"]["blocks_examined"] <= 2

    def test_unknown_repo_returns_error(self, tmp_path):
        from jdocmunch_mcp.tools.link_code_to_symbols import link_code_to_symbols
        out = link_code_to_symbols(
            repo="nope/missing", code_repo="x/y", storage_path=str(tmp_path)
        )
        assert "error" in out


# ---------------------------------------------------------------------------
# Server registration + tool count
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_new_tools_registered(self):
        import asyncio
        from jdocmunch_mcp import server as srv

        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        assert "find_code_examples" in names
        assert "link_code_to_symbols" in names
