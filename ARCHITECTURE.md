# Architecture

## Directory Structure

```
jdocmunch-mcp/
├── pyproject.toml
├── README.md
├── SECURITY.md
├── ARCHITECTURE.md
├── SPEC.md
├── USER_GUIDE.md
├── TOKEN_SAVINGS.md
│
├── src/jdocmunch_mcp/
│   ├── __init__.py
│   ├── server.py                    # MCP server: 11 tool definitions + dispatch
│   ├── security.py                  # Path traversal, symlink, secret, binary detection
│   │
│   ├── parser/
│   │   ├── __init__.py              # parse_file() dispatcher, ALL_EXTENSIONS registry
│   │   ├── sections.py              # Section dataclass, ID generation, slugify, hierarchical slug
│   │   ├── markdown_parser.py       # ATX + setext heading splitter, MDX preprocessor
│   │   ├── rst_parser.py            # RST adornment-based heading parser
│   │   ├── asciidoc_parser.py       # AsciiDoc = heading parser
│   │   ├── html_parser.py           # HTML h1–h6 section parser
│   │   ├── notebook_parser.py       # Jupyter .ipynb markdown-cell parser
│   │   ├── openapi_parser.py        # OpenAPI/Swagger operation grouping by tag
│   │   ├── json_parser.py           # JSON/JSONC top-level key sections
│   │   ├── xml_parser.py            # XML/SVG/XHTML element hierarchy
│   │   ├── godot_parser.py          # Godot .tscn/.tres scene/resource sections
│   │   ├── text_parser.py           # Plain-text paragraph-block splitting
│   │   └── hierarchy.py             # parent_id / children wiring after parse
│   │
│   ├── storage/
│   │   ├── __init__.py
│   │   ├── doc_store.py             # DocIndex, DocStore: save/load, byte-range reads, in-memory cache
│   │   └── token_tracker.py         # Persistent token savings counter; count_tokens() for tiktoken
│   │
│   ├── embeddings/
│   │   ├── __init__.py
│   │   └── provider.py              # Gemini / OpenAI / openai-compatible / sentence-transformers embedding providers
│   │
│   ├── summarizer/
│   │   ├── __init__.py
│   │   └── batch_summarize.py       # Heading text → AI batch → title fallback
│   │
│   └── tools/
│       ├── __init__.py
│       ├── _constants.py            # SKIP_PATTERNS shared across indexing tools
│       ├── index_local.py           # Local folder indexing
│       ├── index_repo.py            # GitHub repository indexing
│       ├── list_repos.py
│       ├── get_toc.py
│       ├── get_toc_tree.py
│       ├── get_document_outline.py
│       ├── search_sections.py
│       ├── get_section.py
│       ├── get_sections.py
│       ├── get_section_context.py
│       └── delete_index.py
│
├── tests/
│   ├── fixtures/
│   ├── test_parser.py
│   ├── test_storage.py
│   ├── test_tools.py
│   ├── test_server.py
│   └── test_security.py
│
└── benchmarks/
    ├── jDocMunch_Benchmark_Kubernetes.md
    ├── jDocMunch_Benchmark_LangChain_MDX.md
    └── jDocMunch_Benchmark_SciPy.md
```

---

## Data Flow

```
Documentation files (GitHub API or local folder)
    │
    ▼
Security filters (path traversal, symlinks, secrets, binary, size)
    │
    ▼
File type dispatch (markdown / MDX / text / RST)
    │
    ▼
MDX pre-processor (strip JSX tags, frontmatter, import/export)
    │
    ▼
Heading-based section splitting (ATX # / setext underline / paragraph blocks)
    │
    ▼
Byte offset recording + content hashing + reference/tag extraction
    │
    ▼
Hierarchy wiring (parent_id, children populated)
    │
    ▼
Summarization (heading text → AI batch → title fallback)
    │
    ▼
Storage (JSON index + raw files, atomic writes, ~/.doc-index/)
    │
    ▼
MCP tools (TOC, search, byte-range retrieval)
```

---

## Parser Design

The parser follows a **format dispatch pattern**. File extension determines which parser is used.

### Supported Formats

| Extension            | Parser        | Notes                                       |
| -------------------- | ------------- | ------------------------------------------- |
| `.md`, `.markdown`               | Markdown    | ATX headings + setext headings                              |
| `.mdx`                           | Markdown    | JSX tags, frontmatter, import/export stripped first         |
| `.rst`                           | RST         | Adornment-character heading levels (overline + underline)   |
| `.adoc`                          | AsciiDoc    | `=` through `======` heading hierarchy                      |
| `.html`                          | HTML        | `<h1>`–`<h6>` headings; boilerplate stripped                |
| `.ipynb`                         | Notebook    | Markdown cells as sections; code cells appended as content  |
| `.yaml`, `.yml` (OpenAPI/Swagger)| OpenAPI     | Operations grouped by tag                                   |
| `.json`, `.jsonc`                | JSON        | Top-level keys as sections; JSONC comments stripped         |
| `.xml`, `.svg`, `.xhtml`         | XML         | Element hierarchy used for section structure                |
| `.tscn`, `.tres`                 | Godot       | Godot scene/resource node sections                          |
| `.txt`                           | Text        | Paragraph-block section splitting                           |

