"""v1.77.0 - get_broken_links: fs existence, GitHub anchors, scheme parsing
(#49, #50, #47 symptom 6).

#49: existence was tested only against the indexed doc set, so links to existing
non-doc files (images, LICENSE, source) reported file_not_found. Now the
filesystem is consulted against source_root before flagging.
#50: #anchor links were validated against jdocmunch's private slug scheme, not
the anchors a renderer emits. A per-document GitHub-rendered anchor namespace is
now accepted alongside the private forms.
#47.6: a typo'd/unknown scheme was classified internal and silently dropped by a
blanket colon-skip. It now reports reason 'unknown_scheme'.
"""

import os
import tempfile
from pathlib import Path

from jdocmunch_mcp.tools.get_broken_links import (
    get_broken_links, _github_slug, _rendered_text,
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
            if isinstance(content, bytes):
                p.write_bytes(content)
            else:
                p.write_text(content, encoding="utf-8")
        index_local(path=str(corpus), name=name, storage_path=store,
                    use_ai_summaries=False, use_embeddings=False)
        r = get_broken_links(f"local/{name}", storage_path=store)["result"]
        return r["broken_link_count"], {(b["target"], b["reason"]) for b in r["broken_links"]}


# --- #50: GitHub slugger helpers ----------------------------------------------

def test_github_slug_preserves_underscores_and_hyphen_runs():
    assert _github_slug("my_function reference") == "my_function-reference"
    assert _github_slug("Foo - Bar") == "foo---bar"


def test_rendered_text_strips_inline_markup():
    assert _github_slug(_rendered_text("[Docs](./d.md) link")) == "docs-link"
    assert _github_slug(_rendered_text("![Build](b.svg) overview")) == "build-overview"
    assert _github_slug(_rendered_text("Run `make build` now")) == "run-make-build-now"


# --- #49: existing non-doc files are not file_not_found -----------------------

def test_existing_non_doc_files_not_reported_missing():
    count, broken = _broken({
        "README.md": ("# Demo\n\nSee [license](LICENSE) and [entry](src/main.py).\n\n"
                      "Read [guide](guide.md).\n\nBroken: [old](missing.md).\n"),
        "guide.md": "# Guide\n\nsteps\n",
        "LICENSE": "MIT\n",
        "src/main.py": "print(1)\n",
    }, "lc49")
    assert count == 1
    assert ("missing.md", "file_not_found") in broken


# --- #50: GitHub-valid anchors are not flagged --------------------------------

def test_github_valid_anchors_not_flagged():
    count, broken = _broken({
        "anchors.md": (
            "# Anchors\n\n## my_function reference\n\n[a](#my_function-reference)\n\n"
            "## Foo - Bar\n\n[b](#foo---bar)\n\n## [Docs](./d.md) link\n\n[c](#docs-link)\n\n"
            "## Install\n\nfirst\n\n## Install\n\n[d](#install-1)\n"
        ),
        "d.md": "# D\n\nx\n",
    }, "lc50")
    assert count == 0, broken


def test_dead_anchor_still_reported():
    # An anchor matching no rendered heading is still flagged.
    count, broken = _broken({
        "a.md": "# A\n\n## Real Heading\n\n[x](#does-not-exist)\n",
    }, "lc50b")
    assert ("#does-not-exist", "anchor_not_found") in broken


# --- #47.6: typo'd scheme reported -------------------------------------------

def test_typo_scheme_reported_unknown_scheme():
    count, broken = _broken({
        "a.md": "# A\n\nDead: [site](htp://example.com/x).\n",
    }, "lc47")
    assert ("htp://example.com/x", "unknown_scheme") in broken


def test_real_external_schemes_still_skipped():
    count, broken = _broken({
        "a.md": "# A\n\n[web](https://example.com) [mail](mailto:x@y.com) <z@y.com>.\n",
    }, "lc47b")
    assert count == 0, broken
