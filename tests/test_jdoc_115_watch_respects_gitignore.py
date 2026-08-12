"""jdoc#115: watch must not re-admit a file that full discovery excluded.

Reported by @MotoMato85 with a complete two-session repro. After a full index
excluded `graphify-out/NOISE.md` via the source root's `.gitignore`, editing that
file made the watcher add it, and its sections became retrieval candidates.

⚠ The fix belongs in `watch.py`, NOT in `index_local`'s `paths=` branch, and the
reporter said so before we did. A caller naming a file explicitly and bypassing
`.gitignore` is INTENTIONAL and documented (SPEC.md, the 1.61.0 changelog): a
human asking for a specific generated file should get it. The watcher is not
that caller. It manufactures the path list from filesystem events, so the bypass
fires for files nobody asked for. jcodemunch splits the same way for
CACHEDIR.TAG: explicit paths opt past the rules, the watcher fast path applies
them.

Acceptance criteria are the reporter's own: editing an ignored doc must not add
it, and editing a non-ignored doc must still update its indexed content.
"""
import asyncio
from pathlib import Path


def _fixture(tmp_path):
    fx = tmp_path / "repro"
    (fx / "graphify-out").mkdir(parents=True)
    (fx / ".gitignore").write_text("graphify-out/\n", encoding="utf-8")
    (fx / "README.md").write_text(
        "# Visible document\n\nThis document must be indexed.\n", encoding="utf-8")
    (fx / "graphify-out" / "NOISE.md").write_text(
        "# Ignored document\n\nInitial ignored content.\n", encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    return fx, store


def _index(fx, store, **kw):
    from jdocmunch_mcp.tools.index_local import index_local
    return index_local(path=str(fx), name="repro", use_embeddings=False,
                       use_ai_summaries=False, storage_path=str(store), **kw)


def _snapshot(store):
    from jdocmunch_mcp.storage.doc_store import DocStore
    i = DocStore(base_path=str(store)).load_index("local", "repro")
    return (sorted(i.doc_paths),
            sorted(s["content_hash"] for s in i.sections),
            dict(i.file_hashes))


def _fire(paths, fx, store):
    from jdocmunch_mcp import watch as W
    roots = {str(Path(fx).resolve()): "repro"}
    asyncio.run(W._handle_changes(
        [(1, str(Path(p).resolve())) for p in paths],
        roots, str(store), False, True, None))


class TestWatcherHonoursGitignore:
    def test_editing_an_ignored_file_does_not_add_it(self, tmp_path):
        """The defect, exactly as reported."""
        fx, store = _fixture(tmp_path)
        _index(fx, store)
        before = _snapshot(store)
        assert before[0] == ["README.md"]

        noise = fx / "graphify-out" / "NOISE.md"
        noise.write_text("# Ignored document\n\nWatcher edit.\n", encoding="utf-8")
        _fire([noise], fx, store)

        assert _snapshot(store) == before, "watcher altered an excluded-file corpus"

    def test_editing_a_visible_file_still_updates_it(self, tmp_path):
        """The other half of the acceptance criteria. A filter that also breaks
        the watcher's actual job is not a fix."""
        fx, store = _fixture(tmp_path)
        _index(fx, store)
        docs0, hashes0, files0 = _snapshot(store)

        (fx / "README.md").write_text(
            "# Visible document\n\nEdited body.\n", encoding="utf-8")
        _fire([fx / "README.md"], fx, store)

        docs1, hashes1, files1 = _snapshot(store)
        assert docs1 == docs0 == ["README.md"]
        assert hashes1 != hashes0, "section content did not change"
        assert files1 != files0, "file hash did not change"

    def test_mixed_batch_keeps_only_the_visible_file(self, tmp_path):
        """A batch containing both must not be dropped wholesale, nor accepted
        wholesale. The filter is per path."""
        fx, store = _fixture(tmp_path)
        _index(fx, store)
        _, hashes0, _ = _snapshot(store)

        (fx / "README.md").write_text(
            "# Visible document\n\nBatch edit.\n", encoding="utf-8")
        (fx / "graphify-out" / "NOISE.md").write_text(
            "# Ignored document\n\nBatch edit.\n", encoding="utf-8")
        _fire([fx / "README.md", fx / "graphify-out" / "NOISE.md"], fx, store)

        docs, hashes, _ = _snapshot(store)
        assert docs == ["README.md"]
        assert hashes != hashes0

    def test_file_rule_not_just_directory_rule(self, tmp_path):
        """The reporter asked for both: 'a directory or file rule'."""
        fx, store = _fixture(tmp_path)
        (fx / ".gitignore").write_text("graphify-out/\nSCRATCH.md\n", encoding="utf-8")
        (fx / "SCRATCH.md").write_text("# Scratch\n\nBody.\n", encoding="utf-8")
        _index(fx, store)
        before = _snapshot(store)
        assert "SCRATCH.md" not in before[0]

        (fx / "SCRATCH.md").write_text("# Scratch\n\nEdited.\n", encoding="utf-8")
        _fire([fx / "SCRATCH.md"], fx, store)
        assert _snapshot(store) == before


class TestWatcherHonoursStoredShapePatterns:
    """jdoc#116's persisted patterns are corpus rules too.

    A watcher that reinstates a pattern-excluded file is jdoc#115 wearing a
    different hat, and the two fixes would otherwise leave a seam between them.
    """

    def test_pattern_excluded_file_is_not_readmitted(self, tmp_path):
        fx, store = _fixture(tmp_path)
        (fx / "TEMPLATE.md").write_text("# Template\n\nBody.\n", encoding="utf-8")
        _index(fx, store, extra_ignore_patterns=["TEMPLATE.md"])
        before = _snapshot(store)
        assert "TEMPLATE.md" not in before[0]

        (fx / "TEMPLATE.md").write_text("# Template\n\nEdited.\n", encoding="utf-8")
        _fire([fx / "TEMPLATE.md"], fx, store)
        assert _snapshot(store) == before


class TestExplicitPathsStillBypass:
    """The contract this fix must NOT change.

    SPEC.md and the 1.61.0 changelog say caller-supplied `paths=` deliberately
    bypasses full discovery. A human naming a generated file on purpose still
    gets it. If this test ever fails, the fix leaked out of watch.py.
    """

    def test_caller_supplied_path_still_indexes_an_ignored_file(self, tmp_path):
        fx, store = _fixture(tmp_path)
        _index(fx, store)
        assert _snapshot(store)[0] == ["README.md"]

        _index(fx, store, paths=[str(fx / "graphify-out" / "NOISE.md")])
        assert "graphify-out/NOISE.md" in _snapshot(store)[0]
