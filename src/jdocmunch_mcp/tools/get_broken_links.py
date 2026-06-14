"""get_broken_links tool: Detect internal cross-references that no longer resolve."""

import os
import posixpath
import re
import time
from typing import Optional

from ..storage import DocStore
from ..parser import ALL_EXTENSIONS

# Links that start with these are external — skip them
_EXTERNAL_SCHEMES = ("http://", "https://", "ftp://", "mailto:", "tel:")
_EMAIL_RE = re.compile(r"^[^\s/@]+@[^\s/@]+\.[^\s/@]+$")
# A URL scheme prefix (scheme:) — used to flag typo'd/unknown schemes (#47.6).
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")

# RST cross-reference patterns: :ref:`target`, :doc:`target`
_RST_REF_RE = re.compile(r":(?:ref|doc):`([^`]+)`")

# RST explicit hyperlink targets: `text <target>`_
_RST_HYPERLINK_RE = re.compile(r"`[^`]+\s+<([^>]+)>`_")

# --- GitHub-rendered anchor namespace (#50) --------------------------------
# Section titles preserve the raw inline markdown, but a renderer emits anchors
# from the heading's rendered TEXT content, then github-slugger rules. Validate
# #anchor links against that namespace, not jdocmunch's private section slugs.
_GH_REDUCTIONS = [
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),   # inline image -> alt
    (re.compile(r"!\[([^\]]*)\]\[[^\]]*\]"), r"\1"),  # reference image -> alt
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),    # inline link -> label
    (re.compile(r"\[([^\]]*)\]\[[^\]]*\]"), r"\1"),   # reference link -> label
    (re.compile(r"`([^`]+)`"), r"\1"),                # code span -> content
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),          # strong -> inner
    (re.compile(r"__([^_]+)__"), r"\1"),
    (re.compile(r"\*([^*]+)\*"), r"\1"),              # emphasis -> inner
]
_GH_SLUG_STRIP_RE = re.compile(r"[^\w\- ]")


def _rendered_text(title: str) -> str:
    """Reduce raw inline markdown to the text content a renderer emits."""
    text = title
    for _ in range(8):  # fixed point; handles nesting like [**x**](y)
        before = text
        for pattern, repl in _GH_REDUCTIONS:
            text = pattern.sub(repl, text)
        if text == before:
            break
    return text


def _github_slug(text: str) -> str:
    """github-slugger base rules: lowercase, drop punctuation except - and _,
    spaces to hyphens; underscores and hyphen runs are preserved."""
    return _GH_SLUG_STRIP_RE.sub("", text.lower()).replace(" ", "-")


def _build_github_anchors(sections: list) -> dict:
    """Map each doc_path to the set of anchors GitHub would render for its
    headings (rendered text + github-slugger, duplicates suffixed -1/-2 in
    document order)."""
    by_doc: dict = {}
    occ: dict = {}
    for sec in sections:
        if sec.get("level", 0) == 0:
            continue  # synthetic doc root has no heading anchor
        doc = sec.get("doc_path", "")
        base = _github_slug(_rendered_text(sec.get("title", "")))
        if not base:
            continue
        d_occ = occ.setdefault(doc, {})
        if base in d_occ:
            d_occ[base] += 1
            anchor = f"{base}-{d_occ[base]}"
        else:
            d_occ[base] = 0
            anchor = base
        by_doc.setdefault(doc, set()).add(anchor)
    return by_doc


def _is_external(href: str) -> bool:
    return any(href.startswith(s) for s in _EXTERNAL_SCHEMES) or bool(_EMAIL_RE.match(href))


def _split_href(href: str) -> tuple:
    """Split href into (file_part, anchor_part). Either may be empty string."""
    if "#" in href:
        file_part, anchor = href.split("#", 1)
    else:
        file_part, anchor = href, ""
    return file_part.strip(), anchor.strip()


def _resolve_file_path(source_doc: str, target_file: str) -> str:
    """Resolve a relative link target against the source document's directory.

    source_doc: e.g.  'docs/guide/install.md'
    target_file: e.g. '../api.md'
    Returns: normalized path like 'docs/api.md'
    """
    if target_file.startswith("/"):
        # Absolute path within the repo root
        return target_file.lstrip("/")
    source_dir = posixpath.dirname(source_doc.replace("\\", "/"))
    joined = posixpath.join(source_dir, target_file.replace("\\", "/"))
    return posixpath.normpath(joined)


