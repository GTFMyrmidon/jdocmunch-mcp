# jdocmunch-mcp

**Version:** 1.124.0 |
**Tests:** `PYTHONPATH=src pytest tests/ -q`

⚠ **`tests/` is shipped inside the sdist, so anything dropped there is
distributed.** `tests/infographic.png` — a 5.9 MB promotional image, referenced
by nothing — sat there from the initial commit and was **87% of the whole
source distribution** until 1.123.2 removed it. ⚠⚠ **No guard would have caught
it**: it is a tracked file, so exclusion rules and untracked-file scans are both
blind to it, and nothing asserts a size budget. **Inspect the artifact's LARGEST
entries, not just its file list** — a clean-looking 607-entry tarball was almost
entirely one image. ⚠ `uv.lock` is **gitignored here** (unlike jcm), so it is
never distributed and never validated; do not reason about it as a pinned input.

⚠ **`numpy` is in the dev group as of 2026-07-31 — test-only, and it must stay
that way.** The runtime import in `storage/doc_store.py` (vectorized semantic
search, pure-Python reference as fallback) stays OPTIONAL and LAZY; numpy is a
dev dep purely so the six tests asserting **fast path == reference** actually
run. They had skipped on EVERY CI run — a divergence between the two paths would
have shipped unnoticed. CI-equivalent env went 1982 passed/14 skipped → 1988
passed/8 skipped. ⚠ **`PYTHONPATH=src pytest` on a dev box is NOT the same run as
CI** — this box has numpy from other packages, so the gap was invisible locally;
reproduce CI with `uv run --python 3.13 python -m pytest tests/ -q`
([[feedback_an_assumption_about_the_machine_is_not_a_fixture]]). Remaining 6
skips are legitimate: all POSIX-only, and they DO run on Linux CI. ⚠ **The two
`_PAIR_LOCK_API` tests were DELETED 2026-07-31** (`a4cef61`) — they called
`DocStore.hold_index_locks`, which does not exist, so a `hasattr` guard skipped
them on every platform on every run since the day they were written: tests for a
canonical-order pair-lock design that was considered and not taken. **The passed
count did not move (1988 → 1988), which is the check that matters** — nothing
that ever executed was removed. If that design is ever revived, write the tests
against the API that exists.

⚠ **2026-08-07: #102/#103/#104/#105 all CLOSED in 1.124.0.** ⚠ Re-run
`gh issue list --state open` before quoting any tracker state; never transcribe
a count into this file. **The `coordinated-retirement` hold is OVER** — #92
merged as `3037428`, branch deleted from the workflow. Nothing is held; ship
from `master`.

## v1.124.0 — four reported defects (#102/#103/#104/#105), all from fresh reporters

⚠⚠ **#102 `lstrip("./")` takes a character SET, not a prefix**, so
`"./.worktrees/"` became `"worktrees/"` and **every gitignored DOT-directory was
walked and indexed** (`.venv/`, `.tox/`, `.next/`, `.cache/`, `.worktrees/`).
Undotted dirs pruned correctly, which is exactly why it read as working.
Reported: **9,813 docs indexed where ~3,100 exist**, ~48 copies of one corpus;
duplicates also DEGRADE retrieval (stale-branch sections compete with the live
one). Fixed with a shared `_walk_rel`; the per-file fallback was corrupted the
same way. ⚠ **Swept the suite first: 14 occurrences in jcm, 5 here — but jcm's
indexer does NOT share it** (it prunes on the bare dir name). Verified, not
assumed; jcm's are path normalizers, a separate lower-severity class.

⚠⚠ **#103 the dedup sidecar was unbounded AND its skips were silent.**
All-pairs Jaccard, ~O(n^2.3) measured (5,931→7.4s, 12,493→42.2s, 25,329→206.7s);
the length pre-filter is a CONSTANT, not a change of asymptote. The code comment
already said "fine up to a few thousand sections" and was right — **nothing
ENFORCED or SURFACED that ceiling**, and the caller's bare `except: pass` meant
a skip would be silent too. **The silence was the defect; the runtime was its
symptom** — a skipped sidecar was indistinguishable from one that found no
duplicates. Now: ceiling (20k default, `JDOCMUNCH_DEDUP_MAX_SECTIONS`, `0`
disables, garbage→default so a typo cannot uncap it), `enabled` opt-out, and a
`dedup_skipped` block naming count/ceiling/knob. ⚠ MinHash+LSH is the REAL fix
and is deliberately NOT in this release.

