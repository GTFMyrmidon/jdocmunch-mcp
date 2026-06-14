"""Markdown parser: ATX + setext heading splitter with byte offsets."""

import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# MDX pre-processor
# ---------------------------------------------------------------------------

_MDX_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n?", re.DOTALL)
_MDX_DISCARD_FENCE_RE = re.compile(r":::js\n.*?(?=\n:::|\Z)", re.DOTALL)
_MDX_FENCE_DELIM_RE = re.compile(r"^:::(?:python|js)\s*$|^:::\s*$", re.MULTILINE)
_MDX_API_LINK_BACKTICK_RE = re.compile(r"@\[`([^`]+)`\]")
_MDX_API_LINK_RE = re.compile(r"@\[([^\]]+)\]")
_MDX_MERMAID_RE = re.compile(r"```mermaid\n.*?```", re.DOTALL)
_MDX_BLANK_LINES_RE = re.compile(r"\n{3,}")

_BLOCK_TAGS = (
    r"Note|Tip|Warning|Info|Accordion|Steps?|Cards?|CardGroup|Tabs?|Tab|CodeGroup"
)
_MDX_OPEN_TAG_RE = re.compile(r"<(?:" + _BLOCK_TAGS + r")(?:\s[^>]*)?>", re.MULTILINE)
_MDX_CLOSE_TAG_RE = re.compile(r"</(?:" + _BLOCK_TAGS + r")>", re.MULTILINE)
_MDX_SELF_CLOSE_KNOWN_RE = re.compile(r"<(?:" + _BLOCK_TAGS + r")\s*/>")
_MDX_SELF_CLOSE_UNKNOWN_RE = re.compile(r"<[A-Z][A-Za-z]*(?:\s[^>]*)?\s*/>")
_MDX_IMPORT_EXPORT_RE = re.compile(r"^(?:import|export)\s+.*$", re.MULTILINE)


def strip_mdx(content: str) -> str:
    """Strip MDX-specific syntax from content, leaving clean Markdown.

    Keeps Python code fences (:::python) and discards JavaScript (:::js).
    JSX component tags are removed; their inner text is preserved.

    Args:
        content: Raw MDX file content.

    Returns:
        Clean Markdown string suitable for the standard parser.
    """
    content = _MDX_FRONTMATTER_RE.sub("", content)
    content = _MDX_DISCARD_FENCE_RE.sub("", content)
    content = _MDX_FENCE_DELIM_RE.sub("", content)
    content = _MDX_API_LINK_BACKTICK_RE.sub(r"\1", content)
    content = _MDX_API_LINK_RE.sub(r"\1", content)
    content = _MDX_MERMAID_RE.sub("", content)
    content = _MDX_OPEN_TAG_RE.sub("", content)
    content = _MDX_CLOSE_TAG_RE.sub("", content)
    content = _MDX_SELF_CLOSE_KNOWN_RE.sub("", content)
    content = _MDX_SELF_CLOSE_UNKNOWN_RE.sub("", content)
    content = _MDX_IMPORT_EXPORT_RE.sub("", content)
    content = _MDX_BLANK_LINES_RE.sub("\n\n", content)
    return content.strip()

from .sections import (
    Section,
    slugify,
    resolve_slug_collision,
    make_section_id,
    make_hierarchical_slug,
    compute_content_hash,
    extract_references,
    extract_tags,
)

_ATX_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+\s*)?$")
_SETEXT_H1_RE = re.compile(r"^=+\s*$")
_SETEXT_H2_RE = re.compile(r"^-+\s*$")
# Code-fence delimiters per CommonMark 4.5: 3+ backticks or 3+ tildes, with an
# arbitrary info string. A backtick fence's info string may not contain a
# backtick (the negative lookahead enforces that); a tilde fence's may.
_FENCE_OPEN_RE = re.compile(r"^(`{3,}(?!.*`)|~{3,}).*$")


