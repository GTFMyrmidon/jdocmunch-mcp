"""v1.76.0 - extract_references rewrite: inline grammar + reference defs (#47, #48).

#47: references were built by two naive regexes over the raw body, storing link
titles / angle-bracket destinations / image targets verbatim, truncating
parenthesized URLs, keeping autolink/bare-URL trailing junk, and extracting link
syntax shown inside code. Now a proper inline-link pass with code-region scrub.
#48: reference-style links ([text][ref], [text][], [text]) and their
[ref]: target definitions contributed no edge. Definition targets are now
captured.
"""

import os
import tempfile
from pathlib import Path

from jdocmunch_mcp.parser.sections import extract_references
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.get_backlinks import get_backlinks


# --- #47: inline link grammar -------------------------------------------------

def test_titled_and_anglebracket_destinations_cleaned():
    assert extract_references('[g](install.md "Quick install")') == ["install.md"]
    assert extract_references("[s](<spaced file.md>)") == ["spaced file.md"]


def test_images_are_not_references():
    assert extract_references("![alt](images/arch.png)") == []


def test_parenthesized_url_not_truncated():
    out = extract_references("[w](https://en.wikipedia.org/wiki/Foo_(bar))")
    assert out == ["https://en.wikipedia.org/wiki/Foo_(bar)"]


def test_autolink_and_bare_url_trailing_trimmed():
    out = extract_references("see <https://example.com/auto>. also https://example.com/bare, ok")
    assert "https://example.com/auto" in out
    assert "https://example.com/bare" in out
    assert all(">" not in r and not r.endswith((",", ".")) for r in out)


def test_link_syntax_in_code_is_not_extracted():
    body = "inline `[x](missing2.md)`.\n\n```text\n[y](missing-fenced.md)\n```\n"
    assert extract_references(body) == []


def test_html_comment_links_not_extracted():
    assert extract_references("<!-- [draft](missing-draft.md) -->") == []


def test_typo_scheme_link_is_extracted_for_the_checker():
    # Dead but real: extraction keeps it so the link checker can flag it.
    assert extract_references("[site](htp://example.com/x)") == ["htp://example.com/x"]


def test_empty_destination_skipped_and_email_autolink():
    assert extract_references("[draft]()") == []
    assert extract_references("Contact <user@example.com>") == ["user@example.com"]


# --- #48: reference-style definitions -----------------------------------------

def test_reference_style_definition_targets_captured():
    assert extract_references("See [g][r].\n\n[r]: ../api.md\n") == ["../api.md"]
    assert extract_references("See [guide][].\n\n[guide]: ../api.md\n") == ["../api.md"]
    assert extract_references("See [guide].\n\n[guide]: ../api.md\n") == ["../api.md"]


def test_inline_link_control_still_works():
    assert extract_references("See [g](../api.md).") == ["../api.md"]


# --- #48 end-to-end: reference-style link creates a backlink edge -------------

def test_reference_style_link_produces_backlink():
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        corpus = Path(tmp, "corpus")
        corpus.mkdir()
        (corpus / "README.md").write_text(
            "# Home\n\nSee the [API reference][apiref].\n\n[apiref]: api.md\n",
            encoding="utf-8",
        )
        (corpus / "api.md").write_text("# API\n\nDocs.\n", encoding="utf-8")
        index_local(path=str(corpus), name="refstyle", storage_path=store,
                    use_ai_summaries=False, use_embeddings=False)
        res = get_backlinks("local/refstyle", "api.md", storage_path=store)
        payload = res.get("result", res)
        assert payload.get("backlink_count", 0) >= 1, payload
