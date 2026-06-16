"""v1.84.0 - get_broken_links: rendered-anchor namespace, no private slugs (#64).

#64: get_broken_links accepted jdocmunch's PRIVATE section slug (underscore
flattening, hyphen-run collapse, hierarchical leaf, parse-time slugify) as a
valid anchor target. That namespace is an internal index artifact no Markdown
renderer emits, so a link that is dead on the rendered page passed validation
whenever it happened to match the private slug -- a false negative in a link
checker. It also modeled only generated GitHub heading anchors, missing explicit
{#id} heading ids and raw HTML <a id>/<a name>/<h* id> anchors.

The fix (clean-room, not the reporter's prototype): _build_rendered_anchors now
derives the full namespace a renderer emits -- generated github-slugger heading
anchors (explicit-id marker stripped), explicit {#id} ids, raw HTML anchors, and
user-content- aliases -- and _anchor_matches_section consults ONLY that set.
Consumer-layer; no reindex (titles + body content already carry the inputs).
"""

import os
import tempfile
from pathlib import Path

from jdocmunch_mcp.tools.get_broken_links import (
    get_broken_links,
    _build_rendered_anchors,
    _split_explicit_id,
    _scrub_code,
)
from jdocmunch_mcp.tools.index_local import index_local


def _broken(corpus_files, name):
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        corpus = Path(tmp, "corpus")
        corpus.mkdir()
        for rel, content in corpus_files.items():
            p = corpus / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        index_local(path=str(corpus), name=name, storage_path=store,
                    use_ai_summaries=False, use_embeddings=False)
        r = get_broken_links(f"local/{name}", storage_path=store)["result"]
        return r["broken_link_count"], {(b["target"], b["reason"]) for b in r["broken_links"]}


# --- #64 core: the private slug is no longer trusted --------------------------

def test_private_underscore_flatten_now_flagged():
    # GitHub renders 'my_function-reference' (underscore preserved); the private
    # slug flattens to 'my-function-reference'. Only the rendered form is valid.
    count, broken = _broken({
        "g.md": (
            "# G\n\n## my_function reference\n\n"
            "[rendered](#my_function-reference)\n"
            "[private](#my-function-reference)\n"
        ),
    }, "n64a")
    assert ("#my-function-reference", "anchor_not_found") in broken
    assert ("#my_function-reference", "anchor_not_found") not in broken


def test_private_hyphen_run_collapse_now_flagged():
    count, broken = _broken({
        "g.md": "# G\n\n## Foo - Bar\n\n[run](#foo---bar)\n[collapse](#foo-bar)\n",
    }, "n64b")
    assert ("#foo-bar", "anchor_not_found") in broken
    assert ("#foo---bar", "anchor_not_found") not in broken


def test_private_duplicate_suffix_now_flagged():
    # GitHub suffixes duplicates -1/-2 (first bare); jdocmunch's private scheme
    # would have accepted #faq-2 for the second heading.
    count, broken = _broken({
        "g.md": "# G\n\n## FAQ\n\nfirst\n\n## FAQ\n\n[ok](#faq-1)\n[priv](#faq-2)\n",
    }, "n64c")
    assert ("#faq-2", "anchor_not_found") in broken
    assert ("#faq-1", "anchor_not_found") not in broken


# --- #64: explicit {#id} heading ids ------------------------------------------

def test_explicit_heading_id_resolves_and_marker_not_polluted():
    count, broken = _broken({
        "s.md": (
            "# S\n\n## My Custom Heading {#my-custom-id}\n\n"
            "[explicit](#my-custom-id)\n"
            "[polluted](#my-custom-heading-my-custom-id)\n"
        ),
    }, "n64d")
    assert ("#my-custom-id", "anchor_not_found") not in broken
    assert ("#my-custom-heading-my-custom-id", "anchor_not_found") in broken


def test_invalid_explicit_id_falls_back_to_text_slug():
    count, broken = _broken({
        "s.md": (
            "# S\n\n## Invalid Explicit ID {#1-invalid}\n\n"
            "[fallback](#invalid-explicit-id)\n"
            "[badid](#1-invalid)\n"
        ),
    }, "n64e")
    assert ("#1-invalid", "anchor_not_found") in broken
    assert ("#invalid-explicit-id", "anchor_not_found") not in broken


