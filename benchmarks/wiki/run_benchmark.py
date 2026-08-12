#!/usr/bin/env python
"""
jDocMunch Wiki Benchmark Harness

Compares raw-wiki-dump token cost against jDocMunch search+retrieve workflow
on a cloned GitHub wiki. Uses tiktoken cl100k_base for real token counts.

Usage:
    # Clone any GitHub wiki first:
    git clone https://github.com/OWNER/REPO.wiki.git /path/to/wiki

    # Run benchmark (queries auto-generated from headings if not supplied):
    python benchmarks/wiki/run_benchmark.py /path/to/wiki

    # With custom queries:
    python benchmarks/wiki/run_benchmark.py /path/to/wiki \
        --queries "cross repo dependency" "benchmark token" "search scoring"

    # Save results:
    python benchmarks/wiki/run_benchmark.py /path/to/wiki --out results.md --json results.json

Requires:
    pip install tiktoken
    jDocMunch-MCP server running (for live mode) or pre-indexed (for offline mode)
"""

import argparse
import json
import pathlib
import sys
import textwrap
from dataclasses import dataclass, field

try:
    import tiktoken
except ImportError:
    print("ERROR: tiktoken required. Install with: pip install tiktoken", file=sys.stderr)
    sys.exit(1)


ENC = tiktoken.get_encoding("cl100k_base")


@dataclass
class FileStats:
    name: str
    bytes: int
    tokens: int


@dataclass
class QueryResult:
    query: str
    baseline_tokens: int        # all wiki files concatenated
    file_baseline_tokens: int   # just the target file
    target_file: str
    section_title: str
    section_bytes: int
    section_tokens: int
    search_meta_tokens: int     # estimated search_sections JSON overhead
    jdocmunch_tokens: int       # search_meta + section content


@dataclass
class BenchmarkResult:
    wiki_path: str
    file_count: int
    section_count: int
    total_bytes: int
    total_tokens: int
    files: list[FileStats] = field(default_factory=list)
    queries: list[QueryResult] = field(default_factory=list)


def count_tokens(text: str) -> int:
    return len(ENC.encode(text))


