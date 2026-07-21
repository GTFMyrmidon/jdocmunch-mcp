# Technical Specification

## Overview

**jdocmunch-mcp** pre-indexes documentation files by their heading hierarchy, assigning each section a stable ID and byte offsets for O(1) content retrieval. Agents discover sections via TOC or search, then retrieve only the content they need.

### Token Savings

| Scenario                              | Raw dump        | jDocMunch       | Savings |
| ------------------------------------- | --------------- | --------------- | ------- |
| Browse 50-file doc set structure      | ~100,000 tokens | ~2,000 tokens   | **98%** |
| Find a specific configuration section | ~12,000 tokens  | ~400 tokens     | **97%** |
| Read one section body                 | ~12,000 tokens  | ~300 tokens     | **97.5%** |
| Understand a module's public API docs | ~8,000 tokens   | ~500 tokens     | **93.7%** |

---

## MCP Tools (11)

### Indexing Tools

#### `index_local` — Index a local documentation folder

```json
{
  "path": "/path/to/docs",
  "use_ai_summaries": true,
  "extra_ignore_patterns": ["drafts/**"],
  "follow_symlinks": false,
  "incremental": true
}
```

`incremental` (default `true`): only re-parse files whose content hash changed since the last index. Set to `false` to force a full re-index.

Walks the local directory with full security controls: path traversal prevention, symlink escape protection, secret detection, binary filtering, `.gitignore` respect, and directory pruning. Parses `.md`, `.mdx`, `.markdown`, `.txt`, and `.rst` files.

#### `index_repo` — Index a GitHub repository's documentation

```json
{
  "url": "owner/repo",
  "ref": "optional-branch-tag-or-commit",
  "name": "optional-safe-storage-name",
  "use_ai_summaries": true,
  "incremental": true
}
```

Fetches documentation files via the GitHub API, parses sections, and saves to local storage.

`ref` (optional): selects the GitHub branch, tag, or commit-ish to index. If omitted, `HEAD` is used. Explicit refs are resolved to a 40-hex commit SHA before fetching tree/content; unresolved explicit refs return an error instead of falling back to `HEAD`. Durable handles stay commit-SHA based via `repo_at_sha`.

