# jDocMunch-MCP Wiki Benchmark
### A/B Token Comparison -- GitHub Wiki Retrieval

---

> **Corpus:** `C:\MCPs\jcodemunch-wiki-bench` (8 pages, 68 sections)
> **Tokenizer:** tiktoken cl100k_base
> **Workflow:** search_sections(top 3) + get_section(best leaf)

---

## Corpus Summary

| File | Bytes | Tokens |
|------|------:|-------:|
| Cross-Repository-Dependency-Tracking.md | 6,618 | 1,700 |
| Expanded-Context-Providers.md | 5,146 | 1,404 |
| Get-Context-Bundle.md | 3,564 | 914 |
| Home.md | 36 | 11 |
| Incremental-Blob-SHA-Indexing.md | 3,387 | 812 |
| jCodeMunch‐MCP---jDocMunch‐MCP-versus-THE-WORLD!.md | 2,887 | 687 |
| Search-Debug-Mode.md | 3,660 | 899 |
| Tokenizer-True-Benchmark-Harness.md | 3,982 | 1,022 |
| **Total** | **29,280** | **7,449** |

---

## Results -- Full Wiki Baseline

Baseline: all wiki pages concatenated (what an LLM without jDocMunch must load).

| Query | Baseline | jDocMunch | Saved | Reduction | Ratio |
|-------|----------|----------|-------|-----------|-------|
| cross repository dependency tracking | 7,449 | 599 | 6,850 | 92.0% | 12.4x |
| benchmark token reduction measurement | 7,449 | 314 | 7,135 | 95.8% | 23.7x |
| search scoring ranking debug | 7,449 | 344 | 7,105 | 95.4% | 21.7x |
| incremental indexing blob SHA performance | 7,449 | 313 | 7,136 | 95.8% | 23.8x |
| context bundle symbol imports | 7,449 | 304 | 7,145 | 95.9% | 24.5x |
| **Total** | **37,245** | **1,874** | **35,371** | **95.0%** | **19.9x** |

---

## Results -- Single File Baseline (Conservative)

Baseline: only the target file (assumes the LLM already knows which file to open).

| Query | File | jDocMunch | Saved | Reduction | Ratio |
|-------|------|----------|-------|-----------|-------|
| cross repository dependency tracking | 1,700 | 599 | 1,101 | 64.8% | 2.8x |
| benchmark token reduction measurement | 1,022 | 314 | 708 | 69.3% | 3.3x |
| search scoring ranking debug | 899 | 344 | 555 | 61.7% | 2.6x |
| incremental indexing blob SHA performance | 812 | 313 | 499 | 61.5% | 2.6x |
| context bundle symbol imports | 914 | 304 | 610 | 66.7% | 3.0x |
| **Total** | **5,347** | **1,874** | **3,473** | **65.0%** | **2.9x** |

---

## Query Detail

### Query 1 -- `cross repository dependency tracking`

| Stat | Value |
|------|-------|
| Target file | `Cross-Repository-Dependency-Tracking.md` |
| Section matched | For engineers |
| Section bytes | 1,572 |
| Section tokens | 409 |
| Search metadata tokens | 190 |
| Total jDocMunch tokens | 599 |
| Full wiki baseline | 7,449 |
| Single file baseline | 1,700 |

### Query 2 -- `benchmark token reduction measurement`

| Stat | Value |
|------|-------|
| Target file | `Tokenizer-True-Benchmark-Harness.md` |
| Section matched | What comes back |
| Section bytes | 377 |
| Section tokens | 124 |
| Search metadata tokens | 190 |
| Total jDocMunch tokens | 314 |
| Full wiki baseline | 7,449 |
| Single file baseline | 1,022 |

### Query 3 -- `search scoring ranking debug`

| Stat | Value |
|------|-------|
| Target file | `Search-Debug-Mode.md` |
| Section matched | Connection to the jMRI Spec |
| Section bytes | 673 |
| Section tokens | 154 |
| Search metadata tokens | 190 |
| Total jDocMunch tokens | 344 |
| Full wiki baseline | 7,449 |
| Single file baseline | 899 |

### Query 4 -- `incremental indexing blob SHA performance`

| Stat | Value |
|------|-------|
| Target file | `Incremental-Blob-SHA-Indexing.md` |
| Section matched | What comes back |
| Section bytes | 418 |
| Section tokens | 123 |
| Search metadata tokens | 190 |
| Total jDocMunch tokens | 313 |
| Full wiki baseline | 7,449 |
| Single file baseline | 812 |

### Query 5 -- `context bundle symbol imports`

| Stat | Value |
|------|-------|
| Target file | `Get-Context-Bundle.md` |
| Section matched | What |
| Section bytes | 570 |
| Section tokens | 114 |
| Search metadata tokens | 190 |
| Total jDocMunch tokens | 304 |
| Full wiki baseline | 7,449 |
| Single file baseline | 914 |

---

## Methodology

- Wiki cloned as a git repo (`git clone <repo>.wiki.git`)
- All `.md` files tokenized with tiktoken cl100k_base (GPT-4 family)
- jDocMunch workflow: `search_sections(query, max_results=3)` + `get_section(best_leaf)`
- Search metadata overhead estimated at 190 tokens (measured from real responses)
- Baseline A: all wiki pages concatenated -- the minimum an LLM must read without structured retrieval
- Baseline B: just the target file -- assumes perfect file selection (unrealistically favorable to baseline)
- No queries were tuned or retried