# --- #64: raw HTML anchors ----------------------------------------------------

def test_html_anchors_resolve():
    # ids chosen so they never coincide with a generated heading slug, isolating
    # the HTML-anchor path: <a id>, <a name>, <h* id>, and user-content- alias.
    count, broken = _broken({
        "h.md": (
            "# H\n\n"
            '<a id="stable-ref"></a>\n\n## Section One\n\n'
            "[html-id](#stable-ref)\n"
            "[gh-alias](#user-content-stable-ref)\n\n"
            '<a name="legacy-ref"></a>\n\n## Section Two\n\n'
            "[html-name](#legacy-ref)\n\n"
            '<h3 id="raw-heading-ref">Raw</h3>\n\n'
            "[raw-html-heading](#raw-heading-ref)\n"
        ),
    }, "n64f")
    assert count == 0, broken


def test_unsafe_html_id_is_not_an_anchor():
    count, broken = _broken({
        "h.md": '# H\n\n<a id="bad id with spaces"></a>\n\n## Bad\n\n[x](#bad-id-with-spaces)\n',
    }, "n64g")
    assert ("#bad-id-with-spaces", "anchor_not_found") in broken


def test_anchor_inside_fenced_code_is_not_accepted():
    count, broken = _broken({
        "h.md": (
            "# H\n\n## Real\n\n"
            "```html\n"
            '<a id="fenced-anchor"></a>\n'
            "```\n\n"
            "[x](#fenced-anchor)\n"
        ),
    }, "n64h")
    assert ("#fenced-anchor", "anchor_not_found") in broken


# --- #64: cross-file explicit + HTML targets ----------------------------------

def test_cross_file_explicit_and_html_targets():
    count, broken = _broken({
        "src.md": (
            "# Src\n\n"
            "[gen](t.md#target-heading)\n"
            "[explicit](t.md#stable-target)\n"
            "[html](t.md#html-target)\n"
            "[dead](t.md#target-heading-2)\n"
        ),
        "t.md": (
            "# T\n\n## Target Heading\n\nbody\n\n"
            "## Stable Heading {#stable-target}\n\nbody\n\n"
            '<a id="html-target"></a>\n\n## HTML Target\n\nbody\n'
        ),
    }, "n64i")
    assert ("t.md#target-heading-2", "section_not_found") in broken
    assert ("t.md#target-heading", "section_not_found") not in broken
    assert ("t.md#stable-target", "section_not_found") not in broken
    assert ("t.md#html-target", "section_not_found") not in broken


# --- unit coverage for the new helpers ----------------------------------------

def test_split_explicit_id():
    assert _split_explicit_id("My Heading {#my-id}") == ("My Heading", "my-id")
    # unsafe id: marker stripped, no explicit id emitted
    assert _split_explicit_id("Invalid {#1-bad}") == ("Invalid", None)
    assert _split_explicit_id("No marker here") == ("No marker here", None)


def test_scrub_code_drops_fenced_and_inline():
    text = (
        "before\n```\n<a id=\"x\"></a>\n```\nmiddle `<a id=\"y\">` end\n"
        "<!-- <a id=\"z\"></a> -->\n"
    )
    out = _scrub_code(text)
    assert "id=\"x\"" not in out
    assert "id=\"y\"" not in out
    assert "id=\"z\"" not in out
    assert "before" in out and "end" in out


def test_build_rendered_anchors_namespace():
    sections = [
        {"doc_path": "d.md", "level": 0, "title": "d"},
        {"doc_path": "d.md", "level": 2, "title": "my_function reference"},
        {"doc_path": "d.md", "level": 2, "title": "Stable {#keep-me}"},
    ]
    raw_by_doc = {"d.md": '## Stable {#keep-me}\n\n<a id="body-ref"></a>\n'}
    anchors = _build_rendered_anchors(sections, raw_by_doc)["d.md"]
    assert "my_function-reference" in anchors      # rendered, underscore kept
    assert "my-function-reference" not in anchors   # private flatten rejected
    assert "keep-me" in anchors                     # explicit id
    assert "user-content-keep-me" in anchors        # gh alias
    assert "stable" in anchors                      # generated text slug
    assert "body-ref" in anchors                    # raw HTML anchor in body
