"""v1.71.0 - CommonMark setext/paragraph correctness + finalize-time byte/hash
invariant (#44, #35, #55).

#44: setext underline detection now keys on the CommonMark paragraph rule
(the block above the underline must be a paragraph) instead of a prev-line
heuristic, so list items / blockquotes / fence-close lines / ATX headings no
longer fabricate, destroy, or mis-title sections; multi-line setext titles are
captured whole; single-dash H2 and pipe-bearing H1 underlines are recognized.

#35 + #55: section bodies are derived from the byte range, so
sha256(raw[byte_start:byte_end]) == content_hash holds by construction for
every section (the invariant the verify family relies on); the old setext path
hashed a subset of its range and reported false drift.

All tests parse inline strings only and touch no index store.
"""

import hashlib

from jdocmunch_mcp.parser.markdown_parser import parse_markdown

REPO = "local/x"


def shape(text):
    return [(s.level, s.title) for s in parse_markdown(text, "doc.md", REPO)]


# --- #44 family (a): non-paragraph block + underline must not fabricate -------

def test_list_then_dash_is_thematic_break_not_heading():
    assert shape("# Top\n\nIntro text.\n\n- item one\n- item two\n---\n\nAfter.\n") == [
        (0, "doc"), (1, "Top"),
    ]


def test_blockquote_then_dash_is_not_heading():
    assert shape("# Top\n\nIntro.\n\n> quoted wisdom\n---\n\nAfter.\n") == [
        (0, "doc"), (1, "Top"),
    ]


def test_fence_close_then_dash_is_not_heading():
    assert shape('# Top\n\n```python\nprint("hi")\n```\n---\n\nAfter.\n') == [
        (0, "doc"), (1, "Top"),
    ]


# --- #44 family (b): ATX heading + underline must not destroy/impostor --------

def test_atx_then_equals_keeps_real_heading():
    secs = parse_markdown("# ATX Title\n===\n\nBody.\n", "doc.md", REPO)
    assert [(s.level, s.title) for s in secs] == [(0, "doc"), (1, "ATX Title")]
    atx = secs[1]
    # The real ATX section is non-degenerate (no [0:0] destroyed section), and
    # the === underline is body content of it, not an impostor heading.
    assert atx.byte_end > atx.byte_start
    assert "===" in atx.content


# --- #44 family (c): multi-line setext paragraph -> whole-paragraph title -----

def test_multiline_setext_title_spans_whole_paragraph():
    secs = parse_markdown(
        "Intro section.\n\nFirst line of para\nSecond line of para\n---\n\nBody.\n",
        "doc.md", REPO,
    )
    titles = [s.title for s in secs]
    assert "First line of para Second line of para" in titles
    assert "Second line of para" not in titles  # not just the last line


# --- #44 family (d): single-dash H2 + pipe-bearing H1 --------------------------

def test_single_dash_underline_is_h2():
    assert shape("Foo\n-\n\nBody.\n") == [(0, "doc"), (2, "Foo")]


def test_pipe_in_h1_setext_is_recognized():
    assert shape("Foo | Bar\n===\n\nBody.\n") == [(0, "doc"), (1, "Foo | Bar")]


# --- #44 regression gate: GFM pipe tables must not become headings ------------

def test_gfm_pipe_tables_never_become_setext_headings():
    tables = {
        "T1": "# Top\n\n| Name | Age |\n|------|-----|\n| Bob  | 4   |\n",
        "T2": "# Top\n\nName | Age\n---- | ---\nBob  | 4\n",
        "T3": "# Top\n\n| Name |\n| ---- |\n| Bob  |\n",
        "T4": "# Top\n\nSome prose.\nName | Age\n---- | ---\nBob  | 4\n",
        "T5": "# Top\n\nName | Age\n-----------\nBob  | 4\n",
    }
    for name, text in tables.items():
        assert shape(text) == [(0, "doc"), (1, "Top")], name


# --- #35 / #55: finalize-time byte/hash invariant ------------------------------

def test_byte_hash_invariant_holds_for_every_section():
    docs = {
        "setext.md": (
            "Setext Title\n============\n\nIntro under the H1.\n\n"
            "First Section\n-------------\n\nBody one.\n\n## ATX Sibling\n\nMixed.\n"
        ),
        "atx-underline.md": "# ATX Title\n---\n\nBody.\n\n## Second\n\nTail.\n",
        "control.md": "# Control\n\nBody.\n\n## Child\n\nMore.\n",
    }
    for path, raw in docs.items():
        data = raw.encode("utf-8")
        for s in parse_markdown(raw, path, REPO):
            slice_hash = hashlib.sha256(data[s.byte_start:s.byte_end]).hexdigest()
            assert slice_hash == s.content_hash, f"{path} {s.title!r} hash != slice"
            if s.level > 0:
                assert s.byte_end > s.byte_start, f"{path} {s.title!r} degenerate range"


def test_setext_section_content_includes_heading_lines():
    # The #35 regression: setext bodies used to exclude the heading + underline.
    secs = parse_markdown("Title Here\n==========\n\nBody text.\n", "doc.md", REPO)
    h1 = [s for s in secs if s.level == 1][0]
    assert h1.content.startswith("Title Here\n==========")