### Markdown Parser

`parse_markdown()` in `parser/markdown_parser.py`:

* Handles **ATX headings** (`# H1` through `###### H6`)
* Handles **setext headings** (text underlined with `===` or `---`)
* Content before the first heading becomes a **level-0 root section**
* Tracks **byte offset per line** using `len(line.encode("utf-8"))` for UTF-8-correct offsets
* Extracts **references** (URLs, markdown link targets) and **#hashtag tags** from each section

### MDX Preprocessor

`strip_mdx()` in `parser/markdown_parser.py`:

* Strips YAML/TOML frontmatter (`---...---`)
* Removes `:::js` fenced blocks (keeps `:::python` blocks)
* Removes JSX component tags, preserving inner text content
* Removes mermaid diagrams, import/export statements
* Collapses excess blank lines

### Hierarchy Wiring

`wire_hierarchy()` in `parser/hierarchy.py`:

After flat parsing, a second pass assigns `parent_id` and populates `children` by tracking the stack of open heading levels.

---

## Section ID Scheme

```
{repo}::{doc_path}::{slug}#{level}
```

Examples:

* `owner/repo::docs/install.md::installation#1`
* `owner/repo::README.md::quick-start#2`
* `local/myproject::guide.md::configuration#2`

**Slug generation:** heading text is lowercased, non-alphanumeric sequences replaced with hyphens, then **prefixed with the ancestor slug chain** (e.g. `### Prerequisites` under `## Installation` → `installation/prerequisites`). This hierarchical path makes IDs stable under sibling insertions: a new same-named heading inserted elsewhere in the document does not renumber IDs in other branches.

IDs are stable across re-indexing as long as the file path, heading text, heading level, and parent heading chain remain unchanged.

---

## Storage

Indexes are stored at `~/.doc-index/` (configurable via `DOC_INDEX_PATH`):

```
~/.doc-index/
├── {owner}/
│   ├── {name}.json           # DocIndex: metadata, section metadata (no content)
│   └── {name}/               # Cached raw doc files for byte-range reads
│       ├── README.md
│       └── docs/
│           └── guide.md
└── _savings.json             # Cumulative token savings counter
```

* Sections in the JSON index include byte offsets but **not** full content.
* Full content is retrieved on demand via **O(1) `seek()` + `read()`** using stored byte offsets.
* Atomic writes (temp file + rename) prevent corrupt indexes on interrupted writes.
* Index version (`INDEX_VERSION = 2`) gates schema migrations; any version mismatch (older or newer) causes the index to be ignored and triggers a full re-index.
* An in-memory cache (`_INDEX_CACHE`, keyed by path + mtime_ns) prevents redundant `json.load()` calls within the same server process lifetime.

---

## Search Algorithm

`DocIndex.search()` uses weighted scoring:

| Match type              | Weight                         |
| ----------------------- | ------------------------------ |
| Title exact match       | +20                            |
| Title substring         | +10                            |
| Title word overlap      | +5 per word                    |
| Summary substring       | +8                             |
| Summary word overlap    | +2 per word                    |
| Tag match               | +3 per matching tag            |
| Content word match      | +1 per word (capped at 5)      |

Optional `doc_path` filter scopes search to a single document. Results scoring zero are excluded. Content is stripped from results — use `get_section` to retrieve full content.

---

## Summarization Tiers

Section summaries are generated in three tiers, in order:

1. **Heading text** — used directly as the summary (free, deterministic, often sufficient)
2. **AI batch** — Claude Haiku (if `ANTHROPIC_API_KEY`) or Gemini Flash (if `GOOGLE_API_KEY`), in batches of 8 sections per prompt
3. **Title fallback** — `"Section: {title}"` when AI is unavailable or fails

---

## Response Envelope

Search and retrieval tools return a `_meta` object with timing and token savings:

```json
{
  "results": [...],
  "_meta": {
    "latency_ms": 12,
    "sections_returned": 5,
    "tokens_saved": 1840,
    "total_tokens_saved": 94320,
    "cost_avoided": { "claude_opus": 0.0276, "gpt5_latest": 0.0184 },
    "total_cost_avoided": { "claude_opus": 1.4148, "gpt5_latest": 0.9432 }
  }
}
```

`total_tokens_saved` and `total_cost_avoided` accumulate across all tool calls and persist to `~/.doc-index/_savings.json`.

---

## Dependencies

| Package                      | Purpose                                |
| ---------------------------- | -------------------------------------- |
| `mcp>=1.0.0,<1.10.0`         | MCP server framework                   |
| `httpx>=0.27.0`              | Async HTTP for GitHub API              |
| `anthropic>=0.40.0`          | AI summarization via Claude Haiku      |
| `pathspec>=0.12.0`           | `.gitignore` pattern matching          |
| `google-generativeai>=0.8.0` | AI summarization via Gemini Flash (optional: `pip install jdocmunch-mcp[gemini]`) |
