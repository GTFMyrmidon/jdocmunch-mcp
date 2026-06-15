"""v1.81.0 - structural_integrity health axis (#54).

doc_health_radar / get_doc_health had no structural axis, so an index that
silently lost sections to a fence accident graded identically to its repair.
A new structural_integrity axis is fed by warnings computed from already-
persisted data: headings swallowed into stored code blocks (fence accidents)
and heading-level skips. No parser change, no reindex beyond code_blocks.
"""

import os
import tempfile
from pathlib import Path

from jdocmunch_mcp.tools.get_doc_health import _structural_signals
from jdocmunch_mcp.tools.health_radar import compute_radar, _score_structural_integrity
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.doc_health_radar import doc_health_radar


# --- signal computation -------------------------------------------------------

def test_structural_signals_swallowed_and_level_skip():
    sections = [
        {"doc_path": "d.md", "level": 0, "code_blocks": []},
        {"doc_path": "d.md", "level": 1, "code_blocks": [
            {"lang": "python", "content": "x = 1\n## Installation\nswallowed\n"}]},
        {"doc_path": "d.md", "level": 4, "code_blocks": []},  # 1 -> 4 skip
    ]
    sig = _structural_signals(sections)
    assert sig["swallowed_heading_blocks"] == 1
    assert sig["level_skips"] == 1
    assert sig["structural_warning_count"] == 2


def test_markdown_fence_exempt_from_swallowed():
    sections = [{"doc_path": "d.md", "level": 1, "code_blocks": [
        {"lang": "markdown", "content": "## A heading in a markdown example\n"}]}]
    assert _structural_signals(sections)["swallowed_heading_blocks"] == 0


def test_level_skip_only_between_headings_not_root():
    # 0 -> 2 (root to first heading) is not a skip; 2 -> 3 is fine.
    sections = [
        {"doc_path": "d.md", "level": 0},
        {"doc_path": "d.md", "level": 2},
        {"doc_path": "d.md", "level": 3},
    ]
    assert _structural_signals(sections)["level_skips"] == 0


# --- radar axis ---------------------------------------------------------------

def test_radar_has_structural_axis():
    radar = compute_radar(
        fresh=1, edited=0, stale=0, broken_links=0, orphan_count=0,
        embedded_sections=0, section_count=10, role_distribution={"unknown": 0},
        structural_warnings=0,
    )
    assert radar["axes"]["structural_integrity"]["score"] == 100.0


def test_structural_score_penalizes_warnings():
    assert _score_structural_integrity(0, 10) == 100.0
    assert _score_structural_integrity(1, 10) == 0.0  # 10% -> 0
    assert _score_structural_integrity(0, 0) == 100.0  # empty corpus


# --- end-to-end: damage is now visible ----------------------------------------

def test_damaged_corpus_scores_below_repaired():
    damaged = {
        "broken.md": ("# Broken\n\nIntro.\n\n```python\ndef e():\n    pass\n\n"
                      "## Installation\n\nswallowed\n\n## Configuration\n\nalso\n"),
        "good.md": "# Good\n\n## Usage\n\nnotes\n\n#### Edge\n\nH2->H4 skip\n",
    }
    repaired = {
        "broken.md": ("# Broken\n\nIntro.\n\n```python\ndef e():\n    pass\n```\n\n"
                      "## Installation\n\nfine\n\n## Configuration\n\nfine\n"),
        "good.md": "# Good\n\n## Usage\n\nnotes\n\n### Edge\n\nno skip\n",
    }

    def composite(files, name):
        with tempfile.TemporaryDirectory() as tmp:
            store = os.path.join(tmp, "s")
            c = Path(tmp, "c")
            c.mkdir()
            for n, t in files.items():
                (c / n).write_text(t, encoding="utf-8")
            index_local(path=str(c), name=name, storage_path=store,
                        use_ai_summaries=False, use_embeddings=False)
            rad = doc_health_radar(repo=f"local/{name}", storage_path=store)["result"]
            return rad["radar"]["composite"], rad["radar"]["axes"]["structural_integrity"]["score"]

    d_comp, d_axis = composite(damaged, "dmg")
    r_comp, r_axis = composite(repaired, "rep")
    assert d_axis < r_axis, (d_axis, r_axis)
    assert d_comp < r_comp, (d_comp, r_comp)
