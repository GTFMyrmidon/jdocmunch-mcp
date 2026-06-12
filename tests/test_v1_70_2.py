"""v1.70.2 — issue batch: jdoc#32, jdoc#33, jdoc#34.

#32: `search_sections` `path_glob` was a tool-layer post-filter applied AFTER
the index-layer top-k cut, so a glob naming one document returned 0 results
whenever that document didn't rank in the corpus-wide top k. The glob is now a
candidate pre-filter inside every `DocStore.search` mode.

#33: `verify_index` silently skipped sections with an empty byte range
(`byte_end <= byte_start`) via a bare `continue`, so the counters didn't sum
to `section_count`. They now land in `skipped_count` / `skipped_sections`
with reason `"empty_byte_range"`.

#34: `index_local` ran synchronously inside the async `call_tool` handler,
blocking the single event loop (and every other tool) for the whole indexing
run. It now executes via `asyncio.to_thread`.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.search_sections import search_sections
from jdocmunch_mcp.tools.verify_index import verify_index


# ---------------------------------------------------------------------------
# jdoc#32 — path_glob pre-filter
# ---------------------------------------------------------------------------

class TestPathGlobPreFilter:
    def _index_skewed_corpus(self, tmp_path: Path) -> Path:
        """Many documents that dominate the global ranking for the query,
        plus one weak-match target document the glob will ask for."""
        docs = tmp_path / "docs"
        docs.mkdir()
        loud = "auth token flow auth token flow auth token flow.\n" * 5
        for i in range(15):
            (docs / f"loud_{i:02d}.md").write_text(
                f"# Auth token flow {i}\n\n{loud}", encoding="utf-8"
            )
        sub = docs / "guides"
        sub.mkdir()
        (sub / "target.md").write_text(
            "# Miscellany\n\nOne passing mention of the auth token here.\n",
            encoding="utf-8",
        )
        result = index_local(
            path=str(docs), name="globrepo",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        assert result.get("success") is True, result
        return docs

    def test_single_doc_glob_survives_global_topk(self, tmp_path: Path):
        self._index_skewed_corpus(tmp_path)

        # Sanity: without the glob, target.md must not make the top 10 —
        # otherwise this test isn't exercising the starvation path.
        unfiltered = search_sections(
            repo="globrepo", query="auth token flow",
            storage_path=str(tmp_path),
        )
        assert all(
            r.get("doc_path") != "guides/target.md"
            for r in unfiltered.get("results", [])
        ), "fixture too weak: target ranked globally, rebalance the corpus"

        globbed = search_sections(
            repo="globrepo", query="auth token flow",
            path_glob="guides/target.md",
            storage_path=str(tmp_path),
        )
        assert globbed.get("result_count", 0) >= 1, globbed
        assert all(
            r.get("doc_path") == "guides/target.md"
            for r in globbed["results"]
        )

    def test_wildcard_glob_restricts_and_ranks_within(self, tmp_path: Path):
        self._index_skewed_corpus(tmp_path)
        globbed = search_sections(
            repo="globrepo", query="auth token flow",
            path_glob="guides/*",
            storage_path=str(tmp_path),
        )
        assert globbed.get("result_count", 0) >= 1
        assert all(
            r.get("doc_path", "").startswith("guides/")
            for r in globbed["results"]
        )

    def test_no_glob_behavior_unchanged(self, tmp_path: Path):
        self._index_skewed_corpus(tmp_path)
        out = search_sections(
            repo="globrepo", query="auth token flow",
            storage_path=str(tmp_path),
        )
        assert out.get("result_count", 0) >= 1

    def test_semantic_only_mode_honors_glob(self, tmp_path: Path, monkeypatch):
        self._index_skewed_corpus(tmp_path)
        store = DocStore(base_path=str(tmp_path))

        # Give every section a stored embedding so _has_embeddings() is True,
        # then stub the query embedder; the pre-filter is what's under test.
        index = store.load_index("local", "globrepo")
        for sec in index.sections:
            sec["embedding"] = [1.0, 0.0]

        from jdocmunch_mcp.storage import doc_store as ds
        monkeypatch.setattr(ds, "embed_query", lambda q: [1.0, 0.0])

        results = index.search(
            "auth token flow", semantic_only=True,
            max_results=5, path_glob="guides/target.md",
        )
        assert len(results) >= 1
        assert all(r.get("doc_path") == "guides/target.md" for r in results)


# ---------------------------------------------------------------------------
# jdoc#33 — verify_index skipped accounting
# ---------------------------------------------------------------------------

class TestVerifyIndexSkippedAccounting:
    def _index_and_zero_ranges(self, tmp_path: Path, zero_n: int = 2) -> tuple:
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "g.md").write_text(
            "# Top\n\n## Auth\n\nbody alpha\n\n## Logs\n\nbody beta\n\n"
            "## Maps\n\nbody gamma\n",
            encoding="utf-8",
        )
        result = index_local(
            path=str(docs), name="viskip",
            use_ai_summaries=False, use_embeddings=False,
            storage_path=str(tmp_path), incremental=False,
        )
        assert result.get("success") is True, result

        # Simulate sections persisted without a byte range (the structured
        # OpenAPI parser emits byte_start=0, byte_end=0 for every section).
        # NB even a plain markdown index carries some naturally-zero ranges
        # (e.g. the synthetic document root), so expectations are computed
        # from the index, not assumed.
        index_file = tmp_path / "local" / "viskip.json"
        data = json.loads(index_file.read_text(encoding="utf-8"))
        ranged = [
            s for s in data["sections"]
            if int(s.get("byte_end", 0) or 0) > int(s.get("byte_start", 0) or 0)
        ]
        assert len(ranged) >= zero_n, "fixture too small for requested zero_n"
        for sec in ranged[:zero_n]:
            sec["byte_start"] = 0
            sec["byte_end"] = 0
        index_file.write_text(json.dumps(data), encoding="utf-8")
        naturally_zero = len(data["sections"]) - len(ranged)
        return len(data["sections"]), naturally_zero + zero_n

    def test_counters_sum_to_section_count(self, tmp_path: Path):
        total, expect_skipped = self._index_and_zero_ranges(tmp_path, zero_n=2)
        out = verify_index(repo="viskip", storage_path=str(tmp_path))
        assert "error" not in out
        assert out["skipped_count"] == expect_skipped
        assert (
            out["clean_count"] + out["drift_count"] + out["missing_count"]
            + out["error_count"] + out["skipped_count"]
        ) == out["section_count"] == total

    def test_skipped_sections_carry_reason(self, tmp_path: Path):
        _, expect_skipped = self._index_and_zero_ranges(tmp_path, zero_n=1)
        out = verify_index(repo="viskip", storage_path=str(tmp_path))
        assert len(out["skipped_sections"]) == expect_skipped >= 1
        for entry in out["skipped_sections"]:
            assert entry["reason"] == "empty_byte_range"
            assert entry["doc_path"] == "g.md"
            assert entry["section_id"]

    def test_untouched_index_still_sums(self, tmp_path: Path):
        total, expect_skipped = self._index_and_zero_ranges(tmp_path, zero_n=0)
        out = verify_index(repo="viskip", storage_path=str(tmp_path))
        assert out["skipped_count"] == expect_skipped
        assert (
            out["clean_count"] + out["drift_count"] + out["missing_count"]
            + out["error_count"] + out["skipped_count"]
        ) == out["section_count"] == total


# ---------------------------------------------------------------------------
# jdoc#34 — index_local must not block the event loop
# ---------------------------------------------------------------------------

class TestIndexLocalOffEventLoop:
    def test_event_loop_stays_responsive_during_index_local(self, monkeypatch):
        import jdocmunch_mcp.server as srv

        started = threading.Event()
        release = threading.Event()

        def blocking_index_local(**kwargs):
            started.set()
            release.wait(timeout=30)
            return {"success": True, "message": "fake"}

        monkeypatch.setattr(srv, "index_local", blocking_index_local)

        async def main() -> float:
            task = asyncio.create_task(srv.call_tool("index_local", {"path": "x"}))
            # Wait (off-loop) until the fake indexer is definitely running.
            assert await asyncio.to_thread(started.wait, 10)
            t0 = time.perf_counter()
            # The probe must run while the indexer still blocks its thread.
            await asyncio.sleep(0)
            probe_latency = time.perf_counter() - t0
            assert not task.done(), "indexer finished early; probe proved nothing"
            release.set()
            out = await asyncio.wait_for(task, timeout=10)
            text = out[0].text if hasattr(out[0], "text") else str(out[0])
            assert "success" in text
            return probe_latency

        probe_latency = asyncio.run(main())
        # Pre-fix, the sync call monopolized the loop until the indexer
        # returned (~30s here); post-fix the probe is effectively instant.
        assert probe_latency < 5.0
