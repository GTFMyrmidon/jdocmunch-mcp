# Changelog

## [1.97.0] - 2026-07-16 - update model price constants to current Anthropic pricing

Updates the model input-price constants used by the `cost_avoided` dollar
estimate to Anthropic's current published rates: Opus $5/MTok, Sonnet $3/MTok,
Haiku $1/MTok. Anthropic has reduced input pricing across the Opus line since
these models launched, so the constants now track current pricing.

Token savings are measured in tokens and valued at the applicable model rate,
so the underlying savings are unchanged; only the price constants now reflect
current pricing.

### Changed
- `claude_opus` input rate set to the current $5/MTok (comment cites the dated
  source, anthropic.com/pricing 2026-06-24).

### Added
- `claude_sonnet` ($3/MTok) and `claude_haiku` ($1/MTok) entries, so
  `cost_avoided` / `total_cost_avoided` show the full current model set (parity
  with the sibling code MCP's price table). Additive keys only; the existing
  `claude_opus` and `gpt5_latest` keys are unchanged in name, so the wire shape
  stays 1.x-compatible.

`cost_avoided` does not touch the public token counter (which stores tokens and
values them at display time). No INDEX_VERSION bump, no tool add/rename. Suite
parity: jcm v1.108.130 (receipt table) + jdata v1.19.0 (same constants).

## [1.95.0] - 2026-07-10 - suite-parity retrieval verdict (`_meta.verdict` on search_sections + find_endpoint)

### Added

- **`search_sections` and `find_endpoint` now emit `_meta.verdict`** — the same
  agent-facing honesty contract the sibling code MCP ships on its search tools. An
  empty or weak result is positive, token-saving evidence: the index can attest
  "this topic is not documented here" instead of leaving the agent to reformulate
  a query for something that provably isn't present. Taxonomy: `ok` /
  `low_confidence` / `absent` / `degraded`.
- **`degraded`** fires when a caller requests semantic search on an index with no
  embeddings — results are lexical-only, so absence is not proven (re-index with
  embeddings for semantic recall). It takes precedence over `absent`.
- **`low_confidence`** keys off the existing retrieval confidence score (the
  documented < 0.4 ambiguity floor), so a returned-but-shaky top hit is flagged.
- **`absent`** carries a `did_you_mean` list of documents whose path or title
  contains a query term, so a miss redirects the agent instead of repeating.
  `find_endpoint` suggests existing endpoint paths that share a segment with a
  missed glob.

Clean-room jDoc implementation (new `retrieval/verdict.py`); only the wire shape
is shared with the sibling MCPs — no cross-suite import. Additive and
1.x-compatible: `_meta.verdict` is a new key, every existing response field is
unchanged, no `INDEX_VERSION` bump, inline compute (no new background or network
behavior). Tests: `tests/test_v1_95_0.py` (13).

## [1.94.0] - 2026-07-08 - large-corpus stability: vectors out of the monolith, throttled reindex hook, cheap list_repos (#75, #76, #77)

Reported by @floke75 (three linked issues, confirmed on two machines; a 16 GB
box suffered cascading jetsam kills / swap storm / WindowServer watchdog restarts
from the interaction of all three). Additive and 1.x-compatible: `INDEX_VERSION`
stays 3, no forced reindex — existing on-disk indexes keep working and drop their
inline vectors on the next save.

### Changed

- **#75 — embedding vectors live only in the sidecar, never inline in the index
  monolith.** `doc_store` persisted every section's vector inline in
  `~/.doc-index/<owner>/<name>.json`, pretty-printed at `indent=2` (~26 KB of JSON
  per 1024-dim section). On a broadly-indexed repo the monolith reached multiple
  GB and every `load_index` parsed the whole thing into Python lists — ~8 GB RSS
  and ~60 s on a 175k-section corpus, and the vectors were already duplicated in
  the `.embeddings.jsonl` cache. Fix: `_index_to_dict` strips the `embedding` key
  non-mutatingly (in-memory sections keep their vectors for the related/
  boilerplate/dedup sidecars built right after `save_index`), the monolith is
  written with compact `separators=(",", ":")` instead of `indent=2`, and vectors
  rehydrate lazily from the sidecar as `array('f')` (~4 KB per section, not ~70 KB
  as float lists) the first time a semantic code path needs them
  (`DocIndex._rehydrate_embeddings`, called from `_ensure_semantic_matrix`,
  `find_similar_sections`, `get_related_sections`, and the `get_doc_health`
  embedding count). `_has_embeddings` treats a present sidecar as "embeddings
  exist". A save-time safety net writes the sidecar first when sections carry
  vectors but none exists yet, so the strip is always lossless. `load_index` on a
  corpus this size drops from ~60 s / ~8 GB to sub-second / <0.5 GB with unchanged
  ranking (float32 shifts cosine ~1e-7, ordering unaffected outside exact ties).

- **#77 — `list_repos` no longer json-parses every monolith to take two
  `len()`s.** `DocStore.list_repos` (the documented first call of a session, also
  hit by the PreCompact snapshot hook) loaded every index monolith just to read
  `repo`/`indexed_at`/`doc_types` and `len(sections)`/`len(doc_paths)`. Each save
  now writes a tiny `<name>.summary.json` sidecar (atomically, inside the same
  per-repo write lock as the monolith), and `list_repos` reads it instead —
  falling back to the full parse for legacy indexes that predate the sidecar, and
  robust against a single corrupt monolith taking the whole listing down.
  `delete_index` removes the sidecar.

- **#76 — the PostToolUse auto-reindex hook is throttled.** `run_posttooluse`
  spawned one fire-and-forget `index-file` per Edit/Write with no lock, debounce,
  or spawn cap, so a burst of N edits fanned out into N concurrent full-index
  loads (the memory amplifier behind the crash). Now: a per-file leading-edge
  **debounce** (`JDOCMUNCH_HOOK_DEBOUNCE_SECONDS`, default 3 s) coalesces rapid
  repeat edits before anything spawns; the hook spawns a new throttled
  `hook-reindex` worker that acquires one of N cross-process **slot locks**
  (`JDOCMUNCH_HOOK_MAX_REINDEX`, default 2) *before* it loads the index and exits
  if the cap is saturated (the next edit reindexes — correctness holds); and an
  opt-in **breadcrumb log** (`JDOCMUNCH_HOOK_LOG=1` → `_hooks/reindex.log`) makes
  pile-ups/skips observable instead of silently discarded to `DEVNULL`. New
  `hook-reindex` CLI subcommand.

Tests: `tests/test_v1_94_0.py` (21); `tests/test_hooks.py` updated for the new
`hook-reindex` spawn target + a debounce-coalesce case.

## [1.93.0] - 2026-07-07 - MCP readOnlyHint annotations (suite parity with jcodemunch PR #361)

### Added

- **Every tool advertises `ToolAnnotations(readOnlyHint=...)`.** MCP clients that
  gate execution (Claude Code plan mode) prompted for approval on every jDoc
  call because tools carried no annotations. Read tools are now
  `readOnlyHint=True` (plan mode runs them silently) and the write-set is
  `False`. Applied at the `list_tools` chokepoint via a non-mutating
  `model_copy`. The write-set (`index_local`, `doc_index_repo`, `delete_index`,
  `define_repo_group`, `tune_weights`, `check_embedding_drift`) is any tool that
  can mutate persistent state under any argument — biased conservative, since
  mislabeling a writer as read-only is the harmful direction. Suite parity with
  jcodemunch-mcp (PR #361) and jdatamunch-mcp. Additive, 1.x-compatible (new
  `tools/list` field only). Tests: `tests/test_v1_93_0.py` (4).

## [1.70.2] - 2026-06-12 - search/verify/event-loop fixes (#32, #33, #34)

Patch release closing the remaining three issues from @mmashwani's
2026-06-11/12 report batch (the first, #31, shipped in v1.70.1).

**#32 - `search_sections` `path_glob` now pre-filters candidates.**
The glob was a tool-layer post-filter applied AFTER the index-layer top-k
cut (only `role` triggered candidate over-fetch), so a glob naming a single
document returned 0 results with confidence 0.0 whenever that document
didn't rank in the corpus-wide top k - near-certain on large corpora.
`DocStore.search` gains a `path_glob` parameter applied as a candidate
pre-filter in all three modes (lexical, semantic, hybrid) alongside the
existing `doc_path` equality check, via a shared `_path_excluded` helper;
the tool-layer post-filter is removed. Ranking now happens within the
glob-matched set, as documented.

**#33 - `verify_index` accounts for unverifiable sections.**
Sections persisted with an empty byte range (`byte_end <= byte_start`) were
skipped with a bare `continue`, so the failure counters didn't sum to
`section_count` and hundreds of unverifiable sections (e.g. every section
from the structured OpenAPI parser) read as a fully clean index. New
`skipped_count` and `skipped_sections` (reason `"empty_byte_range"`) close
the arithmetic; the invariant `clean + drift + missing + error + skipped ==
section_count` is now tested. The docstring's stale promise to route these
into `missing_sections` is corrected: unverifiable-by-design is a distinct
signal from corruption.

**#34 - `index_local` no longer blocks the MCP event loop.**
The full index + embed pipeline ran synchronously inside the async
`call_tool` handler, monopolizing the server's single asyncio loop past
client tool timeouts; once the client timed out, every subsequent call also
timed out while the server kept working. `index_local` now dispatches via
`asyncio.to_thread`, so cheap tools (`doc_list_repos`, `search_sections`)
stay responsive during long indexing runs. The v1.69.2 cross-process
index-write lock already serializes concurrent same-repo writes. The
larger suggestions from #34 (background job + progress polling, vectorized
cosine scoring) are acknowledged and deliberately deferred.

Additive per the 1.x contract: new defaulted kwarg on `DocStore.search`,
new response keys on `verify_index`, no tool or wire-shape removals.
Regression tests in `tests/test_v1_70_2.py`.

## [1.70.1] - 2026-06-12 - `paths` subset refresh no longer prunes the rest of the index (#31)

Patch release. Fixes a data-loss bug reported by @mmashwani in #31:
`index_local(paths=[...])` (and CLI `index-local --paths-from FILE`) on an
existing incremental index treated every indexed file NOT in the list as
deleted. A refresh of 1-3 changed files collapsed a whole corpus — e.g.
176 files / ~2957 sections reduced to the listed file's 11 sections —
directly contradicting the documented intent of `paths` ("batch-indexing
exactly the files an agent already knows about").

Root cause: `paths` only narrowed which files were read into
`current_files`; `DocStore.detect_changes` then computed deletions as
`old_set - new_set` against the full existing index, so every unlisted
file was pruned by `incremental_save`.

Fix, in `tools/index_local.py`: when `paths` is provided on the
incremental path, the deletion diff is scoped to the requested subset.
Listed files are added/updated; a listed file that no longer exists on
disk is removed; files under a listed directory are diffed against that
subtree; indexed files outside the listed subset are never touched.
Listing the corpus root (`.`) keeps the full-corpus diff, and the
walk-based (no-`paths`) path is unchanged. A subset refresh can now also
process pure deletions (every listed file gone from disk) instead of
failing with "No documentation files found"; that error still applies
when there is no existing index to update.

Additive per the 1.x contract: no tool/response shape changes; the only
behavioral change removes an undocumented destructive side effect.
Regression tests in `tests/test_v1_70_1.py`.

## [1.70.0] - 2026-06-11 - recency window on weight tuning

`tune_weights` now learns from a recency window of the ranking ledger
instead of the lifetime history. New `max_age_days` parameter (default 90;
`0` restores the lifetime read) on the MCP tool, `tune_one_repo`, and
`tune_all_repos`; `ranking_db_query` gains a `window_seconds` filter to
support it.

Previously every ranking event ever recorded for a repo fed the
`semantic_weight` proposal, so as a doc corpus and its query patterns
drifted, stale events kept anchoring the learned weight to a distribution
that no longer exists. Recent research on memory systems documents exactly
this failure mode: accumulated context that can't distinguish current from
stale signal degrades retrieval quality over time. Mirrors jcodemunch-mcp
v1.108.53.

Additive per the 1.x contract: new defaulted kwargs and new response keys
(`max_age_days` on tuner results) only; existing call shapes keep working.

## [1.69.2] - 2026-06-10 - serialize concurrent same-repo index writes (PR #28)

Patch release. Fixes a data race when two processes write the same repo's
index at once (e.g. a scheduled reindex and a per-edit hook). Originally
contributed by @Chrisr6records; the cross-platform lock and the Windows
replace-retry were added here to carry it across the finish line.

jdocmunch rewrites the whole `<name>.json` on every save. The two writers
both wrote a shared deterministic `<name>.json.tmp` and then `os.replace`-d
it into place with no lock, so concurrent writers could install corrupt or
partial JSON (the repo then reads as both "corrupt" and "absent") or silently
lose an update (last-replace-wins on the read-modify-write in
`incremental_save`).

Fix, in `storage/doc_store.py`:
- **Per-PID temp name** (`<name>.json.<pid>.tmp`) so concurrent writers never
  share, and clobber, one temp file.
- **Cross-process write lock** around `save_index` / `incremental_save` (the
  whole read-modify-write), backed by `flock` on POSIX and `msvcrt.locking`
  on Windows, on a per-repo `<name>.json.lock`. This is what closes the
  lost-update window, on both platforms.
- **Bounded replace-retry** (`_atomic_replace`): on Windows a concurrent
  reader holding the destination open makes `os.replace` raise
  `PermissionError` (WinError 5/32) transiently; a brief backoff rides it out,
  then re-raises the original error if it never clears. POSIX `rename` is
  atomic and never hits this.
- `delete_index` cleans up the per-repo `.lock` file.

The PR's original lock was POSIX-only (`fcntl`), which no-op'd on Windows and
left both the lost-update race and the `os.replace` `WinError 5` unfixed
there; both are now covered. Fully additive: no `INDEX_VERSION` change, no
tool/response change, and the default failure mode is unchanged (the retry
never introduces a new raise -- 1.x contract). New regression tests in
`tests/test_concurrent_index_writes.py` reproduce both races across real
processes and pass on Windows and POSIX.

## [1.69.1] - 2026-06-10 - redirect git subprocess stdin to DEVNULL (PR #30)

Patch release. Fixes a Windows-only deadlock that wedged the MCP stdio
server permanently on any `index_local` call. Contributed by @Derjyn.

`_git` and `_git_bytes` spawned git with `stdout=PIPE, stderr=DEVNULL`
but left `stdin` un-redirected. Under the MCP stdio transport the git
child inherited the server's stdin, which is the JSON-RPC pipe from the
client; Git for Windows blocked on that inherited handle and never
exited. The `JDOCMUNCH_GIT_TIMEOUT` guard couldn't recover: the timeout
killed the direct child, but the post-kill `communicate()` drain then
blocked forever joining the reader thread because the `cmd\git.exe`
wrapper chain still held the inherited pipe handles. The event loop
wedged inside the synchronous tool call.

The CLI `index-local` path never hung because its stdin is a console
handle, not a pipe, which made the bug look transport-specific. Both git
and non-git target folders hung (a non-git folder still calls
`local_git_head` -> `git rev-parse --is-inside-work-tree`).

Fix: pass `stdin=subprocess.DEVNULL` in both helpers. Pure no-op for
behavior; none of the spawned git commands (`rev-parse`,
`status --porcelain`, `ls-files`) read stdin. With the patch the same
stdio harness calls complete in 0.1-0.6s.

## [1.66.3] - 2026-05-16 - openai-compatible: probe actual dim at init (jdoc#20)

Patch release. Hardens the openai-compatible provider added in v1.66.0.

## The silent-corruption window

`_OpenAICompatibleProvider` returned `(f"{url}::{model}", None)` from
`_provider_identity()` because the embedding dim was unknown without
calling the endpoint. The on-disk cache (`embeddings/cache.py`) handles
`dim=None` by relaxing the strict dim check to a wildcard.

That composes correctly when the backing model stays put. But if a user
keeps the URL/model env vars constant and swaps the backing model behind
the endpoint -- a realistic Ollama scenario, retagging
`nomic-embed-text` to point at `all-minilm` -- the cache identity still
matches, and old 768-dim vectors get mixed with fresh 384-dim vectors.
Downstream cosine math either crashes on shape mismatch or silently
returns garbage similarity scores.

## The fix

`_OpenAICompatibleProvider.__init__` now embeds a one-token canary at
construction time to discover the endpoint's actual embedding dim, and
stores it on `self.dim`. `_provider_identity("openai-compatible")` reads
that dim out of the cached provider singleton. The cache layer's strict
dim check now engages and a silent backing-model swap forces a clean
re-embed.

Probe failure is non-fatal: `self.dim` stays `None`, the cache layer
falls back to its wildcard-dim behavior (v1.66.0 semantics). Network
outage, misbehaved endpoint, or any other probe error degrades
gracefully.

Cost: one extra round-trip on provider init (per process), once per
session. Cheap.

## Tests

3 new regression tests in `tests/test_openai_compatible_embeddings.py`:
probe-discovers-actual-dim, probe-failure-sets-dim-none, and
identity-uses-probed-dim-when-instance-cached. The four existing tests
that assert on the fake client's `.calls` list were updated to account
for the probe as the first recorded call (skipping `calls[0]` or
asserting the probe explicitly).

Full suite: 1254 passing.

## Cross-suite

When jcm and jdata pick up the openai-compatible provider (jcm#302,
jdata#2), the same probe-at-init pattern should ship in those ports.

## [1.66.2] - 2026-05-16 - warm sentence-transformers before stdio (jdoc#19)

Patch release. Reported by @rknighton on jdoc#19.

The first semantic `search_sections` call hung when sentence-transformers
was the configured provider. The model lazy-loads on first `embed_query`,
and that load (a) can exceed the MCP client's tool-call timeout and
(b) writes progress/download chatter to stdout, corrupting MCP JSON-RPC
framing. Same call worked from a direct Python entry point because
nothing was contending for stdout.

Fix: warm the active embedding provider in `run_server()` before
entering `stdio_server()`. `provider.warmup()` is gated on provider
type -- only sentence-transformers gets warmed (it's the only one
with significant cold-start latency and stdout-leak risk); network
providers (gemini, openai, openai-compatible) are skipped to avoid
an avoidable startup round-trip. The warmup runs inside a
`contextlib.redirect_stdout(sys.stderr)` so any noisy library writes
land somewhere safe.

Warmup failure is non-fatal: server still starts, the first real
embed call retries cleanly.

Regression coverage in `tests/test_hybrid_search.py` (4 new tests):
network providers skip warmup, unconfigured provider skips warmup,
sentence-transformers gets warmed, exception during warmup is
swallowed. Full suite: 1251 passing.

## [1.66.1] - 2026-05-16 - `should_embed("false")` now parses as False (jdoc#18)

Patch release. Reported by @rknighton on jdoc#18.

`should_embed(flag)` resolved any non-empty string via `bool(flag)`, so
`use_embeddings="false"` evaluated to `True` and silently turned
embeddings on. MCP tool inputs that arrive over the wire as JSON strings
(`"false"`, `"0"`, `"no"`) all hit this path.

Fix: recognise common string booleans (case-insensitive, whitespace-
trimmed) before the `bool()` fallback. Recognised truthy: `"true"`,
`"1"`, `"yes"`, `"on"`, `"t"`, `"y"`. Recognised falsy: `"false"`,
`"0"`, `"no"`, `"off"`, `"f"`, `"n"`, `""`. `"auto"` behavior preserved.

Unknown strings still fall through to `bool(flag)` to preserve 1.x
compatibility: a typo like `"flase"` remains truthy as it did before,
rather than silently disabling embeddings. The contract is "we
recognise the obvious cases; we don't change behavior for inputs we
don't recognise."

Regression coverage in `tests/test_hybrid_search.py` (3 new tests, 19
total in that file). Full suite: 1247 passing.

## [1.66.0] - 2026-05-16 - openai-compatible embeddings (PR #17)

Adds opt-in `openai-compatible` embedding provider for any
OpenAI-API-shaped endpoint (Ollama, vLLM, LiteLLM, llama.cpp,
LM Studio, etc.). Contributed by @DevItBetter via PR #17.

Four new env vars, all opt-in, no default-behavior change:

- `JDOCMUNCH_EMBEDDING_PROVIDER=openai-compatible` (required to activate)
- `JDOCMUNCH_OPENAI_COMPAT_URL` (required when active)
- `JDOCMUNCH_OPENAI_COMPAT_MODEL` (required when active)
- `JDOCMUNCH_OPENAI_COMPAT_API_KEY` (optional, defaults to literal
  `"local"`; never falls back to `OPENAI_API_KEY`)
- `JDOCMUNCH_OPENAI_COMPAT_BATCH_SIZE` (optional, default 32)

Design highlights worth preserving:

1. **Explicit-only activation.** Never auto-detected. Setting only the
   URL/model without the provider env var returns `None` from
   `get_provider_name()`.
2. **Credential isolation.** Default API key is the literal `"local"`,
   never falls through to `OPENAI_API_KEY`. Closes the bug class where
   a real OpenAI key could leak to a localhost endpoint.
3. **Cache signature** includes URL, model, first-8 of compat key, and
   batch size. Ambient `OPENAI_API_KEY` is excluded.
4. **Provider identity** returns `(f"{url}::{model}", None)`; cache
   layer relaxes the dim check when dim is unknown.

Test coverage in `tests/test_openai_compatible_embeddings.py` (16 tests,
304 lines): provider selection, missing-config handling,
non-auto-detection, credential isolation, batch-size defaults +
overrides + invalid fallback, signature variance, identity, and both
query-cache and section-cache invalidation on model change.

Follow-ups filed: jdoc#20 (pin actual dim via canary at provider init
to close a silent backing-model-swap corruption window),
jcm#302 + jdata#2 (sibling-parity ports).

## [1.65.0] - 2026-05-14 - prefer-newest walk order on truncation (jdoc#16)

Follow-up to jdoc#15 (@LuigiNicaPRO). When the corpus exceeds `max_files`
and truncation kicks in, the previous walker took the first `max_files`
in filesystem-walk order -- non-deterministic from the user's
perspective. A file edited 4 minutes before the index call could be
silently dropped while older files made the cut. Reported by
@LuigiNicaPRO as suggestion #4 on jdoc#15; deferred to its own ship.

New `sort_by` kwarg on `index_local` and `discover_doc_files`:

- `sort_by="newest"` **(new default):** when `discovered > max_files`,
  sorts by mtime descending so the indexed subset is always the N
  most recently-edited files. Recent edits are always in the index
  regardless of filesystem-walk position.
- `sort_by="walk_order"`: pre-1.65 behavior. Useful for deterministic
  reproducible builds where mtimes shift but content doesn't.

The sort only runs on the truncation path (`discovered > max_files`),
so corpora under the cap pay zero cost. mtime is captured in the same
`stat()` call that already does the size check, so no extra syscalls
either.

Regression coverage in `tests/test_index_local_sort_by.py` (6 tests).

## [1.64.2] - 2026-05-14 - silent truncation footgun in `index_local` (jdoc#15)

Reported by @LuigiNicaPRO: `index_local()` on a 5,705-file Obsidian Vault
returned `success: true` and `file_count: 498` with no programmatic
signal that ~90% of the corpus had been silently dropped. Default
`max_files=500` was buried in the schema; the cap-hit hint was a
free-text `note` string in the response.

Four fixes:

1. **Default `max_files` raised from 500 to 10,000.** Modern doc repos
   and Obsidian Vaults routinely exceed 500.
2. **Walker counts past the cap** (up to a 20x safety ceiling) so
   `discovered` reflects the true corpus size, not the cap. Returns a
   new tuple shape `(files, warnings, discovered_count)`.
3. **Structured top-level truncation fields**: when the cap is hit, the
   response now includes `truncated: true`, `discovered: <total>`,
   `indexed: <max_files>`. Programmatic detection is trivial:
   `if result.get("truncated"):`. When the corpus fits, `truncated:
   false` is set explicitly.
4. **Structured warning entry** in the existing `warnings` array
   alongside the legacy `note` string (kept for back-compat).

Both the full-index and incremental code paths surface the new fields.

Walker order is still filesystem order -- prefer-newest is a useful
future enhancement (@LuigiNicaPRO's suggestion #4) but lands cleanest
in a separate ship since it changes which subset gets indexed, not
just how truncation is reported.

Regression coverage in `tests/test_index_local_truncation.py` (6 tests).

## [1.64.1] - 2026-05-14 - O(N^2) hang in `related_persist.build()` (jdoc#14)

Reported by @LuigiNicaPRO with a py-spy backtrace and a working local
patch in hand: `index_local` on a 10-20k-section repo hung at 100% CPU
on a single thread. The docstring claimed `build()` was O(N) on
structural edges; it was actually O(N^2) on two stacked patterns:

1. `section_dicts` was rebuilt inside the per-section loop on every
   iteration -- O(N) work x N iterations = O(N^2) before any neighbor
   computation began.
2. `structural_neighbors()` rebuilt its by-id map and called
   `_children_of(parent_id, sections)` up to 4 times per section, each
   a linear scan -- another O(N) per outer iteration.

Fix: precompute `section_dicts`, the by-id map, and a new
parent->children map once before the loop and thread them into the
per-section calls via two new optional kwargs on `structural_neighbors`
and `semantic_neighbors`. External callers ignore the new kwargs and
keep the original behavior bit-for-bit -- the cache parameters are
prefixed `_` to mark them as internal hot-path use only.

Bench (Windows / Python 3.14): the fixed path indexes 10k sections in
~0.6s. The pre-fix path on the same input ran for minutes before being
killed.

Regression coverage in `tests/test_related_persist_perf.py`: asserts
the build scales linearly between 2k and 4k sections (ratio <3.5x) and
that 15k completes in <5s.

## [1.64.0] - 2026-05-14 - `tool_profile` + `disabled_tools` config (#297)

Reported by @AlexJ-StL in #297: Google Antigravity caps MCP-server tool
counts at 50, but jdocmunch shipped 60 tools with no way to trim them
short of disabling the whole server. Sibling-parity gap with jcm, which
has had `tool_profile` and `disabled_tools` since v1.78.

Two new env-var-driven knobs in `server.py`:

- `JDOCMUNCH_TOOL_PROFILE=core|standard|full` (default `full`).
  - `core` (13 tools): index + the navigation/search essentials.
  - `standard` (~50 tools): core + analysis/cross-reference tools.
  - `full` (60 tools): everything, current behavior.
- `JDOCMUNCH_DISABLED_TOOLS=tool1,tool2,...` removes named tools from
  both the listed schema and the call dispatcher. Composes with
  `tool_profile`.

Filtering is enforced in `list_tools()` (schema visibility) AND
`call_tool()` (call-time rejection) so a client that cached the schema
gets a clear error if it invokes a disabled tool. `jdocmunch_guide`
survives tier filtering (so a one-line CLAUDE.md keeps working at any
tier) but honors `disabled_tools` (it's documentation, not a control
surface) -- mirrors jcm v1.108.8's issue-#298 resolution.

Antigravity users with the full munch suite can now run:

```jsonc
// per-server env vars
"jdocmunch": { "env": { "JDOCMUNCH_TOOL_PROFILE": "core" } }
```

to fit under the 50-tool cap.

## [1.63.3] - 2026-05-13 - `jdocmunch_guide` sibling-parity tool

Adds `jdocmunch_guide` -- the doc-MCP sibling of `jcodemunch_guide` (jcm
since v1.84.0). Returns the version-current CLAUDE.md / AGENT.md policy
snippet for jdocmunch-mcp so an agent can keep a one-line CLAUDE.md
(`"Call jdocmunch_guide and strictly follow its instructions."`) instead
of pasting a static block that drifts from the installed version.

Backstory: GitHub issue #296 (Codex Desktop compatibility report by
@rknighton) noted that jcodemunch-mcp ships a guide tool but jdocmunch-mcp
doesn't, leaving agents told to call `<pkg>_guide first` without an
onboarding entry point for the doc surface. Sibling parity closes the
gap. Companion v1.12.2 release of jdatamunch-mcp ships `jdatamunch_guide`
on the same shape.

Tool count 59 -> 60. No tool, schema, or wire-format change for existing
tools. 1205 tests pass (1199 + 6 new in `test_v1_63_3.py`).

## [1.63.2] - 2026-05-12 - drift-proof __version__ via importlib.metadata

`src/jdocmunch_mcp/__init__.py` now derives `__version__` from
`importlib.metadata.version("jdocmunch-mcp")` instead of a hardcoded
literal. Reads the wheel's metadata at import time, so pyproject.toml
and the runtime version string can no longer disagree by construction.

Backstory: v1.63.0 shipped with the hardcoded `__version__` stuck at
1.60.0 (three minors stale) because nothing cross-checked it against
pyproject. v1.63.1 added a `tests/test_version_sync.py` regex guard,
but jcodemunch-mcp already had a better pattern. This release ports
that pattern over and retires the test (no longer reachable code).

When run from a source checkout without pip install, `__version__`
resolves to `"unknown"`. The replay-runner's `_resolve_version()`
already falls back to parsing `pyproject.toml` in that case, so
baseline-result filenames stay correct on source builds.

No tool, schema, or wire-format changes.

## [1.63.1] - 2026-05-12 - CI green: fixture query rename + full-history checkout

Patch release that turns master green again. Two independent CI fixes,
no behavior change for installed users.

1. Replay fixture: the `wiki stats` query in `self_v1_11_0.json` collided
   with the `### Stats` H3 subheadings that v1.62.0 and v1.63.0 hand-added
   to CHANGELOG.md. BM25 ranked those short, dense sections above the
   target wiki-benchmark page, dropping MRR from 1.0 to 0.925 (over the
   0.06 gate). Renamed to `wiki benchmark`. Expected target returns to
   rank 1 with a clean margin and the slug `jdocmunch-mcp-wiki-benchmark`
   is the unambiguous lexical anchor for it.
2. Workflow checkout: both `test.yml` and `replay.yml` now set
   `fetch-depth: 0` on `actions/checkout@v5`. The shallow default broke
   `tests/test_v1_35_0.py::TestChangelogGenerator::test_runs_against_real_repo`
   on any push whose HEAD wasn't itself a `release:` commit, because
   `scripts/generate_changelog.py` walks `git log` for release subjects
   and a depth-1 clone had none to match.

No tool, schema, or wire-format changes. v1.63.1 baseline result captured
at `benchmarks/replay/results/self_v1_11_0-v1.63.1.json` (1.0 / 1.0 / 1.0).

## [1.63.0] — 2026-05-12 — `get_doc_pr_risk_profile` (Phase-2 sibling-parity COMPLETE)

Composite doc-PR risk profile. Fuses five orthogonal signals over a
caller-supplied list of changed sections into a 0-1 `risk_score` with
overall `risk_level` (low / medium / high / critical), a ranked top-5
list of blockers, and a one-line `recommended_action`. Mirrors jcm's
`get_pr_risk_profile`.

### Signals

| Signal                | Source                                              |
|-----------------------|-----------------------------------------------------|
| `volume`              | changed sections / total sections (×10 cap)         |
| `blast_radius`        | mean blast_score for modified + deleted sections    |
| `backlink_burden`     | avg inbound references per changed section / 5     |
| `tutorial_disruption` | % of changes on tutorial chains                     |
| `role_weight`         | % of changes hitting tutorial/reference/guide roles |

Weights: `volume 0.15 + blast 0.30 + backlinks 0.20 + tutorial 0.20 + role 0.15`.
Thresholds: `≤0.25 low / ≤0.50 medium / ≤0.75 high / >0.75 critical`.

### Input shape

Caller passes `changed_sections` as either bare section IDs (str,
defaults to `kind=modified`) or `{section_id, kind}` dicts where
`kind ∈ {added, modified, deleted}`. Added sections skip backlink
lookup since they cannot have inbound refs yet.

The tool does **not** diff anything itself — pair with `get_recent_changes`
or compute the list from `git diff` in your CI step.

### Stats

- Tool count: 59 (+ `get_doc_pr_risk_profile`)
- Tests: 1196 passed (+12 new — 5 pure-function + 7 integration)

This completes Phase 2 of the sibling-parity PRD across all three munches.

---

## [1.62.0] — 2026-05-12 — `doc_health_radar` + `diff_doc_health_radar`

Six-axis health radar for documentation indexes, plus a pure-function
diff helper. Third leg of the suite-wide radar pattern (jcm's
`health_radar.py` + jData's `data_health_radar`).

### Axes

Each axis scores 0-100 (higher = healthier):

| Axis                | Source                                              |
|---------------------|-----------------------------------------------------|
| `freshness`           | fresh / (fresh + edited + stale) × 100            |
| `link_integrity`      | linear penalty per broken link (relative to sections) |
| `orphan_health`       | linear penalty per orphan section                 |
| `embedding_coverage`  | embedded sections / total sections × 100          |
| `role_coverage`       | sections with non-unknown role / total × 100      |
| `drift_health`        | canary clean → 100; alarm → 0; no canary → omitted|

`freshness` is omitted when section_count is zero. `drift_health` is
omitted when no embedding-drift canary has been captured. Omitted axes
appear in `omitted_axes` and never silently penalise the composite —
radars stay comparable across repos with different setup states.

### `diff_doc_health_radar`

Pure function: takes two radar payloads, returns per-axis deltas,
composite delta, grade change, regression + improvement lists at a
3-point threshold, and a one-line verdict. No I/O.

### Stats

- Tool count: 58 (+ `doc_health_radar`, `diff_doc_health_radar`)
- Tests: 1184 passed (+12 new; 12 pre-existing baseline-gate failures unaffected)

---

## [1.61.0] — 2026-05-12

### New: explicit-paths indexing

`index_local` gains a `paths=[...]` parameter that bypasses the directory
walk and indexes only the listed files / subdirs. Each entry can be
absolute or relative to the `path` root. Useful for batch-indexing
exactly the doc files an agent already knows about — e.g. *the docs git
just touched*, *the pages in this PR's diff*, *the markdown matched by
fd / rg* — without the cost (or surprise) of a full-tree walk.

Security: explicit paths are validated the same way as walk-discovered
files — entries outside the root, path-traversal attempts, and symlink
escapes are rejected with per-entry `warnings`. Unsupported extensions
are warned-and-skipped rather than silently passed.

CLI: new `--paths-from FILE` flag on `jdocmunch-mcp index-local`. Use
`-` for stdin to make the command pipe-friendly with `find`, `fd`,
`fzf`, and `rg`:

```bash
git diff --name-only HEAD~5 -- '*.md' \
  | jdocmunch-mcp index-local --path docs/ --paths-from -
```

Empty input is treated as an error so the command doesn't silently fall
through to a full-tree index. Lines beginning with `#` are skipped.

### Notes
- Fully additive — `paths` defaults to `None`, preserving every existing
  call shape. The MCP `index_local` tool's `inputSchema` gains an
  optional `paths: list[string]` field with the same semantics.
- 10 new tests in `test_v1_61_0.py`. 1174 passed.

## [1.60.0] — 2026-05-11

### New: `find_similar_sections` — multi-signal dedup detection

Every wiki of size accumulates "three pages that all say the same
thing." This tool surfaces them. Multi-signal scoring fuses embedding
cosine (when the index has embeddings) with title + body lexical
Jaccard, gated by a cheap title-token pre-filter to keep cost bounded
on large wikis.

Output is cluster-shaped: one entry per group of overlapping sections,
each with a `canonical` (recommended keeper, ranked by backlink_count +
byte_length) and `variants` to fold in. Verdict tiers per cluster:

- `near_duplicate` — combined score ≥ `near_duplicate_threshold` (0.92)
- `overlapping_topic` — combined score ∈ `[min_score, threshold)`
- `parallel_tutorial` — cluster members live in different doc
  directories (suggests parallel guides that should cross-reference
  rather than be merged)

Defaults: `min_score=0.7`, `max_clusters=50`, `max_sections=1000`.
Parser-artifact filter drops zero-byte-range wrapper sections so they
don't cluster with their own heading-level twins.

Read-only. Inspired by `find_similar_symbols` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §6.2). **Completes the jDoc Phase-1
batch from the sibling-parity PRD** (joins `check_section_delete_safe`
+ `get_section_blast_radius`).

### New: `get_section_blast_radius` — transitive impact of a section change

Companion to `get_backlinks` (which is depth 1 only). Walks the inbound
reference graph to `max_depth` (default 3) and classifies each hit as
`anchor` (link targets this section's slug), `doc` (link targets the
enclosing doc), or `tutorial` (section appears in a Next/Prev / toctree
chain).

Returns `direct_impact` (depth 1), `transitive_impact` (depth ≥ 2), a
`summary` of counts, and a normalised `blast_score` in [0, 1] so blast
radius is comparable across sections of different size.

Read-only. Inspired by `get_blast_radius` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §6.3).

### New: `check_section_delete_safe` — composite deletion preflight

First Phase-1 deliverable from the sibling-parity PRD. Answers the
question every wiki maintainer asks every week: *can I safely remove
this section?*

Fuses four channels into a single verdict plus up to five ranked
blockers and a one-line `recommended_action`:

1. **Tutorial-path membership** — section is part of a Next/Prev chain,
   Sphinx toctree, VuePress sidebar, or ordered-filename sequence. High
   severity — deleting breaks readers walking the chain.
2. **Anchor-specific backlinks** — other sections link to `doc#slug`.
   High severity — those anchored links 404 once the section is gone.
3. **Transitive doc-level backlinks** — BFS over inbound refs to
   `transitive_depth` (default 3). Medium severity above a threshold of
   3 referers.
4. **Recent-edit recency** — section's source touched within
   `recent_edit_days` (default 14), or sits in FreshnessProbe's
   `edited_uncommitted` bucket. Low severity — defer deletion.

Verdict tiers (highest first): `tutorial_path_blocking`,
`anchor_referenced`, `backlinks_blocking`, `recently_edited_blocking`,
`safe_to_delete`.

Read-only. Composes existing primitives (`get_tutorial_path`,
`get_backlinks`, `FreshnessProbe`) — no new persisted state, no
INDEX_VERSION bump.

Inspired by `check_delete_safe` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §6.1).