def _frontmatter_end_line(lines: list) -> int | None:
    """Return the closing line index for top-of-file YAML frontmatter."""
    if not lines or lines[0].strip() != "---":
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return i
    return None


def parse_markdown(content: str, doc_path: str, repo: str) -> list:
    """Parse a markdown file into a list of Section objects.

    Handles both ATX headings (# Heading) and setext headings (underline style).
    Tracks byte offsets per line. Content before the first heading becomes a
    level-0 root section.

    Args:
        content: Raw markdown text.
        doc_path: Relative path of the document (used in section IDs).
        repo: Repository identifier (used in section IDs).

    Returns:
        List of Section objects, in document order, without hierarchy wiring.
    """
    lines = content.splitlines(keepends=True)
    used_slugs: dict = {}
    slug_stack: list = []
    sections = []

    # State for the current open section
    current_title: str = Path(doc_path).stem  # fallback for level-0
    current_level: int = 0
    current_slug: str = ""
    current_byte_start: int = 0
    current_lines: list = []

    byte_cursor = 0

    # Per-section buffer of code blocks parsed inside the current section
    # (v1.17.0). Each entry is a dict {lang, content, byte_start, byte_end};
    # block_id is stamped at _finalize_section once the section_id is known.
    current_code_blocks: list = []

    def _finalize_section(byte_end: int) -> None:
        """Close the current open section and append it to sections."""
        nonlocal current_slug, current_code_blocks
        body = "".join(current_lines)
        slug = current_slug or slugify(current_title)
        section_id = make_section_id(repo, doc_path, slug, current_level)
        # Stamp block_ids ("section_id::code#0", "::code#1", …).
        finalized_blocks = []
        for n, blk in enumerate(current_code_blocks):
            finalized_blocks.append(
                {
                    "block_id": f"{section_id}::code#{n}",
                    "lang": blk.get("lang", ""),
                    "content": blk.get("content", ""),
                    "byte_start": blk.get("byte_start", 0),
                    "byte_end": blk.get("byte_end", 0),
                }
            )
        sec = Section(
            id=section_id,
            repo=repo,
            doc_path=doc_path,
            title=current_title,
            content=body,
            level=current_level,
            parent_id="",      # wired later by hierarchy.py
            children=[],       # wired later by hierarchy.py
            byte_start=current_byte_start,
            byte_end=byte_end,
            summary="",
            code_blocks=finalized_blocks,
        )
        sec.content_hash = compute_content_hash(body)
        sec.references = extract_references(body)
        sec.tags = extract_tags(body)
        sections.append(sec)
        current_code_blocks = []

    prev_line: str = ""
    prev_byte_start: int = 0

    # Fenced-code-block state (B2 + v1.17.0). When inside a fence, ATX and
    # setext detection are suppressed so '# comment' inside code does not
    # become a phantom section. v1.17.0 also captures the body bytes + lang
    # of every fenced block for the find_code_examples tool.
    in_fence: bool = False
    fence_char: str = ""
    fence_len: int = 0
    fence_lang: str = ""
    fence_body_byte_start: int = 0
    fence_body_lines: list = []
    frontmatter_end_line = _frontmatter_end_line(lines)

    for i, line in enumerate(lines):
        line_bytes = len(line.encode("utf-8"))
        line_stripped = line.rstrip("\n").rstrip("\r")

        if frontmatter_end_line is not None and i <= frontmatter_end_line:
            current_lines.append(line)
            byte_cursor += line_bytes
            # YAML metadata is not Markdown body text, so it must not seed
            # Setext heading detection for the closing delimiter.
            prev_line = ""
            prev_byte_start = byte_cursor
            continue

        # --- Fence state machine (B2 + v1.17.0 capture) ---
        if in_fence:
            # Match a closing fence: same char, length >= opening length.
            stripped_left = line_stripped.lstrip()
            is_close = False
            if stripped_left and stripped_left[0] == fence_char:
                run = len(stripped_left) - len(stripped_left.lstrip(fence_char))
                if run >= fence_len and stripped_left[run:].strip() == "":
                    is_close = True
            if is_close:
                # Emit the captured code block: body byte range excludes the
                # fence delimiters themselves.
                body_text = "".join(fence_body_lines)
                current_code_blocks.append(
                    {
                        "lang": fence_lang,
                        "content": body_text,
                        "byte_start": fence_body_byte_start,
                        "byte_end": byte_cursor,
                    }
                )
                in_fence = False
                fence_char = ""
                fence_len = 0
                fence_lang = ""
                fence_body_lines = []
            else:
                fence_body_lines.append(line)
            # Whether opening or closing, lines inside a fence are body content,
            # not headings. Append and advance.
            current_lines.append(line)
            prev_line = line_stripped
            prev_byte_start = byte_cursor
            byte_cursor += line_bytes
            continue

        fence_open_match = _FENCE_OPEN_RE.match(line_stripped)
        if fence_open_match:
            marker = fence_open_match.group(1)
            in_fence = True
            fence_char = marker[0]
            fence_len = len(marker)
            # Info string after the fence run = language tag (e.g. ```python).
            # First whitespace-delimited token; strip RMarkdown braces so
            # ```{r} filters as `r`.
            _info = line_stripped[len(marker):].strip()
            fence_lang = _info.split()[0].strip("{}") if _info else ""
            fence_body_lines = []
            # Body starts at the byte cursor for the NEXT line after this fence opener.
            fence_body_byte_start = byte_cursor + line_bytes
            current_lines.append(line)
            prev_line = line_stripped
            prev_byte_start = byte_cursor
            byte_cursor += line_bytes
            continue
        # --- end fence handling ---

        # Setext heading detection (with B3 guards: reject when prev_line is
        # blank or table-like — '|' present means we're inside a table).
        prev_clean = prev_line.strip()
        prev_is_setext_candidate = bool(prev_clean) and "|" not in prev_clean

        if i > 0 and _SETEXT_H1_RE.match(line_stripped) and prev_is_setext_candidate:
            heading_text = prev_clean
            heading_level = 1
        elif i > 0 and _SETEXT_H2_RE.match(line_stripped) and prev_is_setext_candidate and len(line_stripped) >= 2:
            heading_text = prev_clean
            heading_level = 2
        else:
            heading_text = None
            heading_level = None

        # Check for ATX heading
        atx_match = _ATX_RE.match(line_stripped)
        if atx_match and not heading_text:
            heading_text = atx_match.group(2).strip()
            heading_level = len(atx_match.group(1))

        if heading_text and heading_level:
            # Setext: the previous line was the heading text — remove it from current_lines
            if _SETEXT_H1_RE.match(line_stripped) or (_SETEXT_H2_RE.match(line_stripped) and len(line_stripped) >= 2):
                # prev_line is heading text; finalize up to prev_byte_start
                if current_lines:
                    # Remove the last line (prev_line) from current_lines
                    current_lines = current_lines[:-1]
                _finalize_section(byte_end=prev_byte_start)

                current_title = heading_text
                current_level = heading_level
                current_slug = make_hierarchical_slug(heading_text, heading_level, slug_stack, used_slugs)
                current_byte_start = prev_byte_start
                current_lines = []
            else:
                # ATX: current line is the heading
                _finalize_section(byte_end=byte_cursor)

                current_title = heading_text
                current_level = heading_level
                current_slug = make_hierarchical_slug(heading_text, heading_level, slug_stack, used_slugs)
                current_byte_start = byte_cursor
                current_lines = [line]
        else:
            current_lines.append(line)

        prev_line = line_stripped
        prev_byte_start = byte_cursor
        byte_cursor += line_bytes

    # Finalize last open section
    _finalize_section(byte_end=byte_cursor)

    return sections
