"""jdoc#88 QA-04/QA-05 follow-up (v1.114.0).

QA-04: doc_resolve_repo must disclose a failed Git verification instead of
returning the same not-found shape as a confirmed non-Git path.

QA-05: every publicly returned status / reason code / top-level error must be
documented at the field where it is returned, and the consistency guard must
cover values the runtime can emit — including codes that used to be inline
literals in resolver code, which bypassed the STATUS_*/REASON_* attribute scan.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools import _worktree_corpus as wc
from jdocmunch_mcp.tools import resolve_repo as rr
from jdocmunch_mcp.tools._git import GIT_NOT_A_REPO, GIT_UNAVAILABLE

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "jdocmunch_mcp"


def _spec() -> str:
    return (ROOT / "SPEC.md").read_text(encoding="utf-8")


# --- QA-04 ------------------------------------------------------------------

def test_qa04_discloses_git_failure(tmp_path: Path, monkeypatch):
    """A failed Git verification must be visible in the not-found response."""
    monkeypatch.setattr(DocStore, "list_repos", lambda self: [])
    monkeypatch.setattr(
        wc, "_git_probe", lambda path, args: (False, "", GIT_UNAVAILABLE)
    )

    result = rr.doc_resolve_repo(str(tmp_path), storage_path=str(tmp_path / "store"))

    assert result["found"] is False
    gv = result.get("git_verification")
    assert gv, result
    assert gv["verified"] is False
    assert gv["reason_code"] == wc.REASON_GIT_VERIFICATION_UNAVAILABLE
    # The disclosure must not block the provisional-creation path (#84): the
    # hint still points at index_local.
    assert "index_local" in result["hint"]
    rendered = json.dumps(result, sort_keys=True).lower()
    assert "verification" in rendered and (
        "failed" in rendered or "unavailable" in rendered
    )


def test_qa04_confirmed_non_git_omits_block(tmp_path: Path, monkeypatch):
    """A clean not-a-repo determination stays an ordinary not-found."""
    monkeypatch.setattr(DocStore, "list_repos", lambda self: [])
    monkeypatch.setattr(
        wc, "_git_probe", lambda path, args: (False, "", GIT_NOT_A_REPO)
    )

    result = rr.doc_resolve_repo(str(tmp_path), storage_path=str(tmp_path / "store"))

    assert result["found"] is False
    assert "git_verification" not in result


# --- QA-05 ------------------------------------------------------------------

def test_resolver_emitted_code_documented():
    """The rknighton repro: a directly-emitted resolver reason_code must be in
    SPEC.md (it previously bypassed the STATUS_*/REASON_* attribute guard)."""
    evidence = wc.GitEvidence(
        in_git=True,
        lineage_state=wc.LINEAGE_CONFIRMED,
        lineage_key="test-lineage",
        common_dir="/test/repo/.git",
        toplevel="/test/repo",
        relative_root="docs",
        head_sha="a" * 40,
    )
    decision = wc.resolve_worktree_corpus(
        wc.ResolutionRequest(tool="doc_resolve_repo", evidence=evidence),
        [{"repo": "local/existing", "source_root": "/test/repo/docs"}],
    )
    emitted = decision.to_public()["reason_code"]
    assert emitted == wc.REASON_UNIQUE_LOCATION_CANDIDATE
    assert f"`{emitted}`" in _spec()


def test_no_inline_reason_code_literals():
    """Guard with teeth: any reason_code assigned from a bare string literal in
    src would bypass the SPEC drift-guard, so none may exist. New codes must be
    REASON_* module constants (which the existing guard checks against SPEC)."""
    offenders = []
    for py in SRC.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            values = []
            if isinstance(node, ast.Call):
                values = [kw.value for kw in node.keywords if kw.arg == "reason_code"]
            elif isinstance(node, ast.Dict):
                values = [
                    v
                    for k, v in zip(node.keys, node.values)
                    if isinstance(k, ast.Constant) and k.value == "reason_code"
                ]
            for value in values:
                for c in ast.walk(value):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str):
                        offenders.append(
                            f"{py.relative_to(ROOT)}:{c.lineno} {c.value!r}"
                        )
    assert not offenders, f"Inline reason_code literals bypass the guard: {offenders}"


def test_top_level_error_codes_documented_at_error_field():
    """provisional_cap_exceeded / legacy_reconcile_not_applicable are returned
    as the top-level `error`, and must be documented there — not as
    reconciliation/legacy_reconciliation reason codes."""
    spec = _spec()
    marker = spec.index("Top-level `error` codes")
    error_table = spec[marker:]
    before = spec[:marker]
    for code in ("provisional_cap_exceeded", "legacy_reconcile_not_applicable"):
        assert f"`{code}`" in error_table
        assert f"| `{code}` |" not in before, (
            f"{code} still documented as a reason_code table row"
        )


def test_user_guide_documents_legacy_reconcile():
    guide = (ROOT / "USER_GUIDE.md").read_text(encoding="utf-8")
    assert 'legacy_reconcile="report"' in guide
    assert 'legacy_reconcile="apply"' in guide


def test_changelog_v1_108_0_date_corrected():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [1.108.0] - 2026-07-20" in changelog
    assert "## [1.108.0] - 2026-07-28" not in changelog
