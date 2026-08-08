"""The Counter: an adaptive tool surface for jdocmunch-mcp.

Problem this solves
-------------------
jdocmunch exposes ~30+ MCP tools. The host serializes every resident tool's schema
into the model's context on every turn (a fixed per-turn token tax), and the
model must select one tool out of the whole catalog (dispatch dilution).

The Counter is a small, stable front door that fronts the full catalog without
removing any capability:

  * ``order(action, args)`` -- single dispatch verb. Re-enters the normal
    tool pipeline for the chosen action. Read-only by default at the boundary:
    state-changing actions require an explicit opt-in.
  * ``menu(query, limit)`` -- discovery. Search/browse the action catalog and
    return compact entries, so the full schema set need not stay resident.
  * ``route(task, execute)`` -- intent to action. Map a natural-language task
    to the best catalog action(s); optionally dispatch the top one.

This module is pure logic with no server import. server.py owns tool registration,
the live catalog, and call_tool re-dispatch.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# The front-door tool names. These are never themselves dispatchable via order.
FRONT_DOOR: frozenset[str] = frozenset({"order", "menu", "route", "get_tool_details"})

# Actions that change persistent index / session / config state.
STATE_CHANGING_ACTIONS: frozenset[str] = frozenset({
    "index_local",
    "doc_index_repo",
    "delete_index",
    "define_repo_group",
    "tune_weights",
    "check_embedding_drift",
    "finalize_handoff",
})

# Forward-looking tripwire. ``order`` refuses to dispatch any action whose name
# matches one of these verbs. jdocmunch is read-only by charter and ships none.
_FORBIDDEN_VERB_RE = re.compile(
    r"(^|[._-])(exec|shell|run_command|spawn|eval|"
    r"write_file|edit_file|patch|apply_patch|delete_file|rm|mv|chmod)($|[._-])",
    re.IGNORECASE,
)


def is_state_changing(action: str) -> bool:
    return action in STATE_CHANGING_ACTIONS


def forbidden_reason(action: str) -> Optional[str]:
    """Return a rejection reason if *action* matches the exec/write tripwire."""
    if _FORBIDDEN_VERB_RE.search(action or ""):
        return (
            f"'{action}' names a write/exec verb. The Counter is a read-only "
            f"dispatch surface by charter and refuses to route execution or "
            f"file-mutation actions."
        )
    return None


def order_gate(
    action: str,
    catalog_names: Iterable[str],
    allow_state_change: bool,
) -> Optional[str]:
    """Validate an ``order`` request. Return an error string, or None if OK."""
    if not action or not isinstance(action, str):
        return "order requires a non-empty 'action' name. Call 'menu' to list actions."
    if action in FRONT_DOOR:
        return f"'{action}' is a front-door tool and cannot be dispatched through order."
    names = set(catalog_names)
    if action not in names:
        return (
            f"Unknown action '{action}'. Call 'menu' (optionally with a query) "
            f"to discover valid actions."
        )
    tripwire = forbidden_reason(action)
    if tripwire is not None:
        return tripwire
    if is_state_changing(action) and not allow_state_change:
        return (
            f"'{action}' changes index/session state. Re-issue with "
            f"allow_state_change=true to proceed. (Read-only actions need no opt-in.)"
        )
    return None


# --- menu: catalog search -------------------------------------------------- #

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "any", "are", "as", "at", "be", "been", "but", "by",
    "can", "could", "did", "do", "does", "for", "from", "had", "has", "have",
    "how", "i", "if", "in", "into", "is", "it", "its", "just", "me", "my",
    "of", "on", "or", "our", "out", "over", "should", "so", "some", "that",
    "the", "their", "them", "then", "these", "this", "those", "to", "up",
    "us", "was", "we", "were", "what", "when", "where", "which", "will",
    "with", "would", "you", "your",
})

_MIN_SUBSTRING_LEN = 4


def _query_tokens(text: str) -> list[str]:
    """Tokens worth scoring a query on."""
    return [t for t in _tokens(text) if t not in _STOPWORDS]


def _first_sentence(desc: str, limit: int = 160) -> str:
    desc = (desc or "").strip().replace("\n", " ")
    m = re.search(r"(?<=[.!?])\s", desc)
    out = desc[: m.start() + 1] if m else desc
    if len(out) > limit:
        out = out[: limit - 1].rstrip() + "…"
    return out


def _required_args(schema: dict) -> list[str]:
    if not isinstance(schema, dict):
        return []
    req = schema.get("required")
    return list(req) if isinstance(req, list) else []


# Curated example invocations for high-traffic catalog actions.
EXAMPLES: dict[str, dict] = {
    # discovery / indexing
    "doc_index_repo": {"url": "owner/name"},
    "index_local": {"path": "."},
    "doc_list_repos": {},
    "get_index_overview": {"repo": "owner/name"},
    "list_docs": {"repo": "owner/name"},
    "get_doc": {"repo": "owner/name", "doc_path": "docs/readme.md"},
    # outlines / TOC
    "get_toc": {"repo": "owner/name"},
    "get_toc_tree": {"repo": "owner/name"},
    "get_document_outline": {"repo": "owner/name", "doc_path": "docs/readme.md"},
    # section search & retrieval
    "search_sections": {"repo": "owner/name", "query": "authentication"},
    "count_sections": {"repo": "owner/name", "query": "authentication"},
    "search_titles": {"repo": "owner/name", "query": "getting started"},
    "get_section": {"repo": "owner/name", "section_id": "docs/readme.md#installation"},
    "get_sections": {"repo": "owner/name", "section_ids": ["docs/readme.md#installation"]},
    "get_section_context": {"repo": "owner/name", "section_id": "docs/readme.md#installation"},
    "find_similar_sections": {"repo": "owner/name", "section_id": "docs/readme.md#installation"},
    # links / references / impact
    "get_backlinks": {"repo": "owner/name", "doc_path": "docs/readme.md", "section_id": "docs/readme.md#installation"},
    "get_broken_links": {"repo": "owner/name"},
    "get_section_blast_radius": {"repo": "owner/name", "section_id": "docs/readme.md#installation"},
    "check_section_delete_safe": {"repo": "owner/name", "section_id": "docs/readme.md#installation"},
    # health & metrics
    "get_doc_coverage": {"repo": "owner/name", "symbol_ids": ["src/auth.py::login"]},
    "get_stale_pages": {"repo": "owner/name"},
    "get_wiki_stats": {"repo": "owner/name"},
    "doc_health_radar": {"repo": "owner/name"},
    "diff_doc_health_radar": {"baseline": "owner/name", "current": "owner/name"},
    "get_doc_pr_risk_profile": {"repo": "owner/name", "changed_sections": ["docs/readme.md#installation"]},
    "get_watch_status": {},
    # maintenance
    "verify_index": {"repo": "owner/name"},
    "delete_index": {"repo": "owner/name"},
    "finalize_handoff": {
        "repo": "owner/name",
        "task": "Audit documentation",
        "sections": [{"heading": "Findings", "content": "Markdown findings"}],
        "evidence_refs": ["docs/readme.md#installation"],
    },
}


def example_for(name: str) -> Optional[dict]:
    """Curated example args for *name*, or None. Used by menu/route surfaces."""
    ex = EXAMPLES.get(name)
    return dict(ex) if ex is not None else None


from .schema_minifier import (
    json_schema_to_typescript_signature,
    minify_description,
    minify_json_schema,
)


def catalog_entry(name: str, description: str, schema: dict) -> dict:
    """Compact, dense menu row for one action (~10-15 tokens)."""
    min_desc = minify_description(description)
    row = {
        "name": name,
        "action": name,
        "summary": _first_sentence(min_desc),
        "state_changing": is_state_changing(name),
    }
    ex = EXAMPLES.get(name)
    if ex:
        row["example"] = ex
    return row


def get_tool_details(name: str, description: str, schema: dict) -> dict:
    """Full tool details: TypeScript signature, minified parameters, docstrings, required args, and examples."""
    min_desc = minify_description(description)
    min_schema = minify_json_schema(schema) if isinstance(schema, dict) else {}
    sig = json_schema_to_typescript_signature(name, min_schema)
    res = {
        "name": name,
        "action": name,
        "signature": sig,
        "summary": _first_sentence(min_desc),
        "description": min_desc,
        "parameters": min_schema,
        "required": _required_args(min_schema),
        "state_changing": is_state_changing(name),
    }
    ex = EXAMPLES.get(name)
    if ex is not None:
        res["example"] = ex
    return res



def score_action(
    query_tokens: list[str],
    name: str,
    description: str,
    weights: Optional[dict[str, float]] = None,
) -> float:
    """Heuristic relevance of an action to a query. Higher is better."""
    if not query_tokens:
        return 0.0
    name_l = name.lower()
    name_toks = set(_tokens(name))
    desc_toks = set(_tokens(description))
    score = 0.0
    for qt in query_tokens:
        w = weights.get(qt, 1.0) if weights else 1.0
        if qt == name_l:
            score += 10.0 * w
        elif qt in name_toks:
            score += 4.0 * w
        elif len(qt) >= _MIN_SUBSTRING_LEN and qt in name_l:
            score += 1.5 * w
        if qt in desc_toks:
            score += 1.0 * w
    return score


def _idf_weights(query_tokens: list[str], rows: list[dict]) -> dict[str, float]:
    """Inverse document frequency of query tokens across the catalog."""
    import math
    n = max(1, len(rows))
    docs = [set(_tokens(r["action"])) | set(_tokens(r.get("_description", r.get("summary", "")))) for r in rows]
    weights: dict[str, float] = {}
    for qt in set(query_tokens):
        df = sum(1 for d in docs if qt in d)
        weights[qt] = max(0.15, math.log((n + 1) / (df + 1)) + 0.3)
    return weights


def search_catalog(
    catalog: list[dict],
    query: Optional[str],
    limit: int,
) -> list[dict]:
    """Rank/filter catalog rows for *query*."""
    rows = [r for r in catalog if r["action"] not in FRONT_DOOR]
    if not query:
        return rows[:limit]
    qt = _query_tokens(query)
    if not qt:
        return rows[:limit]
    weights = _idf_weights(qt, rows)
    scored = []
    for r in rows:
        s = score_action(qt, r["action"], r.get("_description", r["summary"]), weights)
        if s > 0:
            scored.append((s, r))
    scored.sort(key=lambda x: (-x[0], x[1]["action"]))
    return [r for _, r in scored[:limit]]


# --- route: intent to action ----------------------------------------------- #

_INTENT_RULES: list[tuple[re.Pattern, str, str]] = [
    # Specificity rules for documentation intents
    (re.compile(r"\b(broken|dead|missing)\s+links?\b", re.I),
     "get_broken_links", "Scan for broken cross-references and internal doc links."),
    (re.compile(r"\b(backlinks|referenced by|linked from)\b", re.I),
     "get_backlinks", "Find all pages linking to a specific section."),
    (re.compile(r"\b(blast radius|impact|affect|if i delete|if i edit)\b", re.I),
     "get_section_blast_radius", "Show what links or sections would break if this section changes."),
    (re.compile(r"\b(safe to delete|delete safe)\b", re.I),
     "check_section_delete_safe", "Preflight safety check before deleting a doc section."),
    (re.compile(r"\b(health|radar|risk|stale|coverage)\b", re.I),
     "doc_health_radar", "Documentation health, link validity, and coverage metrics."),

    # Content & Section Search
    (re.compile(r"\b(search|find|query|lookup)\b.*\b(section|content|topic|doc|text)\b", re.I),
     "search_sections", "Search documentation sections by relevance (hybrid BM25+semantic)."),
    (re.compile(r"\b(titles|headings|heading text)\b", re.I),
     "search_titles", "Search section headings and titles."),
    (re.compile(r"\b(count|how many)\b.*\bsections\b", re.I),
     "count_sections", "Count sections matching search criteria without returning content."),

    # Navigation & Structure
    (re.compile(r"\b(toc|table of contents|outline|structure|tree)\b", re.I),
     "get_toc_tree", "Retrieve table of contents tree for indexed documentation."),
    (re.compile(r"\b(list|show|inventory)\s+(docs|documents|files|pages)\b", re.I),
     "list_docs", "List all indexed documentation files in a repository."),
    (re.compile(r"\b(overview|at a glance|summary of repo|stats)\b", re.I),
     "get_index_overview", "Single-call summary overview of indexed docs."),

    # Reading Content
    (re.compile(r"\b(get|read|show|view|fetch)\s+(section|content|heading|body)\b", re.I),
     "get_section", "Fetch full text content for a single section handle."),
    (re.compile(r"\b(bundle|full doc|all sections of)\b", re.I),
     "get_context_bundle", "Fetch all sections for a single document file."),

    # Indexing / Setup
    (re.compile(r"\b(index|ingest|parse)\s+(repo|repository|github)\b", re.I),
     "doc_index_repo", "Index documentation from a GitHub repository."),
    (re.compile(r"\b(index|ingest|parse)\s+(folder|directory|local|path)\b", re.I),
     "index_local", "Index documentation from a local folder."),

    # Broad fallback intent
    (re.compile(r"\b(find|locate|search|where is|read|how to)\b", re.I),
     "search_sections", "Search documentation sections."),
]

_QUERY_ARG: dict[str, str] = {
    "search_sections": "query",
    "search_titles": "query",
    "count_sections": "query",
    "get_doc": "doc_path",
    "get_document_outline": "doc_path",
    "get_context_bundle": "doc_path",
    "index_local": "path",
}


def classify_intent(task: str, catalog_names: Iterable[str]) -> list[dict]:
    """Return ranked recommended actions for a task."""
    names = set(catalog_names)
    out: list[dict] = []
    seen: set[str] = set()
    for pat, action, why in _INTENT_RULES:
        if action in names and action not in seen and pat.search(task or ""):
            out.append({"action": action, "why": why})
            seen.add(action)
    return out


def shape_execute_args(action: str, repo: Optional[str], task: str) -> Optional[dict]:
    """Build best-effort arguments dict for dispatching *action* from (repo, task)."""
    qarg = _QUERY_ARG.get(action)
    if qarg is None:
        if action in ("get_index_overview", "list_docs", "get_toc", "get_toc_tree", "doc_health_radar", "get_broken_links"):
            return {"repo": repo} if repo else None
        return None
    if action in ("get_doc", "get_document_outline", "get_context_bundle"):
        return None
    if action == "index_local":
        return {"path": task or "."}
    if not repo:
        return None
    return {"repo": repo, qarg: task}
