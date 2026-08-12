"""Result-row projection + snippet inlining for search_sections (jdoc#101).

Reported by @vondecron: a default `search_sections` row spends ~44% of its
bytes on fields the calling agent cannot act on (a 64-hex content_hash, a
parent_id derivable from `id`, a summary byte-identical to the title, empty
collections, internal byte offsets, a per-row `repo` already in the envelope),
and carries no content at all — so even a perfect top hit costs a second
`get_section` round-trip.

Two independent knobs, both opt-in, default behavior byte-identical:

  compact=True    drop the dead-weight fields (see COMPACT_DROP below)
  fields=[...]    explicit whitelist, full control, wins over compact
  snippet_bytes=N inline the first N bytes of section content

Projection runs LAST in search_sections — after every filter, after
attach_scores, after the ranking/replay logs — because those consumers read
fields this drops ([[feedback_strip_a_field_after_its_consumer_reads_it]]).
"""

from typing import Callable, Optional

# `id` is the handle every follow-up call needs; a projection that drops it
# produces rows nothing can act on, so it survives even an explicit `fields`.
ALWAYS_KEEP = frozenset({"id"})

# Fields compact mode removes unconditionally. Each is either derivable,
# duplicated in the response envelope, or an internal offset.
COMPACT_DROP = frozenset({
    "repo",           # identical on every row; already payload["repo"]
    "parent_id",      # derivable from id; section_neighbors serves it properly
    "children",       # navigation, not ranking; get_section_descendants owns it
    "byte_start",     # internal offsets; get_section reads by id
    "byte_end",
    "content_hash",   # 64 hex chars a model cannot act on
    "inline_code",    # extracted fragments; find_code_examples owns them
    "references",     # link targets; get_backlinks owns them
    # A full code dump with its own byte offsets, inside a tool whose own
    # description says "summaries only". find_code_examples serves these
    # properly; snippet_bytes= is the way to get body text out of a search.
    "code_blocks",
})

# Fields compact mode removes only when they carry no information: an empty
# collection, or a value duplicating another field on the same row.
def _is_noise(key: str, value, row: dict) -> bool:
    if key in ("tags", "roles") and not value:
        return True
    if key == "summary":
        # Heading-derived summaries are byte-identical to the title.
        return isinstance(value, str) and value.strip() == str(row.get("title", "")).strip()
    if key == "_freshness":
        # Keep the per-row signal exactly where it differs from the happy
        # path; `fresh` on every row is what _meta.freshness already says.
        return value == "fresh"
    return False


def project_row(row: dict, *, compact: bool = False,
                fields: Optional[list] = None,
                extra_keep: frozenset = frozenset()) -> dict:
    """Return a projected copy of one result row. Never mutates `row`.

    `extra_keep` names fields this caller must not lose regardless of mode —
    a repo_group fan-out passes `repo`, which is dead weight in a single-repo
    response but the only thing telling two members' rows apart.
    """
    keep = ALWAYS_KEEP | extra_keep
    if fields:
        wanted = {str(f) for f in fields if isinstance(f, str) and f.strip()}
        wanted |= keep
        return {k: v for k, v in row.items() if k in wanted}
    if not compact:
        return dict(row)
    out = {}
    for k, v in row.items():
        if k in keep:
            out[k] = v
            continue
        if k in COMPACT_DROP or _is_noise(k, v, row):
            continue
        out[k] = v
    return out


def project(rows: list, *, compact: bool = False,
            fields: Optional[list] = None,
            extra_keep: frozenset = frozenset()) -> list:
    """Project a result list. Returns `rows` unchanged when nothing is asked."""
    if not compact and not fields:
        return rows
    return [project_row(r, compact=compact, fields=fields,
                        extra_keep=extra_keep) for r in rows]


def _truncate_utf8(text: str, max_bytes: int) -> tuple:
    """Cut `text` to at most max_bytes UTF-8 bytes without splitting a
    codepoint. Returns (snippet, truncated)."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text, False
    # errors="ignore" drops a trailing partial sequence rather than raising —
    # matters for the CJK corpora jdoc indexes, where one char is 3 bytes.
    return raw[:max_bytes].decode("utf-8", errors="ignore"), True


def attach_snippets(rows: list, *, snippet_bytes: int,
                    loader: Callable[[dict], str]) -> None:
    """Inline the first `snippet_bytes` bytes of each row's section content.

    In-place. A row whose content cannot be loaded gets no `snippet` key at
    all — an empty string would read as "this section is empty", which is a
    different claim than "we could not read it".
    """
    if not snippet_bytes or snippet_bytes <= 0:
        return
    for row in rows:
        try:
            text = loader(row)
        except Exception:
            continue
        if not text:
            continue
        snippet, truncated = _truncate_utf8(text, int(snippet_bytes))
        if not snippet:
            continue
        row["snippet"] = snippet
        if truncated:
            row["snippet_truncated"] = True
