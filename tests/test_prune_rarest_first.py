"""Stage-A prune admits postings rarest term first.

Regression guard for the defect where `PostingIndex.candidates` collected
postings in QUERY order: one high-document-frequency term drained the whole
MAX_CANDIDATES budget before any other term was consulted, so a section
containing every rare term in the query was never scored at all. BM25 cannot
re-rank what Stage A dropped, so the section did not rank low -- it vanished.

Two symptoms, both asserted below:

* the result set depended on WORD ORDER (prepending a common word crowded the
  best match out; appending the same word changed nothing), which no BM25
  engine may do; and
* it got worse as a corpus grew, because document frequency rises with the
  corpus and more terms reach the cap. A small corpus is immune, which is why
  this survived the suite.

The fill for the leftover slots must also be deterministic: set iteration
order follows the per-process string hash seed, so the same query could return
different sections in two processes.
"""

from __future__ import annotations

from jdocmunch_mcp.retrieval.prune import MAX_CANDIDATES, PostingIndex


def _corpus(n_common: int = 500):
    """`common` fills more than the cap; `needle` is in exactly one section, and
    that section does NOT contain `common`.

    The disjointness is what makes this fixture seed-independent, and it is also
    the shape the defect was reported in: a common word ABSENT from the target
    document crowded the target out. Let the needle section carry `common` too and
    whether the old algorithm loses it depends on where the needle falls in a
    set iteration, i.e. on PYTHONHASHSEED -- a test that passes by luck.
    """
    sections = [
        {"id": f"s{i}#1", "title": "Common", "summary": "", "content": "common filler"}
        for i in range(n_common)
    ]
    sections.append({"id": "needle#1", "title": "Needle",
                     "summary": "", "content": "needle"})
    return PostingIndex.build(sections)


class TestRarestFirst:
    def test_rare_term_survives_a_common_term_that_fills_the_cap(self):
        idx = _corpus()
        assert len(idx.postings["common"]) > MAX_CANDIDATES
        assert idx.candidates("common needle") is not None
        assert "needle#1" in idx.candidates("common needle")

    def test_result_does_not_depend_on_word_order(self):
        idx = _corpus()
        assert idx.candidates("common needle") == idx.candidates("needle common")

    def test_prepending_a_common_word_never_drops_the_target(self):
        """Adding context to a query may add candidates; it may never remove one."""
        idx = _corpus()
        alone = idx.candidates("needle")
        for prefix in ("common", "common filler", "filler common"):
            with_prefix = idx.candidates(f"{prefix} needle")
            assert alone <= with_prefix, prefix

    def test_cap_is_still_respected(self):
        idx = _corpus()
        assert len(idx.candidates("common needle", max_candidates=42)) == 42

    def test_overflow_fill_is_deterministic(self):
        """Separate interpreters, different seeds, same answer.

        ⚠ A loop inside this process cannot check this. PYTHONHASHSEED is
        already fixed by the time the test body runs, so every iteration sees
        the same set iteration order and the assertion passes whatever the
        code does. Only fresh interpreters under different seeds can fail it.
        """
        import os
        import subprocess
        import sys
        from pathlib import Path

        import jdocmunch_mcp

        script = (
            "import hashlib\n"
            "from jdocmunch_mcp.retrieval.prune import PostingIndex\n"
            "secs = [{'id': f's{i}#1', 'title': '', 'summary': '',"
            " 'content': 'common filler'} for i in range(500)]\n"
            "got = sorted(PostingIndex.build(secs)"
            ".candidates('common', max_candidates=50))\n"
            "print(hashlib.md5(','.join(got).encode()).hexdigest())\n"
        )
        src_root = Path(jdocmunch_mcp.__file__).resolve().parents[1]

        digests = set()
        for seed in ("1", "2", "3"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=str(src_root))
            # stdin=DEVNULL: with pytest capturing, an inherited stdin handle
            # may already be closed, which raises OSError on Windows.
            run = subprocess.run(
                [sys.executable, "-c", script], env=env, capture_output=True,
                text=True, stdin=subprocess.DEVNULL,
            )
            assert run.returncode == 0, run.stderr
            digests.add(run.stdout.strip())
        assert len(digests) == 1, digests

    def test_every_rare_term_is_admitted_before_a_common_one(self):
        sections = [{"id": f"c{i}#1", "title": "", "summary": "", "content": "common"}
                    for i in range(400)]
        sections += [{"id": f"r{i}#1", "title": "", "summary": "", "content": f"rare{i}"}
                     for i in range(5)]
        idx = PostingIndex.build(sections)
        got = idx.candidates("common " + " ".join(f"rare{i}" for i in range(5)))
        for i in range(5):
            assert f"r{i}#1" in got
