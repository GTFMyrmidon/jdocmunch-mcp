"""Byte-range content retrieval for one section."""

import hashlib
import os
import time
from typing import Optional

from ..storage import DocStore
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided


def get_section(
    repo: str,
    section_id: str,
    verify: bool = False,
    strip_boilerplate: bool = False,
    compress_code: bool = False,
    storage_path: Optional[str] = None,
) -> dict:
    """Retrieve the full content of a single section using byte-range reads.

    Args:
        repo: Repository identifier.
        section_id: Section ID from get_toc, search_sections, etc.
        verify: If True, verify the RAW indexed section against the stored
            content hash and report it as ``hash_verified`` (and the explicit
            alias ``source_hash_verified``). This is a source-integrity check:
            it is NOT flipped false by ``compress_code`` / ``strip_boilerplate``
            response transforms. When the response was transformed, a separate
            ``response_hash_matches_content_hash`` reports whether the returned
            (transformed) bytes still hash to the stored content hash.
        strip_boilerplate: If True, strip cross-section repeated fragments.
        compress_code: v1.35+ — if True, drop blank lines and full-line
            comments inside fenced code blocks before returning. The
            on-disk content is never mutated; only the response copy is
            compressed. Bytes saved are reported in
            ``_meta.code_compressed_bytes``.
        storage_path: Custom storage path.

    Returns:
        Dict with section content and metadata.
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    sec = index.get_section(section_id)
    if not sec:
        return {"error": f"Section not found: {section_id}"}

    content = store.get_section_content(owner, name, section_id, _index=index)
    if content is None:
        return {"error": f"Content not available for section: {section_id}"}

    # jdoc#70: capture the raw indexed bytes BEFORE any response-only transform
    # so verification certifies source integrity, not the transformed copy.
    raw_content = content

    boilerplate_stripped_bytes = 0
    if strip_boilerplate:
        from ..retrieval.boilerplate import load as _load_bp, strip as _strip_bp
        fragments = _load_bp(storage_path, owner, name)
        if fragments:
            content, boilerplate_stripped_bytes = _strip_bp(content, fragments)

    code_compressed_bytes = 0
    if compress_code:
        from ..retrieval.code_compress import compress_fenced_code as _compress
        content, code_compressed_bytes = _compress(content)

    # Which response-only transforms actually changed the returned bytes.
    transformations = []
    if strip_boilerplate and boilerplate_stripped_bytes > 0:
        transformations.append("strip_boilerplate")
    if compress_code and code_compressed_bytes > 0:
        transformations.append("compress_code")

    # Strip the raw embedding vector — it's an internal index artifact, not
    # API-consumer payload, and a 384-dim float list is ~2,000 tokens (issue #11).
    result_sec = {k: v for k, v in sec.items() if k not in ("content", "embedding")}
    result_sec["content"] = content
    if transformations:
        result_sec["response_transformed"] = True
        result_sec["transformations"] = transformations

    if verify:
        stored_hash = sec.get("content_hash", "")
        # jdoc#70: hash_verified certifies the RAW indexed section against the
        # stored content_hash. It must NOT flip false merely because a
        # response-only transform (compress_code/strip_boilerplate) changed the
        # returned bytes. source_hash_verified is the explicit name for the same
        # source-integrity check.
        source_hash = hashlib.sha256(raw_content.encode("utf-8")).hexdigest()
        source_verified = (source_hash == stored_hash) if stored_hash else None
        result_sec["hash_verified"] = source_verified
        result_sec["source_hash_verified"] = source_verified
        if transformations:
            # The intentionally-transformed response bytes won't match the raw
            # content_hash; expose that separately rather than overloading
            # hash_verified with a transform artifact.
            response_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            result_sec["response_hash_matches_content_hash"] = (
                (response_hash == stored_hash) if stored_hash else None
            )

    # Token savings: raw file size vs this section's bytes
    doc_path = sec.get("doc_path", "")
    raw_bytes = 0
    try:
        raw_file = store._safe_content_path(store._content_dir(owner, name), doc_path)
        if raw_file:
            raw_bytes = os.path.getsize(raw_file)
    except OSError:
        pass
    response_bytes = len(content.encode("utf-8"))
    tokens_saved = estimate_savings(raw_bytes, response_bytes)
    total = record_savings(tokens_saved, storage_path)
    ca = cost_avoided(tokens_saved, total)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    meta = {
        "latency_ms": latency_ms,
        "sections_returned": 1,
        "tokens_saved": tokens_saved,
        **ca,
    }
    if strip_boilerplate:
        meta["boilerplate_stripped_bytes"] = boilerplate_stripped_bytes
    if compress_code:
        meta["code_compressed_bytes"] = code_compressed_bytes
    # v1.32.0: citation block — verifiable provenance for the returned content.
    meta["citation"] = {
        "repo": f"{owner}/{name}",
        "doc_path": sec.get("doc_path", ""),
        "section_id": section_id,
        "byte_start": int(sec.get("byte_start", 0) or 0),
        "byte_end": int(sec.get("byte_end", 0) or 0),
        "content_hash": sec.get("content_hash", ""),
        "indexed_at": index.indexed_at,
    }
    # Per-section freshness + a verdict, the same honesty contract the search
    # tools have carried since v1.16.0. A content read is a claim about what
    # the file holds RIGHT NOW, so a caller deserves to know whether the bytes
    # we served still match the source — and to be told when we could not
    # establish that, rather than being left to assume.
    from ..retrieval.freshness import FreshnessProbe
    from ..retrieval.verdict import section_verdict_for_index

    # ⚠ jdoc#71: the DEFAULT probe compares the index against jdoc's own cached
    # content mirror, which does not change when the workspace file does. A
    # reading taken that way answers `fresh` for a file that has been edited —
    # or deleted — so for a content read, which is a claim about the file right
    # now, the live-source layer is the only one that answers the question.
    # Which layer answered is DISCLOSED, never assumed.
    _source_root = getattr(index, "source_root", "") or ""
    _use_live = bool(_source_root) and os.path.isdir(_source_root)
    _probe = FreshnessProbe(
        store, owner, name, index,
        source_root=_source_root if _use_live else None,
    )
    _probe.annotate(result_sec)
    meta["freshness"] = _probe.summary([result_sec])
    meta["drift_layer"] = "live_source" if _use_live else "cached_mirror"
    meta["verdict"] = section_verdict_for_index(
        index, found_count=1, freshness=result_sec.get("_freshness")
    )
    return {
        "section": result_sec,
        "_meta": meta,
    }
