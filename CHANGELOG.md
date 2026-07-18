# Changelog

All notable changes to jdocmunch-mcp by release. Generated from git history via `scripts/generate_changelog.py`. See [README.md](./README.md) for the 1.x compatibility commitment.

## v1.100.0 — 2026-07-18

**corpus identity: index_local won't duplicate an equivalent source (#81)**

Item A of the #80 identity meta-issue, spec by @rknighton — index-time
prevention complementing doc_resolve_repo's read-time detection (#79).

## v1.99.0 — 2026-07-18

**doc_resolve_repo: path→doc-index handle lookup (#79)**

Reported by @rknighton. New read-only doc_resolve_repo(path) resolves an
index root, subfolder, or file to its documentation repo handle via stored
source_root metadata — an O(1)-sized response instead of the full
doc_list_repos listing. Exact root match, then most-specific containing
root; bounded ambiguity (candidates max 5 + total_matches); compact
not-found; GitHub corpora never match; Windows casing/separator
normalization; relative paths echoed as _meta.resolved_path. Suite parity
with jCodeMunch resolve_repo; doc_ prefix keeps the servers collision-free.
Tool count 62→63. Tests: tests/test_v1_99_0.py (15) + test_server.py
updates. Docs: SPEC, README, USER_GUIDE, live guide.

## v1.98.0 — 2026-07-16

**watch daemon keeps doc indexes fresh on any on-disk change (#78)**

Reported by @oderwat. Freshness previously rode only the PostToolUse hook, which
fires only when the agent edits a doc. Docs changed outside the agent (git pull,
editor, build, teammate) went stale. Adds jCodeMunch's watch-all equivalent,
scoped to doc file types.

## v1.97.1 — 2026-07-16

**docs only (packaged changelog reflects current-pricing wording)**

## v1.97.0 — 2026-07-16

**correct cost_avoided to current Opus pricing (was overstating 3x)**

## v1.96.0 — 2026-07-13

**never auto-bill a paid cloud provider from a bare env key**

A bare cloud API key in the environment (ANTHROPIC_API_KEY, OPENAI_API_KEY, ...)
silently enabled AI summarization; get_provider_name auto-selected the first
provider whose key was present, billing per doc section on every index. Every
provider jDoc auto-detects is remote-cloud, so auto-detect now suppresses all of
them unless the user opts in via JDOCMUNCH_SUMMARIZER_PROVIDER or the new
JDOCMUNCH_ALLOW_PAID_SUMMARIES=1. One-time warning names the setting.

## v1.95.0 — 2026-07-10

**suite-parity retrieval verdict (_meta.verdict on search_sections + find_endpoint)**

Phase 3 of the suite-wide retrieval-verdict work. search_sections and
find_endpoint now emit _meta.verdict (ok/low_confidence/absent/degraded):
degraded = semantic requested on an index with no embeddings; low_confidence
keys off the existing confidence <0.4 floor; absent carries a did_you_mean
list. Clean-room jDoc impl in new retrieval/verdict.py (no cross-suite import).

## v1.94.0 — 2026-07-08

**large-corpus stability: vectors out of monolith (#75), throttled reindex hook (#76), cheap list_repos (#77)**

Reported by @floke75 (three linked issues; a 16 GB machine hit cascading jetsam
kills + swap storm + WindowServer watchdog restarts from all three interacting).

## v1.93.0 — 2026-07-07

**MCP readOnlyHint annotations (suite parity with jcodemunch PR #361)**

Every tool now advertises ToolAnnotations(readOnlyHint=...) at the list_tools
chokepoint (non-mutating model_copy), so MCP clients that gate execution
(Claude Code plan mode) run jDoc's query tools silently while still prompting on
the write-set. _NON_READONLY_TOOLS = index_local, doc_index_repo, delete_index,
define_repo_group, tune_weights, check_embedding_drift — any tool that can mutate
persistent state under any argument (conservative bias; link_code_to_symbols /
verify_index / resolve_related_code_repos load and return, never persist, so they
stay read). Suite parity with jcodemunch-mcp (PR #361) and jdatamunch-mcp.
Additive, 1.x-compatible (new tools/list field only; no tool add/rename/removal).
Tests: tests/test_v1_93_0.py (4).

## v1.86.0 — 2026-06-18

**cross-suite repo identity (#67, #68)**

#67: index_local(name=local/<name>) round-trip. doc_list_repos returns
local handles as local/<name> but index_local validated name as a single
storage component, so reusing a discovered handle as the refresh name raised
Invalid name. New normalize_local_index_name strips the local/ prefix (rejects
other owners / nested slashes / empty); doc_list_repos rows gain typed
repo_kind / owner / name fields.

## v1.84.0 — 2026-06-15

**get_broken_links rendered-anchor namespace, no private slugs (#64)**

Reported by @mmashwani. get_broken_links accepted jdocmunch's private section
slug (underscore flattening, hyphen-run collapse, hierarchical leaf, parse-time
slugify) as a valid anchor target alongside the GitHub-rendered set. That
private namespace is an internal index artifact no Markdown renderer emits, so a
link dead on the rendered page passed validation whenever it matched the private
slug -- a false negative in a link checker.

## v1.83.0 — 2026-06-15

**vectorized query-time semantic scoring (#63)**

DocIndex._semantic_search and the semantic half of _hybrid_search scored the
query against every embedded section with a per-section pure-Python
cosine_similarity (O(N*D) per query, ~242 ms on a 10.7k-section corpus, and
synchronous on the event loop). New _ensure_semantic_matrix builds + caches an
L2-normalized embedding matrix once per DocIndex (cached on the instance, which
DocStore keys by path + mtime, so a re-index rebuilds it; not a dataclass field
so it never serializes); _semantic_scored replaces both loops with a single
matrix-vector product. numpy lazy-imported with the original per-section loop as
fallback. Same tuples, same (-score, id) sort, same _path_excluded / no-embedding
filtering, same downstream RRF; float64 keeps _score equal to fp noise.

## v1.82.0 — 2026-06-15

**vectorized related-graph semantic build + save core index first (#62)**

related_persist.build's semantic half was an O(N^2) pure-Python all-pairs
cosine (semantic_neighbors per section), stalling index_local on large embedded
corpora; the sidecar was also built before save_index, gating the core index.

## v1.81.0 — 2026-06-14

**structural_integrity health axis (#54)**

doc_health_radar/get_doc_health had no structural axis, so an index that
silently lost sections to a fence accident graded identically to its repair.
New structural_integrity axis fed from already-persisted data (no parser change,
no reindex beyond code_blocks): _structural_signals counts headings swallowed
into stored fenced bodies (column-0 ^#{2,6} lines, md/markdown/mdx exempt) +
heading-level skips (consecutive level jumps > 1 per doc). compute_radar grows
the axis (_score_structural_integrity, same slope as link_integrity); warnings
flow get_doc_health -> doc_health_radar -> compute_radar. Repairing swallowed
sections now moves the axis 0 -> 100 and the composite/grade.

## v1.80.0 — 2026-06-14

**shared prose view for hybrid-search scoring (#58)**

Hybrid search fused a BM25 score (fences stripped) with an embedding score
(title + content[:1000], fences AND frontmatter raw), so the two channels scored
different texts; heavy frontmatter flooded the capped embed window so prose
never reached it. Consumption-layer fix (content/content_hash untouched): new
prose_view in tokenize.py strips top-of-text frontmatter (YAML --- / TOML +++,
same-delimiter backreference) + fences; tokenize() applies the frontmatter strip
(BM25 path); _section_embed_text reduces content via prose_view before the cap.
Embedding cache key salted with _EMBED_TEXT_VERSION so stale vectors re-embed.

## v1.79.0 — 2026-06-14

**TOML (+++) frontmatter recognition (#60)**

_frontmatter_end_line recognized only YAML ---, so Hugo TOML +++ blocks were
indexed as root-section prose and their URLs entered references. The detector
now accepts a +++ opener with a matching +++ closer (composes with the #56
blank-line discriminator, scoped to --- since +++ has no thematic-break
collision). Per-section references now derive from the same frontmatter-free
prose view used for tags/inline_code, so frontmatter values (YAML + TOML) and
in-code link syntax no longer become references (the #47 follow-on for refs).
content/content_hash untouched.

## v1.78.0 — 2026-06-14

**inline_code artifact for the code<->docs bridge (#59)**

The parser extracted only fenced code_blocks, so inline backtick mentions
(`name`) never reached link_code_to_symbols or get_undocumented_symbols. Three
layers: parser extract_inline_code collects identifier-shaped spans from the
prose view (fenced code excluded), persisted as Section.inline_code (omit when
empty, round-trips, no migration); link_code_to_symbols routes each section's
inline spans as a synthetic {section_id}::inline bridge input through the same
resolution path; get_undocumented_symbols feeds inline spans into the haystack
(recall) plus an exact lowercased-span set for authoritative-documented hits
(precision).

## v1.77.0 — 2026-06-14

**get_broken_links: fs existence + GitHub anchors + scheme parsing (#49, #50, #47.6)**

#49: existence was tested only against the indexed doc set, so links to existing
non-doc files (images, LICENSE, source) reported file_not_found. Now the
filesystem is consulted against source_root before flagging.
#50: #anchor links were validated against jdocmunch's private hierarchical slug
scheme, which diverges from rendered GitHub anchors. New _build_github_anchors
derives the GitHub-rendered namespace per document (rendered text + github-
slugger rules), accepted alongside the existing forms so valid rendered anchors
stop being flagged.
#47 symptom 6: the blanket colon-skip silently dropped typo'd/unknown schemes;
a scheme prefix that isn't known-external now reports unknown_scheme. Bare email
autolinks treated as external. Closes #47.

## v1.76.0 — 2026-06-14

**extract_references rewrite: inline grammar + reference defs (#47, #48)**

#47 (High): references were built by two naive regexes over the raw body,
storing link titles / angle-bracket destinations / image targets verbatim,
truncating parenthesized URLs, keeping autolink/bare-URL trailing junk, and
extracting link syntax shown inside code. extract_references is now a proper
inline pass: scrub fenced code + inline code spans + HTML comments, then match
inline links (images skipped, titles + <...> stripped, balanced parens for wiki
URLs), autolinks, and bare URLs (trailing punctuation trimmed); empty [t]()
skipped. Extraction half; symptom 6 (typo scheme reporting) lands with the
get_broken_links release.

## v1.75.0 — 2026-06-14

**parse-loop block detection: indentation + HTML blocks (#43, #45)**

#43 (High): block-start detection ran against the raw column-0 line, so
ATX/setext headings indented 1-3 spaces folded into the previous section and
indented/list-nested fences never opened (interiors parsed as markdown, minting
phantoms). parse_markdown now dedents up to 3 leading spaces for ATX/setext
detection (4+ stays indented code) and fence opens are indent-tolerant.

## v1.74.0 — 2026-06-14

**markdown block-detection, isolated set (#46, #51, #56, #57)**

#46 (High): strip_mdx ran import/export + JSX removers over the whole .mdx
document, mutilating code inside fences and storing the corrupted text as the
hash-verified mirror. Now fence-aware: strip_mdx segments on backtick/tilde
fences (frontmatter + mermaid whole-doc) and applies _strip_mdx_plain only to
non-fence regions.
#51 (bug half): open fence at EOF was buffered then dropped; now flushed before
the final _finalize_section (CommonMark closes an unterminated fence). 4-space
indented-code enhancement descoped to land with #43.
#56: leading '---' thematic break + later bare '---' read as frontmatter,
swallowing headings between; _frontmatter_end_line now rejects a '---' opener
followed by a blank line.
#57: extract_tags ran over raw body incl. fenced code + frontmatter; new
_prose_view blanks those byte ranges before tag extraction. Content/hash
untouched.

## v1.73.0 — 2026-06-14

**CLI/config ergonomics batch (#36-41)**

Six independent CLI/config gaps, several with jcodemunch precedent.

## v1.72.0 — 2026-06-14

**byte-space fidelity: CRLF preservation + BOM strip (#52, #53)**

#52: index_local/index_file read with Path.read_text under universal newlines,
collapsing CRLF/CR to LF before byte offsets were measured, so published
byte_start/byte_end/content_hash verified only against a hidden LF-normalized
mirror, never the on-disk file, and disagreed byte-for-byte with the GitHub leg.
Local reads now use open(..., errors="replace", newline="") (Path.read_text
lacks newline before 3.13) so offsets/hashes address real on-disk bytes and the
local path converges onto the already-correct GitHub path.

## v1.71.0 — 2026-06-14

**CommonMark setext/paragraph correctness + byte/hash invariant (#44, #35, #55)**

Rewrote parse_markdown around the CommonMark paragraph rule. #44: setext
underline detection keyed on a prev-line heuristic, so ---/=== after a list
item, blockquote, fence-close, or ATX heading fabricated phantom H2s, destroyed
the real section as a [0:0] range, or mis-titled it; multi-line titles kept
only the last line; single-dash H2 and pipe-bearing H1 underlines were rejected.
Now a para_lines + para_byte_start block-state tracker arms only on paragraph
text and clears on every non-paragraph context; narrow | guard on H2 only keeps
all five GFM pipe-table shapes from becoming headings.

## v1.70.3 — 2026-06-14

**fence-open regex accepts arbitrary CommonMark info strings (#42)**

_FENCE_OPEN_RE accepted only an empty info string or one bare [\w.+-] token,
so attribute-bearing fences (```python title="x", ```js {1,3}, ```{r}, ```c#)
were not recognized as openers. The fence state machine then inverted: block
body parsed as markdown (phantom # comment sections), the block's bare closing
fence opened a phantom lang="" fence that swallowed every real heading after it,
and code blocks were lost - corruption that self-verified green. Widen to
CommonMark 4.5; strip RMarkdown {} from fence_lang. Existing indexes over such
fences need a reindex. Tests: tests/test_v1_70_3.py (4).

## v1.70.0 — 2026-06-11

**tune_weights recency window (max_age_days, default 90)**

Weight learning now reads a recency window of the ranking ledger instead
of the lifetime history, so stale events can't anchor semantic_weight
proposals to a query distribution that no longer exists. max_age_days=0
restores the lifetime read. Mirrors jcodemunch-mcp v1.108.53.

## v1.69.1 — 2026-06-10

**redirect git subprocess stdin to DEVNULL, fixes Windows stdio deadlock (PR #30)**

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

## v1.69.0 — 2026-06-04

**GitHub ref selection for versioned doc snapshots (PR #27)**

Optional 'ref' selector (branch/tag/commit-ish) for index_repo; resolved to a commit SHA before fetch, never persisted. Durable handles stay SHA-based. Fails closed on unresolvable explicit refs. Additive; INDEX_VERSION unchanged. Contributed by @DevItBetter, closes #26.

## v1.68.0 — 2026-06-03

**doc_index_repo name override for named GitHub doc indexes (PR #25)**

Optional 'name' storage handle for GitHub doc indexes; persists upstream identity via DocIndex.source_repo and surfaces source_repo / source_repo_at_sha. Additive; INDEX_VERSION unchanged. Contributed by @DevItBetter, closes #24.

## v1.67.0 — 2026-06-01

**certified repo@sha handles for citeable doc snapshots (PR #23)**

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

## v1.66.3 — 2026-05-16

**openai-compatible: probe dim at init (jdoc#20)**

Hardens v1.66.0's openai-compatible provider against a silent-corruption
window. When _provider_identity returned dim=None, the on-disk cache fell
back to wildcard dim matching. A backing-model swap behind the same
URL/model env vars (e.g. Ollama retagging nomic-embed-text to all-minilm)
would then silently mix vectors of different dims in the cache, breaking
cosine math downstream.

## v1.66.2 — 2026-05-16

**warm sentence-transformers before stdio (jdoc#19)**

Reported by @rknighton. First semantic search_sections hung when
sentence-transformers was the configured provider: lazy model load
exceeded MCP tool-call timeouts and leaked download/progress chatter
to stdout, corrupting JSON-RPC framing.

## v1.66.1 — 2026-05-16

**should_embed("false") parses as False (jdoc#18)**

Reported by @rknighton. should_embed() ran any non-empty string through
bool(), so use_embeddings="false" enabled embeddings instead of disabling
them. Hit whenever an MCP client sent the flag as a JSON string.

## v1.66.0 — 2026-05-16

**openai-compatible embeddings (PR #17)**

Opt-in embedding provider for Ollama, vLLM, LiteLLM, llama.cpp, LM Studio,
and any other OpenAI-API-shaped endpoint. Contributed by @DevItBetter.

## v1.63.3 — 2026-05-13

**jdocmunch_guide sibling-parity tool**

Adds jdocmunch_guide, the doc-MCP sibling of jcodemunch_guide (in jcm
since v1.84.0). Returns the version-current CLAUDE.md / AGENT.md policy
snippet for jdocmunch-mcp so an agent can keep a one-line CLAUDE.md
("Call jdocmunch_guide and strictly follow its instructions.") instead
of pasting a static block that drifts from the installed version.

## v1.63.2 — 2026-05-12

**drift-proof __version__ via importlib.metadata**

Replace hardcoded `__version__ = "X.Y.Z"` in src/jdocmunch_mcp/__init__.py
with `importlib.metadata.version("jdocmunch-mcp")`. pyproject.toml is now
the single source of truth; the wheel's metadata is read at import time
so runtime and packaging version strings cannot disagree by construction.

## v1.63.1 — 2026-05-12

**CI green (fixture rename + full-history checkout)**

Two independent CI fixes, no installed-user behavior change.

## v1.61.0 — 2026-05-11

**explicit-paths indexing**

`index_local(paths=[...])` skips the directory walk and indexes exactly
the listed files / subdirs. Each entry can be absolute or relative to
the path root. Security validation matches the walk path: outside-root,
traversal, and symlink-escape entries are rejected with per-entry
warnings; unsupported extensions warn-and-skip.

## v1.59.1 — 2026-05-03

**strip raw embedding vector from per-section responses (#11)**

get_section, get_sections, describe_section, get_section_summary, and
get_section_summaries were passing the 384-dim embedding through to
callers. The vector serves the internal semantic-search pipeline only
and inflates each section response by ~2,000 tokens with no consumer
value. Now stripped consistently with get_section_context and the
already-correct DocStore.search().

## v1.59.0 — 2026-05-03

**count_sections filter-only count tool**

Adds count_sections, a filter-only counterpart to search_sections that
skips BM25/embeddings/scoring. Same filter axes (path_glob, role(s),
tag(s), level range, byte_length range) with AND semantics. Use for UI
counters or 'does anything match?' probes without paying for ranking.

## v1.58.0 — 2026-04-26

**get_doc per-doc detail view**

Single-doc detail view that pairs with v1.55's list_docs (cross-doc
inventory). Returns sections list (handles), role/tag distributions
per doc, byte_size, format, indexed_at — the "tell me everything
about this one doc" answer in a single call.

## v1.57.0 — 2026-04-26

**search_titles fast title-only navigation**

Different from search_sections (full hybrid retrieval). Title-only
token-overlap match for the navigation case: agent has a heading text
from a URL fragment, screenshot, or prior result and just wants the
section_id. Pure-Python scorer, no embeddings, no posting-list
traversal — fast enough for keystroke-rate calls.

## v1.56.0 — 2026-04-26

**get_index_overview snapshot (50-tool milestone)**

Single-call repo snapshot composing v1.46 (tags), v1.50 (roles), v1.55
(docs/format) aggregations. New get_index_overview(repo, top_n=5)
returns doc_count, section_count, total_byte_size, format_breakdown,
top_tags, top_roles, indexed_at — the "what is this repo at a glance?"
answer.

## v1.55.0 — 2026-04-26

**list_docs flat per-doc inventory**

Doc-level navigation primitive. New list_docs(repo) returns every
indexed document with {doc_path, section_count, format, byte_size}.
Lighter than get_toc_tree (full section trees per doc) and complements
list_repos (which enumerates repos rather than docs within one).

## v1.54.0 — 2026-04-26

**describe_section consolidated handle bundle**

Round-trip elimination. New describe_section(repo, section_id) returns
the union of get_section_summary + get_section_path + section_neighbors
in one call against a single load_index(). Saves three round-trips for
the common "tell me everything about this section without content"
pattern.

## v1.53.0 — 2026-04-26

**byte-length range filter on search_sections**

New min_byte_length / max_byte_length args drop sections by computed
byte_end - byte_start. Use to filter out stub sections (one-line
definitions) at the small end or oversized dumps at the large end.

## v1.52.0 — 2026-04-26

**roles / exclude_roles plural ANY-match filters**

Completes the role-axis filter parity with v1.45 + v1.51 tags. New
`roles: list[str]` (positive ANY-match) and `exclude_roles: list[str]`
(negative ANY-match) on search_sections. Existing singular `role: str`
(exact post-filter) keeps working unchanged.

## v1.51.0 — 2026-04-26

**exclude_tags filter on search_sections**

Negative companion to v1.45's tags (positive AND-include). New
exclude_tags: list[str] arg drops sections whose Section.tags contains
ANY listed tag. Fills the obvious gap: agents can scope by required
tags AND ban irrelevant ones in the same query.

## v1.50.0 — 2026-04-26

**get_all_roles role discovery (milestone)**

41-release milestone. Finishes the discovery symmetry begun by v1.46
get_all_tags. New get_all_roles(repo) returns every distinct role
classification (from the v1.19 role classifier's metadata.role) with
section counts and id samples per role. Mirrors the tag-axis pattern
for the role axis.

## v1.49.0 — 2026-04-26

**get_section_excerpts batch preview**

Batch counterpart to v1.41's get_section_excerpt. Resolves N previews
in one call against a single load_index() — saves round-trips when an
agent has multiple search hits and wants a quick peek at each.

## v1.48.0 — 2026-04-26

**get_section_summaries batch metadata**

Batch counterpart to v1.38's get_section_summary. Resolves indexed
metadata for many section_ids in one call against a single
load_index() — saves N round-trips when an agent has multiple search
hits and wants to inspect role/tags/structured metadata for all of
them before deciding which to read.

## v1.47.0 — 2026-04-26

**get_recent_changes drift surface**

New MCP tool surfaces sections currently in edited_uncommitted or
stale_index buckets via the v1.16 FreshnessProbe. Pre-flight check
before deciding whether to re-index — get_doc_health exposes the
counts; this returns the actual section list.

## v1.46.0 — 2026-04-26

**get_all_tags discovery tool**

Discovery companion to v1.45's tags filter. New tool returns every
unique #hashtag across the repo with per-tag section counts. Lets
agents learn what tag namespaces exist before constructing a tag-
filtered search query — no more guessing whether a doc set uses #api
vs #API or whether a tag exists at all.

## v1.45.0 — 2026-04-26

**tags filter on search_sections**

New tags: list[str] arg restricts results to sections whose
Section.tags (auto-extracted from #hashtag markers in content via
extract_tags) contains every listed tag. AND semantics — section
must contain ALL listed tags; case-insensitive matching.

## v1.44.0 — 2026-04-26

**level filters on search_sections + replay CI gate fix**

(a) New min_level / max_level args on search_sections restrict results
to a heading-depth range. Inclusive on both ends; either may be omitted
independently. Filter runs alongside the existing path_glob filter,
before quality filters. _meta.min_level / _meta.max_level echoed when
set. Use to scope a query to top-level pages only (max_level=1) or
ignore deep sub-sections.

## v1.43.0 — 2026-04-26

**get_section_descendants subtree traversal**

Pairs with v1.40's get_section_path (ancestors). New tool walks
parent_id downward via BFS and returns every descendant in document
order with a depth offset from the target (immediate child = 1,
grandchild = 2, etc).

## v1.42.0 — 2026-04-26

**search_sections quality filters**

New min_answerability / min_quotability args on search_sections drop
results below the v1.33 per-result quality thresholds. Pure additive;
defaults None mean no filter applied.

## v1.41.0 — 2026-04-26

**get_section_excerpt content preview**

Cheap content peek between handle-only metadata and full byte-range
read. New tool returns title + first N bytes of content (default 500).
Truncation is UTF-8 char-boundary safe (walks back from the cap to a
valid char boundary) and trims to last newline before the cap so the
excerpt ends on a paragraph boundary when possible. Truncated content
gets a `…` marker; _meta.tokens_saved reports byte savings vs full
content.

## v1.40.0 — 2026-04-26

**get_section_path + doc_health orphan rollup**

Two small additive wins.

## v1.39.0 — 2026-04-26

**get_orphan_sections doc-rot finder**

Surfaces sections nobody links to — the third leg of the doc-health
triad alongside get_broken_links and get_stale_pages. Inverts the link
graph one time and reports every section whose doc_path receives zero
inbound references from any other doc.

## v1.38.0 — 2026-04-26

**get_section_summary metadata-only retrieval**

Fills the gap between get_toc (brief handles: title/level/summary)
and get_section (full content via byte-range read). New
get_section_summary returns the full indexed metadata for one
section — title, summary, role, tags, metadata, parent_id, children,
content_hash, byte_start/end — plus a derived byte_length so callers
can size content reads without a separate call. Content is excluded;
that's the whole contract.

## v1.37.0 — 2026-04-26

**section_neighbors navigation tool**

New MCP tool for cheap document-order navigation. Given a section_id
returns prev/next siblings (in byte_start order, restricted to same
doc_path so nav doesn't accidentally hop documents), parent (via
parent_id), and first child. Handles only — {id, title, level,
doc_path} — no content reads, no byte-range fetches. Fills the gap
between get_toc (whole repo) and get_section_context (target plus
ancestors plus children with content).

## v1.36.3 — 2026-04-26

**bump CI gate to 0.06 (off-by-epsilon)**

The MRR drop on Linux was exactly 0.05000000000000004 — floating-point
sliver above the 0.05 gate. Gate compare is `drop > gate_pct`, not `>=`,
so the test failed by ~4e-17. Bump platform-tolerance gate from 0.05
to 0.06 to absorb this without redesigning gate arithmetic. The
release-time strict 0.02 gate is unchanged.

## v1.36.2 — 2026-04-26

**platform-tolerant replay-lock threshold (CI fix)**

v1.36.1 added deterministic tie-break which fixed scoring ties, but CI
was still failing at the same numbers (0.963 nDCG / 0.95 MRR). The
remaining variance is genuine BM25 score difference from CRLF (Windows
checkout) vs LF (Linux checkout) — line endings shift each section's
byte_length by ~1 byte/line, which moves avgdl by enough to flip the
depth-1 vs depth-3 ranking on one query in the wiki-stats family.

## v1.36.1 — 2026-04-26

**deterministic ranking tie-break (CI fix)**

CI on Linux+Python 3.10/3.11 was failing on test_self_fixture_meets_lock
and test_pass_when_within_gate at 0.963 nDCG / 0.95 MRR. Root cause:
all four ranking sort sites in storage/doc_store.py used
`key=lambda x: x[0], reverse=True` — score-only — leaving tied results
in insertion order, which depends on os.walk traversal. Different
filesystems produce different orderings.

## v1.36.0 — 2026-04-26

**path_glob filter for monorepo retrieval scoping**

New `path_glob` arg on search_sections, get_toc, and get_toc_tree
restricts results to sections whose doc_path matches an fnmatch
pattern (e.g. "api/**/*.md", "reference/*"). Stacks with the
existing exact-match doc_path arg. Defaults to None (no filter).

## v1.35.0 — 2026-04-26

**CHANGELOG generator + code-block compression**

Two small additive wins on the 1.x line. (a) scripts/generate_changelog.py
walks `git log` for `release: vN.N.N — title` commits and re-renders a
Keep-a-Changelog markdown file from git state — release-time utility,
idempotent, em/en/hyphen-tolerant separator, drops Co-Authored-By
trailers. (b) Opt-in `compress_code` kwarg on get_section/get_sections
strips blank lines and full-line comments inside fenced code blocks
before returning. Disk content is never touched; _meta.code_compressed_bytes
reports savings. Language→comment-marker map covers Python/JS/TS/SQL/Lua/
Lisp/Erlang families; partial-line comments preserved; unknown language
tags pass through unchanged.

## v1.34.0 — 2026-04-26

**section dedup detector + dedupe flag**

PRD F20 lands. Catches whole-section near-duplicates (the v1.24
boilerplate detector caught line-level repetition; this catches
copied-page-level repetition: same "Configuration" section across
products, FAQ entries reproduced in multiple guides, etc).

## v1.33.0 — 2026-04-26

**answerability + quotability scoring**

Two PRD §5 next-level ideas land. Both pure-Python heuristics; no AI
calls; no external dependencies. Emitted as advisory _meta on each
result — they do NOT affect ranking. Agents that want to gate on them
can; agents that don't see them get unchanged behavior.

## v1.32.0 — 2026-04-26

**citation block + task-aware retrieval profiles**

Two PRD §5 next-level ideas land. Both pure additive on the 1.x line.

## v1.31.0 — 2026-04-26

**stale-index simulation + multi-format regression**

Final entry on the Phase-6 infrastructure backlog. Pure tests; no API
change.

## v1.30.0 — 2026-04-26

**source_root + grouped VuePress + 1.x commitment**

Three small additive deliverables. No API removed.

## v1.29.0 — 2026-04-26

**toctree + VuePress + OpenAPI 3.1/Swagger 2.0 + autotune**

Three additive deliverables. No API removed; no schema bump.

## v1.28.0 — 2026-04-26

**drift sim + cross-platform paths + replay log**

Second batch of Phase-6 infrastructure. Three additive deliverables;
no API change.

## v1.27.0 — 2026-04-26

**verify_index + section-boundary golden corpus**

First batch of Phase-6 infrastructure. Protects against the silent-
corruption bug class that motivated B1 / B2 in the v1.10 audit.

## v1.26.0 — 2026-04-26

**cross-repo concept graph (RRF fan-out)**

Final entry in the originally-planned 1.x roadmap. Monorepo-friendly
search across multiple indexed repos via Reciprocal Rank Fusion.

## v1.25.0 — 2026-04-26

**notebook output preservation**

Outputs from .ipynb files now reach the indexed body so
search_sections finds them. Previously convert_notebook stripped
outputs entirely.

## v1.24.0 — 2026-04-26

**related-graph sidecar + boilerplate detector**

Two pure-additive optimizations: speed up related-section lookups on
large indexes, and let callers strip repeated cross-section content
(license headers, "Edit this page on GitHub" footers, nav menus).

## v1.23.0 — 2026-04-26

**ranking-event ledger + online weight tuning**

Closes the retrieval-quality feedback loop: every search now records a
ranking event when telemetry is enabled, and a new tune_weights tool
proposes per-repo semantic_weight steps based on the event history.

## v1.22.0 — 2026-04-26

**get_tutorial_path + get_undocumented_symbols**

Two pure-additive tools that complete the navigation surface. Both
1.x-safe per the compatibility contract: no schema change, no breakage,
no existing tool affected.

## v1.21.0 — 2026-04-26

**real-world replay corpora + 1.x compatibility contract**

License-binding compatibility contract codified in CLAUDE.md: the 1.x line
will never remove a tool, drop a Section field, force a reindex, or break
a wire format. Items that can't be done additively get explicitly listed
under todo.md § "Reserved for 2.x" and are deferred until a major-version
license revision is approved.

## v1.20.0 — 2026-04-26

**related graph + section diff + doc health + adaptive context**

Lights up the v1.10–v1.19 foundations with navigation tools and
diagnostics. Originally scoped as v2.0.0; major-version bump rejected
as unjustified — the only user-visible break is lexical_engine="legacy"
now raising (was deprecated in v1.12.0); everything else is additive.

## v1.19.0 — 2026-04-26

**section role classifier + glossary**

Task-aware retrieval lands. Agents can ask for troubleshooting only,
or look up canonical definitions of terms used in the codebase.

## v1.18.0 — 2026-04-26

**structured OpenAPI retrieval**

Promotes OpenAPI / Swagger specs from prose-flattened markdown to first-
class queryable structure. Each operation and each schema becomes its
own Section with typed metadata. Four new MCP tools surface the
structure to agents.

## v1.17.0 — 2026-04-26

**code-block-aware indexing + jcodemunch bridge**

The differentiator release. Doc code samples become first-class
addressable units. Pairs with jcodemunch via best-effort bridge so
agents can ask "show me Python install examples that call
Client.authenticate" end-to-end.

## v1.16.0 — 2026-04-26

**section freshness probe + retrieval confidence**

Agents stop quoting stale sections. LLMs get a "should I trust top-1
or expand the search" signal.

## v1.15.0 — 2026-04-26

**embedding cache + drift canary**

Cuts embedding cost 60–95% on typical doc churn (most edits touch <10%
of sections; cache hit-rate dominates) and adds a tripwire for silent
provider/model regressions.

## v1.14.0 — 2026-04-26

**telemetry foundation + analyze_perf**

Per-tool latency observability — the data layer that v1.15+ embedding
cache, v1.16 freshness, and the v2.0 weight-tuning loop all read from.

## v1.13.0 — 2026-04-26

**two-stage prune + RRF + query embedding cache**

Latency and cost. Stage A reduces the BM25 candidate set; RRF replaces
unstable min-max hybrid fusion; query embeddings are cached for the
common multi-call retrieval flow.

## v1.12.0 — 2026-04-26

**BM25-Okapi engine + heading-path-aware ranking**

Replaces the v1.0–v1.11 hand-rolled scorer (no IDF, no length norm, no
TF saturation) with real BM25-Okapi. The legacy scorer is preserved
behind lexical_engine="legacy" until v2.0.0.

## v1.11.0 — 2026-04-26

**replayable retrieval-quality harness + CI gate**

Locks current retrieval behavior so every future release proves it didn't
regress. The mandatory dependency for v1.12.0 (BM25), v1.13.0 (two-stage
retrieval), and every later retrieval-quality change.

## v1.10.0 — 2026-04-26

**correctness foundation (B1–B7)**

Seven critical bug fixes that block downstream retrieval/quality work.
PRD captured at todo.md.

## v1.9.0 — 2026-04-19

**hybrid BM25 + semantic search, use_embeddings="auto"**

search_sections now fuses lexical and semantic scores when the index has
embeddings. New params match jcodemunch-mcp's shape: semantic (None/true/false),
semantic_only, semantic_weight (0.0-1.0, default 0.5). Each channel is min-max
normalized to [0,1] within the candidate set, then weighted-summed. Zero
performance impact when the index has no embeddings; graceful degradation when
embed_query returns None at query time.

## v1.8.0 — 2026-04-12

**LLM Wiki support (get_backlinks, get_stale_pages, get_wiki_stats)**

Three new tools for the LLM Wiki pattern (a la Karpathy):
- get_backlinks: inverse reference graph — find all pages linking to a target doc
- get_stale_pages: frontmatter-based source provenance and staleness detection
- get_wiki_stats: wiki health dashboard (orphans, most-linked, tags, link density)

## v1.5.2 — 2026-04-06

**contrib deb packaging script (#7)**

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>

## v1.5.1 — 2026-04-04

**expose max_files param on index_local**

Surfaces the existing discover_doc_files max_files cap as a first-class
parameter on the index_local MCP tool and Python function. Users with
doc trees larger than 500 files can now pass max_files to raise the limit.
Default unchanged at 500.

## v1.5.0 — 2026-04-01

**get_broken_links + get_doc_coverage**

New tools:
- get_broken_links(repo): scan indexed doc sections for internal
  cross-references that no longer resolve (file links, cross-file
  anchors, anchor-only #heading links); external links skipped;
  reason: file_not_found | section_not_found | anchor_not_found
- get_doc_coverage(repo, symbol_ids): check which jcodemunch symbols
  are mentioned in section titles; bridges jcodemunch <-> jdocmunch;
  symbol_ids capped at 200; output: documented, undocumented, coverage_pct

## v1.4.5 — 2026-03-29

**multi-provider AI summarization**

Refactor summarizer module: _BaseSummarizer + concrete provider classes
(_AnthropicSummarizer, _GeminiSummarizer, _OpenAICompatSummarizer).
Add MiniMax and GLM-5 (ZhipuAI) providers via OpenAI-compatible API.
Add JDOCMUNCH_SUMMARIZER_PROVIDER env var for explicit provider selection.
Export get_provider_name(). Gemini model bumped to 2.0-flash.
Backward compat aliases BatchSummarizer + GeminiBatchSummarizer preserved.
22 new tests (272 total). Contributed by SkaldeStefan (PR #5).

## v1.4.3 — 2026-03-22

**fuzzy/prefix matching in lexical search + search_mode hint in _meta**

- _score_section: adds prefix matching (>= 3 chars) alongside exact word matching;
  partial queries like "authenticat" now hit "authentication"
- search_sections _meta: always includes search_mode (semantic|lexical); lexical
  mode adds tip pointing users to use_embeddings=True

## v1.4.2 — 2026-03-21

**add MCP Registry metadata for official registry listing**

Adds <!-- mcp-name: io.github.jgravelle/jdocmunch-mcp --> to README.md
for PyPI ownership verification and server.json for mcp-publisher CLI.

## v1.4.1 — 2026-03-18

**supply-chain integrity check at startup**

Adds verify_package_integrity() to security.py. Called at the top of
main() to detect if the code is running from a distribution with a
different name than the canonical 'jdocmunch-mcp' (e.g. a re-published
fork). Uses importlib.metadata.packages_distributions() to identify the
owning distribution of the running code, not just what's installed.

## v1.3.0 — 2026-03-11

**semantic embedding search**

Add optional embedding-based search alongside the existing lexical scorer.
When use_embeddings=true is passed to index_local or index_repo, each section
gets a vector embedding (Gemini text-embedding-004 or OpenAI text-embedding-3-small).
search_sections auto-detects embedded indexes and switches to cosine-similarity
ranking, solving the short-title / bullet-list matching problem.

## v1.1.0 — 2026-03-08

**OpenAPI 3.x / Swagger 2.x parser**

- openapi_parser.py: content-sniffs .yaml/.yml/.json for openapi:/swagger: key
- Operations grouped by tag, each endpoint rendered as ### METHOD /path subsection
- Parameters, request body, responses, and schemas/definitions indexed
- Non-OpenAPI YAML/JSON produces zero sections (safe pass-through)
- 25 new tests (176 -> 201 total)
