"""v1.74.0 - markdown block-detection fixes, isolated set (#46, #51, #56, #57).

#46: strip_mdx ran its import/export + JSX removers over the whole document,
mutilating code inside fences (and storing the corrupted text as the
hash-verified mirror). Now fence-aware.
#51: code blocks were emitted only on fence close, so a fence left open at EOF
was buffered then dropped. Now flushed at EOF (CommonMark closes it).
#56: a leading '---' thematic break plus a later bare '---' was read as
frontmatter, swallowing every heading between. Now the blank-line discriminator
rejects it.
#57: extract_tags ran over the raw body incl. fenced code + YAML frontmatter, so
#include / #fff / YAML values became tags. Now a prose-only view.
"""

import hashlib

from jdocmunch_mcp.parser.markdown_parser import parse_markdown, strip_mdx

REPO = "local/x"


def _titles(text):
    return [(s.level, s.title) for s in parse_markdown(text, "doc.md", REPO)]


# --- #56: thematic-break start is not frontmatter ------------------------------

def test_thematic_break_start_does_not_swallow_headings():
    doc = "---\n\n# Heading One\n\ntext\n\n---\n\n# Heading Two\n"
    assert _titles(doc) == [(0, "doc"), (1, "Heading One"), (1, "Heading Two")]


def test_real_frontmatter_still_detected():
    doc = "---\ntitle: Foo\nauthor: Bar\n---\n\n# Real Heading\n\nBody.\n"
    titles = _titles(doc)
    assert (1, "Real Heading") in titles
    assert "author: Bar" not in [t for _, t in titles]


def test_lone_leading_dashes_no_closer_sections_normally():
    # No later bare '---' -> not frontmatter; heading still found.
    doc = "---\n\n# Only Heading\n\nbody\n"
    assert (1, "Only Heading") in _titles(doc)


# --- #57: tags come from a prose-only view -------------------------------------

def test_tags_exclude_fenced_code_and_frontmatter():
    demo = (
        "---\ntitle: demo\naccent: #f0Acolor\n---\n\n"
        "# Build Guide\n\nProse tagged #howto for discovery.\n\n"
        "## C Section\n\n```c\n#include <stdio.h>\n#define MAX 10\n```\n\n"
        "## CSS Section\n\n```css\na { color: #fff; border: 1px solid #ABCdef; }\n```\n"
    )
    by_title = {s.title: s for s in parse_markdown(demo, "demo.md", REPO)}
    assert by_title["Build Guide"].tags == ["howto"]
    assert by_title["demo"].tags == []
    assert by_title["C Section"].tags == []
    assert by_title["CSS Section"].tags == []


def test_prose_view_does_not_disturb_content_or_hash():
    demo = "# H\n\nProse #tag.\n\n```c\n#include <x>\n```\n"
    raw = demo.encode("utf-8")
    for s in parse_markdown(demo, "demo.md", REPO):
        assert hashlib.sha256(raw[s.byte_start:s.byte_end]).hexdigest() == s.content_hash
        # Content still includes the fenced code verbatim; only tags are scrubbed.
    h = [s for s in parse_markdown(demo, "demo.md", REPO) if s.level == 1][0]
    assert "#include <x>" in h.content


# --- #51: unclosed fence flushed at EOF ----------------------------------------

def test_unclosed_fence_captured_at_eof():
    unclosed = (
        "# Before Fence\n\nintro\n\n```python\n"
        'code = "here"\n# looks like a heading\nstill_code_at_eof = True'
    )
    secs = parse_markdown(unclosed, "u.md", REPO)
    h = [s for s in secs if s.level == 1][0]
    assert [b["lang"] for b in h.code_blocks] == ["python"]
    assert "still_code_at_eof" in h.code_blocks[0]["content"]
    # No phantom heading minted from the in-fence '# looks like a heading'.
    assert all("looks like a heading" not in s.title for s in secs)


def test_closed_fence_unchanged_control():
    closed = "# Before\n\n```python\nx = 1\n```\n"
    h = [s for s in parse_markdown(closed, "c.md", REPO) if s.level == 1][0]
    assert [b["lang"] for b in h.code_blocks] == ["python"]


# --- #46: strip_mdx is fence-aware --------------------------------------------

def test_strip_mdx_preserves_fenced_code():
    mdx = (
        "---\ntitle: Quickstart\n---\n\nimport Tabs from '@theme/Tabs';\n\n"
        "# Quickstart\n\n```jsx\nimport React from 'react';\n"
        "export default function App() {\n  return <Tabs queryString=\"lang\" />;\n}\n```\n"
    )
    out = strip_mdx(mdx)
    # Top-level import + frontmatter removed.
    assert "title: Quickstart" not in out
    assert "import Tabs from '@theme/Tabs';" not in out
    # Fence interior fully preserved.
    assert "import React from 'react';" in out
    assert "export default function App() {" in out
    assert "<Tabs queryString=\"lang\" />" in out


def test_strip_mdx_still_strips_plain_jsx_and_imports():
    mdx = "import X from 'x';\n\n<Note>hello</Note>\n\nReal text.\n"
    out = strip_mdx(mdx)
    assert "import X" not in out
    assert "<Note>" not in out
    assert "hello" in out and "Real text." in out
