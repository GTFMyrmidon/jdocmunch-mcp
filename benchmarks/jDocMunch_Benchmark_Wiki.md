# jDocMunch-MCP Wiki Benchmark
### A/B Token Comparison -- GitHub Wiki Retrieval

---

> **Corpus:** jcodemunch-mcp GitHub Wiki (7 content pages, 68 sections, 29 KB)
> **Source:** `git clone https://github.com/jgravelle/jcodemunch-mcp.wiki.git`
> **Engine:** jDocMunch-MCP (local stdio server, keyword search)
> **Tokenizer:** tiktoken cl100k_base (GPT-4 family)
> **Date:** 2026-04-12
> **Environment:** Windows 11 Home, Python 3.14, Claude Opus 4.6

---

## Index Snapshot

| Metric | Value |
|--------|-------|
| Total files in corpus | **8** `.md` |
| Content pages (excl. Home) | **7** |
| Sections extracted | **68** |
| Corpus size (raw) | **29,280 bytes** |
| Corpus tokens (baseline) | **7,449** |

---

## What This Benchmark Tests

Can jDocMunch reduce token cost when an LLM needs to answer questions about a
GitHub wiki? Two baselines are compared:

- **Baseline A (realistic):** Without jDocMunch, the LLM must read every wiki
  page to find the answer. Cost = 7,449 tokens per query.
- **Baseline B (conservative):** The LLM somehow already knows which file
  contains the answer and reads only that file. This is unrealistically
  favorable to the baseline -- real usage requires reading everything.

The jDocMunch workflow is two tool calls:
1. `search_sections(query, max_results=3)` -- returns ranked section metadata (~190 tokens)
2. `get_section(best_leaf)` -- returns the targeted section content (variable)

---

## Corpus Summary

| File | Bytes | Tokens |
|------|------:|-------:|
| Cross-Repository-Dependency-Tracking.md | 6,618 | 1,700 |
| Expanded-Context-Providers.md | 5,146 | 1,404 |
| Get-Context-Bundle.md | 3,564 | 914 |
| Home.md | 36 | 11 |
| Incremental-Blob-SHA-Indexing.md | 3,387 | 812 |
| jCodeMunch-MCP---jDocMunch-MCP-versus-THE-WORLD!.md | 2,887 | 687 |
| Search-Debug-Mode.md | 3,660 | 899 |
| Tokenizer-True-Benchmark-Harness.md | 3,982 | 1,022 |
| **Total** | **29,280** | **7,449** |

---

## Results -- Full Wiki Baseline (Realistic)

| Query | Baseline | jDocMunch | Saved | Reduction | Ratio |
|-------|----------|----------|-------|-----------|-------|
| cross repository dependency tracking | 7,449 | 599 | 6,850 | 92.0% | 12.4x |
| benchmark token reduction measurement | 7,449 | 314 | 7,135 | 95.8% | 23.7x |
| search scoring ranking debug | 7,449 | 344 | 7,105 | 95.4% | 21.7x |
| incremental indexing blob SHA performance | 7,449 | 313 | 7,136 | 95.8% | 23.8x |
| context bundle symbol imports | 7,449 | 304 | 7,145 | 95.9% | 24.5x |
| **Total (5 queries)** | **37,245** | **1,874** | **35,371** | **95.0%** | **19.9x** |

---

## Results -- Single File Baseline (Conservative)

| Query | File | jDocMunch | Saved | Reduction | Ratio |
|-------|------|----------|-------|-----------|-------|
| cross repository dependency tracking | 1,700 | 599 | 1,101 | 64.8% | 2.8x |
| benchmark token reduction measurement | 1,022 | 314 | 708 | 69.3% | 3.3x |
| search scoring ranking debug | 899 | 344 | 555 | 61.7% | 2.6x |
| incremental indexing blob SHA performance | 812 | 313 | 499 | 61.5% | 2.6x |
| context bundle symbol imports | 914 | 304 | 610 | 66.7% | 3.0x |
| **Total (5 queries)** | **5,347** | **1,874** | **3,473** | **65.0%** | **2.9x** |

---

## Query Detail

### Query 1 -- `cross repository dependency tracking`

| Stat | Value |
|------|-------|
| Target file | `Cross-Repository-Dependency-Tracking.md` |
| Section matched | For engineers |
| Section tokens | 409 |
| Search overhead | 190 |
| **jDocMunch total** | **599** |
| **Full wiki baseline** | **7,449** |