def _anchor_matches_section(anchor: str, doc_path: str, sections: list,
                            gh_anchors: Optional[set] = None) -> bool:
    """Return True if any section in doc_path has a slug matching the anchor.

    Comparison is case-insensitive but preserves hyphens and underscores —
    'foo-bar' must NOT match 'foobar'. The hierarchical slug stored in the
    section ID (e.g. ``installation/prerequisites``) is canonical; anchors
    typically reference only the leaf, so we accept either the full path or
    the trailing path segment. ``gh_anchors`` (#50) adds the GitHub-rendered
    anchor namespace for the document so valid rendered anchors aren't flagged.
    """
    target = anchor.strip().lower()
    if not target:
        return False
    if gh_anchors and target in gh_anchors:
        return True
    for sec in sections:
        if sec.get("doc_path") != doc_path:
            continue
        # Section ID format: repo::doc_path::slug#level
        raw_id = sec.get("id", "")
        slug_part = raw_id.split("::")[-1].split("#")[0] if "::" in raw_id else ""
        slug_lower = slug_part.lower()
        if slug_lower == target:
            return True
        # Hierarchical slugs encode ancestor chain ('install/prereqs'); accept the leaf.
        leaf = slug_lower.rsplit("/", 1)[-1]
        if leaf == target:
            return True
        # Also accept the title rendered through the same slugify rules used at parse time.
        from ..parser.sections import slugify
        if slugify(sec.get("title", "")) == target:
            return True
    return False


def get_broken_links(
    repo: str,
    storage_path: Optional[str] = None,
) -> dict:
    """Scan indexed doc files for internal cross-references that no longer resolve.

    Checks:
    - Markdown links [text](target) with relative file paths
    - RST :ref: and :doc: directives
    - Anchor-only links (#heading) within the same doc

    External links (http/https/mailto) are skipped.
    Output: list of {source_file, source_section, source_section_id, target, reason}
    """
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    index = store.load_index(owner, name)

    if not index:
        return {"error": f"Repo not found: {repo}"}

    doc_path_set = set(index.doc_paths)
    sections = index.sections
    src_root = getattr(index, "source_root", "") or ""
    gh_by_doc = _build_github_anchors(sections)  # #50
    broken: list = []

    for sec in sections:
        source_doc = sec.get("doc_path", "")
        sec_id = sec.get("id", "")
        sec_title = sec.get("title", "")
        refs = sec.get("references", [])

        # Collect internal refs from the stored references list
        internal_refs = [r for r in refs if r and not _is_external(r)]

        # Also scan content for RST patterns if content is present
        content = sec.get("content", "")
        if content:
            for m in _RST_REF_RE.finditer(content):
                ref = m.group(1).strip()
                if not _is_external(ref) and ref not in internal_refs:
                    internal_refs.append(ref)
            for m in _RST_HYPERLINK_RE.finditer(content):
                ref = m.group(1).strip()
                if not _is_external(ref) and ref not in internal_refs:
                    internal_refs.append(ref)

        for href in internal_refs:
            file_part, anchor = _split_href(href)

            # Anchor-only link (e.g. #installation): relative to the current document
            if not file_part and anchor:
                if not _anchor_matches_section(anchor, source_doc, sections,
                                               gh_by_doc.get(source_doc)):
                    broken.append({
                        "source_file": source_doc,
                        "source_section": sec_title,
                        "source_section_id": sec_id,
                        "target": href,
                        "reason": "anchor_not_found",
                    })
                continue

            # Skip non-file refs (bare words like "external-project", RST directives without paths)
            if not file_part:
                continue

            # A scheme prefix means a URL. Known external schemes were already
            # filtered; anything still here is an unrecognized/typo'd scheme —
            # a genuinely dead link, not something to silently drop (#47.6).
            if _SCHEME_RE.match(file_part):
                broken.append({
                    "source_file": source_doc,
                    "source_section": sec_title,
                    "source_section_id": sec_id,
                    "target": href,
                    "reason": "unknown_scheme",
                })
                continue

            resolved = _resolve_file_path(source_doc, file_part)

            if resolved not in doc_path_set:
                # Not an indexed doc — but it may be an existing non-doc file
                # (image, LICENSE, source). Stat the filesystem before flagging
                # it missing (#49). With no source_root (e.g. GitHub indexes) we
                # can't stat, so don't claim missing for non-doc extensions.
                if src_root:
                    if os.path.exists(os.path.join(src_root, resolved)):
                        continue
                else:
                    ext = os.path.splitext(resolved)[1].lower()
                    if ext and ext not in ALL_EXTENSIONS:
                        continue
                broken.append({
                    "source_file": source_doc,
                    "source_section": sec_title,
                    "source_section_id": sec_id,
                    "target": href,
                    "reason": "file_not_found",
                })
                continue

            # File exists; now check anchor if present
            if anchor and not _anchor_matches_section(anchor, resolved, sections,
                                                      gh_by_doc.get(resolved)):
                broken.append({
                    "source_file": source_doc,
                    "source_section": sec_title,
                    "source_section_id": sec_id,
                    "target": href,
                    "reason": "section_not_found",
                })

    return {
        "result": {
            "repo": f"{owner}/{name}",
            "docs_scanned": len(doc_path_set),
            "sections_scanned": len(sections),
            "broken_link_count": len(broken),
            "broken_links": broken,
        },
        "_meta": {
            "timing_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
    }
