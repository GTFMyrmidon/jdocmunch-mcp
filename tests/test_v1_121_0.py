"""Tests for v1.121.0: search_sections compact / fields / snippet_bytes (jdoc#101).

Reported by @vondecron: ~44% of every result row is bytes the calling agent
cannot act on, and no content snippet, so a perfect top hit still costs a
get_section round-trip.
"""

from __future__ import annotations

import json
import textwrap

from jdocmunch_mcp.retrieval.projection import COMPACT_DROP, _truncate_utf8
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections


def _index(tmp_path, name="proj"):
    repo = tmp_path / "docs"
    repo.mkdir()
    (repo / "guide.md").write_text(textwrap.dedent("""
        # Guide

        Intro.

        ## Configuration loader

        The configuration loader reads paths in a fixed order. It is long
        enough that a byte-bounded snippet has to cut it somewhere, which is
        the case the snippet_bytes flag exists for. Lorem ipsum dolor sit
        amet, consectetur adipiscing elit, sed do eiusmod tempor.

        ### Nested loader detail

        More configuration loader prose. #config #loader
    """).lstrip("\n"), encoding="utf-8")
    index_local(
        path=str(repo), name=name,
        use_ai_summaries=False, use_embeddings=False,
        storage_path=str(tmp_path), incremental=False,
    )


def _search(tmp_path, **kw):
    return search_sections(repo="proj", query="configuration loader",
                           semantic=False, storage_path=str(tmp_path), **kw)


class TestDefaultUnchanged:
    """1.x wire contract: the default response must not move."""

    def test_default_still_carries_every_field(self, tmp_path):
        _index(tmp_path)
        out = _search(tmp_path)
        row = out["results"][0]
        for key in ("repo", "parent_id", "content_hash", "byte_start", "byte_end"):
            assert key in row, f"{key} vanished from the default row"
        assert "compact" not in out["_meta"]
        assert "fields" not in out["_meta"]
        assert "snippet" not in row

    def test_default_rows_are_identical_with_flags_off(self, tmp_path):
        _index(tmp_path)
        a = _search(tmp_path)["results"]
        b = _search(tmp_path, compact=False, fields=None, snippet_bytes=0)["results"]
        assert json.dumps(a, default=str) == json.dumps(b, default=str)


class TestCompact:
    def test_drops_dead_weight_keeps_actionable(self, tmp_path):
        _index(tmp_path)
        out = _search(tmp_path, compact=True)
        assert out["_meta"]["compact"] is True
        for row in out["results"]:
            for dropped in COMPACT_DROP:
                assert dropped not in row
            assert row["id"]
            assert "title" in row and "doc_path" in row and "_score" in row

    def test_is_substantially_smaller(self, tmp_path):
        _index(tmp_path)
        base = len(json.dumps(_search(tmp_path)["results"], default=str))
        lean = len(json.dumps(_search(tmp_path, compact=True)["results"], default=str))
        assert lean < base * 0.7, f"compact saved too little: {lean} vs {base}"

    def test_summary_dropped_only_when_it_repeats_the_title(self, tmp_path):
        _index(tmp_path)
        rows = _search(tmp_path)["results"]
        lean = _search(tmp_path, compact=True)["results"]
        by_id = {r["id"]: r for r in lean}
        for row in rows:
            summary = (row.get("summary") or "").strip()
            title = str(row.get("title", "")).strip()
            if summary and summary != title:
                assert by_id[row["id"]].get("summary") == row["summary"]
            else:
                assert "summary" not in by_id[row["id"]]

    def test_empty_tags_dropped_populated_tags_kept(self, tmp_path):
        _index(tmp_path)
        rows = _search(tmp_path)["results"]
        lean = {r["id"]: r for r in _search(tmp_path, compact=True)["results"]}
        assert any(not r.get("tags") for r in rows), "fixture has no untagged section"
        for row in rows:
            if row.get("tags"):
                assert lean[row["id"]]["tags"] == row["tags"]
            else:
                assert "tags" not in lean[row["id"]]

    def test_freshness_kept_when_not_fresh(self, tmp_path):
        """The per-row signal is dropped as noise only on the happy path."""
        from jdocmunch_mcp.retrieval.projection import project_row
        fresh = project_row({"id": "a", "_freshness": "fresh"}, compact=True)
        stale = project_row({"id": "a", "_freshness": "stale"}, compact=True)
        assert "_freshness" not in fresh
        assert stale["_freshness"] == "stale"

    def test_filters_still_work_under_compact(self, tmp_path):
        """Projection runs AFTER filtering, so filters may read dropped fields."""
        _index(tmp_path)
        out = _search(tmp_path, compact=True, min_byte_length=50)
        assert out["results"], "byte-length filter starved under compact"
        assert all("byte_start" not in r for r in out["results"])
        assert out["_meta"]["min_byte_length"] == 50


