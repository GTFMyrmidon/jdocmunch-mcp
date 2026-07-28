"""jdoc#95 QA-25: contention intent is stated by the caller, never inferred.

Two tests once called ``DocStore.delete_index(owner, name)`` with no
``lock_wait`` while requiring opposite behavior on the same lock, so whichever
way the default fell, one of them failed. The resolution the reviewer chose
(they authored both tests) is that every contention-sensitive caller states
whether it waits or refuses, and the lock never deduces intent from
surrounding state.

These tests pin that resolution so it cannot silently regress:

1. The default stays ``False``. A caller that forgets to say gets the refusing
   behavior, which preserves the QA-17 guarantee that both participating
   indexes are never simultaneously absent. Defaulting to blocking would make
   forgetting cost an index.
2. Every contention-sensitive caller — the two production sites and the two
   tests that provoked this — passes the flag explicitly, so the default
   arbitrates nothing.

Test 1 is the default-drift assertion QA-25 asks for. Test 2 is what makes it
non-vacuous: without it, someone could satisfy the drift guard while quietly
reintroducing an implicit caller.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from jdocmunch_mcp.storage.doc_store import DocStore

TESTS_DIR = Path(__file__).parent
SRC_DIR = TESTS_DIR.parent / "src" / "jdocmunch_mcp"

# The contention-sensitive call sites: those that run while another party holds
# the same handle's write lock, so the wait-or-refuse choice decides the
# outcome. Keyed by (file, enclosing function) rather than line number, which
# drifts. Deliberately NOT every delete_index call — the uncontended setup and
# teardown deletes in these files are first acquirers, where the flag cannot
# change anything, and demanding it there would be noise.
CONTENTION_SENSITIVE = {
    (
        SRC_DIR / "tools" / "delete_index.py",
        "delete_index",
    ): (False, "the public tool refuses fast with index_lifecycle_busy"),
    (
        SRC_DIR / "tools" / "index_local.py",
        "_execute_retirement",
    ): (True, "retirement waits for the retained handle"),
    (
        TESTS_DIR / "test_v1_115_0_qa90.py",
        "test_qa17_retained_delete_refused_inside_final_gate",
    ): (False, "QA-17: the gate holds work that cannot finish until released"),
    (
        TESTS_DIR / "test_v1_115_0_lifecycle_v2.py",
        "_process_blocking_delete",
    ): (True, "QA-15: an ordinary cross-process writer that will release"),
}


def test_delete_index_lock_wait_default_is_false():
    """The default must stay False: forgetting to say must not cost an index."""
    param = inspect.signature(DocStore.delete_index).parameters["lock_wait"]
    assert param.default is False, (
        "DocStore.delete_index(lock_wait=) default drifted to "
        f"{param.default!r}. jdoc#95 QA-25: it stays False so a caller that "
        "forgets gets the refusing behavior, preserving the QA-17 no-both-"
        "absent guarantee. Pass an explicit argument at the call site instead."
    )


def _calls_in_function(path: Path, func_name: str):
    """Yield .delete_index(...) calls lexically inside the named function."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == func_name
    ]
    assert targets, f"{path.name} has no function named {func_name}()"
    for target in targets:
        for node in ast.walk(target):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "delete_index":
                yield node


def test_every_contention_sensitive_caller_states_its_intent():
    """Each contention-sensitive site states its intent explicitly.

    This is a PRESENCE check, not an exhaustiveness one: it asserts that the
    named function contains a ``delete_index(..., lock_wait=<expected>)`` call,
    and says nothing about its other calls. That is deliberate. Two of these
    functions also delete uncontended — the retirement that takes the lock
    first, and a post-release retry — and those are first acquirers where the
    flag cannot change the outcome, so demanding it there would be noise a
    future reader would have to relitigate.

    What this catches is someone deleting the explicit argument, which is how
    this regressed the first time. What proves the SEMANTICS is the pair of
    behavioral tests themselves (QA-15 in lifecycle_v2, QA-17 in qa90): flip
    either flag and they fail on their own, on the platform that exercises
    them. This guard exists because one of that pair skips on Windows, so a
    Windows-only run could not otherwise notice the loss.
    """
    problems = []
    for (path, func_name), (expected, why) in CONTENTION_SENSITIVE.items():
        assert path.exists(), f"{path} moved; update this guard"
        calls = list(_calls_in_function(path, func_name))
        assert calls, (
            f"no delete_index call inside {path.name}::{func_name}(); the "
            "call moved and this guard no longer protects anything"
        )
        stated = [
            call
            for call in calls
            if any(
                kw.arg == "lock_wait"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is expected
                for kw in call.keywords
            )
        ]
        if not stated:
            lines = ", ".join(str(call.lineno) for call in calls)
            problems.append(
                f"{path.name}::{func_name} has no delete_index call passing "
                f"lock_wait={expected} (calls at line(s) {lines}). It needs "
                f"one because {why}."
            )
    assert not problems, "jdoc#95 QA-25 violations:\n" + "\n".join(problems)

# ── exhaustiveness (jdoc#98) ────────────────────────────────────────────────
# The guard above is a PRESENCE check and says so. It proves the sites we knew
# about still state their intent; it cannot fail when a NEW contention-sensitive
# caller arrives with no policy at all, which is the drift QA-25 exists to catch.
#
# This closes that, for production code only. Every `delete_index` call under
# src/ must either pass `lock_wait` explicitly or be named here with a reason.
# Silence stops being an option: adding a caller forces a human to state intent
# one way or the other, which IS QA-25 ("intent is stated by the caller, never
# inferred").
#
# Tests are deliberately out of scope. Their setup and teardown deletes are
# first acquirers where the flag cannot change the outcome, and demanding it
# there would be the noise the guard above already declined to create.
#
# The exemption map is empty on purpose. If a genuinely uncontended production
# delete ever appears, add it WITH its reason rather than loosening the rule.
UNCONTENDED_EXEMPT: dict[tuple[Path, str, str], str] = {}


def test_every_production_delete_states_a_wait_policy():
    """No production `delete_index` call may leave `lock_wait` unstated."""
    unstated = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]:
            for node in ast.walk(fn):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (
                    isinstance(func, ast.Attribute) and func.attr == "delete_index"
                ):
                    continue
                if any(kw.arg == "lock_wait" for kw in node.keywords):
                    continue
                key = (path, fn.name, ast.unparse(func))
                if key in UNCONTENDED_EXEMPT:
                    continue
                unstated.append(
                    f"  {path.relative_to(SRC_DIR)}::{fn.name}() line {node.lineno}"
                )

    assert not unstated, (
        "production delete_index call(s) with no explicit lock_wait:\n"
        + "\n".join(unstated)
        + "\n\njdoc#95 QA-25 / jdoc#98: a contention-sensitive caller states "
        "whether it waits or refuses; the lock never deduces intent. Pass "
        "lock_wait=True (this caller waits for a handle that will be released) "
        "or lock_wait=False (this caller refuses fast with "
        "index_lifecycle_busy). If the call is genuinely uncontended, add it to "
        "UNCONTENDED_EXEMPT with the reason it cannot matter."
    )


def test_the_exhaustiveness_guard_is_not_vacuous():
    """A guard over zero call sites would pass forever and prove nothing."""
    found = 0
    for path in sorted(SRC_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "delete_index"
            ):
                found += 1
    assert found >= 2, (
        f"only {found} production delete_index call site(s) found; the "
        "exhaustiveness guard above is walking the wrong tree or the calls "
        "moved out of src/"
    )
