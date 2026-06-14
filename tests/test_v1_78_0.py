"""v1.78.0 - inline_code artifact for the code<->docs bridge (#59).

The parser extracted only fenced code_blocks, so inline backtick mentions
(`name`) — the conventional way prose names symbols — never reached
link_code_to_symbols or get_undocumented_symbols. The parser now persists a
per-section `inline_code` list, and both bridge consumers read it.
"""

import os
import tempfile
from pathlib import Path

from jdocmunch_mcp.parser.markdown_parser import parse_markdown
from jdocmunch_mcp.parser.sections import extract_inline_code
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.retrieval.tokenize import tokenize_unique

REPO = "local/x"


# --- parser artifact ----------------------------------------------------------

def test_parser_extracts_inline_code_not_fenced():
    text = ("## Usage\n\nCall `safe_create_task` before scheduling. "
            "See `apply_guardrail`.\n\n```python\nfenced_only(x)\n```\n")
    usage = [s for s in parse_markdown(text, "README.md", REPO) if s.level > 0][0]
    assert usage.inline_code == ["safe_create_task", "apply_guardrail"]
    # Fenced identifiers are NOT inline code (they are code_blocks).
    assert "fenced_only" not in usage.inline_code


def test_inline_code_omitted_when_empty():
    sec = [s for s in parse_markdown("# H\n\nplain prose.\n", "x.md", REPO)][-1]
    assert sec.inline_code == []
    assert "inline_code" not in sec.to_dict()


# --- extractor filters --------------------------------------------------------

def test_extract_inline_code_filters():
    out = extract_inline_code(
        "`safe_create_task` `x` `has space` `call()` `safe_create_task` `a.b.c`"
    )
    assert out == ["safe_create_task", "call", "a.b.c"]  # x too short, space skipped, deduped


def test_extract_inline_code_caps():
    spans = " ".join(f"`ident_{i}`" for i in range(60))
    assert len(extract_inline_code(spans)) == 40


# --- persistence round-trip ---------------------------------------------------

def test_inline_code_persisted_and_loaded():
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        corpus = Path(tmp, "corpus")
        corpus.mkdir()
        (corpus / "g.md").write_text(
            "## Guide\n\nCall `safe_create_task` first. `apply_guardrail` validates.\n",
            encoding="utf-8",
        )
        index_local(path=str(corpus), name="ic", storage_path=store,
                    use_ai_summaries=False, use_embeddings=False)
        idx = DocStore(base_path=store).load_index("local", "ic")
        carriers = [s for s in idx.sections
                    if (s if isinstance(s, dict) else s.__dict__).get("inline_code")]
        assert carriers, "no section persisted inline_code"
        names = (carriers[0] if isinstance(carriers[0], dict) else carriers[0].__dict__)["inline_code"]
        assert "safe_create_task" in names and "apply_guardrail" in names


# --- get_undocumented_symbols data flow ---------------------------------------

def test_inline_code_feeds_undocumented_haystack():
    # Reproduces the tool's haystack construction: a symbol named only in inline
    # spans is now reachable (recall fix). Pre-#59 the haystack read neither
    # inline spans nor code_blocks, and markdown sections persist no content.
    text = "## Usage\n\nCall `safe_create_task` before scheduling work.\n"
    sections = [s.to_dict() for s in parse_markdown(text, "README.md", REPO)]
    haystack = set()
    for sec in sections:
        haystack |= tokenize_unique(sec.get("title") or "")
        haystack |= tokenize_unique(sec.get("summary") or "")
        if sec.get("content"):
            haystack |= tokenize_unique(sec["content"])
        for span in sec.get("inline_code", []) or []:
            haystack |= tokenize_unique(span)
    sym_tokens = {t for t in tokenize_unique("safe_create_task") if len(t) >= 3}
    assert sym_tokens and sym_tokens & haystack, "inline mention not reachable"
