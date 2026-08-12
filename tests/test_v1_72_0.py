"""v1.72.0 - byte-space fidelity: CRLF preservation (#52) + BOM strip (#53).

#52: local indexing read sources with universal newlines, collapsing CRLF/CR to
LF before measuring byte offsets, so published byte_start/byte_end/content_hash
verified only against a hidden normalized mirror, never the on-disk file, and
disagreed with the GitHub leg. Local reads now use open(..., newline="") so
offsets and hashes address the real on-disk bytes.

#53: a leading UTF-8 BOM (U+FEFF) survived ingestion and broke every first-line
detector (the first ATX heading vanished, YAML frontmatter became a phantom
section, setext titles embedded U+FEFF). preprocess_content now strips one
leading BOM, fixing all formats and keeping the mirror aligned with the parse.
"""

import hashlib
import os
import tempfile
from pathlib import Path

from jdocmunch_mcp.parser import preprocess_content
from jdocmunch_mcp.parser.markdown_parser import parse_markdown
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.index_local import index_local
from jdocmunch_mcp.tools.verify_index import verify_index


def _sections(store, owner, name):
    idx = DocStore(base_path=store).load_index(owner, name)
    out = []
    for s in idx.sections:
        d = s if isinstance(s, dict) else s.__dict__
        out.append((d["byte_start"], d["byte_end"], d["content_hash"], d["level"]))
    return out


# --- #52: CRLF offsets/hashes verify against the real on-disk file -------------

def test_crlf_offsets_and_hashes_match_disk_bytes():
    crlf = b"# Top\r\nIntro line.\r\n\r\n## CRLF Title\r\n\r\nbody line\r\n"
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        corpus = Path(tmp, "corpus")
        corpus.mkdir()
        (corpus / "crlf.md").write_bytes(crlf)
        index_local(path=str(corpus), name="crlf-repro", storage_path=store,
                    use_ai_summaries=False, use_embeddings=False)

        disk = (corpus / "crlf.md").read_bytes()
        for bs, be, h, level in _sections(store, "local", "crlf-repro"):
            assert hashlib.sha256(disk[bs:be]).hexdigest() == h, (
                f"hash for [{bs}:{be}] does not match on-disk bytes")
            if level > 0:
                assert be <= len(disk), f"byte_end {be} past EOF {len(disk)}"

        v = verify_index(repo="local/crlf-repro", storage_path=store)
        assert v.get("drift_count") == 0, v


# --- #53: leading BOM no longer corrupts the parse ----------------------------

def test_preprocess_strips_single_leading_bom():
    assert preprocess_content("\ufeff# Heading\n", "x.md") == "# Heading\n"
    # Only one, and only at the start.
    assert preprocess_content("# H\n\ufeff still here\n", "x.md") == "# H\n\ufeff still here\n"


def test_bom_prefixed_atx_heading_survives_end_to_end():
    raw = b"\xef\xbb\xbf# First Heading\n\nBody.\n\n## Second\n\nMore.\n"
    with tempfile.TemporaryDirectory() as tmp:
        store = os.path.join(tmp, "store")
        corpus = Path(tmp, "corpus")
        corpus.mkdir()
        (corpus / "bom.md").write_bytes(raw)
        index_local(path=str(corpus), name="bom-repro", storage_path=store,
                    use_ai_summaries=False, use_embeddings=False)
        idx = DocStore(base_path=store).load_index("local", "bom-repro")
        titles = [
            (s if isinstance(s, dict) else s.__dict__)["title"] for s in idx.sections
        ]
        assert "First Heading" in titles, titles
        assert "Second" in titles, titles
        # No title carries an invisible BOM.
        assert all("\ufeff" not in t for t in titles)


def test_bom_frontmatter_not_misparsed_as_phantom():
    # With the BOM stripped, frontmatter is detected and its closing --- is not
    # read as a setext underline that manufactures an 'author: Bar' phantom.
    raw = "\ufeff---\ntitle: Foo\nauthor: Bar\n---\n\n# Real Heading\n\nBody.\n"
    titles = [s.title for s in parse_markdown(preprocess_content(raw, "fm.md"),
                                              "fm.md", "local/x")]
    assert "Real Heading" in titles
    assert "author: Bar" not in titles