`name` (optional): stores the index under `owner/name` instead of `owner/repo`. This is a storage-name override, not a moving alias. The value must be a single safe storage component using only letters, numbers, dot, underscore, and hyphen; `/`, `\`, and `@` are rejected. GitHub indexing responses include the upstream source identity as `source_repo`, and certified indexes include both `repo_at_sha` for the stored index and `source_repo_at_sha` for the upstream GitHub repository.

`incremental` (default `true`): first checks the selected GitHub ref's commit SHA — if it matches the stored SHA the call returns immediately without any file fetches. If the SHA differs, only changed files are re-parsed. Set to `false` to force a full re-index.

#### `delete_index` — Delete index for a repository

```json
{
  "repo": "owner/repo"
}
```

Deletes both the index JSON and the raw content cache directory.

---

### Discovery Tools

#### `list_repos` — List indexed documentation sets

No input required. Returns all indexed repositories with section counts, document counts, document type breakdown, and commit metadata when available. Clean Git-backed indexes include `repo_at_sha`, an immutable handle in the form `owner/repo@40hexsha`.

#### `doc_resolve_repo` — Resolve a filesystem path to its doc-index handle

```json
{
  "path": "C:\\projects\\project-docs\\guides\\intro.md"
}
```

Given an index root, subfolder, or file path, returns the matching documentation repo handle via stored `source_root` metadata — a response whose size is independent of how many indexes exist. Prefer this over `list_repos` when the path is known. Exact root match wins, then the most specific containing root; equally-specific duplicates return `ambiguous: true` with a bounded `candidates` list (max 5) plus `total_matches`. A path outside every index returns a compact not-found result. GitHub-indexed corpora (no `source_root`) never match. Read-only: never creates, refreshes, or deletes an index. Absolute paths are recommended; relative paths resolve against the server's working directory, echoed back as `_meta.resolved_path`.

**Linked Git worktrees (jdoc#83):** when no root matches but the path belongs to a linked-worktree family with an established index for the same repository-relative corpus location, the not-found response additively carries `canonical_candidates` (bounded, max 5) and a `worktree_resolution` object (`status`, `reason_code`, `identity` with lineage/relative-location/selection evidence states, `freshness`, `write_policy`, `did_write`, `next_action`). The requested path stays `found: false` / `indexed: false`, and selection evidence is reported as `unavailable` — a path alone never proves durable-selection identity. Discovery is strictly read-only and fails closed on any Git-evidence failure.

`index_local` applies the same resolver as a gate: an equivalent corpus in a linked worktree with a proven-fresh established index (same certified revision, no relevant uncommitted docs) is **reused** — the established handle is returned, nothing is created or refreshed. Stale, dirty, ambiguous, legacy-unresolved, or evidence-incomplete outcomes return a bounded decision with no persistent write. `worktree_mode="branch_local"` opts out and intentionally creates an exact-path index for the requesting worktree. Cross-worktree creation races contend on one lineage-keyed claim, extending the jdoc#82 single-winner rule.

#### `get_toc` — Flat table of contents

```json
{
  "repo": "owner/repo"
}
```

Returns all sections in document order with their IDs, titles, levels, and summaries. Content is excluded — use `get_section` to retrieve full content.

#### `get_toc_tree` — Nested table of contents tree

```json
{
  "repo": "owner/repo"
}
```

Returns sections organized by document, with parent/child heading relationships visible. Content excluded.

#### `get_document_outline` — Section hierarchy for one document

```json
{
  "repo": "owner/repo",
  "doc_path": "docs/configuration.md"
}
```

Returns the heading hierarchy for a single file without content. Lighter than `get_toc` when you already know which document is relevant.

---

### Search Tools

#### `search_sections` — Weighted section search

```json
{
  "repo": "owner/repo",
  "query": "authentication",
  "doc_path": "docs/security.md",
  "max_results": 10
}
```

Weighted scoring across title, summary, tags, and content. Returns summaries only — use `get_section` for full content. `doc_path` is optional; omit to search all documents. `repo` accepts the normal `owner/repo` identifier or a strict `owner/repo@40hexsha` handle; the latter resolves only when the stored index is clean and matches that exact commit.

---

### Retrieval Tools

#### `get_section` — Retrieve full content of one section

```json
{
  "repo": "owner/repo",
  "section_id": "owner/repo::docs/install.md::installation#1",
  "verify": true
}
```

Retrieves section source via byte-offset seeking (O(1)). Optional `verify` re-hashes the retrieved content and compares it to the stored `content_hash`. The response field `section.hash_verified` will be `true` if the cached file matches the stored hash, `false` if the cache has been modified since indexing. This is **cache integrity verification**, not live-source drift detection.

#### `get_section_context` — Retrieve a section with its hierarchy context

```json
{
  "repo": "owner/repo",
  "section_id": "owner/repo::docs/install.md::installation/prerequisites#3",
  "max_tokens": 2000,
  "include_children": true
}
```

Returns three components:
- **`ancestors`**: list of `{id, title, level}` dicts from root down to the immediate parent — provides orientation without bulk content
- **`section`**: the target section with full content (byte-range read, capped by `max_tokens`)
- **`children`**: immediate child section summaries (no content reads), included when `include_children=true`

Prevents "section too thin to answer" failures without falling back to whole-file reads.

#### `get_sections` — Batch retrieve multiple sections

```json
{
  "repo": "owner/repo",
  "section_ids": ["id1", "id2", "id3"],
  "verify": false
}
```

Returns a list of sections with full content, plus an error list for any IDs not found.

---

## Data Models

### Section

```python
@dataclass
class Section:
    id: str            # "{repo}::{doc_path}::{slug}#{level}"
    repo: str
    doc_path: str      # Relative path of the source document
    title: str         # Heading text
    content: str       # Full section text (heading + body, including subsections)
    level: int         # 1–6 (ATX heading level); 0 = pre-first-heading root section
    parent_id: str     # Section ID of parent heading; "" if top-level
    children: list     # List of child section IDs
    byte_start: int    # Start byte offset in the cached raw file
    byte_end: int      # End byte offset in the cached raw file
    summary: str       # One-sentence summary (heading text / AI / fallback)
    tags: list         # #hashtag tags extracted from content
    references: list   # URLs and markdown link targets extracted from content
    content_hash: str  # SHA-256 of section content (drift detection)
