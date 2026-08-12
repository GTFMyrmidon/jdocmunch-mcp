"""Dotted directories are pruned by RULE, not by enumeration (jdoc#113).

`SKIP_PATTERNS` was a 12-entry denylist, so the walk skipped exactly the dotted
directories somebody thought of in advance and descended into every other one.

⚠ This is NOT jdoc#102. That was `lstrip("./")` eating the leading dot of a
**gitignored** path. Nothing here is gitignored and most fixtures are not git
repos at all, so there is no pattern to miss: the directories were never
pruning candidates. `test_gitignore_dot_directories.py` still owns #102.

**What it cost:** a sibling tool wrote a projection into `.jmemorymunch/` inside
an indexed corpus of 243 notes. The next index reported 486 documents, each note
present twice, the second copy a lossy condensation about a fifth the size and
frozen mid-day. `search_sections` could answer from the condensation with
nothing marking it as a summary or as stale.
"""
import pytest

from jdocmunch_mcp.tools._constants import DOT_DIR_ALLOWLIST, is_skipped_dot_dir
from jdocmunch_mcp.tools.index_local import discover_doc_files
from jdocmunch_mcp.tools.index_repo import _should_skip as repo_should_skip


def _corpus(root):
    (root / "real.md").write_text("# Real\n\nbody\n", encoding="utf-8")
    for d in (".git", ".venv", "node_modules", ".cache", ".claude",
              ".jmemorymunch/semantic", ".github", "docs"):
        p = root / d
        p.mkdir(parents=True, exist_ok=True)
        (p / "f.md").write_text(f"# {d}\n\nbody\n", encoding="utf-8")
    return root


def _found(root, **kwargs):
    files = discover_doc_files(root, **kwargs)[0]
    return sorted(f.relative_to(root).as_posix() for f in files)


@pytest.fixture
def corpus(tmp_path):
    return _corpus(tmp_path)


# --- the rule itself -------------------------------------------------------

@pytest.mark.parametrize("name", [".cache", ".claude", ".jmemorymunch", ".next", ".idea"])
def test_unknown_dotted_directory_is_skipped(name):
    """The point of the rule: a name nobody enumerated is still skipped."""
    assert is_skipped_dot_dir(name)


@pytest.mark.parametrize("name", ["docs", "src", "node_modules", "github"])
def test_undotted_directory_is_untouched(name):
    assert not is_skipped_dot_dir(name)


def test_github_is_allowlisted():
    """⚠ Dotted and legitimately full of docs. Skipping it would trade one
    silent omission for another, which is the defect this rule closes."""
    assert ".github" in DOT_DIR_ALLOWLIST
    assert not is_skipped_dot_dir(".github")


@pytest.mark.parametrize("name", [".", ".."])
def test_relative_path_components_are_not_dot_dirs(name):
    assert not is_skipped_dot_dir(name)


def test_caller_can_opt_a_directory_back_in():
    assert is_skipped_dot_dir(".claude")
    assert not is_skipped_dot_dir(".claude", [".claude"])


def test_opt_in_is_scoped_to_the_named_directory():
    assert is_skipped_dot_dir(".cache", [".claude"])


# --- the local walk --------------------------------------------------------

def test_walk_skips_dotted_and_keeps_the_rest(corpus):
    assert _found(corpus) == [".github/f.md", "docs/f.md", "real.md"]


def test_walk_opt_in_restores_one_directory(corpus):
    assert ".claude/f.md" in _found(corpus, include_dot_dirs=[".claude"])
    assert ".cache/f.md" not in _found(corpus, include_dot_dirs=[".claude"])


def test_pruned_directories_are_counted_not_silent(corpus):
    """⚠ A walk that silently drops a subtree is indistinguishable from a corpus
    that never had one. That silence is why #113 took a doubled index to notice."""
    counts = {}
    discover_doc_files(corpus, skip_counts=counts)
    assert counts.get("dot_directory") == 5  # .git .venv .cache .claude .jmemorymunch


def test_nested_dotted_directory_is_pruned(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "keep.md").write_text("# k\n\nb\n", encoding="utf-8")
    buried = tmp_path / "docs" / ".cache" / "deep"
    buried.mkdir(parents=True)
    (buried / "drop.md").write_text("# d\n\nb\n", encoding="utf-8")
    assert _found(tmp_path) == ["docs/keep.md"]


def test_corpus_whose_root_is_itself_dotted_is_unaffected(tmp_path):
    """⚠⚠ The regression that would empty a corpus outright.

    The real memory store lives at ~/.claude/projects/<slug>/memory. Matching on
    the absolute path, or on the root's own name, drops every document in it and
    reports success.
    """
    root = tmp_path / ".claude" / "projects" / "slug" / "memory"
    root.mkdir(parents=True)
    for n in ("a.md", "b.md"):
        (root / n).write_text("# x\n\ny\n", encoding="utf-8")
    assert _found(root) == ["a.md", "b.md"]


def test_explicitly_named_dotted_directory_is_still_indexed(tmp_path):
    """Naming a directory in `paths` is a request, not a stray cache."""
    d = tmp_path / ".claude"
    d.mkdir()
    (d / "skill.md").write_text("# s\n\nb\n", encoding="utf-8")
    assert _found(d) == ["skill.md"]


# --- index_repo parity -----------------------------------------------------

@pytest.mark.parametrize("path,skipped", [
    ("README.md", False),
    ("docs/guide.md", False),
    (".github/CONTRIBUTING.md", False),
    (".gitignore", False),            # a dotfile FILE is not a directory
    (".claude/skills/x/SKILL.md", True),
    (".cache/a.md", True),
    (".jmemorymunch/semantic/n.md", True),
    ("docs/.hidden/x.md", True),
])
def test_github_tree_paths_follow_the_same_rule(path, skipped):
    """⚠ Two walkers, one rule. A hazard fixed on one path is not fixed:
    `index_repo` filters a flat GitHub tree and has no os.walk to prune."""
    assert repo_should_skip(path) is skipped


def test_github_tree_opt_in_matches_the_local_walk():
    assert repo_should_skip(".claude/x.md") is True
    assert repo_should_skip(".claude/x.md", [".claude"]) is False


# --- the incident, end to end ---------------------------------------------

def test_a_sidecar_projection_no_longer_doubles_a_corpus(tmp_path):
    """The #113 report, reduced: every note mirrored into a dotfile cache."""
    for i in range(6):
        (tmp_path / f"note{i}.md").write_text(f"# Note {i}\n\nfull body here\n", encoding="utf-8")
    mirror = tmp_path / ".jmemorymunch" / "semantic"
    mirror.mkdir(parents=True)
    for i in range(6):
        (mirror / f"note{i}.md").write_text(f"# Note {i}\n\ncondensed\n", encoding="utf-8")

    found = _found(tmp_path)
    assert len(found) == 6, f"corpus doubled: {found}"
    assert not any(f.startswith(".jmemorymunch/") for f in found)
