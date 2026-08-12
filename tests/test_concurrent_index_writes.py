"""Regression tests for concurrent same-repo index writes.

jdocmunch rewrites the whole ``<name>.json`` index on every save. Before the
cross-process lock + per-PID temp name + retried replace in
``DocStore.save_index`` / ``incremental_save``, two processes writing the same
repo shared the deterministic ``<name>.json.tmp`` with no lock, so the replace
could install corrupt/partial JSON (the repo then reads as both "corrupt" --
``load_index`` raises -- and "absent" -- ``list_repos`` drops it) or silently
lose an update (last-replace-wins).

These tests reproduce that race across real processes and assert it no longer
corrupts the index or drops updates, on every platform: the lock is backed by
``flock`` on POSIX and ``msvcrt`` on Windows.

Originally contributed by @Chrisr6records (PR #28); the cross-platform lock and
the Windows replace-retry were added to carry it across the finish line.
"""
from __future__ import annotations

import json
import multiprocessing as mp

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.storage.doc_store import DocStore

_OWNER = "local"
_NAME = "concurrent"


def _join_all(procs, timeout: float = 120.0) -> None:
    """Join workers, terminate any straggler, then assert all exited cleanly."""
    for p in procs:
        p.join(timeout=timeout)
    for p in procs:
        if p.is_alive():  # pragma: no cover - only on a hang
            p.terminate()
            p.join(timeout=10)
    for p in procs:
        assert p.exitcode == 0, f"writer process failed (exitcode={p.exitcode})"


def _hammer_save(base_path: str, barrier, iters: int) -> None:
    store = DocStore(base_path=base_path)
    md = "# Root\n\nIntro.\n\n## A\n\nContent A.\n\n## B\n\nContent B.\n"
    sections = parse_file(md, "README.md", f"{_OWNER}/{_NAME}")
    raw_files = {"README.md": md}
    doc_types = {".md": 1}
    barrier.wait()  # start all writers together to maximize contention
    for _ in range(iters):
        store.save_index(_OWNER, _NAME, sections, raw_files, doc_types)


def _add_one_doc(base_path: str, barrier, i: int) -> None:
    store = DocStore(base_path=base_path)
    doc = f"doc{i}.md"
    md = f"# Doc {i}\n\nBody for doc {i} with enough text for a real section.\n"
    new_sections = parse_file(md, doc, f"{_OWNER}/{_NAME}")
    barrier.wait()
    store.incremental_save(
        _OWNER,
        _NAME,
        changed_files=[],
        new_files=[doc],
        deleted_files=[],
        new_sections=new_sections,
        raw_files={doc: md},
        doc_types={".md": 1},
    )


def test_concurrent_save_no_corruption(tmp_path):
    """N processes hammering save_index on the same repo never corrupt it."""
    base = str(tmp_path)
    n_procs, iters = 8, 15
    barrier = mp.Barrier(n_procs)
    procs = [mp.Process(target=_hammer_save, args=(base, barrier, iters)) for _ in range(n_procs)]
    for p in procs:
        p.start()
    _join_all(procs)

    # The on-disk index must be valid JSON (raises JSONDecodeError if corrupt).
    index_file = tmp_path / _OWNER / f"{_NAME}.json"
    assert index_file.exists()
    json.loads(index_file.read_text())

    # And it must still load + list cleanly.
    store = DocStore(base_path=base)
    assert store.load_index(_OWNER, _NAME) is not None
    assert any(r["repo"] == f"{_OWNER}/{_NAME}" for r in store.list_repos())

    # No leftover temp files after success.
    assert list((tmp_path / _OWNER).glob("*.tmp")) == []


def test_concurrent_incremental_no_lost_update(tmp_path):
    """N processes each add a distinct doc via incremental_save; all survive.

    Without the cross-process lock the workers all read the same base index and
    last-replace-wins drops every addition but one. The lock serializes the
    read-modify-write so every doc lands. Runs on every platform now that the
    lock is cross-platform (flock on POSIX, msvcrt on Windows).
    """
    base = str(tmp_path)
    # Seed the index so every worker takes the incremental (read-modify-write) path.
    store = DocStore(base_path=base)
    seed = "# Root\n\nSeed.\n"
    store.save_index(_OWNER, _NAME, parse_file(seed, "README.md", f"{_OWNER}/{_NAME}"),
                     {"README.md": seed}, {".md": 1})

    n_procs = 6
    barrier = mp.Barrier(n_procs)
    procs = [mp.Process(target=_add_one_doc, args=(base, barrier, i)) for i in range(n_procs)]
    for p in procs:
        p.start()
    _join_all(procs)

    index = DocStore(base_path=base).load_index(_OWNER, _NAME)
    assert index is not None
    json.loads((tmp_path / _OWNER / f"{_NAME}.json").read_text())  # not corrupt
    # Every concurrently-added doc must be present (none lost to last-replace-wins).
    for i in range(n_procs):
        assert f"doc{i}.md" in index.doc_paths, f"lost update: doc{i}.md missing"