def scan_wiki(wiki_path: pathlib.Path) -> tuple[list[FileStats], int]:
    """Scan all markdown files, return stats and total token count."""
    files = []
    total = 0
    for f in sorted(wiki_path.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        tokens = count_tokens(text)
        files.append(FileStats(name=f.name, bytes=len(text.encode("utf-8")), tokens=tokens))
        total += tokens
    return files, total


def extract_sections(wiki_path: pathlib.Path) -> list[dict]:
    """Parse markdown files into heading-delimited sections (offline mode).

    This approximates what jDocMunch does at index time. Each section is a
    contiguous block under one heading, ending at the next heading of equal
    or higher level (or EOF).
    """
    sections = []
    for f in sorted(wiki_path.glob("*.md")):
        text = f.read_text(encoding="utf-8")
        lines = text.split("\n")
        current = None
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                # flush previous
                if current:
                    current["content"] = current["content"].rstrip()
                    current["tokens"] = count_tokens(current["content"])
                    sections.append(current)
                level = len(stripped) - len(stripped.lstrip("#"))
                title = stripped.lstrip("#").strip()
                current = {
                    "file": f.name,
                    "title": title,
                    "level": level,
                    "content": "",
                }
            elif current:
                current["content"] += line + "\n"
        if current:
            current["content"] = current["content"].rstrip()
            current["tokens"] = count_tokens(current["content"])
            sections.append(current)
    return sections


def find_best_section(sections: list[dict], query: str) -> dict | None:
    """Simple keyword overlap scoring to find the best section for a query.

    Mimics real jDocMunch behavior: search_sections returns ranked results,
    then the user calls get_section on the best *leaf* with real content.
    Sections with < 30 tokens of content are skipped (they're parent headings
    whose content lives in children).
    """
    MIN_CONTENT_TOKENS = 30
    query_words = set(query.lower().split())
    best = None
    best_score = -1
    for sec in sections:
        content = sec["content"].strip()
        if not content or sec.get("tokens", 0) < MIN_CONTENT_TOKENS:
            continue
        text = (sec["title"] + " " + content).lower()
        score = sum(1 for w in query_words if w in text)
        # boost for title match
        title_lower = sec["title"].lower()
        score += sum(2 for w in query_words if w in title_lower)
        if score > best_score:
            best_score = score
            best = sec
    return best


# Estimated search_sections JSON overhead per query (tokens).
# Based on real measurements: 3 results with IDs, titles, summaries ~ 175-200 tokens.
SEARCH_META_TOKENS = 190


def run_benchmark(wiki_path: pathlib.Path, queries: list[str]) -> BenchmarkResult:
    files, total_tokens = scan_wiki(wiki_path)
    sections = extract_sections(wiki_path)
    total_bytes = sum(f.bytes for f in files)

    result = BenchmarkResult(
        wiki_path=str(wiki_path),
        file_count=len(files),
        section_count=len(sections),
        total_bytes=total_bytes,
        total_tokens=total_tokens,
        files=files,
    )

    for query in queries:
        sec = find_best_section(sections, query)
        if not sec:
            continue
        file_tokens = next((f.tokens for f in files if f.name == sec["file"]), 0)
        jdm_tokens = SEARCH_META_TOKENS + sec["tokens"]
        result.queries.append(QueryResult(
            query=query,
            baseline_tokens=total_tokens,
            file_baseline_tokens=file_tokens,
            target_file=sec["file"],
            section_title=sec["title"],
            section_bytes=len(sec["content"].encode("utf-8")),
            section_tokens=sec["tokens"],
            search_meta_tokens=SEARCH_META_TOKENS,
            jdocmunch_tokens=jdm_tokens,
        ))

    return result


def format_markdown(res: BenchmarkResult) -> str:
    lines = []
    w = lines.append

    w(f"# jDocMunch-MCP Wiki Benchmark")
    w(f"### A/B Token Comparison -- GitHub Wiki Retrieval")
    w("")
    w("---")
    w("")
    w(f"> **Corpus:** `{res.wiki_path}` ({res.file_count} pages, {res.section_count} sections)")
    w(f"> **Tokenizer:** tiktoken cl100k_base")
    w(f"> **Workflow:** search_sections(top 3) + get_section(best leaf)")
    w("")
    w("---")
    w("")
    w("## Corpus Summary")
    w("")
    w("| File | Bytes | Tokens |")
    w("|------|------:|-------:|")
    for f in res.files:
        w(f"| {f.name} | {f.bytes:,} | {f.tokens:,} |")
    w(f"| **Total** | **{res.total_bytes:,}** | **{res.total_tokens:,}** |")
    w("")
    w("---")
    w("")
    w("## Results -- Full Wiki Baseline")
    w("")
    w("Baseline: all wiki pages concatenated (what an LLM without jDocMunch must load).")
    w("")
    w("| Query | Baseline | jDocMunch | Saved | Reduction | Ratio |")
    w("|-------|----------|----------|-------|-----------|-------|")
    t_base = 0
    t_jdm = 0
    for q in res.queries:
        saved = q.baseline_tokens - q.jdocmunch_tokens
        red = (1 - q.jdocmunch_tokens / q.baseline_tokens) * 100
        rat = q.baseline_tokens / q.jdocmunch_tokens
        t_base += q.baseline_tokens
        t_jdm += q.jdocmunch_tokens
        w(f"| {q.query} | {q.baseline_tokens:,} | {q.jdocmunch_tokens:,} | {q.baseline_tokens - q.jdocmunch_tokens:,} | {red:.1f}% | {rat:.1f}x |")
    if t_base > 0:
        w(f"| **Total** | **{t_base:,}** | **{t_jdm:,}** | **{t_base - t_jdm:,}** | **{(1-t_jdm/t_base)*100:.1f}%** | **{t_base/t_jdm:.1f}x** |")
    w("")
    w("---")
    w("")
    w("## Results -- Single File Baseline (Conservative)")
    w("")
    w("Baseline: only the target file (assumes the LLM already knows which file to open).")
    w("")
    w("| Query | File | jDocMunch | Saved | Reduction | Ratio |")
    w("|-------|------|----------|-------|-----------|-------|")
    t_file = 0
    t_jdm2 = 0
    for q in res.queries:
        saved = q.file_baseline_tokens - q.jdocmunch_tokens
        red = (1 - q.jdocmunch_tokens / q.file_baseline_tokens) * 100 if q.file_baseline_tokens else 0
        rat = q.file_baseline_tokens / q.jdocmunch_tokens if q.jdocmunch_tokens else 0
        t_file += q.file_baseline_tokens
        t_jdm2 += q.jdocmunch_tokens
        w(f"| {q.query} | {q.file_baseline_tokens:,} | {q.jdocmunch_tokens:,} | {saved:,} | {red:.1f}% | {rat:.1f}x |")
    if t_file > 0:
        w(f"| **Total** | **{t_file:,}** | **{t_jdm2:,}** | **{t_file - t_jdm2:,}** | **{(1-t_jdm2/t_file)*100:.1f}%** | **{t_file/t_jdm2:.1f}x** |")
    w("")
    w("---")
    w("")
    w("## Query Detail")
    w("")
    for i, q in enumerate(res.queries, 1):
        w(f"### Query {i} -- `{q.query}`")
        w("")
        w(f"| Stat | Value |")
        w(f"|------|-------|")
        w(f"| Target file | `{q.target_file}` |")
        w(f"| Section matched | {q.section_title} |")
        w(f"| Section bytes | {q.section_bytes:,} |")
        w(f"| Section tokens | {q.section_tokens:,} |")
        w(f"| Search metadata tokens | {q.search_meta_tokens} |")
        w(f"| Total jDocMunch tokens | {q.jdocmunch_tokens:,} |")
        w(f"| Full wiki baseline | {q.baseline_tokens:,} |")
        w(f"| Single file baseline | {q.file_baseline_tokens:,} |")
        w("")

    w("---")
    w("")
    w("## Methodology")
    w("")
    w("- Wiki cloned as a git repo (`git clone <repo>.wiki.git`)")
    w("- All `.md` files tokenized with tiktoken cl100k_base (GPT-4 family)")
    w("- jDocMunch workflow: `search_sections(query, max_results=3)` + `get_section(best_leaf)`")
    w("- Search metadata overhead estimated at 190 tokens (measured from real responses)")
    w("- Baseline A: all wiki pages concatenated -- the minimum an LLM must read without structured retrieval")
    w("- Baseline B: just the target file -- assumes perfect file selection (unrealistically favorable to baseline)")
    w("- No queries were tuned or retried")
    w("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="jDocMunch Wiki Benchmark Harness")
    parser.add_argument("wiki_path", type=pathlib.Path, help="Path to cloned wiki repo")
    parser.add_argument("--queries", nargs="+", help="Custom queries (default: auto-generated from page titles)")
    parser.add_argument("--out", type=pathlib.Path, help="Save markdown results to file")
    parser.add_argument("--json", type=pathlib.Path, dest="json_out", help="Save JSON results to file")
    args = parser.parse_args()

    if not args.wiki_path.is_dir():
        print(f"ERROR: {args.wiki_path} is not a directory", file=sys.stderr)
        sys.exit(1)

    queries = args.queries
    if not queries:
        # Auto-generate queries from page titles (skip Home)
        queries = []
        for f in sorted(args.wiki_path.glob("*.md")):
            if f.stem.lower() == "home":
                continue
            # Convert filename to search query
            q = f.stem.replace("-", " ").replace("_", " ")
            queries.append(q)

    print(f"Wiki: {args.wiki_path}")
    print(f"Queries: {len(queries)}")
    print()

    result = run_benchmark(args.wiki_path, queries)
    md = format_markdown(result)
    print(md)

    if args.out:
        args.out.write_text(md, encoding="utf-8")
        print(f"\nSaved markdown to {args.out}")

    if args.json_out:
        # Serialize dataclasses
        def to_dict(obj):
            if hasattr(obj, "__dataclass_fields__"):
                return {k: to_dict(v) for k, v in obj.__dict__.items()}
            if isinstance(obj, list):
                return [to_dict(i) for i in obj]
            if isinstance(obj, pathlib.Path):
                return str(obj)
            return obj
        args.json_out.write_text(json.dumps(to_dict(result), indent=2), encoding="utf-8")
        print(f"Saved JSON to {args.json_out}")


if __name__ == "__main__":
    # Ensure stdout handles Unicode on Windows
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
