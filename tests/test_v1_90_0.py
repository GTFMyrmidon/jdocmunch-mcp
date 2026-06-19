"""Tests for v1.90.0 — get_section(verify=true) source vs response hashing (#70).

`get_section` / `get_sections` applied response-only transforms
(`compress_code`, `strip_boilerplate`) BEFORE computing `hash_verified`, so a
transformed read could report `hash_verified: false` even though the indexed raw
section still matched the stored content hash. That overloaded the flag to mean
both "index is stale" and "the response was transformed".

Verification is now computed against the RAW indexed bytes: `hash_verified`
(and the explicit alias `source_hash_verified`) certify source integrity and are
not flipped false by a transform. When the response was transformed,
`response_transformed` / `transformations` disclose it and
`response_hash_matches_content_hash` reports the returned-byte identity
separately.
"""

from __future__ import annotations

from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.get_section import get_section
from jdocmunch_mcp.tools.get_sections import get_sections
from jdocmunch_mcp.tools.index_local import index_local


_DOC = (
    "# Guide\n\n"
    "## Code\n\n"
    "```python\n"
    "# leading comment line\n"
    "import os\n"
    "\n"
    "\n"
    "def go():\n"
    "    return os.getcwd()\n"
    "```\n"
)


def _setup(tmp_path):
    docs = tmp_path / "wiki"
    docs.mkdir()
    (docs / "guide.md").write_text(_DOC, encoding="utf-8")
    storage = str(tmp_path / "store")
    res = index_local(path=str(docs), use_ai_summaries=False, storage_path=storage)
    assert res["success"], res
    repo = res["repo"]
    owner, name = DocStore(base_path=storage)._resolve_repo(repo)
    idx = DocStore(base_path=storage).load_index(owner, name)
    code_sid = next(s["id"] for s in idx.sections if s.get("code_blocks"))
    return repo, storage, code_sid


def test_untransformed_verify_is_true(tmp_path):
    repo, storage, sid = _setup(tmp_path)
    r = get_section(repo=repo, section_id=sid, verify=True, storage_path=storage)
    sec = r["section"]
    assert sec["hash_verified"] is True, sec
    assert sec["source_hash_verified"] is True
    assert "response_transformed" not in sec
    assert "response_hash_matches_content_hash" not in sec


def test_compressed_read_keeps_source_hash_verified(tmp_path):
    repo, storage, sid = _setup(tmp_path)
    r = get_section(
        repo=repo, section_id=sid, verify=True, compress_code=True, storage_path=storage,
    )
    sec = r["section"]
    # The transform actually removed bytes (otherwise this test proves nothing).
    assert r["_meta"]["code_compressed_bytes"] > 0, r["_meta"]
    # Source integrity is intact and NOT reported as drift.
    assert sec["hash_verified"] is True, sec
    assert sec["source_hash_verified"] is True
    # The transform is disclosed, and response-byte identity is separate + false.
    assert sec["response_transformed"] is True
    assert sec["transformations"] == ["compress_code"]
    assert sec["response_hash_matches_content_hash"] is False


def test_compressed_read_matches_untransformed_verdict(tmp_path):
    """Regression: transformed and untransformed reads agree on source integrity."""
    repo, storage, sid = _setup(tmp_path)
    plain = get_section(repo=repo, section_id=sid, verify=True, storage_path=storage)
    comp = get_section(
        repo=repo, section_id=sid, verify=True, compress_code=True, storage_path=storage,
    )
    assert plain["section"]["hash_verified"] == comp["section"]["hash_verified"] is True


def test_no_verify_still_discloses_transform(tmp_path):
    repo, storage, sid = _setup(tmp_path)
    r = get_section(
        repo=repo, section_id=sid, verify=False, compress_code=True, storage_path=storage,
    )
    sec = r["section"]
    assert sec["response_transformed"] is True
    assert sec["transformations"] == ["compress_code"]
    # Verification fields are verify-only.
    assert "hash_verified" not in sec
    assert "response_hash_matches_content_hash" not in sec


def test_get_sections_batch_same_contract(tmp_path):
    repo, storage, sid = _setup(tmp_path)
    r = get_sections(
        repo=repo, section_ids=[sid], verify=True, compress_code=True, storage_path=storage,
    )
    sec = r["sections"][0]["section"]
    assert sec["hash_verified"] is True
    assert sec["source_hash_verified"] is True
    assert sec["response_transformed"] is True
    assert sec["transformations"] == ["compress_code"]
    assert sec["response_hash_matches_content_hash"] is False
