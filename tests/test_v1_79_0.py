"""v1.79.0 - TOML (+++) frontmatter recognition (#60).

The frontmatter detector recognized only YAML '---'. Hugo's TOML '+++' block was
indexed as root-section prose and its URLs entered the stored references.
_frontmatter_end_line now accepts '+++' (same-delimiter closer), and per-section
references/tags/inline_code derive from the frontmatter-free prose view, so
frontmatter values no longer pollute the references artifact (also covers YAML).
"""

import hashlib

from jdocmunch_mcp.parser.markdown_parser import parse_markdown

REPO = "local/x"


def test_toml_frontmatter_recognized_and_not_in_references():
    doc = ('+++\ntitle = "Hugo Page"\ndate = 2026-01-01\n'
           'canonical = "https://example.com/canonical"\n+++\n\n# Hugo Heading\n\nbody\n')
    secs = parse_markdown(doc, "hugo.md", REPO)
    assert (1, "Hugo Heading") in [(s.level, s.title) for s in secs]
    root = [s for s in secs if s.level == 0][0]
    assert root.references == []  # canonical URL no longer harvested


def test_yaml_frontmatter_url_not_in_references_but_prose_link_is():
    doc = "---\ntitle: Y\ncanonical: https://example.com/y\n---\n\n# H\n\nSee [real](real.md).\n"
    secs = parse_markdown(doc, "y.md", REPO)
    root = [s for s in secs if s.level == 0][0]
    h = [s for s in secs if s.level == 1][0]
    assert root.references == []
    assert h.references == ["real.md"]


def test_toml_opener_without_closer_is_not_frontmatter():
    # A lone '+++' with no matching closer must not swallow the document.
    assert (1, "Not Frontmatter") in [
        (s.level, s.title) for s in parse_markdown("+++\n\n# Not Frontmatter\n", "x.md", REPO)
    ]


def test_frontmatter_bytes_still_in_content_hash_invariant():
    # content/content_hash are byte-exact; only derived artifacts change.
    doc = '+++\ntitle = "T"\n+++\n\n# H\n\nbody\n'
    raw = doc.encode("utf-8")
    for s in parse_markdown(doc, "h.md", REPO):
        assert hashlib.sha256(raw[s.byte_start:s.byte_end]).hexdigest() == s.content_hash
    root = [s for s in parse_markdown(doc, "h.md", REPO) if s.level == 0][0]
    assert "+++" in root.content  # frontmatter still in the byte-accurate content
