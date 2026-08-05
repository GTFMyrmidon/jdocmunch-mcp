<!-- mcp-name: io.github.jgravelle/jdocmunch-mcp -->

# jDocMunch MCP: Section-Level Documentation Retrieval for AI Agents

**jDocMunch is an MCP server for coding agents that retrieves the exact documentation section a task needs, without loading whole files into the context window.**

Index a documentation set once by heading hierarchy, then fetch a single section, a heading subtree, or a ranked search result — extracted byte-precisely from the original file.

[**Install**](#install) · [**Quickstart**](#quickstart) · [**Benchmarks**](benchmarks/) · [**Commercial licensing**](#licensing-and-commercial-use)

[![PyPI version](https://img.shields.io/pypi/v/jdocmunch-mcp)](https://pypi.org/project/jdocmunch-mcp/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/jdocmunch-mcp)](https://pypi.org/project/jdocmunch-mcp/)
![License](https://img.shields.io/badge/license-dual--use-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-purple)
![Local-first](https://img.shields.io/badge/local--first-yes-brightgreen)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20102349.svg)](https://doi.org/10.5281/zenodo.20102349)

**Free for personal use.** Commercial use requires a paid license — [terms below](#licensing-and-commercial-use).

---

## Why jDocMunch?

**The problem.** An agent asked "how do I configure authentication?" opens a documentation file, skims hundreds of paragraphs it does not need, opens another, and repeats. Large context windows do not fix this. They just make the waste affordable enough to ignore until the bill arrives, and they crowd out the context the model actually needed.

**The mechanism.** jDocMunch parses a documentation set into a section tree keyed by heading hierarchy, stores each section's byte offsets into the original file, and exposes retrieval over MCP. Sections keep durable identities across re-indexing as long as path, heading text, and heading level are unchanged.

**The outcome.** The unit of access changes from *file* to *section*. An agent retrieves the installation section, one configuration block, or a specific heading subtree — and nothing else.

---

## Evidence

Four benchmarks against public documentation corpora, each with the corpus, date, and per-query results recorded in [`benchmarks/`](benchmarks/).

| Corpus | Scale | Indexed in | Result |
|---|---|---|---|
| [Kubernetes](benchmarks/jDocMunch_Benchmark_Kubernetes.md) (`kubernetes/website`, 2026-03-04) | 1,569 `.md` files, 4,355 sections, 16 MB | 3,352 ms | 27,285 tokens saved on a single node-affinity query; 100 ms latency |
| [SciPy](benchmarks/jDocMunch_Benchmark_SciPy.md) | 10,402 sections, ~855,000 corpus tokens | 2,247 ms | 135–152 ms per query across sparse-solver, FFT, and optimization lookups |
| [LangChain](benchmarks/jDocMunch_Benchmark_LangChain_MDX.md) (MDX) | 5,973 sections | 5,204 ms | MDX-aware sectioning found 754% more sections than the naive pass |
| [Wiki](benchmarks/jDocMunch_Benchmark_Wiki.md) | 7,449-token corpus | — | Search returns ranked metadata in ~190 tokens against a 7,449-token whole-corpus read |

**Read these as per-corpus results, not as a single headline multiple.** Savings depend on how large the containing file is relative to the section you needed: a small file with one heading saves almost nothing, and the Kubernetes corpus saves a great deal. The benchmark files record the queries that did poorly alongside the ones that did well.

A separate, measured result from the [v1.121.0](CHANGELOG.md) projection work, on this repository's own docs at `max_results=10`: a search row went **1,989 chars → 319 with `compact=true` (−84%)**, or 431 with `snippet_bytes=200` (−78%) while removing the follow-up `get_section` call entirely.

**Retrieval quality is gated, not assumed.** Every release runs a replay fixture over a frozen golden set and fails below **nDCG 0.95**. That gate has failed builds and blocked releases; it is not decorative.

---

## Install

**Requirements:** Python 3.10+, any MCP-compatible client.

```bash
pip install jdocmunch-mcp
jdocmunch-mcp init
```

`init` detects your MCP clients, writes their config entries, installs the doc-exploration prompt policy so your agent actually reaches for the tools, and optionally installs hooks and indexes your docs.

> **Ubuntu 24.04+ / Debian 12+:** system Python is externally managed (PEP 668). Use `pipx install jdocmunch-mcp` or `uv tool install jdocmunch-mcp`.

Verify:

```bash
jdocmunch-mcp --version
```

**Manual Claude Code setup:**

```bash
pip install jdocmunch-mcp
claude mcp add -s user jdocmunch jdocmunch-mcp
```

Installing the server makes the tools available; it does not break an agent's habit of brute-reading files. One line in your `CLAUDE.md` does that:

```markdown
Call the jdocmunch_guide tool and strictly follow its instructions.
```

---

## Quickstart

**Assumes:** jDocMunch installed and registered with your client, and a folder of documentation.

Index a local documentation folder:

```bash
jdocmunch-mcp index-local --path ./docs
```

It prints JSON naming the corpus and what it found:

```json
{
  "success": true,
  "repo": "local/docs",
  "file_count": 1,
  "section_count": 4,
  "doc_types": { ".md": 1 },
  "semantic_search": false
}
```

`section_count` greater than `file_count` is the whole point: the index addresses headings, not files.

Then, inside your agent:

> Using jdocmunch, search the docs for "authentication configuration" and show me that section.

The agent should call `search_sections`, then `get_section` on the top hit — returning one section rather than a file. `_meta.tokens_saved` on the response reports what that cost versus reading the containing document.

**Next step:** `get_toc_tree` for a structural view of the whole corpus, or `index_repo` to index documentation straight from a GitHub repository.

---

## What you can do

- **Retrieve one section instead of a document.** `get_section` and `get_sections` pull byte-precise content from the original file; `get_section_excerpt` narrows further.
- **Search by meaning, not just keywords.** `search_sections` fuses BM25 with semantic cosine when an embedding provider is configured. `compact=true`, `fields=[...]`, and `snippet_bytes=N` cut the response further.
- **Navigate structure.** `get_toc`, `get_toc_tree`, `get_section_path`, `get_section_descendants`, and `section_neighbors` traverse the heading tree without reading content.
- **Find what documentation is missing or rotting.** `get_doc_coverage`, `get_undocumented_symbols`, `get_stale_pages`, `get_orphan_sections`, `get_broken_links`, and `doc_health_radar`.
- **Work across API specs.** `find_endpoint`, `list_endpoints_by_tag`, `find_operations_using_schema`, and `get_schema_graph` treat OpenAPI documents as first-class.
- **Preflight documentation changes.** `check_section_delete_safe` and `get_section_blast_radius` before you remove or restructure.
- **Know when an answer is stale.** Content reads disclose `_meta.freshness`, `_meta.verdict`, and which source layer answered.

64 tools in total. The full reference is in [USER_GUIDE.md](USER_GUIDE.md).

---

## How it works

Everything runs locally. Indexes live under your home directory; no hosted service is required for indexing or retrieval.

```text
docs/ ──► parser (per format) ──► section tree ──► local index
                                                      │
                          MCP client ◄── retrieval ◄──┘
```

- **Parsing** is per format, one module each: Markdown/MDX, reStructuredText, AsciiDoc, Jupyter notebooks, HTML, plain text, OpenAPI (YAML), JSON/JSONC, XML/SVG/XHTML, Godot scenes, and — via the optional `[office]` extra — PDF, DOCX, PPTX, and EPUB.
- **Storage** is a versioned local index (`INDEX_VERSION = 3`) that auto-migrates on first load. A 1.x release never forces a reindex.
- **Retrieval** is lexical BM25 by default, hybrid when embeddings are available.
- **Embeddings are optional and provider-agnostic** — Gemini, OpenAI, an OpenAI-compatible endpoint, or local sentence-transformers. Without one, search stays lexical and entirely offline.

Deeper detail: [ARCHITECTURE.md](ARCHITECTURE.md) and [SPEC.md](SPEC.md).

---

## Security and privacy

Local-first by design. Your documentation is parsed and stored on your machine, and the base package's only default network behavior is an anonymous savings counter — a random ID plus aggregate token counts, no content, no paths, no PII.

Opt out completely:

```bash
JDOCMUNCH_SHARE_SAVINGS=0
```

Embedding and summarizer providers call their configured API **only when you enable them**, and never by default. `watch-install` registers a login service **only** when you run it yourself.

Path traversal prevention, symlink escape protection, secret exclusion, file-size limits, binary detection, and encoding safety are documented in [SECURITY.md](SECURITY.md), along with how to report a vulnerability.

---

## Limitations

- **Section retrieval helps least on small files.** If a document has one heading and 40 lines, retrieving the section and reading the file cost about the same.
- **Semantic search requires an embedding provider.** Without one, search is lexical only — good for identifiers and exact phrasing, weaker for paraphrased questions.
- **Office formats need the optional `[office]` extra** and are supported for local indexing only.
- **Freshness is disclosed, not guaranteed.** A section whose source cannot be checked is reported as `unknown` rather than assumed current.
- **jDocMunch does not parse code.** Symbols, signatures, and call graphs belong to [jcodemunch-mcp](https://github.com/jgravelle/jcodemunch-mcp); tabular data belongs to [jdatamunch-mcp](https://github.com/jgravelle/jdatamunch-mcp).

---

## Documentation

| Doc | What it covers |
|-----|----------------|
| [USER_GUIDE.md](USER_GUIDE.md) | Full tool reference, workflows, and best practices |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Storage model, parsing pipeline, extension points |
| [SPEC.md](SPEC.md) | Response contracts and reason-code vocabulary |
| [SECURITY.md](SECURITY.md) | Security controls and vulnerability reporting |
| [TOKEN_SAVINGS.md](TOKEN_SAVINGS.md) | How savings are counted and reported |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and the CLA requirement |
| [CHANGELOG.md](CHANGELOG.md) · [ROADMAP.md](ROADMAP.md) | Release history and what's next |

---

## Licensing and commercial use

Released under the **jDocMunch-MCP Dual-Use License** ([full terms](LICENSE)). **Free for non-commercial use. Commercial use requires a paid license**, one-time, sold by jMunch LLC.

**jDocMunch only:** [Builder, $29](https://jcodemunch.com/descriptions.php#builder) (1 developer) · [Studio, $99](https://jcodemunch.com/descriptions.php#studio) (up to 5) · [Platform, $499](https://jcodemunch.com/descriptions.php#platform) (org-wide internal deployment)

**Full jMunch suite (code + docs + data):** [Trio Builder, $99](https://jcodemunch.com/descriptions.php#builder) · [Trio Studio, $449](https://jcodemunch.com/descriptions.php#studio) · [Trio Platform, $2,499](https://jcodemunch.com/descriptions.php#platform)

Individual developers and non-commercial projects need no license. Organizations deploying jDocMunch across internal teams do.

### 1.x compatibility commitment

Every 1.x license entitles you to every future 1.x release. No 1.x version will remove or rename an MCP tool, drop a `Section` field, force a reindex without auto-migrating, break an existing consumer of the JSON wire format, or make a previously-default behavior raise. Anything requiring one of those is reserved for 2.x. The contract is machine-checked by `tests/test_server.py` and a replay-fixture gate on every release.

---

## Support and project status

Actively maintained. Issues and bug reports: [GitHub Issues](https://github.com/jgravelle/jdocmunch-mcp/issues). Security reports: see [SECURITY.md](SECURITY.md). Commercial licensing questions go through [jcodemunch.com](https://jcodemunch.com/).

Part of the jMunch suite alongside [jcodemunch-mcp](https://github.com/jgravelle/jcodemunch-mcp) (code symbols) and [jdatamunch-mcp](https://github.com/jgravelle/jdatamunch-mcp) (tabular data). All three implement [jMRI](https://github.com/jgravelle/mcp-retrieval-spec), the open retrieval interface spec.