## [1.9.0] — 2026-04-19

### New: Hybrid BM25 + semantic search

- **`search_sections` now fuses lexical and semantic scores** when the index has embeddings. New parameters match jcodemunch-mcp's shape:
  - `semantic` — `null`/omit (auto — hybrid when embeddings exist), `true` (force hybrid), `false` (force lexical-only)
  - `semantic_only` — skip lexical entirely, rank purely by embedding cosine
  - `semantic_weight` — 0.0–1.0 weight of the semantic channel in fusion (default 0.5)
- Each channel min-max-normalized to [0,1] within the candidate set, then weighted sum. When `embed_query` returns `None` (provider disabled at query time), hybrid gracefully degrades to lexical. Zero performance impact when the index has no embeddings.
- `_meta.search_mode` now reports one of `hybrid`, `semantic_only`, or `lexical` (replacing the previous binary `semantic`/`lexical`). `_meta.semantic_weight` is surfaced on hybrid calls.

### New: `use_embeddings="auto"` default

- `index_local` and `doc_index_repo` now default `use_embeddings` to `"auto"` — embeddings are generated automatically whenever an embedding provider is configured (`GOOGLE_API_KEY`, `OPENAI_API_KEY`, or sentence-transformers installed). Explicit `true`/`false` still honored.
- `index-file` now preserves embedding parity: when re-indexing a single file into an index that already has embeddings, the new sections get embedded too (previously left empty).