⚠ **#104 unknown arguments were silently dropped.** `get_toc{doc_path:...}`
returned the WHOLE-CORPUS TOC (`doc_path` is `get_document_outline`'s param).
⚠⚠ **The direction is the harm**: an agent that means to SCOPE and misnames the
param silently gets a LARGER response. `additionalProperties:false` is forbidden
by the 1.x contract (a previously-accepted call must not start raising), so it
is additive `_meta.ignored_arguments`. ⚠ Built from the **UNFILTERED** catalog (a
tier-hidden tool still has a schema) and attached **AFTER meta_fields
filtering** — the default strips `_meta`, and a warning the default deletes is
no warning at all.

⚠⚠ **#105 `verify_index` verified the CACHED MIRROR while its docstring promised
"its current on-disk content".** Both sides came from the index, so an edited /
truncated / DELETED source still verified CLEAN (reporter proved it with a
SAME-LENGTH modification, ruling out a size check). **The description was wrong,
not the behaviour** — cache verification is a real check (B1/B2 of the v1.10
audit) and flipping the default would silently change what existing CI gates on.
Default kept and now honest; new `source="live"` checks the workspace under
`source_root`; `_meta.verify_layer` names which ran on EVERY call and
`_meta.verifies` says in words that clean is NOT proof the source is current.
⚠ **Live with no `source_root` REFUSES (`no_source_root`) rather than falling
back** — a fallback would answer the cache question under the live label, i.e.
the exact confusion reported. Same discipline as v1.122.0's content tools.
⚠ **OPEN for jjg, recorded not decided: should `live` be the DEFAULT?** v1.122.0
flipped content tools to live-when-available (argues yes); it changes counts for
anyone gating CI on `drift_count == 0` (argues do it deliberately).

Tests: `test_gitignore_dot_directories.py` (20; **10 fail pre-fix**, 10 controls
pass BOTH sides), `test_dedup_ceiling.py` (17), `test_unknown_arguments.py` (15,
incl. a whole-catalog round-trip proving no tool flags its OWN declared args),
`test_verify_index_source_layer.py` (19, the reporter's 4-file fixture).
Suite **2144 passed / 6 skipped / 0 failed**. No INDEX_VERSION change.

## v1.123.0 — offloadable-work annotation, OFF BY DEFAULT

`JMUNCH_OFFLOADABLE=1` (suite) or `JDOCMUNCH_OFFLOADABLE=1` (this server;
narrower scope WINS) makes `get_section`/`get_sections` carry an advisory
`_meta.offloadable` block. ⚠⚠ **We LABEL. We never route, execute, or hold
model credentials** — no process, no network, no new tool, no model of ours
runs. Routers classify the PROMPT because that is all they can see; this sits
downstream of retrieval and classifies THE EVIDENCE JUST ASSEMBLED. Tri-state +
reason-coded, fails closed; `verify_with` names the call that ADJUDICATES a
cheap model's answer.

⚠⚠ **This is WHY v1.122.0 shipped first.** A section whose source cannot be
checked, or that comes back stale, is REFUSED rather than labelled — before the
identity tools disclosed freshness there was nothing to refuse on and every
payload would have gated on `TRI_STATE_UNKNOWN`.

⚠ **Suite contract**: identical in jcm (symbols/files) and jdata
(columns/datasets). `EvidenceShape` speaks *units*/*containers*; a pinned
`CONTRACT_DIGEST` + generated contract test fails the build in any of the three
that drifts. ⚠ **This copy is GENERATED from jcodemunch's module** — never
hand-edit it; edit jcm and re-run the maintainer sync. Additive `_meta` key,
emitted only when gated on; no tool/schema/INDEX_VERSION change. Tests
`tests/test_offload_contract.py` (23).

## v1.122.0 — a content read discloses its freshness, and `fresh` means proven

`get_section`/`get_sections` handed back bytes with NO freshness and NO verdict
— `search_sections` has carried per-section freshness since v1.16.0, the tools
that serve actual content carried none. Now emit `_meta.freshness`,
`_meta.verdict`, `_meta.drift_layer`.

⚠⚠ **Two over-claims INSIDE the probe were fixed first; without them the new
disclosure would have been worse than none.** `_classify` answered `fresh`
having compared NOTHING for (a) a section with no `doc_path` and (b) a file that
exists but is unreadable (`_file_hash` → `(None, True)` on `OSError`) — both
fell through to a closing `return "fresh"`. Both now `unknown`. `summary()`
tallied three buckets and SILENTLY DROPPED anything else, so such a section
vanished and the counts could sum to fewer than the sections described; it now
counts `unknown`, and an absent/unrecognised bucket counts as `unknown`.

⚠⚠ **The DEFAULT probe reads jdoc's CACHED MIRROR, not the workspace.** Wired
naively the new reading said `fresh` for a file that had been EDITED and for one
that had been DELETED — verified at the entry point, which is the only reason it
was caught. Content tools now use the jdoc#71 live-source layer when the index
records a usable `source_root`, and DISCLOSE which layer answered.

⚠ `build_verdict` had the same two-state `"stale" if index_stale else "fresh"`
as jcm's v1.108.240 defect; extracted as `index_channel` with an optional
richer reading (Boolean-only callers unchanged). ⚠ **`stale_index` was missing
from its accepted set on the first pass, so a DELETED source fell through to
`fresh`** — the exact failure the function exists to prevent, reintroduced by
its own membership test.

New `section_verdict_for_index`. ⚠ A batch verdict takes the **WORST** section
reading, never first-or-average: otherwise one stale section rides out under an
`ok` covering the others. Additive `_meta` keys only; no tool/schema/
INDEX_VERSION change. Tests `tests/test_identity_freshness.py` (20). Suite 2063
passed / 6 skipped.

## v1.121.1 — git output is decoded as UTF-8, not as cp1252

In-house, found by an AST sweep across the suite after the same defect was
reproduced in jcm. **9 call sites** carried `text=`/`universal_newlines=` with no
`encoding=` — `tools/_git.py` x2, `service_installer.py` x5, `cli/init.py`,
`scripts/evidence_receipt.py` — all now pass `encoding="utf-8", errors="replace"`.

⚠⚠ **`index-local` FAILED OUTRIGHT for any corpus checked out under a non-ASCII
path** — `{"success": false, "error": "Indexing failed: 'NoneType' object has no
attribute 'strip'"}`. Not a degraded index, no index at all. jcm's version of the
same bug merely degraded silently, so **do not reason about jdoc's blast radius
from jcm's**.

⚠ **The trigger is `git rev-parse --show-toplevel`, which prints the repo path
RAW AND UNQUOTED** — unlike `status`/`ls-files`, whose paths go through
`core.quotepath` and come back ASCII. `_git_root` is the gateway every git-aware
path here goes through. On Windows the usual way to have a non-ASCII character in
your checkout path is your own user name.

⚠ **All three of `_git`'s carefully-separated except clauses were bypassed.**
`UnicodeDecodeError` is raised inside `subprocess`'s **reader thread**, so no
`try/except` around the call catches it; `proc.stdout` returns `None` and the
caller dies later on `.strip()`, naming neither git nor encoding.

⚠⚠ **`local_git_paths_tracked` was ALREADY correct and is untouched** — it uses
`_git_bytes` + an explicit `.decode("utf-8", errors="surrogateescape")`. Someone
recognised this hazard for `ls-files` and did not generalise it. **That is the
exact shape of gap a convention-without-a-test leaves**, and why the fix ships
with `tests/test_subprocess_encoding_guard.py` (12) rather than a habit.

⚠ Only `81 8d 8f 90 9d` are undefined in cp1252 — `0x9f` IS defined (`Ÿ`), so
many non-ASCII bytes produce **silent mojibake** instead of a crash. Never read
"it did not raise" as evidence the decode was right; check the `repr`.

Guard proven non-vacuous BOTH ways: stashing `src/`+`scripts/` fails it naming
all 9 sites, and the detector is parametrized over known-good/known-bad through
the SAME function the repo-wide check uses. `tests/` and `unused/` are **EXEMPT
BY NAME with reasons recorded**, not skipped; `KNOWN_UNENCODED` is an empty
ratchet with its own anti-rot test.

⚠ **Invisible to CI and to any UTF-8 dev box** — CI is Linux. Verified at
`index-local`, not at the function edited. Suite 2020 passed / 6 skipped (was
2008/6; +12 is the guard). No INDEX_VERSION, tool-count or wire-format change.

## v1.121.0 — search_sections projection + snippets (#101, @vondecron)

Three opt-in knobs, default response byte-identical (pinned by a test):
`compact=true`, `fields=[...]` (whitelist, wins over compact, `id` always
survives), `snippet_bytes=N`. **1,989 chars/row → 319 compact (-84%) → 431 with
`snippet_bytes=200` (-78% AND the `get_section` hop is gone)**, measured on this
repo's own docs at `max_results=10`.

⚠ **Projection runs LAST — after every filter, after `attach_scores`, after the
ranking/replay logs and the verdict.** Those consumers read fields compact drops
(`min_byte_length` reads `byte_start`/`byte_end`), so projecting earlier would
silently starve them. There is a test asserting `min_byte_length` still filters
under `compact=True` ([[feedback_strip_a_field_after_its_consumer_reads_it]]).

⚠ **In a `repo_group` fan-out compact KEEPS `repo`** — dead weight on a
single-repo row, and the ONLY thing telling two members' rows apart in a fused
one. That is what `project(..., extra_keep=...)` exists for; the same flag
means different things on the two code paths
([[feedback_a_flag_that_fits_one_caller_breaks_on_the_second]]). Snippets are
produced member-side (they need the member's index to read content); projection
is applied once to the fused list.

⚠ Per-row `_freshness` is dropped **only when it is `fresh`**. An all-fresh set
is what `_meta.freshness` already reports; a single stale row is a signal the
caller needs, so noise-dropping must not become signal-dropping.

`_meta.tokens_saved` now measures the **served** (post-projection) payload —
it previously measured rows that had not yet had `_answerability`/`_quotability`
attached either, so the figure never described what crossed the wire.

**Not adopted:** jcm's interned `#MUNCH/1` wire format, which the reporter
raised as prior art. Changing a tool response's JSON shape is forbidden by the
1.x contract; `compact`/`fields` reaches the same saving additively.

New `retrieval/projection.py`. Tests `tests/test_v1_121_0.py` (20). Suite 2008
passed / 6 skipped. No INDEX_VERSION or tool-count change.

## v1.120.0 SHIPPED: the retirement arc closed, independently verified

@rknighton re-verified QA-15 + QA-17 together at **exactly `132c8e1`** on Linux,
in a clean detached checkout inside an isolated container: **10 passed, 2
skipped, 0 failed**, plus his frozen harness **7/7** at sha256 `88381e18…`,
byte-identical to ours. ⚠ **`test_three_processes_keep_one_lock_inode` EXECUTED
rather than skipping** — the reason Linux was the platform that mattered. His
acceptance criteria PREDATED any implementation, so the gate could not be
reshaped to fit the fix.

⚠⚠ **The fallback disclosure sentence was NOT used and must never be quoted as
if it were.** It would have been false: QA-17 was independently re-verified, so
the notes make the STRONGER claim. **Keeping that distinction honest in the
direction that favored HIM is the whole point of having written it down.**

⚠ **Shipped as 1.120.0, NOT 1.115.0.** That heading stays in CHANGELOG as the
branch's historical record; `pip` resolves to the highest version, so 1.115.0
after 1.119.0 would ship into a version nobody receives. See the label section
below.

⚠ The reconcile that moved the head past his verified SHA was **docs-only**:
`git diff 132c8e1 4122a56 -- src/ tests/` EMPTY, and that empty diff is PUBLISHED
in the release notes so a reader can check it rather than trust us. Same argument
used to accept his pre-rebase evidence on #97.

Suite 1973 passed / 8 skipped local; CI 10/10 + Replay at the merge commit.

## Issue + release policy (suite-wide, 2026-07-28)

**1. One issue, one verdict.** A multi-finding report gets SPLIT at triage into
one issue per finding, cross-linked, credit on each. Detail is not discouraged;
the reason is closure mechanics. A 4-finding issue closes only when the last one
settles, so three finished fixes sit behind one unfinished conversation.

⚠⚠ **THIS REPO IS WHERE THE LESSON CAME FROM.** On 2026-07-27 five issues
(#80/#89/#90/#93) were CONSOLIDATED into one gate, #95. That cut the open count
from 5 to 1 and manufactured a single artifact with the power to block a
release, which is exactly what it then did. **Tracker-tidiness and granularity
pull in opposite directions; do not optimize the count.**

**2. A release is NEVER blocked on an open issue**, including a verification we
asked for. Done + tested + green ships on schedule, carrying a plain
verification-status line. The #95 sentence is the canonical template and is
deliberately WEAKER than a sign-off; never blur the two in a changelog. Late
re-verification counts IN FULL and is announced retroactively. Nothing expires.
**Every timebox names its default action** ("verification by X, or Y ships with
disclosure Z").

⚠ **A reviewer's thoroughness must never become a veto.** If being careful can
stall a release, careful review becomes expensive to accept, which is backwards.

**3. A contributor's PR is never the only path.** Timebox and keep our own path
warm.

⚠⚠ **Do NOT answer "an issue is stuck" with aggregate stats.** jdoc's median
time-to-close is 1 day (60 issues, 45 within a day, 1 ever past a week). True,
and NOT a response: the cost of a blocked issue is CONCENTRATED, not
distributed. Design the fix at the OUTLIER. See
[[feedback_dont_answer_pain_with_aggregates]].

Surfaces: `CONTRIBUTING.md` + `.github/ISSUE_TEMPLATE/`.

## #95 SPLIT 2026-07-28: 15 of 19 criteria satisfied, 3 split out and fixed

Applied the one-issue-one-verdict rule to our own gate. All 19 acceptance
criteria were checked **against the branch**, not against a summary of it:
**15 satisfied**, evidenced by 58 tests across six `test_issue95_*.py` files.
⚠ **PR #97 was FAR larger than the four items recorded above** — it includes
seven real-subprocess `test_spawn_*` cases, which is the "real-process
interruption, not mocked exceptions" criterion nobody had ticked.

Three were genuinely open, split into their own issues, and all three are now
fixed and closed:

- **#98** QA-25 exhaustiveness (`b476e09`). The old guard was a PRESENCE check by
  design; it could not fail when a NEW caller arrived with no policy. Now every
  production `delete_index` call must pass `lock_wait` or be named in
  `UNCONTENDED_EXEMPT` with a reason. ⚠ **`UNCONTENDED_EXEMPT` is empty ON
  PURPOSE** — add a site with its reason, never loosen the rule.
- **#99** installed-wheel smoke (`a84c757`). New `package-smoke` CI job on ubuntu
  + windows builds the wheel, installs into a clean venv, runs
  `scripts/smoke_installed.py` from a dir with no `src/` reachable. ⚠ **The
  script REFUSES to run if it imported from a source tree** — without that it
  passes by testing `src/` again and the job is decorative.
- **#100** machine-generated evidence (`a84c757` + `79c6542` + `132c8e1`).
  `scripts/evidence_receipt.py` emits a receipt per matrix job from
  `pytest --junitxml` (built in, no new dep) and rolls them into a summary.

⚠⚠ **The receipt tool took THREE defects, all found by reading a REAL CI receipt
rather than the local one.** (1) `tree_clean` false on a pristine checkout,
because the run writes junit.xml/coverage/receipts before emitting — **a signal
that always fires hides the case it exists for**. (2) It recorded the synthetic
PR-merge SHA, which is `fatal: bad object` in branch history. (3) The fix for (2)
then CLAIMED `GITHUB_SHA` was the branch head — **on a `pull_request` event
`GITHUB_SHA` IS the merge commit**; the head is reachable ONLY via
`github.event.pull_request.head.sha` passed from the workflow as `PR_HEAD_SHA`.
**A false provenance line inside the provenance artifact.**

Two honesty guards, both proven to fire: a dirty tree is recorded and surfaced,
and a summary spanning >1 SHA prints `MIXED SHAs. This is not evidence for a
single candidate.` rather than averaging runs into a figure describing nothing.

⚠ Receipt counts split **1972 Linux / 1967 Windows on identical 1981 totals**
(9 vs 14 skips) — that is the five POSIX-only tests, i.e. the QA-24 mechanism
showing itself. **Never read a Windows pass as verifying a locking contract.**

⚠ **The QA harness is an ISSUE ATTACHMENT, not a repo file** — `find` in the
tree returns nothing. Pull it from the #95 body links, copy into `tests/` to
run, then DELETE it.

⚠ **State that lives only on a PR is state the gate does not carry.** On
2026-07-28 every substantive point was answered on #97 and NOT on #95, leaving
the gate of record showing "Ready for your review" as its last word. Mirror PR
outcomes back to #95.

## v1.119.0 — 5th absence refusal rule: a rebuild underneath a scan cannot prove absence

Suite parity with jcm v1.108.168. v1.117.0's four rules (only `absent`;
not `low_confidence`/`degraded`; not stale; not truncated) had **no rule for an
index being REWRITTEN while the scan reads it**. Index staleness here is
`source_dirty` = the SOURCE moved; it is **blind to a reindex that rewrites
sections under an unchanged tree**, so such a scan reported `index:"fresh"`,
reached `absent`, and minted a citable `absent:<sha>` ref over a half-written
index. **Worse here than in the siblings**: sections score through a lazy
`_content_loader` that reads body text from disk at scan time, so a rebuild
mid-scan can move the very bytes being ranked.

Fix: zero results + detected rewrite ⇒ `degraded`, so the 5th rule **falls out
of the existing "only `absent` proves absence" check** — nothing new to keep in
sync. `absence_refusal` gains a branch BEFORE the generic state check so the
reason names the rebuild. `channels.index` gains **`"rebuilding"`, disclosed on
EVERY state** (an `ok` caller deserves to know the index moved under it); only
the absence CLAIM is refused.

⚠ Detection is a **FILESYSTEM** signal — `DocStore._stamp_load_provenance`
stamps `_index_path` + `_loaded_mtime_ns` at BOTH load return points (cache hit
and cold), `retrieval.verdict.index_changed_since_load` re-stats. **NOT
in-process reindex state**: a separate watcher process drives most rebuilds and
in-process state cannot see it. **Unknown ≠ changed** (unstamped index → False).
⚠ `doc_store.py` has NO module `logger` — the helper builds one locally in its
except (the jcm v1.108.100 NameError-in-except trap).

Files: `storage/doc_store.py`, `retrieval/verdict.py`, `handoff.py`,
`tools/search_sections.py`. Tests `tests/test_v1_119_0.py` (13). NO
tool/schema/INDEX_VERSION change. jdoc publishes no JSON Schema, so unlike jcm
there was no enum to update.

## v1.118.0 - lexical query no longer lowercased before tokenizing (#91 follow-up)
Reported by @tetiz123 while validating the v1.114.1 CJK tokenizer on a real
111-doc / 2,053-section Korean corpus (fix confirmed: no reindex, lexical went
from nothing to their best ranker). Second, unrelated defect found while
measuring. `DocIndex._lexical_search` passed `query.lower()` to the scorer, but
`bm25.tokenize` inserts CamelCase boundaries BEFORE lowercasing, so the query
side and document side disagreed for case-bearing identifiers:
`tokenize("OvertimeService")` -> `['overtime','service']` (doc) vs
`tokenize("overtimeservice")` -> `['overtimeservice']` (query). Every
code-identifier query scored 0.0 and returned a SILENT empty list - silent
because the Stage-A posting prune tokenizes the ORIGINAL query, so candidates
survive the prune then each scores 0 in Stage B. CamelCase + acronym-suffix
(`HCA060T`) hit; underscore names (`SPM_NOTIFICATION`) unaffected (delimiter is
case-independent). **Fix:** pass the raw query to `_score_section` ->
`bm25.score_section`; `tokenize` lowercases internally after de-camel, so it is
correct and free. `_score_section`'s first param renamed `query_lower` ->
`query` (both call sites in `_lexical_search` + the hybrid lexical leg updated);
`query_words` (tag kicker) stays the lowercased set (tags matched case-folded).
Consumer-layer, NO reindex (`tokenize` runs on stored content at scoring time).
Tests `tests/test_v1_118_0.py` (7: root-cause asymmetry + e2e CamelCase/acronym/
repository identifiers + underscore control + lowercase-prose control); suite
1831. Additive/1.x, no INDEX_VERSION or tool-count change. **Shipped from MASTER
as a patch while `coordinated-retirement` (1.115.0) stays HELD; on merge resolve
versions up and keep all CHANGELOG entries.**

## v1.117.0 - absence evidence (handoff/v2 phase 3, suite parity)
Suite parity with jcm v1.108.166 (jcodemunch-mcp#377 phase 3, design by
@mightydanp). A ZERO-RESULT section search is now citable proof. v1/v2 could
not cite it (nothing served, no id), yet "searched the complete/fresh/
non-truncated index and it is NOT there" is the claim audits most need.
`build_verdict` already emits state/scanned/channels/coverage/scorer;
`handoff.note_absence` records those under a deterministic ref. An `absent`
verdict surfaces a citable ref. **jdoc-specific carrier:** its default
`meta_fields` STRIPS `_meta`, so the ref rides in `_meta.absence_evidence`,
re-attached AFTER filtering (the v1.104.0 budget lesson) - a token the default
config deletes is one the agent can never cite. **Refusal rules (his, verbatim):**
only `absent` proves absence; `low_confidence`/`degraded` do NOT; stale index
does NOT; truncated index does NOT. Refused scans STILL recorded so citing
returns the REASON (`refused_absence` / `refused_absence_claims`), not a bare
unknown-ref; absent-but-not-citable -> `_meta.absence_evidence.citable:false` +
`blocked_by`. Rendered proof carries tool+query, SCOPE, sections/documents
scanned, channels, coverage w/ exclusion counts, scorer; unknown coverage
disclosed as unknown NEVER as complete; detail renders ONCE. Ref = sha256[:12]
over `(tool, repo, query, scope)`; jdoc `_SCOPE_ARGS` = doc_path/path_glob/role/
tag/repo_group/lang. Session-scoped, in-memory, capped. Receipt gains
`absence_attested`. Additive/1.x, NO INDEX_VERSION/tool-count change. Tests
`tests/test_v1_117_0.py` (23, one per refusal rule); suite 1824.
**Shipped from MASTER while `coordinated-retirement` (1.115.0) stays HELD;
1.115.0 SKIPPED so the held branch keeps it; on merge resolve versions up + keep
all CHANGELOG entries.**

## v1.116.0 - claim-scoped evidence (handoff/v2 phase 1, suite parity)
Suite parity with jcm v1.108.165 / jdata v1.25.0 (jcodemunch-mcp#377 phase 1,
design by @mightydanp). A handoff section may now carry caller-authored
`claims`, each with its OWN `evidence_refs`. v1 proved a ref was retrieved
this session but never bound it to a sentence - refs landed in ONE global
block at the end of the body. New `_validate_claims` takes
`{id, statement, evidence_refs, classification?}`; **ids unique across the
WHOLE handoff, not per section** (the id is the citation anchor - two sections
owning one id makes a citation ambiguous); statements/classifications
preserved VERBATIM (server never authors); each claim's refs attested
SEPARATELY through the unchanged `_validate_evidence`, so an unknown ref
returns `invalid_claims: [{claim_id, unknown_refs}]` naming the claim instead
of one global failure list. `render_handoff` prints `### <statement>` +
`- Claim id:` + indented evidence, and takes the schema string as a param.
**Three calls carried from jcm:** (1) the INPUT picks the contract - no claims
anywhere means the schema stays `jdocmunch.handoff/v1`, body BYTE-IDENTICAL to
v1, `claims_attested` omitted (not `0`); any claim promotes to `.../v2`.
(2) claims can satisfy `evidence_refs` (top-level may be empty when claims
carry refs - strictly more permissive, no existing call changes). (3) claim
refs join the canonical index, caller order first, so a v1 consumer reading a
v2 handoff sees every ref where it expects. Section `content` optional ONLY
when claims present. Additive/1.x, no INDEX_VERSION or tool-count change.
Tests `tests/test_v1_116_0.py` (18, incl. the byte-identical-v1 guard); suite 1801.
WARNING **Known limit, disclosed on #377 first:** phase 1 does NOT narrow what
counts as a match - the doc-path broadening in `_validate_evidence` means
citing a whole document still attests when one unrelated section from it was
served. That is phase 2 (evidence receipts), DEFERRED.
**Shipped from MASTER as a patch (like 1.114.1 / 1.114.2) while
`coordinated-retirement` (CHANGELOG entry `[1.115.0]`) stays HELD for
rknighton's re-verification. 1.115.0 deliberately SKIPPED on master so the held
branch keeps that CHANGELOG heading; on merge, resolve version conflicts to the
higher number and keep all CHANGELOG entries.** ⚠ **The shipped version is
1.120.0+, NOT 1.115.0 — see the label section below.**

## #93/#95 contribution path DECIDED 2026-07-26: rknighton implements, via PR

Answered the contribution-path question he raised on #93 and escalated on #95 as
formally unanswered: **option 3, a PR against `coordinated-retirement`.**
⚠ **The deciding factor is the CLA, not review convenience.** `CONTRIBUTING.md:7`
makes a signed CLA a hard merge gate (jdoc is dual-licensed, paid commercial
tier), and he has **16 issues / ZERO PRs** here, so nothing is on file.
⚠ **A patch pasted into an issue is the WORST of the three options** — real code
with no signing record at all — which inverts the intuition that a patch is the
lighter-weight ask. A PR makes cla-assistant prompt automatically. Same
reasoning that closed jcm#380.

⚠ **Independence: the ORACLE survives his authorship, his JUDGMENT does not.**
jjg committed on #90 that v1.115 is held for independent re-verification and
QA-17 will not be self-certified. `qa_lifecycle_contract.py` is already LOCKED
with published pre-fix receipts (3 passed / 4 failed at `99a31c1`, identical
across 5 runs) and #95's acceptance criteria predate any implementation — that
is pre-registration, so the gate cannot be reshaped to fit the fix. What is lost
is his adversarial pass on the new code; **that role moves to US, and the release
notes must SAY so** rather than let the record imply author-verification.

PR scope requested: QA-19 + QA-23 + QA-21 + the `reason_code` vocabulary w/
SPEC.md drift guard, Path A. Follow-ons: process-interruption durability,
installed-wheel matrix, frozen-SHA run. **QA-25 was in that scope and we then
took it — see below. Disclosed on #95 rather than left for him to find, with an
offer to revert if his local version differs, since he owns the contract.**

## ⚠ "v1.115.0" IS A LABEL, NOT THE SHIPPED VERSION (clarified 2026-07-27)

⚠ **The retirement release ships as 1.120.0 or later. It can never be 1.115.0,
and the reservation plan never actually said it would be.** Read this before
writing "v1.115.0" in another release note or issue comment.

What is TRUE: master deliberately skipped 1.115.0 (1.114.2 -> 1.116.0) to reserve
the number for the held branch, and `CHANGELOG.md` on `coordinated-retirement`
carries a `[1.115.0]` entry that master's does not. **That part worked as
designed and the entry STAYS** as the historical record of this branch's work.

What is ALSO true and was being missed: **the branch's `pyproject.toml` has NEVER
said 1.115.0 — zero occurrences across its entire history** (`git log -p
coordinated-retirement -- pyproject.toml`). Master merges carried it forward and
it currently reads 1.119.0. The plan always said "resolve version conflicts to
the HIGHER number," so the shipped artifact was ALWAYS going to be >= 1.119.0.

⚠ **Publishing 1.115.0 after 1.119.0 would ALSO be self-defeating even if we
tried: `pip install jdocmunch-mcp` resolves to the HIGHEST version, so the
retirement work would ship into a version nobody receives by default.**

**How to say it:** "tracked as the 1.115.0 CHANGELOG entry, shipping as 1.120.0."
Disclosed to rknighton on
[#95](https://github.com/jgravelle/jdocmunch-mcp/issues/95) 2026-07-27 rather
than left for him to notice, since he has been verifying something under a name
that will never appear in `pip show`. ⚠ **Nothing about the harness, the frozen
oracle, the acceptance criteria or any receipt depends on the number.**

## ⏰ Retirement release TIME-BOXED through 2026-08-02 (set 2026-07-26) — RESOLVED

✅ **RESOLVED 2026-07-29: he completed it AHEAD of the box, so the fallback
never fired.** Kept below for the reasoning, which stands: the time-box existed
to stop OUR latency becoming HIS obligation, and it is now the suite-wide policy
in `CONTRIBUTING.md`. ⚠ **Do NOT quote the fallback sentence as if it shipped.**

⚠ **Original text follows (historical):** ACTION DUE 2026-08-02. The release was gated on an unpaid volunteer's
re-verification with no deadline, holding #80/#89/#90 open indefinitely. That is
our design error, not his. Posted on
[#95](https://github.com/jgravelle/jdocmunch-mcp/issues/95#issuecomment-5083861358)
+ [#90](https://github.com/jgravelle/jdocmunch-mcp/issues/90#issuecomment-5083862220).

**If his re-verification/PR lands by 2026-08-02** it is the gate, as agreed.
**If it does not**, release on his pre-registered harness green at a frozen SHA,
with the release notes carrying VERBATIM: *"Verified against the reviewer's
pre-registered lifecycle harness at a frozen SHA. Not independently re-verified
by its author."* ⚠ **That exact wording is the point** — jjg promised on #90 that
QA-17 would not be self-certified, and a harness pass is a WEAKER claim than his
sign-off. Label it as weaker; never let the changelog blur the two.

⚠ **Nothing expires.** Findings stay credited by ID, issues stay open, and a
re-verification arriving AFTER the box still counts in full — correct anything it
contradicts, in a follow-up release if needed. He was also told explicitly he may
hand back QA-19/QA-23/QA-21 at no cost, because a clear no beats an open-ended
maybe.

**Engagement data behind the decision (2026-07-26):** he is NOT disengaged — his
median turnaround is **3.1h vs our 6.7h**, his longest self-gap in the arc is
50.3h and he broke it unprompted, and he filed #95 with 5 attachments 14.4h
before this was written. ⚠ **His activity clusters at UTC 00-04 and 17-23, so
posts landing 13:00-14:00 UTC sit in his off-hours** — silence there is his
normal pattern, not a warning sign. **We have been the slower party**; he had to
re-raise the contribution-path question in #95 before we answered ~20h later.
The time-box exists to stop OUR latency becoming HIS obligation.

## QA-25 SHIPPED by us 2026-07-26 (`8d15897`): intent is stated, never inferred

Closes the branch's single known red test, Linux-only
`test_v1_115_0_lifecycle_v2.py::test_three_processes_keep_one_lock_inode`
("DID NOT RAISE Empty"). ⚠ **Root cause is NOT the default's value — it is that
two tests asked the default to arbitrate a question it cannot answer.** Both
production callers were ALREADY explicit (`tools/delete_index.py:36` `False`,
`tools/index_local.py:231` `True`), so **those two tests were the only implicit
callers in the entire repo**, requiring OPPOSITE behavior on the SAME lock. No
default could satisfy both. At `False` the QA-15 deleter returned instead of
blocking, so nothing reached the queue and `pytest.raises(queue.Empty)` got a
value.

Fix is the reviewer's rule, verbatim: every contention-sensitive caller states
whether it waits or refuses; the lock never infers intent from surrounding
state. QA-15 deleter → `lock_wait=True`; QA-17 gate contender →
`lock_wait=False`; `⚠ UNRESOLVED` docstring block replaced with the resolution.
⚠ **This SUPERSEDES our proposed retirement-record inference — do not resurrect
it.** It also dissolved a constraint that was OURS, not his: we were trying to
satisfy both tests WITHOUT editing either, and their author told us to edit them.

⚠ **The default STAYS `False`, and that is a data-loss argument, not a
preference:** a caller that forgets to say gets the REFUSING behavior, which
preserves the QA-17 guarantee that both participating indexes are never
simultaneously absent. Defaulting to blocking would make forgetting cost an
index. New `tests/test_v1_115_0_qa25.py` pins it by signature inspection.

⚠ **The second guard is a PRESENCE check, deliberately, and its docstring says
so.** It asserts each contention-sensitive function contains a
`delete_index(..., lock_wait=<expected>)` call and says nothing about its other
calls. **Our first version demanded EVERY call be explicit and produced 8
findings that were all noise** — two of those functions also delete uncontended,
as first acquirers where the flag cannot change the outcome. It is a
signature-level assertion on purpose: it runs on BOTH platforms, whereas the
behavioral test that would catch the loss SKIPS on Windows, which is exactly how
this regressed unnoticed. Both guards proven non-vacuous (remove the argument →
guard fails naming function+line; flip the default → drift assertion fails).

Receipts, all 8 jobs green at `8d158975ad2b515289c8ad524f3e2b971d397dbe`
([run 30204690565](https://github.com/jgravelle/jdocmunch-mcp/actions/runs/30204690565)):
Linux 1875 passed / 9 skipped ×4, Windows 1870 passed / 14 skipped ×4. Against
`69c91c4`, Linux went 1 failed / 1872 passed → 0 failed / 1875 passed.
⚠ **The 1875/1870 split is 5 POSIX-only tests (9 vs 14 skips, identical 1884
totals) and QA-15 is one of them — that number IS the QA-24 mechanism**, so
never read a Windows pass as verifying a locking contract.

**CI: `fail-fast: false` added to the Tests matrix (`69c91c4`).** One ubuntu-3.10
failure was cancelling all four Windows jobs, so the frozen review SHA carried
NO Windows result while the panel showed 8 failures where there was 1. Code
identical to the old pin (`git diff --stat 99a31c1 69c91c4 -- src/ tests/
pyproject.toml` is empty), announced on-issue rather than pushed quietly.
⚠ **RETRACTED on the record: our claim that the draft PR's `synchronize` event
"has not been firing" is WRONG** — `gh run list` shows `pull_request`-event runs
at BOTH `99a31c1` and `69c91c4`. Branch CI has been firing on push all along;
cancellations made those runs unreadable in the panel. `workflow_dispatch` is
still worth keeping (re-run any ref without pushing), but the diagnosis attached
to it was false. Head is HELD from here while he works.

## CHANGELOG maintenance warning (2026-07-18 incident)
CHANGELOG.md's established format is `## [X.Y.Z] - date - title` with curated
prose. Do NOT run `scripts/generate_changelog.py` against it: the script emits
a different heading format and rewrites all historical entries, which changes
every CHANGELOG section id — and the replay self-fixture's goldens for
'hybrid search' / 'broken links' / 'openai compatible embeddings' point at
CHANGELOG section ids, so regeneration turned Tests+Replay red on an otherwise
docs-only commit (recall 1.0 -> 0.7, exactly the 3 CHANGELOG goldens).
Maintain CHANGELOG by hand-appending entries in the established format, and
keep new entry wording clear of fixture query phrases
([[feedback_fixture_query_corpus_pollution]] class).


## Replay-corpus warning: trimming CLAUDE.md can break the replay gate

Same class as the CHANGELOG incident above, hit 2026-07-26 while tracking
`docs/CLAUDE-history.md`. ⚠ **The replay self-fixture indexes `repo_path: "."` —
the WHOLE REPO — so any large markdown file added to the tree joins the retrieval
corpus and competes with the goldens.** Tracking 115 KB of trimmed release-brief
prose dropped nDCG to **0.906 against a 0.95 gate with recall still 1.0** (every
golden found, just outranked), failing
`test_replay_metrics.py::TestGate::test_pass_when_within_gate` and
`TestBaselineLock::test_self_fixture_meets_lock`.

⚠ **It was INVISIBLE to CI because the file was UNTRACKED — a fresh clone did not
have it.** Local runs were red while all 8 CI jobs at `69c91c4` were green. Any
"CI is green at the frozen SHA" claim is blind to untracked working-tree files.

Fix was not a new judgment: **`CLAUDE.md` is ALREADY in the fixture's
`extra_ignore_patterns`** for precisely this reason (recorded there: its
tool-keyword-dense entries shadow stable CHANGELOG goldens, e.g. 'broken links'
demoted by the #47-50 release notes at v1.77.0). `docs/CLAUDE-history.md` **IS
that content**, so trimming into it moved the shadowing prose out from under its
own exclusion; the archive inherits the pattern. Goldens and the 0.95 gate
UNTOUCHED — ⚠ **never fix this class by moving a golden or lowering the gate; the
signal is correct, the corpus scope was wrong.**

Every future batch trimmed into `docs/CLAUDE-history.md` stays excluded, so this
will not recur for that file. It WILL recur for any new large doc added at a new
path ([[feedback_fixture_query_corpus_pollution]]).

## Release history
Versions 1.115.0 and earlier: see `docs/CLAUDE-history.md` (moved out of this file
2026-07-25). `CHANGELOG.md` covers most of them, but 1.67.0-1.92.0 and 1.96.0 exist
ONLY in the history file.

## Purpose
Documentation section indexing for the jMunch suite. Companion to jcodemunch-mcp (which owns code symbols). Do NOT add code/docstring parsing here.

## Supported Formats
`.md/.mdx`, `.rst`, `.adoc`, `.ipynb`, `.html`, `.txt`, `.yaml/.yml` (OpenAPI only), `.json/.jsonc`, `.xml/.svg/.xhtml`, `.tscn/.tres` (Godot scenes/resources), `.pdf/.docx/.pptx/.epub` (optional `[office]` extra, local indexing only, markitdown conversion)

## Key Modules
- `storage/doc_store.py` — DocIndex, DocStore, detect_changes, incremental_save
- `parser/` — one file per format (markdown, rst, asciidoc, notebook, html, text, openapi, json, xml)
- `tools/` — index_local, index_repo, index_file, get_toc, get_toc_tree, search_sections, get_section, get_sections, list_repos, delete_index, get_broken_links, get_doc_coverage, get_backlinks, get_stale_pages, get_wiki_stats, check_section_delete_safe, get_section_blast_radius, find_similar_sections
- `cli/hooks.py` — PreToolUse (Read interceptor) + PostToolUse (auto-reindex) + PreCompact (session snapshot) hook handlers for Claude Code; owns `_DOC_EXTENSIONS`
- `watch.py` — (#78) `watch` daemon: `discover_local_doc_repos` + `watch_docs` (watchfiles-based, incremental `index_local` refresh, rediscover loop)
- `service_installer.py` — (#78) cross-platform login-service installer for `watch` (`jdocmunch-watch`; systemd/launchd/Task Scheduler)
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
| `watch` | (#78) Foreground daemon: auto-reindex every locally-indexed doc repo on any on-disk doc change. `--no-ai-summaries`, `--quiet` |
| `watch-install` / `watch-uninstall` | (#78) Install/remove the doc watcher as a login service (systemd/launchd/Task Scheduler; `jdocmunch-watch`) |
| `watch-status` | (#78) Print doc-watcher service state + per-repo watch coverage (also the `get_watch_status` MCP tool) |

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
- See `ROADMAP.md` § "Reserved for 2.x" for the canonical list.

## Architecture
- INDEX_VERSION=3; version mismatch triggers auto-migration on first load (NEVER a forced reindex on 1.x)
- O(1) section lookup via `DocIndex.__post_init__` id dict
- `pyyaml>=6.0` required (hard dep)
- Hybrid search (v1.9.0): `search_sections` fuses BM25 + semantic cosine when embeddings exist. `use_embeddings` defaults to `"auto"` (embed when provider configured). `search_sections` params: `semantic` (None/auto, True, False), `semantic_only`, `semantic_weight` (0.0–1.0, default 0.5). `_meta.search_mode` reports `hybrid`/`semantic_only`/`lexical`.
- Embedding providers: GOOGLE_API_KEY (Gemini, text-embedding-004), OPENAI_API_KEY (text-embedding-3-small), openai-compatible + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL, or sentence-transformers; override with JDOCMUNCH_EMBEDDING_PROVIDER env var
- Summarizer providers: ANTHROPIC_API_KEY, GOOGLE_API_KEY, OPENAI_API_KEY, MINIMAX_API_KEY, ZHIPUAI_API_KEY; override with JDOCMUNCH_SUMMARIZER_PROVIDER env var (values: anthropic, gemini, openai, minimax, glm, none)