**What this demonstrates:** The query targets a 1,700-token page with detailed technical
content spread across six subsections. jDocMunch returned the "For engineers" subsection --
the one with the updated tool parameters and ecosystem table -- at 409 tokens. Even in the
worst case (largest section in the corpus), the ratio is still 12.4x.

---

### Query 2 -- `benchmark token reduction measurement`

| Stat | Value |
|------|-------|
| Target file | `Tokenizer-True-Benchmark-Harness.md` |
| Section matched | What comes back |
| Section tokens | 124 |
| Search overhead | 190 |
| **jDocMunch total** | **314** |
| **Full wiki baseline** | **7,449** |

**What this demonstrates:** Returns the example JSON output format showing per-repo
reduction ratios. The section is compact (124 tokens) because it contains structured
data rather than prose -- exactly the kind of precise answer an LLM needs.

---

### Query 3 -- `search scoring ranking debug`

| Stat | Value |
|------|-------|
| Target file | `Search-Debug-Mode.md` |
| Section matched | Connection to the jMRI Spec |
| Section tokens | 154 |
| Search overhead | 190 |
| **jDocMunch total** | **344** |
| **Full wiki baseline** | **7,449** |

**What this demonstrates:** Even though the most content-rich section in this file is
the scoring table (For engineers, 281 tokens), the keyword scorer selected the jMRI Spec
section which discusses scoring transparency. Both are valid answers. The ratio holds
either way.

---

### Query 4 -- `incremental indexing blob SHA performance`

| Stat | Value |
|------|-------|
| Target file | `Incremental-Blob-SHA-Indexing.md` |
| Section matched | What comes back |
| Section tokens | 123 |
| Search overhead | 190 |
| **jDocMunch total** | **313** |
| **Full wiki baseline** | **7,449** |

**What this demonstrates:** Surfaces the response structure showing the three-tier
optimization levels. The entire 812-token file is replaced by a 123-token section
that contains the performance data.

---

### Query 5 -- `context bundle symbol imports`

| Stat | Value |
|------|-------|
| Target file | `Get-Context-Bundle.md` |
| Section matched | What |
| Section tokens | 114 |
| Search overhead | 190 |
| **jDocMunch total** | **304** |
| **Full wiki baseline** | **7,449** |

**What this demonstrates:** The "What" section is a self-contained explanation of
`get_context_bundle` -- its purpose, scope, and design constraints -- in 114 tokens.
An LLM reading the full wiki would consume 7,449 tokens to find this same answer.

---

## Grand Summary

```
Baseline A (full wiki):    37,245 tokens across 5 queries
jDocMunch workflow:         1,874 tokens across 5 queries
                           ─────────────────────────────
Saved:                     35,371 tokens (95.0%)
Average ratio:             19.9x

Baseline B (target file):   5,347 tokens across 5 queries
jDocMunch workflow:         1,874 tokens across 5 queries
                           ─────────────────────────────
Saved:                      3,473 tokens (65.0%)
Average ratio:              2.9x
```

**Note:** This wiki is small (7,449 total tokens). These ratios represent the *floor*
for jDocMunch's advantage. On larger doc sets (see Kubernetes benchmark: 1,569 files,
4,355 sections), reductions exceed 99%.

---

## Reproducibility

```bash
# Clone the wiki
git clone https://github.com/jgravelle/jcodemunch-mcp.wiki.git /tmp/wiki

# Run the harness
python benchmarks/wiki/run_benchmark.py /tmp/wiki \
  --queries "cross repository dependency tracking" \
           "benchmark token reduction measurement" \
           "search scoring ranking debug" \
           "incremental indexing blob SHA performance" \
           "context bundle symbol imports" \
  --out results.md --json results.json
```

The harness works on any cloned GitHub wiki. Pass `--queries` for custom queries
or omit to auto-generate from page titles.

---

## Methodology

- Wiki cloned as a standard git repo (`git clone <repo>.wiki.git`)
- All `.md` files tokenized with tiktoken cl100k_base (matches GPT-4 family)
- jDocMunch workflow: `search_sections(query, max_results=3)` + `get_section(best_leaf)`
- Search metadata overhead: 190 tokens (measured from real jDocMunch JSON responses)
- Sections with < 30 tokens of content excluded from matching (parent headings with
  no leaf content -- prevents artificially low jDocMunch counts)
- Baseline A: all wiki pages concatenated -- the minimum cost without structured retrieval
- Baseline B: just the target file -- assumes perfect file selection (unrealistic)
- No queries were tuned or retried. All results are first-pass.

---

*Generated by Claude Opus 4.6 -- jDocMunch-MCP -- 2026-04-12*