### Tests

- 16 new tests covering `should_embed` flag resolution, hybrid fusion ranking, `semantic=False` short-circuit, semantic-only, `semantic_weight=0` reduction to lexical, graceful degradation, and search_mode reporting (400 total).

## [1.8.1] — 2026-04-15

### Documentation
- **Hermes Agent integration** — added "Works with" section to README with Hermes Agent config example; submitted optional skill PR to [NousResearch/hermes-agent#10413](https://github.com/NousResearch/hermes-agent/pull/10413)

## [1.7.1] — 2026-04-09

### New features

- **`meta_fields` support** — control which `_meta` fields appear in tool responses via `JDOCMUNCH_META_FIELDS` env var. Matches jcodemunch-mcp's `meta_fields` affordance. Values: unset/`[]` = strip `_meta` entirely (default, maximum token savings), `null`/`all`/`*` = include all fields, comma-separated list = include only those fields (e.g. `timing_ms,powered_by`).

### Tests

- 11 new tests for meta_fields config parsing and filtering (358 total)

## [1.7.0] — 2026-04-09

### New: Full `init` onboarding

- **`jdocmunch-mcp init`** — One-command setup matching jcodemunch-mcp's UX:
  - Detects installed MCP clients (Claude Code CLI, Claude Desktop, Cursor, Windsurf, Continue)
  - Patches each client's config JSON to add jdocmunch as an MCP server
  - Installs a Doc Exploration Policy into CLAUDE.md (global or project scope)
  - Installs Cursor rules (`.cursor/rules/jdocmunch.mdc`) and Windsurf rules (`.windsurfrules`)
  - Installs enforcement hooks (PreToolUse, PostToolUse, PreCompact)
  - Indexes the current working directory
  - Supports `--dry-run`, `--demo`, `--yes`, `--no-backup`, `--client`, `--claude-md`, `--hooks`, `--index`
  - Interactive prompts for scope selection when run in a terminal

### New: `claude-md` subcommand

- **`jdocmunch-mcp claude-md`** — Print the Doc Exploration Policy to stdout
- **`jdocmunch-mcp claude-md --install global|project`** — Append policy to CLAUDE.md (idempotent)

### New: `index-file` single-file re-index

- **`jdocmunch-mcp index-file <path>`** — Re-index a single doc file within an existing index without re-walking the entire folder. Finds the owning index automatically, re-parses, and updates in place via incremental_save.
- PostToolUse hook now spawns `index-file <path>` instead of `index-local --path <dir>` for faster, more targeted re-indexing after edits.

### Tests

- 20 new tests for client detection, config patching, CLAUDE.md injection, Cursor/Windsurf rules, claude-md command, index-file tool, CLI dispatch (347 total)

## [1.6.0] — 2026-04-09

### New: CLI hook system for Claude Code

- **`hook-pretooluse`** — PreToolUse hook that intercepts `Read` on large doc files (.md, .rst, .adoc, .txt, etc.) and suggests `search_sections` + `get_section` instead. Warns via stderr; allows the read to proceed (Edit workflow requires Read first).
- **`hook-posttooluse`** — PostToolUse hook that auto-reindexes after `Edit`/`Write` on doc files. Spawns `jdocmunch-mcp index-local` as a fire-and-forget background process.
- **`hook-precompact`** — PreCompact hook that generates a session snapshot (indexed repos, doc/section counts) before Claude Code context compaction, injected as `systemMessage`.
- **`index-local --path <dir>`** — CLI equivalent of the MCP `index_local` tool, callable from shell hooks without a live MCP session.
- **`init --hooks`** — One-command installer that merges all three enforcement hooks into `~/.claude/settings.json`. Additive (preserves existing hooks), creates `.bak` backup by default. Supports `--dry-run`.

### Fixed

- Version mismatch between `__init__.py` and `pyproject.toml` — both now track 1.6.0.

### Tests

- 29 new tests for hooks + init (327 total)

Closes [#8](https://github.com/jgravelle/jdocmunch-mcp/issues/8). Thanks @Will-Luck for the detailed feature request.

## [1.5.3] — 2026-04-07

### Changed
- Switch MCP tool responses from pretty-printed JSON to compact JSON — saves 30-40% tokens per response (jcodemunch-mcp#219)

## [1.5.2] — 2026-04-06

### Added
- **`contrib/build-deb.sh`** — Community-contributed Debian/Ubuntu packaging script for Proxmox and other Linux deployments. Includes venv isolation, systemd unit, and streamable HTTP wrapper. Contributed by @Tikilou. Closes #7.

## [1.5.0] — 2026-04-01

### New tools

- **`get_broken_links(repo)`** — scan all indexed doc sections for internal cross-references that no longer resolve. Checks markdown `[text](target)` links, RST `:ref:`/`:doc:` directives, and anchor-only links (`#heading`). External links (http/https/mailto) are skipped. Each broken entry reports `source_file`, `source_section`, `target`, and `reason` (`file_not_found` | `section_not_found` | `anchor_not_found`). Pure index scan — no re-reading source files.
- **`get_doc_coverage(repo, symbol_ids)`** — given a list of jcodemunch symbol IDs, reports which symbols are mentioned in section titles (documented) vs absent (undocumented). Bridges jcodemunch ↔ jdocmunch. `symbol_ids` capped at 200. Output: `{documented, undocumented, coverage_pct}`.

### Tests

- 26 new tests (298 total)

## [1.4.6] — 2026-03-31

### Housekeeping

- Added `LICENSE` file (dual-use: free for non-commercial, paid for commercial)

## [1.4.0] — 2026-03-13

### New features

- **`get_section_context` tool** — returns a target section's full content alongside its ancestor heading chain (root→parent) and immediate child summaries, all under a configurable `max_tokens` budget. Eliminates the need for whole-file reads when a section alone is too thin to answer a question.
- **sentence-transformers embedding backend** — fully offline embeddings via `sentence-transformers` (default model `all-MiniLM-L6-v2`, override with `JDOCMUNCH_ST_MODEL`). Auto-detected as fallback after Gemini/OpenAI. Nothing leaves the machine.
- **tiktoken-aware token counting** — `count_tokens()` in `storage/token_tracker.py` uses `tiktoken` when installed (cl100k_base), falling back to bytes/4 when not present. Opt-in: no new required dependency.
- **`incremental` parameter on `index_local` and `index_repo`** — callers can now pass `incremental: false` to force a full re-index without deleting the existing index first.

### Performance and correctness

- **In-memory index cache** — `load_index()` now caches parsed `DocIndex` objects keyed by path + `mtime_ns`. Zero `json.load()` calls on repeated tool calls against the same unchanged repo.
- **True incremental GitHub indexing** — `index_repo(incremental=True)` now fetches the HEAD commit SHA first and exits immediately (no tree or file fetches) when the SHA matches the stored value. HEAD SHA stored in the index.
- **Hierarchical section IDs** — slugs are now prefixed with the ancestor heading chain (e.g. `installation/prerequisites` instead of bare `prerequisites`). A new heading inserted in one branch no longer renumbers IDs in other branches. `INDEX_VERSION` bumped to `2` — existing indexes are automatically re-indexed on first access.

### Documentation

- SPEC, ARCHITECTURE, USER_GUIDE, and README audited and reconciled against code reality
- `verify` parameter correctly described as cache integrity verification, not live-source drift detection
- Section ID format updated to show hierarchical slug paths
- Embedding environment variables (`OPENAI_API_KEY`, `JDOCMUNCH_EMBEDDING_PROVIDER`, `JDOCMUNCH_ST_MODEL`) documented throughout

### Tests

- 8 new `get_section_context` tests (248 → 256 total)

---

## [1.1.0] — 2026-03-08

- OpenAPI 3.x / Swagger 2.x parser (`parser/openapi_parser.py`)
- `.yaml`, `.yml`, `.json` files content-sniffed: indexed when spec contains `openapi:` or `swagger:` key; skipped otherwise
- Operations grouped by tag → `## Tag` sections; each endpoint becomes a `### METHOD /path` subsection with parameters, request body, and responses rendered
- Schemas / Definitions section appended with property types and required markers
- `pyyaml>=6.0` already a hard dependency (no new deps)
- 25 new tests (176 → 201 total)

---

## [1.0.0] — 2026-03-07

First stable release. API is now frozen under semantic versioning — no breaking
changes without a major version bump.

### Stable feature set

**Document formats** (11 formats, 14 extensions):
- `.md`, `.markdown`, `.mdx` — Markdown (ATX + setext headings, MDX preprocessing)
- `.txt` — plain text paragraph splitting
- `.rst` — RST heading/adornment parser
- `.adoc`, `.asciidoc`, `.asc` — AsciiDoc `=` heading parser
- `.ipynb` — Jupyter notebook JSON → Markdown conversion
- `.html`, `.htm` — HTML → text conversion, chrome stripped
- `.yaml`, `.yml`, `.json` — OpenAPI 3.x / Swagger 2.x specs (content-sniffed)

**Indexing**
- Incremental indexing: hash-based change detection, only changed/new files re-parsed, atomic save
- Full indexing with gitignore-aware file discovery and security filtering

**Retrieval**
- O(1) section lookup via `__post_init__` id→section dict
- Byte-offset content retrieval with SHA-256 content hash verification
- Token savings tracking (raw file size vs. section response size)

**AI summaries**
- Claude Haiku (`ANTHROPIC_API_KEY`) or Gemini Flash (`GOOGLE_API_KEY`) for section summaries
- Graceful fallback to heading text when no AI key is set

**Security**
- Path traversal protection on all file I/O
- Secret file detection (`.env`, `.pem`, credentials, keys)
- Binary file filtering
- Max file size enforcement

**Test coverage**: 201 tests passing.

### Breaking changes from 0.x
None — the index schema and MCP tool interface are unchanged from 0.1.x.

---

## [0.1.5] — 2026-03-07

- OpenAPI/Swagger parser (`parser/openapi_parser.py`)
- `.yaml`, `.yml`, `.json` added to `ALL_EXTENSIONS` with content sniffing
- `pyyaml>=6.0` added as a hard dependency
- 25 new tests (176 → 201)

## [0.1.4] — 2026-03-07

- Incremental indexing for both `index_local` and `index_repo`
- `DocStore.detect_changes()` and `DocStore.incremental_save()`
- O(1) section lookup via `DocIndex.__post_init__`
- `time.time()` → `time.perf_counter()` across all tools
- 7 new incremental indexing tests (169 → 176)

## [0.1.3] — 2026-03-06

- HTML parser (`parser/html_parser.py`): `<h1>`–`<h6>` → Markdown headings, chrome stripped
- Double `load_index()` fix: `_index` parameter on `get_section_content`
- Token savings: `os.path.getsize()` replaces per-section content summing

## [0.1.2] — 2026-03-05

- Jupyter notebook parser (`parser/notebook_parser.py`)
- AsciiDoc parser (`parser/asciidoc_parser.py`)
- RST parser (`parser/rst_parser.py`)
- Plain text paragraph parser (`parser/text_parser.py`)

## [0.1.1] — 2026-03-04

- Markdown parser with ATX + setext heading support
- Section hierarchy wiring (`parser/hierarchy.py`)
- `DocStore` with atomic save, path traversal protection, secret file detection
- MCP tools: `index_local`, `index_repo`, `get_section`, `get_sections`, `get_toc`,
  `get_toc_tree`, `get_document_outline`, `search_sections`, `list_repos`, `delete_index`