class TestFields:
    def test_whitelist_projects_exactly(self, tmp_path):
        _index(tmp_path)
        out = _search(tmp_path, fields=["title", "doc_path"])
        for row in out["results"]:
            assert set(row) == {"id", "title", "doc_path"}
        assert out["_meta"]["fields"] == ["title", "doc_path"]

    def test_id_survives_even_when_not_requested(self, tmp_path):
        _index(tmp_path)
        for row in _search(tmp_path, fields=["_score"])["results"]:
            assert row["id"]

    def test_fields_wins_over_compact(self, tmp_path):
        _index(tmp_path)
        out = _search(tmp_path, compact=True, fields=["content_hash"])
        for row in out["results"]:
            assert set(row) == {"id", "content_hash"}


class TestSnippets:
    def test_inlines_body_text(self, tmp_path):
        _index(tmp_path)
        out = _search(tmp_path, snippet_bytes=120)
        assert out["_meta"]["snippet_bytes"] == 120
        top = out["results"][0]
        assert top["snippet"]
        assert len(top["snippet"].encode("utf-8")) <= 120
        assert top["snippet_truncated"] is True

    def test_short_section_is_not_marked_truncated(self, tmp_path):
        _index(tmp_path)
        out = _search(tmp_path, snippet_bytes=100_000)
        for row in out["results"]:
            assert "snippet_truncated" not in row

    def test_snippet_survives_compact_and_fields(self, tmp_path):
        _index(tmp_path)
        lean = _search(tmp_path, compact=True, snippet_bytes=80)["results"][0]
        assert lean["snippet"]
        picked = _search(tmp_path, fields=["title"], snippet_bytes=80)["results"][0]
        assert picked["snippet"]

    def test_off_by_default(self, tmp_path):
        _index(tmp_path)
        out = _search(tmp_path)
        assert "snippet_bytes" not in out["_meta"]
        assert all("snippet" not in r for r in out["results"])

    def test_never_splits_a_codepoint(self):
        """jdoc indexes CJK corpora; one char is 3 UTF-8 bytes."""
        text = "한국어 문서 색인"
        snippet, truncated = _truncate_utf8(text, 7)
        assert truncated is True
        assert len(snippet.encode("utf-8")) <= 7
        assert snippet == snippet.encode("utf-8").decode("utf-8")  # no mojibake
        whole, cut = _truncate_utf8(text, 1000)
        assert whole == text and cut is False


class TestSavingsMeasuredOnServedPayload:
    def test_response_bytes_reflect_projection(self, tmp_path):
        """tokens_saved must describe what the caller received, not the
        pre-projection rows ([[feedback_measure_the_tradeoff_you_accepted]])."""
        _index(tmp_path)
        base = _search(tmp_path)["_meta"]["tokens_saved"]
        lean = _search(tmp_path, compact=True)["_meta"]["tokens_saved"]
        assert lean >= base


class TestRepoGroup:
    def test_compact_keeps_repo_in_a_fan_out(self, tmp_path):
        """`repo` is dead weight on a single-repo row and the only thing
        telling two members apart in a group response."""
        from jdocmunch_mcp.tools.repo_group_tools import define_repo_group

        for member in ("one", "two"):
            sub = tmp_path / member
            sub.mkdir()
            (sub / "d.md").write_text(
                f"# {member}\n\nConfiguration loader notes for {member}.\n",
                encoding="utf-8")
            index_local(path=str(sub), name=member, use_ai_summaries=False,
                        use_embeddings=False, storage_path=str(tmp_path),
                        incremental=False)
        define_repo_group(name="grp", repos=["one", "two"],
                          storage_path=str(tmp_path))

        out = search_sections(query="configuration loader", repo_group="grp",
                              compact=True, semantic=False,
                              storage_path=str(tmp_path))
        assert out["results"], "fan-out returned nothing"
        for row in out["results"]:
            assert row["repo"], "compact stripped the only member discriminator"
            assert "content_hash" not in row

    def test_snippets_survive_the_fan_out(self, tmp_path):
        from jdocmunch_mcp.tools.repo_group_tools import define_repo_group

        sub = tmp_path / "one"
        sub.mkdir()
        (sub / "d.md").write_text(
            "# One\n\nConfiguration loader notes for one.\n", encoding="utf-8")
        index_local(path=str(sub), name="one", use_ai_summaries=False,
                    use_embeddings=False, storage_path=str(tmp_path),
                    incremental=False)
        define_repo_group(name="grp", repos=["one"], storage_path=str(tmp_path))

        out = search_sections(query="configuration loader", repo_group="grp",
                              snippet_bytes=60, semantic=False,
                              storage_path=str(tmp_path))
        assert any(r.get("snippet") for r in out["results"])


class TestSchema:
    def test_tool_schema_exposes_the_params(self):
        import asyncio
        from jdocmunch_mcp import server

        tools = asyncio.run(server.list_tools())
        schema = next(t for t in tools if t.name == "search_sections").inputSchema
        props = schema["properties"]
        assert props["compact"]["type"] == "boolean"
        assert props["fields"]["type"] == "array"
        assert props["snippet_bytes"]["type"] == "integer"
