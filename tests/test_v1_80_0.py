"""v1.80.0 - shared prose view for hybrid-search scoring (#58).

Hybrid fusion combined a BM25 score (fences stripped) with an embedding score
(title + raw content[:1000], fences AND frontmatter included), so the two
channels scored different texts of the same section; heavy YAML/TOML frontmatter
flooded the capped embed window so prose never reached it. Both channels now
derive from a shared prose_view (frontmatter + fences stripped); the embed cache
key is salted so existing vectors recompute.
"""

from jdocmunch_mcp.parser.markdown_parser import parse_markdown
from jdocmunch_mcp.embeddings.provider import _section_embed_text, _embed_cache_key
from jdocmunch_mcp.retrieval.tokenize import tokenize, prose_view


def test_prose_view_strips_frontmatter_and_fences():
    assert prose_view("---\na: 1\n---\n\nProse here.\n").strip() == "Prose here."
    assert prose_view("+++\na = 1\n+++\n\nProse here.\n").strip() == "Prose here."
    assert "code()" not in prose_view("text\n\n```py\ncode()\n```\n")


def test_embed_text_is_prose_only():
    doc = ("---\ntitle: P\nlayout: default\npermalink: /x/\n---\n\n"
           "Intro about the frobnicator.\n\n```python\nfrob.connect(host=1)\n```\n")
    root = parse_markdown(doc, "p.md", "local/x")[0]
    et = _section_embed_text(root)
    assert "Intro about the frobnicator." in et
    assert "permalink" not in et and "layout" not in et
    assert "frob.connect" not in et


def test_heavy_frontmatter_does_not_crowd_out_prose():
    fm = "\n".join(f"param_{i:02d}: value_{i:02d}" for i in range(60))
    doc = f"---\n{fm}\n---\n\nThe quickstart explains how to deploy the widget.\n"
    root = parse_markdown(doc, "q.md", "local/x")[0]
    assert "quickstart explains" in _section_embed_text(root)


def test_bm25_tokenize_drops_frontmatter_keys():
    doc = ("---\ntitle: P\nlayout: default\npermalink: /docs/x/\n---\n\n"
           "Configuring the widget service.\n")
    root = parse_markdown(doc, "p.md", "local/x")[0]
    toks = tokenize(root.content)
    assert "configuring" in toks
    assert not ({"layout", "default", "permalink", "docs"} & set(toks))


def test_embed_cache_key_salted():
    root = parse_markdown("# H\n\nbody\n", "h.md", "local/x")[-1]
    assert _embed_cache_key(root).endswith("#pv1")
    # Empty when no hash.
    class _S:
        content_hash = ""
    assert _embed_cache_key(_S()) == ""