```

### DocIndex

```python
@dataclass
class DocIndex:
    repo: str              # "owner/repo"
    owner: str
    name: str
    indexed_at: str        # ISO timestamp
    doc_paths: list        # Sorted list of indexed document paths
    doc_types: dict        # {".md": 12, ".txt": 3}
    sections: list         # Serialized Section dicts (metadata only — no content field)
    index_version: int     # Schema version (current: 3); mismatch triggers full re-index
    file_hashes: dict      # {doc_path: SHA-256} for incremental change detection
    head_sha: str          # HEAD commit SHA when known (GitHub or local Git indexes)
    source_dirty: bool     # True when cached content is not certified clean at head_sha
    sha_certified: bool    # True when the corpus was built under strict repo@sha rules
    source_root: str       # Absolute source folder for local indexes, if known
    source_repo: str       # Original upstream GitHub repo for named GitHub indexes
```

`DocStore` persists each `DocIndex` as JSON plus cached raw document files.
`repo_at_sha` is derived, not stored: it is emitted only when `head_sha` is a 40-hex commit SHA, `source_dirty` is false, and `sha_certified` is true. Surgical `index_file` updates never certify a legacy, dirty, moved-HEAD, untracked-path, or no-longer-Git-backed local index; run `index_local` to recertify the full corpus.

For local Git indexes, "clean" means the *indexed corpus* is reproducible at `head_sha`, not that the source folder is pristine. `source_dirty` is set when tracked content within the indexed scope differs from HEAD, when HEAD moves mid-index, or when any indexed path is not tracked by Git — including a gitignored file indexed explicitly via `paths=[...]`, which `git status` cannot see and which is caught by a separate `git ls-files` tracked-ness check. Untracked files that are not part of the index — unsupported extensions, build artifacts, scratch files — do not affect certification. Git state is probed with short-lived `git` subprocesses bounded by `JDOCMUNCH_GIT_TIMEOUT` (seconds, default 10; set to a value `<= 0` to disable the ceiling); a timed-out or otherwise failed probe is treated as dirty so an immutable handle is never emitted for an unknown state.

---

## File Discovery

### GitHub Repositories

Fetches via GitHub API. `.gitignore` is fetched and respected (if present in the repo root).

### Local Folders

Recursive directory walk using `os.walk` with early directory pruning to skip `SKIP_PATTERNS` before descending.

### Filtering Pipeline (Both Paths)

1. **Skip patterns** — `node_modules/`, `vendor/`, `venv/`, `.venv/`, `__pycache__/`, `dist/`, `build/`, `.git/`, `.tox/`, `.mypy_cache/`, `.gradle/`, `target/`
2. **`.gitignore`** — respected via the `pathspec` library
3. **`extra_ignore_patterns`** — user-supplied gitignore-style patterns (local only)
4. **Extension filter** — must be in `ALL_EXTENSIONS` (`.md`, `.markdown`, `.mdx`, `.txt`, `.rst`)
5. **Secret detection** — `.env`, `*.pem`, `*.key`, credentials files excluded
6. **Binary detection** — extension-based + null-byte content sniffing
7. **Size limit** — 500 KB per file
8. **File count limit** — 500 files max

---

## Section ID Format

```
{repo}::{doc_path}::{slug}#{level}
```

Examples:

```
owner/repo::README.md::installation#1
owner/repo::docs/config.md::authentication-options#2
local/myproject::guide.md::quick-start#1
```

**Slug:** heading text is lowercased and non-alphanumeric sequences replaced with hyphens, then **prefixed with the ancestor slug chain** to form a hierarchical path. For example, `### Prerequisites` under `## Installation` becomes `installation/prerequisites`. This makes IDs stable under sibling insertions: adding a new same-named heading in one branch of the document does not renumber IDs in another branch.

