"""v1.75.0 - parse-loop block detection: indentation + HTML blocks (#43, #45).

#43: all block-start detection ran against the raw column-0 line, so headings
and fences indented 1-3 spaces (and list-nested fences) were missed, folding
headings into the previous section and parsing fence interiors as markdown.
Now up to 3 leading spaces are dedented for ATX/setext detection and fence
opens are indent-tolerant.

#45: parse_markdown had no HTML-block state, so heading-like lines inside HTML
comments / script / pre / style / div blocks became phantom sections. Now a
CommonMark HTML-block state machine (types 1-7) suppresses heading detection
inside HTML blocks, mirroring the fenced-code machine.
"""

import hashlib

from jdocmunch_mcp.parser.markdown_parser import parse_markdown
from jdocmunch_mcp.parser.hierarchy import wire_hierarchy

REPO = "local/x"


def _titles(text):
    return [(s.level, s.title) for s in parse_markdown(text, "doc.md", REPO)]


# --- #43: indentation tolerance ------------------------------------------------

def test_indented_atx_headings_are_sectioned():
    doc = "# Top\n\nIntro.\n\n ## One Space\n\nb\n\n   ### Three Space\n\nb\n"
    titles = _titles(doc)
    assert (2, "One Space") in titles
    assert (3, "Three Space") in titles


def test_four_space_indent_is_not_a_heading():
    # CommonMark: 4-space indent is code, not a heading.
    doc = "# Top\n\nIntro.\n\n    # Not A Heading\n\nb\n"
    assert (1, "Not A Heading") not in _titles(doc)


def test_indented_and_list_nested_fences_capture_code():
    doc = (
        "# Top\n\n1. Apply:\n\n   ```yaml\n   replicas: 3\n   ```\n\n"
        "  ```python\n# col-zero comment inside indented fence\nprint(1)\n  ```\n\n"
        "# After\n"
    )
    secs = parse_markdown(doc, "doc.md", REPO)
    langs = [b["lang"] for s in secs for b in s.code_blocks]
    assert "yaml" in langs and "python" in langs
    # The col-zero comment inside the indented fence is not a phantom heading.
    assert all("comment inside" not in t for _, t in [(s.level, s.title) for s in secs])
    assert (1, "After") in [(s.level, s.title) for s in secs]


def test_indented_setext_underline_detected():
    doc = "Intro.\n\n Title Here\n ==========\n\nbody\n"
    assert (1, "Title Here") in _titles(doc)


# --- #45: HTML blocks ----------------------------------------------------------

def _shape(text, name):
    return [(s.level, s.title) for s in wire_hierarchy(parse_markdown(text, name, REPO))]


def test_html_comment_block_suppresses_heading():
    text = "Intro.\n\n<!--\n## Draft Section\nhidden do not publish\n-->\n\n## Real After\n\nv\n"
    shape = _shape(text, "c.md")
    assert (2, "Real After") in shape
    assert all("Draft Section" not in t for _, t in shape)


def test_html_script_block_type1_suppresses_phantoms():
    text = "Intro.\n\n<script>\n# fake heading\nconst x = 1;\n---\n</script>\n\n## Real After\n\nv\n"
    shape = _shape(text, "s.md")
    assert shape == [(0, "s"), (2, "Real After")]


def test_html_div_block_type6_runs_to_blank_line():
    text = "Intro.\n\n<div>\n# fake heading in div\n</div>\n\n# Real After Div\n\nv\n"
    shape = _shape(text, "d.md")
    assert shape == [(0, "d"), (1, "Real After Div")]


def test_single_line_html_comment_does_not_swallow_following_heading():
    text = "Intro.\n\n<!-- a one line comment -->\n\n## Real Heading\n\nv\n"
    assert (2, "Real Heading") in _titles(text)


def test_inline_less_than_in_prose_is_not_html_block():
    # A prose line containing '<' mid-line must not start an HTML block.
    text = "# Top\n\nUse a < b when comparing.\n\n## Still A Heading\n\nv\n"
    assert (2, "Still A Heading") in _titles(text)


def test_byte_hash_invariant_holds_across_html_blocks():
    text = "# Top\n\n<!--\n## hidden\n-->\n\n<script>\n# x\n</script>\n\n## Real\n\nbody\n"
    raw = text.encode("utf-8")
    for s in parse_markdown(text, "doc.md", REPO):
        assert hashlib.sha256(raw[s.byte_start:s.byte_end]).hexdigest() == s.content_hash
