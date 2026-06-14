# jdocmunch-mcp

**Version:** 1.77.0 | **Tests:** `pytest tests/ -q` (1388 passed)

## v1.77.0 - get_broken_links: fs existence + GitHub anchors + scheme parsing (#49, #50, #47.6)
Second links-group release (tracking #61); the consumer-layer link-health
fixes, no reindex needed. **#49:** existence was tested only against the indexed
doc set, so links to existing non-doc files (images, LICENSE, source) reported
`file_not_found`. Now the filesystem is consulted against `source_root` before
flagging; with no `source_root` (GitHub indexes) non-doc extensions aren't
claimed missing. **#50:** `#anchor` links were validated against jdocmunch's
private hierarchical slug scheme, which diverges from rendered GitHub anchors on
four axes (underscore flattening, hyphen-run collapse, `-2`-vs-`-1` duplicate
suffix, raw-markup leakage). New `_build_github_anchors` derives the
GitHub-rendered namespace per document (rendered text via `_rendered_text` +
`_github_slug` with github-slugger rules), accepted alongside the existing forms
so valid rendered anchors stop being flagged. (Accepting alongside fixes the
false-positive direction, the dangerous one for a link checker; github-dead but
private-valid anchors like `#install-2` stay unflagged, a documented limit.)
**#47 symptom 6:** the blanket colon-skip silently dropped typo'd/unknown
schemes; a scheme prefix that isn't a known-external scheme now reports
`unknown_scheme`. Bare email autolinks are treated as external. Closes #47
(extraction shipped in 1.76.0). Tests: `tests/test_v1_77_0.py` (7). Next: #59
inline_code bridge, then the frontmatter/scoring tail (#54, #58, #60).

## v1.76.0 - extract_references rewrite: inline grammar + reference defs (#47, #48)
First of three links-group releases (tracking #61); the parser-layer reference
extractor. **#47 (High):** `references` was built by two naive regexes over the
raw section body, so titled links `[t](x "title")` and angle-bracket
destinations `[t](<a b>)` stored the junk verbatim (broken-link false positive
+ backlink-zero false orphan from one defect), images were file-checked,
parenthesized URLs truncated at the first `)`, autolinks/bare URLs kept trailing
`>`/`.`/`,`, and link syntax shown inside code fences/spans became permanent
"broken" links. `extract_references` is now a proper inline pass: scrub fenced
code + inline code spans + HTML comments, then match inline links (images
skipped, titles + `<...>` stripped, one level of balanced parens for wiki URLs),
autolinks (`<https://...>` and `<user@email>`), and bare URLs (trailing
punctuation trimmed); empty `[t]()` destinations skipped. **#48 (High):**
reference-style links (`[t][ref]`, `[t][]`, `[t]`) contributed no edge; the
`[ref]: target` definition targets are now captured (file-level edges for
broken-links / backlinks / orphans / blast-radius). Section-precise resolution
of reference usages is a noted follow-up. **Reindex required** (rebuilds the
stored `references` artifact). Tests: `tests/test_v1_76_0.py` (11). Next:
get_broken_links fixes (#47 symptom-6 scheme parsing + #49 fs-existence + #50
GitHub-anchor namespace), then #59 inline_code bridge.

## v1.75.0 - parse-loop block detection: indentation + HTML blocks (#43, #45)
Second half of the block-detection sub-group (tracking #61); the two
parse-loop-rewriting fixes, landed together since both classify lines before
heading detection. **#43 (High):** every block-start regex ran against the raw
column-0 line, so ATX/setext headings indented 1-3 spaces folded into the
previous section and indented/list-nested fences never opened (their interiors
parsed as markdown, minting phantoms; `find_code_examples` went blind).
parse_markdown now computes leading indent: ATX/setext detection runs on a
dedented view when indent <= 3 (4+ stays indented code, not a heading), and
fence opens are indent-tolerant (`line.lstrip(" ")`, matching the already-loose
close side) so list-nested runbook fences open. **#45 (High):** the loop had no
HTML-block state, so `#`/`---` lines inside `<!-- -->`, `<script>`/`<pre>`/
`<style>`/`<textarea>`, and `<div>`-class blocks became phantom indexed
sections (hidden comment text searchable, raw code as section titles). New
CommonMark HTML-block state machine (`_html_block_start`, types 1-7) suppresses
heading detection inside HTML blocks like the fence machine does; type 1-5 end
inclusively on their close marker, type 6-7 at the first blank line, type 7
can't interrupt a paragraph, single-line blocks never enter the state. Byte
ranges / content_hash unchanged (invariant holds across HTML regions).
**Deferred:** #51's 4-space indented-code extraction stays out, it needs a list/
container model the parser lacks (4-space list-continuation prose would be
misclassified as code); documented limitation. Blockquote/list-item heading
sectioning (`> # X`) is a follow-on per #43 (anchor side is #50). **Reindex
required** (clears phantoms, picks up indented headings/fences). Tests:
`tests/test_v1_75_0.py` (10). Block-detection sub-group complete; remaining:
links (#47-50, #59) + frontmatter/scoring (#54, #58, #60).

## v1.74.0 - markdown block-detection, isolated set (#46, #51, #56, #57)
First half of the block-detection sub-group (tracking #61); the four fixes that
don't rewrite the parse loop. **#46 (High):** `strip_mdx` ran its import/export
+ JSX removers over the whole `.mdx` document, mutilating code inside fences and
storing the corrupted text as the hash-verified mirror. Now fence-aware: split
into `strip_mdx` (frontmatter + mermaid whole-doc, then segment on backtick/
tilde fences) + `_strip_mdx_plain` (the substitution pipeline, applied only to
non-fence regions). **#51 (bug half):** code blocks were emitted only on fence
close, so a fence left open at EOF was buffered then dropped; now flushed before
the final `_finalize_section` (CommonMark closes an unterminated fence at EOF).
The 4-space indented-code enhancement is descoped (documented limitation; lands
with the #43 indentation work). **#56:** a leading `---` thematic break plus a
later bare `---` was read as frontmatter, silently swallowing every heading
between; `_frontmatter_end_line` now rejects the opener when the next line is
blank (pandoc's discriminator). **#57:** `extract_tags` ran over the raw body
incl. fenced code + YAML frontmatter, so `#include`/`#fff`/YAML values became
corpus tags; new `_prose_view` blanks fenced-code byte ranges + the frontmatter
span (via a C-speed translate table) before tag extraction. Content/byte/hash
untouched. **Reindex required** for #46/#57 (changes stored mirror / tags).
Tests: `tests/test_v1_74_0.py` (9). Next: #43 (indentation) + #45 (HTML-block
state machine), the two loop-rewriting fixes.

## v1.73.0 - CLI/config ergonomics batch (#36-41)
Cluster B of @mmashwani's batch (tracking #61); six independent CLI/config gaps,
several with jcodemunch precedent. **#36 (High, operator-control bypass):**
`JDOCMUNCH_DISABLED_TOOLS` was enforced against the literal incoming tool name,
so a deprecated call-time alias (`index_repo`/`list_repos`) reached the disabled
canonical handler unchecked. New module-level `_ALIAS_TO_CANONICAL` map resolved
at the top of `call_tool` BEFORE the gate, so disabling a canonical tool blocks
every spelling. **#37 (storage split-brain):** `DOC_INDEX_PATH` was honored only
on the MCP dispatch path; `DocStore.__init__`'s default branch now consults it,
so CLI + hooks resolve the same root (explicit `base_path` still wins). **#38:**
`index-file` owner detection was folder-name inference only, unusable for a
custom `--name` index or a non-path-safe (spaced) root; new `--name` arg +
`_resolve_named_index` loads the index directly and derives the rel path from
its `source_root`. **#39:** `init --hooks` wrote bare command names that fail
under Claude Code's minimal-PATH `/bin/sh`; ported jcm's `_hook_invocation()` /
`_enforcement_hooks()` (resolve `shutil.which` to an absolute path at install
time, forward-slash on Windows, quote spaces, bare-name fallback). Dedup marker
widened to `jdocmunch-mcp` so bare/absolute/.EXE spellings collapse to one.
**#40:** `-V`/`--version` flag. **#41:** `delete-index --repo` CLI subcommand
(wraps the existing `delete_index` tool). Tests: `tests/test_v1_73_0.py` (9) +
3 updated `test_hooks.py` assertions for the absolute-path hooks. Byte-space +
CLI clusters both done; remaining: block-detection/links/frontmatter/scoring
tail (#43, #45-51, #54, #56-60).

## v1.72.0 - byte-space fidelity: CRLF preservation + BOM strip (#52, #53)
Third drop of @mmashwani's parser batch (tracking #61); the ingestion /
mirror-space pair. **#52 (CRLF):** `index_local`/`index_file` read with
`Path.read_text` under universal newlines, collapsing `\r\n`/`\r` to `\n`
*before* byte offsets were measured, so every published `byte_start`/`byte_end`/
`content_hash` verified only against a hidden LF-normalized mirror, never the
on-disk file, and disagreed byte-for-byte with the GitHub leg (which preserves
CRLF via `response.text`). Local reads now use
`open(..., encoding="utf-8", errors="replace", newline="")` (Path.read_text
lacks `newline` before 3.13), so offsets/hashes address the real on-disk bytes
and the local path converges onto the already-correct GitHub path. **#53 (BOM):**
a leading UTF-8 BOM (U+FEFF) survived both ingestion legs and broke every
first-line detector (the first ATX heading folded into the root, YAML
frontmatter became a phantom `author: Bar` section, setext titles embedded
U+FEFF). `preprocess_content` now strips one leading BOM as its first step,
fixing all formats + both legs at once and keeping the stored mirror aligned
with the parsed string (so the #55 invariant still holds end-to-end:
`sha256(disk[bs:be]) == content_hash`, verify drift 0 on a fresh CRLF index).
**Reindex required** (CRLF corpora get new disk-accurate offsets/hashes/mirror).
Not in scope: `get_section_diff`'s `@disk` label still compares index-vs-mirror
snapshots, so it can't observe a *post-index* on-disk edit — tracked as a
follow-on on #52. Tests: `tests/test_v1_72_0.py` (4, CRLF end-to-end +
BOM survival + preprocess unit).

## v1.71.0 - CommonMark setext/paragraph correctness + byte/hash invariant (#44, #35, #55)
Second drop of @mmashwani's parser batch (tracking #61); the parser-correctness
core. `parse_markdown` was rewritten around the CommonMark paragraph rule.
**#44 (High):** setext underline detection keyed on a prev-line heuristic
(non-blank + no `|`), so `---`/`===` after a list item, blockquote, fence-close,
or ATX heading fabricated phantom H2s, destroyed the real section as a `[0:0]`
range, or mis-titled it; multi-line setext titles kept only the last line; and
single-dash H2 / pipe-bearing H1 underlines were rejected. Now a `para_lines` +
`para_byte_start` block-state tracker arms only on real paragraph text and
clears on every non-paragraph context (blank, ATX, fence, frontmatter, list,
blockquote, thematic break via new `_LIST_ITEM_RE`/`_BLOCKQUOTE_RE`/
`_THEMATIC_BREAK_RE`); narrow `|` guard on H2 only (H1 never collides with
tables) keeps all five GFM pipe-table shapes from becoming headings.
**#35 + #55:** section bodies are now derived from the byte range
(`content_bytes[byte_start:byte_end].decode()`) instead of a separately
bookkept line buffer, so `sha256(raw[byte_start:byte_end]) == content_hash`
holds by construction. The old setext path hashed a subset of its range and
made `verify_index` report false drift on every setext section; a fresh setext
index now verifies `drift: 0` (was 3) end-to-end. Removed the `current_lines`
body buffer and `prev_line` bookkeeping entirely. **Reindex required** to clear
prior false drift and pick up corrected sections. Tests: `tests/test_v1_71_0.py`
(10, incl. #44 families a-d + the 5-shape table gate + the invariant).
Next: #52 (CRLF disk-byte fidelity) + #53 (BOM), the ingestion/mirror-space pair.

## v1.70.3 - fence-open regex accepts arbitrary CommonMark info strings (#42)
First fix of @mmashwani's 26-issue parser-correctness batch (#42-60) + CLI
batch (#35-41). `_FENCE_OPEN_RE` accepted only an empty info string or one bare
`[\w.+-]` token, so attribute-bearing fences (`` ```python title="x" ``,
`` ```js {1,3} ``, RMarkdown `` ```{r} ``, `` ```c# ``) were not recognized as
openers. The fence state machine then inverted: block body parsed as markdown
(phantom `# comment` sections), the block's bare closing ``` opened a phantom
`lang=""` fence that swallowed every real heading after it until the next bare
fence or EOF, and code blocks were lost - and the corruption self-verified
green (`get_section_diff` `identical:true`, `check_section_delete_safe`
`safe_to_delete` over real content). Fix: widen to CommonMark 4.5
(`r"^(`{3,}(?!.*`)|~{3,}).*$"` - backtick fences reject a backtick in the info
string, tilde fences accept anything); strip RMarkdown `{}` from `fence_lang`
so `{r}` filters as `r`. Existing indexes built over such fences carry phantom
+ missing sections and need a reindex. Tests: `tests/test_v1_70_3.py` (4,
incl. the report's fixture + 8 regex unit cases). Next: #55 finalize-time
`sha256(raw[byte_start:byte_end]) == content_hash` invariant, then the #52/#53
byte-space cluster.

## v1.70.2 - search/verify/event-loop fixes (#32, #33, #34)
Closes the rest of @mmashwani's issue batch. **#32**: `path_glob` was a
tool-layer post-filter after the index top-k cut, starving single-document
globs to 0 results on large corpora; now a candidate pre-filter inside
`DocStore.search` (new defaulted `path_glob` kwarg, shared `_path_excluded`
helper across lexical/semantic/hybrid). **#33**: `verify_index` bare-`continue`d
zero-byte-range sections (every structured-OpenAPI section is 0,0; even plain
markdown indexes carry one for the synthetic doc root), so counters didn't sum;
new `skipped_count`/`skipped_sections` (reason `empty_byte_range`), invariant
clean+drift+missing+error+skipped == section_count tested. **#34**: `index_local`
was a sync call inside async `call_tool`, wedging the stdio server past client
timeouts on big embeddings corpora; now `asyncio.to_thread` (write-safety via
the v1.69.2 cross-process lock). Deferred from #34: background-job/progress
architecture + vectorized cosine. Tests: `tests/test_v1_70_2.py` (8).

## v1.70.1 - `paths` subset refresh no longer prunes the index (#31)
Data-loss fix reported by @mmashwani. `index_local(paths=[...])` / CLI
`--paths-from` on an existing incremental index marked every unlisted indexed
file as deleted (`detect_changes` diffed the subset against the whole index),
collapsing a corpus on a 1-file refresh. Now the deletion diff is scoped to
the requested subset: listed files add/update, a listed file missing from disk
deletes, listed dirs diff their subtree, unlisted files are never pruned, and
`paths=["."]` keeps the full-corpus diff. Pure-deletion subset refreshes work
(no more "No documentation files found" early-return when an index exists).
`_resolve_explicit_paths` now returns `(files, warnings, requested)`.
Tests: `tests/test_v1_70_1.py` (7). Related rough edge (index-file owner
detection needing a `--repo` override) deliberately NOT addressed here.

## v1.70.0 - tune_weights recency window (mirrors jcm v1.108.53)
New `max_age_days` param (default 90, `0` = lifetime/pre-1.70 behavior) on
`tune_weights` / `tune_one_repo` / `tune_all_repos`; `ranking_db_query` gained
`window_seconds`. Closes the stale-anchor exposure from the 2026-06-10
memory-degradation research sweep: lifetime ledger events anchored
`semantic_weight` proposals to dead query distributions. `tune_all_repos`
discovery is also windowed, so repos with only aged-out events aren't scanned.
Additive (defaulted kwargs + new response keys) per the 1.x contract. 3 new
tests in `tests/test_v1_23_0.py` (`_backdate` helper shifts ledger ts).
jdata swept clean the same day (no tuner/learned state at all — no change).

## v1.69.2 - serialize concurrent same-repo index writes (PR #28)
Originally contributed by @Chrisr6records; carried across the finish line with
a cross-platform lock + Windows replace-retry. Two processes writing the same
repo's `<name>.json` at once (e.g. scheduled reindex + per-edit hook) could
install corrupt/partial JSON or silently lose an update: both wrote a shared
`<name>.json.tmp` and `os.replace`-d it with no lock. Fix in
`storage/doc_store.py`: per-PID temp name (`<name>.json.<pid>.tmp`); a
cross-process write lock around `save_index`/`incremental_save` (the whole
read-modify-write), `flock` on POSIX and `msvcrt.locking` on Windows via a
per-repo `<name>.json.lock`; and a bounded `_atomic_replace` retry for the
Windows `PermissionError` (WinError 5/32) that a concurrent reader triggers on
`os.replace`. The PR's lock was POSIX-only (`fcntl`) and no-op'd on Windows,
leaving both the lost-update race and the replace error unfixed there; both are
covered now. Fully additive (no `INDEX_VERSION`/tool/response change; the retry
never newly-raises -- 1.x contract). Regression tests in
`tests/test_concurrent_index_writes.py` reproduce both races across real
processes and pass on Windows + POSIX.

## v1.69.1 - git subprocess stdin -> DEVNULL, fixes Windows stdio deadlock (PR #30)
Contributed by @Derjyn. On Windows, every `index_local` over the MCP stdio
transport deadlocked the server permanently. `_git`/`_git_bytes` spawned git
with `stdout=PIPE, stderr=DEVNULL` but no `stdin` redirect, so the git child
inherited the server's stdin (the JSON-RPC pipe) and Git for Windows blocked
on it forever. The `JDOCMUNCH_GIT_TIMEOUT` guard couldn't recover: the timeout
killed the direct child but the post-kill `communicate()` drain wedged joining
the reader thread, because the `cmd\git.exe` wrapper chain still held the
inherited pipe handles. CLI `index-local` never hung (console-handle stdin, not
a pipe), which masked the bug as transport-specific. Fix: `stdin=DEVNULL` in
both helpers. Pure no-op for behavior (none of `rev-parse`/`status`/`ls-files`
read stdin). Diagnosis via faulthandler traceback + raw stdio JSON-RPC harness;
post-fix calls complete in 0.1-0.6s. `_git.py` is the only git-spawning module
on the server tool path, so the two helpers cover every affected call.

## v1.69.0 - GitHub `ref` selection for versioned doc snapshots (PR #27, closes #26)
Contributed by @DevItBetter; completes the index-identity arc (#17 -> #25
-> #27). Adds an optional `ref` arg to `doc_index_repo`/`index_repo` to
index a specific GitHub branch, tag, or commit-ish. Without `ref`,
behavior is unchanged (HEAD).

`ref` is selection input only, never persisted: explicit refs resolve to a
concrete 40-hex commit SHA *before* fetching tree/content, and durable
lookup/citation handles stay commit-SHA based (`repo_at_sha`,
`source_repo_at_sha`). Because the SHA fast-path still gates on
`current_sha == stored head_sha`, a different ref pointing at a different
commit can never trigger a false no-change short-circuit. Refs are
URL-encoded (`quote(ref, safe="")`) so branch names containing `/` (e.g.
`release/1.x`) resolve correctly. Fails closed: an explicit unresolvable
ref errors instead of silently falling back to HEAD, and invalid refs
(empty, whitespace, non-string) are rejected before any network call. The
omitted-ref path keeps the existing uncertified-HEAD fallback. Composes
with PR #25 named indexes.

Fully additive: `INDEX_VERSION` unchanged; omitted-`ref` calls behave
exactly as before. Out of scope (deliberately): `owner/repo@branch|tag`
lookup handles, moving aliases, persisted ref identity, snapshot
retention, changes to strict `repo@sha` semantics.

## v1.68.0 - `doc_index_repo` name override for named GitHub doc indexes (PR #25, closes #24)
Contributed by @DevItBetter; follow-up to #17 and #23. Adds an optional
`name` arg to `doc_index_repo`/`index_repo` so a GitHub doc index can be
stored under a caller-chosen safe handle (`owner/name`) while preserving
the upstream GitHub identity. Without `name`, behavior is unchanged. With
it, the stored index is `owner/name`, the upstream source is persisted on
a new `DocIndex.source_repo` field, and indexing responses + `list_repos`
surface `source_repo` (and `source_repo_at_sha` for certified indexes)
alongside the stored `repo` / `repo_at_sha`.

`name` is a storage-name override, not a moving alias: it must be a single
safe component (`[A-Za-z0-9._-]+`; `/`, `\`, `@`, `.`, `..`, empty, and
non-strings rejected before any network fetch). The SHA fast-path and
incremental paths stay keyed to the stored name while fetching from the
upstream source, and both now guard on `existing_source_repo ==
source_repo_id` so reusing one stored name for a different upstream source
can't serve stale content via the fast path. Legacy indexes (empty
`source_repo`) fall back to `repo` and get backfilled on next touch.

Fully additive: `INDEX_VERSION` stays 3 (new field uses `.get` default on
load + omit-when-empty on write). Out of scope (deliberately): branch/tag/
ref selection, moving aliases, multi-alias, snapshot retention.

## v1.67.0 - certified repo@sha handles for citeable doc snapshots (PR #23, closes #22)
Contributed by @DevItBetter; follow-up to #17. Adds an immutable
`owner/repo@40hexsha` handle so downstream workflows can cite the exact
doc snapshot used for retrieval. New `DocIndex` fields `head_sha`,
`source_dirty`, `sha_certified`, `source_root`; derived `repo_at_sha`
property (never stored, emitted only when SHA is 40-hex AND not dirty
AND certified). Surfaced in `list_repos`, `search_sections`,
`get_doc_health`, `get_index_overview`, `doc_health_radar`, session
snapshot. `search_sections` (and every read tool) accepts a strict
`repo@sha` alias that resolves only when the stored index matches that
commit and is certified clean; a miss resolves to an uncreatable
sentinel name so it cannot collide with a real repo.

Certification rules (conservative by design):
- GitHub: `index_repo` resolves HEAD->SHA once, then fetches tree,
  `.gitignore`, and all content pinned at that SHA (`?ref=<sha>`),
  closing the old per-call HEAD drift window. Certified only when the
  SHA actually resolved.
- Local Git: "clean" means the *indexed corpus* is reproducible at
  HEAD, not that the worktree is pristine. Dirty files outside the
  indexed scope don't block; gitignored-but-explicitly-indexed paths
  do (separate `git ls-files` tracked-ness check). New `tools/_git.py`
  helpers; git probes bounded by `JDOCMUNCH_GIT_TIMEOUT` (default 10s,
  `<=0` disables); a timed-out/failed probe falls to dirty so an
  immutable handle is never emitted for an unknown state.
- `index_file` is sticky: surgical updates never upgrade a dirty,
  moved-HEAD, untracked-path, or no-longer-Git-backed index to
  certified. Run `index_local` to recertify the full corpus.

Fully additive: `INDEX_VERSION` stays 3 (new fields use `.get` defaults
on load + omit-when-empty on write, so old<->new indexes round-trip).
`load_index`/`delete_index` now catch `ValueError` from the sentinel
name. Suite-parity follow-up candidate for jcm/jdata if citeable
handles earn their keep.

## v1.66.3 - openai-compatible: probe actual dim at init (jdoc#20)
Hardens v1.66.0's openai-compatible provider. Backing-model swap
behind the same URL/model env vars (e.g. Ollama retagging) used to
silently mix vectors of different dims in the on-disk cache because
`_provider_identity` returned `dim=None`. Fix:
`_OpenAICompatibleProvider.__init__` now probes the endpoint with a
one-token canary and stores the discovered dim on `self.dim`;
`_provider_identity` reads it from the cached singleton. Cache layer's
strict dim check now engages; a model swap forces clean re-embed.
Probe failure is non-fatal (dim falls back to None → wildcard
behavior of v1.66.0). When jcm/jdata pick up openai-compatible (#302,
jdata#2), this pattern should ship there too.

## v1.66.2 - warm sentence-transformers before stdio (jdoc#19)
Reported by @rknighton. First semantic `search_sections` hung when
sentence-transformers was configured: lazy model load exceeded MCP
timeouts and leaked progress chatter to stdout, corrupting JSON-RPC
framing. Fix: `provider.warmup()` (provider-type-gated; only
sentence-transformers needs warming) runs in `run_server()` before
`stdio_server()` takes over, wrapped in `redirect_stdout(sys.stderr)`
so noisy library writes land safely. Network providers skip warmup
to avoid avoidable startup round-trips. Warmup failure is non-fatal.

## v1.66.1 - `should_embed("false")` parses as False (jdoc#18)
Reported by @rknighton. `should_embed()` ran any non-empty string
through `bool()`, so `use_embeddings="false"` enabled embeddings.
MCP tool inputs hit this whenever a client sent the flag as a JSON
string. Fix: recognise common string booleans before the bool()
fallback (`"true"/"false"`, `"1"/"0"`, `"yes"/"no"`, `"on"/"off"`,
`"t"/"f"`, `"y"/"n"`, case-insensitive, whitespace-trimmed).
Unknown strings still fall through to `bool()` so 1.x compat holds
(`"flase"` typo remains truthy as it did before).

## v1.66.0 - openai-compatible embeddings (PR #17)
Opt-in `openai-compatible` embedding provider for Ollama, vLLM,
LiteLLM, llama.cpp, LM Studio, and any other OpenAI-API-shaped
endpoint. Four env vars: `JDOCMUNCH_OPENAI_COMPAT_{URL,MODEL,API_KEY,
BATCH_SIZE}`. Explicit-only activation (never auto-detected).
Credential isolation: default API key is the literal `"local"`, never
falls through to `OPENAI_API_KEY`. Cache signature includes URL +
model + first-8 of compat key + batch size; ambient `OPENAI_API_KEY`
is excluded. Contributed by @DevItBetter. Follow-ups: jdoc#20
(canary-pin actual dim), jcm#302 + jdata#2 (sibling parity).

## v1.63.3 - `jdocmunch_guide` sibling-parity tool
Adds `jdocmunch_guide` -- doc-MCP sibling of `jcodemunch_guide` (jcm since
v1.84.0). Returns the version-current CLAUDE.md / AGENT.md policy snippet
for jdocmunch-mcp so an agent can keep a one-line CLAUDE.md
(`"Call jdocmunch_guide and strictly follow its instructions."`) instead
of pasting a static block that drifts. Tool count 59 -> 60. Companion
release of jdatamunch-mcp v1.12.2 ships `jdatamunch_guide` on the same
shape. Backstory: issue #296 (Codex compatibility report by @rknighton).

## v1.63.2 - drift-proof __version__ via importlib.metadata
`__version__` is now derived from `importlib.metadata.version("jdocmunch-mcp")`
in `__init__.py`, mirroring jcodemunch-mcp's pattern. pyproject.toml is
the single source of truth; the hardcoded literal can no longer drift.
v1.63.1's `tests/test_version_sync.py` regex guard retired as redundant.
Source-checkout callers without pip install see `__version__ = "unknown"`;
the replay runner's `_resolve_version()` already handles this via pyproject
fallback.

## v1.63.1 - CI green (fixture query rename + full-history checkout)
Patch release. Two independent CI fixes, no installed-user behavior change.
(1) `self_v1_11_0` fixture query renamed `wiki stats` -> `wiki benchmark`:
the old query collided with `### Stats` subheadings added to CHANGELOG.md
in v1.62.0/v1.63.0 and demoted the target wiki-benchmark page from
rank 1 to rank 4 (MRR 1.0 -> 0.925). (2) Both CI workflows now set
`fetch-depth: 0` so `scripts/generate_changelog.py` can walk every
`release:` commit; the shallow default broke `test_runs_against_real_repo`
on any push whose HEAD wasn't a `release:` commit.

## v1.63.0 — `get_doc_pr_risk_profile` (Phase-2 sibling-parity COMPLETE)
Composite doc-PR risk: volume + blast_radius + backlink_burden + tutorial_disruption
+ role_weight → 0-1 risk_score + level (low/medium/high/critical) + top-5 blockers +
recommended_action. Caller supplies the change list (paired with git diff or
get_recent_changes). Mirrors jcm's get_pr_risk_profile. Tool count 58 → 59.

## v1.62.0 — `doc_health_radar` + `diff_doc_health_radar` (Phase-2 sibling-parity)
Six-axis 0-100 doc-index health scorecard: freshness, link_integrity, orphan_health,
embedding_coverage, role_coverage, drift_health (omitted when no canary). Pure-function
diff helper alongside. Third leg of the suite-wide radar pattern (jcm + jData).
Tool count 56 → 58.

## v1.61.0 — explicit-paths indexing
`index_local(paths=[...])` skips the directory walk and indexes exactly the
listed files / subdirs. Each entry is validated against the root the same
way walked files are (path-traversal, symlink-escape, unsupported-extension
all warn-and-skip). CLI: `jdocmunch-mcp index-local --path <dir> --paths-from FILE`
(use `-` for stdin) — composes with `find` / `fd` / `rg`. Additive: omitting
`paths` preserves every existing call shape. Helper `_load_paths_from_arg`
in `server.py` parses the file/stdin (strips blanks + `#` comments).

## Purpose
Documentation section indexing for the jMunch suite. Companion to jcodemunch-mcp (which owns code symbols). Do NOT add code/docstring parsing here.

## Supported Formats
`.md/.mdx`, `.rst`, `.adoc`, `.ipynb`, `.html`, `.txt`, `.yaml/.yml` (OpenAPI only), `.json/.jsonc`, `.xml/.svg/.xhtml`, `.tscn/.tres` (Godot scenes/resources)

## Key Modules
- `storage/doc_store.py` — DocIndex, DocStore, detect_changes, incremental_save
- `parser/` — one file per format (markdown, rst, asciidoc, notebook, html, text, openapi, json, xml)
- `tools/` — index_local, index_repo, index_file, get_toc, get_toc_tree, search_sections, get_section, get_sections, list_repos, delete_index, get_broken_links, get_doc_coverage, get_backlinks, get_stale_pages, get_wiki_stats, check_section_delete_safe, get_section_blast_radius, find_similar_sections
- `cli/hooks.py` — PreToolUse (Read interceptor) + PostToolUse (auto-reindex) + PreCompact (session snapshot) hook handlers for Claude Code
- `cli/init.py` — `jdocmunch-mcp init` full onboarding: client detection, config patching, CLAUDE.md policy, Cursor/Windsurf rules, hooks, index; `claude-md` subcommand
- `embeddings/` — provider.py (Gemini + OpenAI), cosine_similarity, embed_sections, embed_query

## CLI Subcommands
| Subcommand | Purpose |
|------------|---------|
| `serve` (default) | Run the MCP server (stdio) |
| `init` | One-command onboarding: detect clients, write config, install policy, hooks, index |
| `claude-md` | Print or install the Doc Exploration Policy (`--install global\|project`) |
| `index-local --path <dir>` | Index a local folder (CLI, no MCP session needed) |
| `index-file <path>` | Re-index a single file within an existing index |
| `hook-pretooluse` | PreToolUse hook: intercept Read on large doc files (reads stdin) |
| `hook-posttooluse` | PostToolUse hook: auto-reindex doc files after Edit/Write (reads stdin) |
| `hook-precompact` | PreCompact hook: session snapshot before context compaction (reads stdin) |

## 1.x compatibility contract (license-binding)

Existing 1.x licensees must be able to upgrade between any two 1.x versions
with zero surprise. This is a hard constraint, not a guideline.

**Never on 1.x:**
- Remove or rename an MCP tool. Aliases for any rename must stay in place forever.
- Remove a `Section` field from `to_dict` output (additive only; new fields use the "omit when empty" convention).
- Drop a runtime dependency that an existing user might rely on (e.g. tiktoken stays optional; bytes/4 fallback stays).
- Force a reindex without auto-migrating on load. `INDEX_VERSION` bumps are allowed when the loader silently migrates v(N-1) → v(N) on first read.
- Change the JSON wire format of any tool response in a way that breaks an existing consumer. New keys are fine; renames + removals are not.
- Make a previously-default behavior raise. If we deprecate a flag value, keep it accepted (with a deprecation note in `_meta`) until a 2.x is approved.

**Acceptable on 1.x:**
- Add new tools, fields, response keys, env vars, kwargs (all defaulted to backwards-compat values).
- Tighten internal behavior (faster algorithms, better defaults) when no public output changes.
- Add new error returns for inputs that previously errored differently.
- Add new opt-in code paths gated by env var or kwarg.

**Reserved for 2.x (won't ship until a major-version license revision is planned):**
- See `todo.md` § "Reserved for 2.x" for the canonical list.

## Architecture
- INDEX_VERSION=3; version mismatch triggers auto-migration on first load (NEVER a forced reindex on 1.x)
- O(1) section lookup via `DocIndex.__post_init__` id dict
- `pyyaml>=6.0` required (hard dep)
- Hybrid search (v1.9.0): `search_sections` fuses BM25 + semantic cosine when embeddings exist. `use_embeddings` defaults to `"auto"` (embed when provider configured). `search_sections` params: `semantic` (None/auto, True, False), `semantic_only`, `semantic_weight` (0.0–1.0, default 0.5). `_meta.search_mode` reports `hybrid`/`semantic_only`/`lexical`.
- Embedding providers: GOOGLE_API_KEY (Gemini, text-embedding-004), OPENAI_API_KEY (text-embedding-3-small), openai-compatible + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL, or sentence-transformers; override with JDOCMUNCH_EMBEDDING_PROVIDER env var
- Summarizer providers: ANTHROPIC_API_KEY, GOOGLE_API_KEY, OPENAI_API_KEY, MINIMAX_API_KEY, ZHIPUAI_API_KEY; override with JDOCMUNCH_SUMMARIZER_PROVIDER env var (values: anthropic, gemini, openai, minimax, glm, none)