Section IDs are returned by `get_toc`, `get_toc_tree`, `get_document_outline`, and `search_sections`. Pass them to `get_section`, `get_sections`, or `get_section_context` to retrieve content.

---

## Response Envelope

Search and retrieval tools return a `_meta` object:

```json
{
  "_meta": {
    "latency_ms": 12,
    "sections_returned": 5,
    "tokens_saved": 1840,
    "total_tokens_saved": 94320,
    "cost_avoided": {
      "claude_opus": 0.0276,
      "gpt5_latest": 0.0184
    },
    "total_cost_avoided": {
      "claude_opus": 1.4148,
      "gpt5_latest": 0.9432
    }
  }
}
```

- **`tokens_saved`**: Tokens saved this call (raw bytes of matched docs vs response bytes, ÷ 4)
- **`total_tokens_saved`**: Cumulative tokens saved, persisted to `~/.doc-index/_savings.json`
- **`cost_avoided`**: Dollar value saved this call, valued at current model input rates (`storage/token_tracker.PRICING` is the authoritative table)
- **`total_cost_avoided`**: Cumulative cost avoided across all sessions

Present on: `search_sections`, `get_section`, `get_sections`.

---

## Reconciliation Status & Reason Codes

The complete runtime vocabulary for corpus-identity resolution and
provisional-index reconciliation (jdoc#80 arc; the published table required by
#84 item 4). Every value below is drift-guarded: a test fails if the runtime
can emit a status or reason code not listed here.

**`worktree_resolution.status`** (read-time resolution, jdoc#83):

| status | Meaning |
|---|---|
| `exact` | The requested path is an indexed corpus root. |
| `created` | A new index was created for this corpus. |
| `reusable` | An established index for this corpus identity exists; its handle is returned without a write. |
| `reference_only` | An equivalent index exists but cannot be written from this location. |
| `ambiguous` | More than one stored index matches; no write, candidates listed. |
| `related` | Same repository lineage, different corpus location or selection. |
| `unknown` | Identity evidence unavailable; nothing asserted. |
| `no_match` | No stored index relates to this path. |

**`reconciliation.reason_code`** (index-time quarantine, graduation, and
supersession; jdoc#80 Parts B/C, #85, #86):

| reason_code | Outcome |
|---|---|
| `provisional_verification_unavailable` | Git verification could not run; the index was created authority-free (provisional). |
| `provisional_cap_exceeded` | Too many provisional indexes for one source root; creation refused. |
| `graduated_verified` | Git lineage confirmed on a full refresh; the provisional index was promoted in place. |
| `reconciled_to_established` | Exact duplicate proven (verified identity + per-file hash equality); the provisional was retired and the established handle returned. |
| `graduation_ambiguous` | More than one established peer matches; kept provisional, nothing removed. |
| `graduation_content_diverged` | The provisional holds documents the established index lacks; kept provisional, nothing removed. |
| `graduation_content_differs` | Same paths, different or unprovable content; not a duplicate — both kept, differing files listed. |
| `superseded_by_established` | Git proved the provisional snapshot a strict ancestor of the established one (both certified clean); the older provisional was retired. |
| `provisional_newer_than_established` | Git proved the provisional snapshot strictly newer; the established index is never replaced automatically — both kept, explicit refresh path reported. |
| `supersession_conflict` | The target or candidate set changed before retirement; nothing removed, retry safe. |
| `supersession_cleanup_incomplete` | Supersession proven but retirement did not complete; the provisional remains discoverable, retry idempotent. |

**`legacy_reconciliation.reason_code`** (Part C.2 — explicit-intent
reconciliation of genuine pre-1.102 fieldless legacy indexes; jdoc#87.
Fires only under `index_local(legacy_reconcile="report"|"apply")`; an
ordinary refresh stays backfill-only and never retires anything):

| reason_code | Outcome |
|---|---|
| `legacy_reconcile_not_applicable` | A precondition failed (no explicit name, handle missing or not fieldless at call start, subset refresh, branch_local, provisional target, or unconfirmed Git lineage); refused fail-closed, nothing written. |
| `legacy_reconcile_no_modern_peer` | No non-provisional modern peer matches the verified corpus identity; the legacy index was refreshed and kept (an ordinary refresh backfills it), nothing removed. |
| `legacy_reconcile_ambiguous` | More than one modern peer matches; retirement requires exactly one, nothing removed. |
| `legacy_reconcile_uncertified` | The two indexes are not both clean and certified at the same commit; nothing removed. |
| `legacy_reconcile_content_differs` | A selected-handle path is missing from the peer or its stored hash differs (or is unprovable); not a duplicate — both kept, differing files listed. |
| `legacy_reconcile_ready` | Report mode: proof passed (single peer, same clean certified commit, full path-and-hash coverage); nothing was changed. |
| `legacy_reconciled_to_established` | Apply mode: proof repeated immediately before retirement; the selected legacy handle was retired, the peer is unchanged and its handle returned. |
| `legacy_reconcile_conflict` | The peer or candidate set changed between proof and retirement; nothing removed, retry safe. |
| `legacy_reconcile_cleanup_incomplete` | Retirement proven but removal did not complete; the legacy index remains discoverable, retry idempotent. |

---

## Error Handling

All errors return:

```json
{
  "error": "Human-readable message"
}
```

| Scenario                           | Behavior                                              |
| ---------------------------------- | ----------------------------------------------------- |
| Repository not found (GitHub 404)  | Error with message                                    |
| Rate limited (GitHub 403)          | Error with message; suggest setting `GITHUB_TOKEN`    |
| File fetch fails                   | File skipped; indexing continues                      |
| Parse fails (single file)          | File skipped with warning; indexing continues         |
| No documentation files found      | Error returned                                        |
| No sections extracted              | Error returned                                        |
| Section ID not found               | Error in per-section error list                       |
| Repository not indexed             | Error suggesting indexing first                       |
| AI summarization fails             | Falls back to title fallback                          |
| Index version mismatch             | Old index ignored; full re-index required             |

---

## Environment Variables

| Variable                          | Purpose                                                              | Required |
| --------------------------------- | -------------------------------------------------------------------- | -------- |
| `GITHUB_TOKEN`                    | GitHub API authentication (higher limits, private repos)             | No       |
| `ANTHROPIC_API_KEY`               | AI summarization via Claude Haiku (takes priority)                   | No       |
| `GOOGLE_API_KEY`                  | AI summarization via Gemini Flash; also Gemini embeddings            | No       |
| `OPENAI_API_KEY`                  | OpenAI embeddings (text-embedding-3-small)                           | No       |
| `JDOCMUNCH_EMBEDDING_PROVIDER`    | Force embedding provider: `gemini`, `openai`, `openai-compatible`, `sentence-transformers`, or `none` | No |
| `JDOCMUNCH_OPENAI_COMPAT_URL`      | Endpoint URL for `openai-compatible` embeddings                      | No       |
| `JDOCMUNCH_OPENAI_COMPAT_MODEL`   | Model for `openai-compatible` embeddings                             | No       |
| `JDOCMUNCH_OPENAI_COMPAT_API_KEY` | Dedicated optional API key for `openai-compatible` embeddings        | No       |
| `JDOCMUNCH_OPENAI_COMPAT_BATCH_SIZE` | Batch size for `openai-compatible` embeddings (default: `32`)      | No       |
| `JDOCMUNCH_ST_MODEL`              | sentence-transformers model name (default: `all-MiniLM-L6-v2`)      | No       |
| `DOC_INDEX_PATH`                  | Custom storage path (default: `~/.doc-index/`)                       | No       |
| `JDOCMUNCH_GIT_TIMEOUT`           | Per-call `git` subprocess ceiling in seconds for local repo@sha probing (default: `10`; `<= 0` disables) | No |
| `JDOCMUNCH_SHARE_SAVINGS`         | Set to `0` to disable anonymous token savings reporting              | No       |
