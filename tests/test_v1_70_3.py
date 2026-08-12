"""v1.70.3 - fence-open regex accepts arbitrary CommonMark 4.5 info strings (#42).

Before this release `_FENCE_OPEN_RE` accepted only an empty info string or a
single bare `[\\w.+-]` token, so attribute-bearing fences
(`` ```python title="x" ``, `` ```js {1,3} ``, RMarkdown `` ```{r} ``, `` ```c# ``)
were not recognized as openers. The fence state machine then inverted: the
block body parsed as markdown (phantom `# comment` sections), the block's bare
closing ` ``` ` opened a phantom `lang=""` fence that swallowed every real
heading after it, and code blocks were lost. These tests pin the corrected
behavior; they parse inline strings only and touch no index store.
"""

import re

from jdocmunch_mcp.parser.markdown_parser import parse_markdown, _FENCE_OPEN_RE

REPO = "local/fence-repro"

FIXTURE = (
    "# Top\n"
    "\n"
    '```python title="x" {.line-numbers}\n'
    "# fake heading inside fancy fence\n"
    'print("hello")\n'
    "```\n"
    "\n"
    "## After Fancy Fence\n"
    "\n"
    "Normal paragraph that should be under After Fancy Fence.\n"
    "\n"
    "```js {highlight: [1]}\n"
    "const x = 1; // [missing](./missing-from-fence.md)\n"
    "```\n"
    "\n"
    "## Final Real Heading\n"
    "\n"
    "Tail.\n"
)


def test_fence_open_regex_unit_cases():
    """The shipped regex recognizes attribute-bearing info strings and still
    rejects a backtick fence whose info string contains a backtick."""
    accept = [
        "```python",
        '```python title="app.py"',
        "```js {1,3}",
        "```js {highlight: [1]}",
        "```{r}",
        "```c#",
        "```",
        "~~~python",
        "~~~python ``` still a tilde fence",
    ]
    reject = [
        "```bad`info",      # backtick fences cannot carry a backtick in the info string
        "not a fence",
        "## heading",
    ]
    for line in accept:
        assert _FENCE_OPEN_RE.match(line), f"should match: {line!r}"
    for line in reject:
        assert not _FENCE_OPEN_RE.match(line), f"should not match: {line!r}"


def test_fancy_fence_does_not_corrupt_sections():
    """The fixture parses to the correct heading tree: no phantom section made
    from the in-fence `# comment`, and the real headings after the fence
    survive."""
    secs = parse_markdown(FIXTURE, "fence-info-strings.md", REPO)
    titles = [(s.level, s.title) for s in secs]
    assert titles == [
        (0, "fence-info-strings"),
        (1, "Top"),
        (2, "After Fancy Fence"),
        (2, "Final Real Heading"),
    ], titles
    # The in-fence comment must not have become a section.
    assert all("fake heading" not in t for _, t in titles)


def test_code_blocks_captured_with_languages():
    """Both fenced blocks are captured with their real languages, and the
    in-fence comment is code content, not a heading."""
    secs = parse_markdown(FIXTURE, "fence-info-strings.md", REPO)
    by_title = {s.title: s for s in secs}

    top_blocks = by_title["Top"].code_blocks
    assert len(top_blocks) == 1
    assert top_blocks[0]["lang"] == "python"
    assert "fake heading inside fancy fence" in top_blocks[0]["content"]
    assert 'print("hello")' in top_blocks[0]["content"]

    aff_blocks = by_title["After Fancy Fence"].code_blocks
    assert len(aff_blocks) == 1
    assert aff_blocks[0]["lang"] == "js"
    assert "const x = 1;" in aff_blocks[0]["content"]


def test_rmarkdown_chunk_lang_strips_braces():
    """RMarkdown `` ```{r} `` chunks expose language `r`, not `{r}`."""
    doc = "# H\n\n```{r}\nx <- 1\n```\n"
    secs = parse_markdown(doc, "rmd.md", REPO)
    blocks = [b for s in secs for b in s.code_blocks]
    assert len(blocks) == 1
    assert blocks[0]["lang"] == "r"
