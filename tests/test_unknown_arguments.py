"""Unknown tool arguments are disclosed, not silently dropped (jdoc#104).

The direction of the failure is what matters. An agent that means to SCOPE a
call and misnames the parameter gets back a much LARGER response than it asked
for: `get_toc{doc_path: ...}` returns the whole-corpus TOC, because `doc_path`
is `get_document_outline`'s parameter and `get_toc` scopes with `path_glob`.
Silently dropping a scope is the outcome this server exists to prevent.

Rejecting unknown properties would be the stricter answer. It is forbidden here:
the 1.x contract does not allow a previously-accepted call to start raising, so
this is additive disclosure instead. `test_unknown_argument_still_succeeds`
pins that.
"""

import json

import pytest

from jdocmunch_mcp.server import (
    _all_tools,
    _declared_properties,
    _unknown_arguments,
)


class TestDetection:
    def test_the_reported_case(self):
        assert _unknown_arguments("get_toc", {"repo": "r", "doc_path": "CLAUDE.md"}) == ["doc_path"]

    def test_meaningless_argument(self):
        assert _unknown_arguments("get_toc", {"repo": "r", "zzz_not_real": "x"}) == ["zzz_not_real"]

    def test_declared_arguments_are_clean(self):
        assert _unknown_arguments("get_toc", {"repo": "r", "path_glob": "*.md"}) == []

    def test_sorted_and_complete(self):
        assert _unknown_arguments("get_toc", {"repo": "r", "zeta": 1, "alpha": 2}) == ["alpha", "zeta"]

    def test_empty_arguments(self):
        assert _unknown_arguments("get_toc", {}) == []

    def test_non_dict_arguments_do_not_raise(self):
        assert _unknown_arguments("get_toc", None) == []

    def test_unknown_tool_reports_nothing(self):
        """Silence beats a false accusation when there is no schema to check."""
        assert _unknown_arguments("no_such_tool_at_all", {"anything": 1}) == []


class TestSchemaSource:
    def test_properties_come_from_the_unfiltered_catalog(self):
        """A tool hidden by a tier filter or JDOCMUNCH_DISABLED_TOOLS still has a
        schema; resolving against the VISIBLE list would report every argument of
        a hidden tool as unknown."""
        names = {t.name for t in _all_tools()}
        assert "get_toc" in names
        assert _declared_properties("get_toc") is not None

    def test_every_tool_resolves_to_a_property_set(self):
        """Guard against the cache silently going empty on a refactor."""
        tools = _all_tools()
        assert len(tools) > 20, "catalog looks empty; the check would be vacuous"
        missing = [t.name for t in tools if _declared_properties(t.name) is None]
        assert not missing, f"no declared properties resolved for {missing}"

    def test_no_tool_reports_its_own_declared_args_as_unknown(self):
        """Round-trip the whole catalog: a false positive here is worse than
        the silence this replaces, because it would train callers to ignore it."""
        problems = []
        for t in _all_tools():
            props = ((t.inputSchema or {}).get("properties") or {})
            if not props:
                continue
            found = _unknown_arguments(t.name, {k: "x" for k in props})
            if found:
                problems.append(f"{t.name}: {found}")
        assert not problems, "tools flagged their own declared arguments: " + "; ".join(problems)


class TestAliasesResolve:
    def test_a_deprecated_alias_uses_its_canonical_schema(self):
        """Aliases are never advertised, so they have no schema of their own.
        Without canonical resolution every alias call would report every
        argument as unknown."""
        from jdocmunch_mcp.server import _ALIAS_TO_CANONICAL
        if not _ALIAS_TO_CANONICAL:
            pytest.skip("no aliases defined")
        alias, canonical = next(iter(_ALIAS_TO_CANONICAL.items()))
        props = _declared_properties(canonical)
        assert props is not None
        if not props:
            pytest.skip(f"{canonical} declares no properties")
        assert _unknown_arguments(alias, {k: "x" for k in props}) == []


@pytest.mark.asyncio
class TestEndToEnd:
    async def _call(self, name, args):
        from jdocmunch_mcp.server import call_tool
        out = await call_tool(name, args)
        return json.loads(out[0].text)

    async def test_unknown_argument_still_succeeds(self, monkeypatch):
        """1.x contract: a previously-accepted call must not start raising."""
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "")
        body = await self._call("list_repos", {"zzz_not_a_real_arg": "x"})
        assert "error" not in body or "zzz_not_a_real_arg" not in str(body.get("error", ""))

    async def test_disclosure_survives_the_default_meta_stripping(self, monkeypatch):
        """jdoc's default meta_fields strips `_meta` entirely. A warning the
        default config deletes is no warning at all, so this must be attached
        AFTER filtering, like the budget and absence blocks."""
        monkeypatch.delenv("JDOCMUNCH_META_FIELDS", raising=False)
        from jdocmunch_mcp import config as c
        monkeypatch.setattr(c, "get_meta_fields", lambda: [])
        body = await self._call("list_repos", {"zzz_not_a_real_arg": "x"})
        assert body.get("_meta", {}).get("ignored_arguments") == ["zzz_not_a_real_arg"], (
            "disclosure was stripped by the token-efficient default"
        )

    async def test_clean_call_carries_no_disclosure(self):
        """Omit-when-empty: a correct call must not pay for this."""
        body = await self._call("list_repos", {})
        assert "ignored_arguments" not in (body.get("_meta") or {})


def test_get_toc_description_points_at_the_single_document_tool():
    """The reporter's docs-only suggestion: reaching for doc_path on get_toc is
    a natural mistake when both tools exist."""
    toc = next(t for t in _all_tools() if t.name == "get_toc")
    assert "get_document_outline" in (toc.description or "")
    assert "doc_path" not in ((toc.inputSchema or {}).get("properties") or {})


class TestIgnoredArgumentCannotBackAnAbsenceClaim:
    """The half v1.124.0 MISSED (added in v1.124.1).

    Disclosure alone does not stop the harm. jdoc mints citable `absent:<sha>`
    refs (v1.117.0), so a call whose scoping argument was silently dropped could
    reach `absent` and be cited as proof the target is not there. The call that
    ran is not the call that was requested, so it cannot prove anything about
    what was asked for.

    Suite parity: jcm v1.108.175 and jdata both degrade; jdoc was the last.
    """

    def test_absent_is_downgraded_to_degraded(self):
        from jdocmunch_mcp.tools import _arg_contract as ac
        result = {"_meta": {"verdict": {"state": "absent", "scanned": {"sections": 9}}}}
        ac.degrade_absent_verdict(result, ["doc_path"])
        v = result["_meta"]["verdict"]
        assert v["state"] == "degraded"
        assert v["ignored_arguments"] == ["doc_path"]
        assert "not the call that was requested" in v["note"]

    @pytest.mark.parametrize("state", ["ok", "degraded", "low_confidence"])
    def test_other_states_are_left_alone(self, state):
        """Only the absence CLAIM is refused; everything else is untouched."""
        from jdocmunch_mcp.tools import _arg_contract as ac
        result = {"_meta": {"verdict": {"state": state}}}
        ac.degrade_absent_verdict(result, ["zzz"])
        assert result["_meta"]["verdict"]["state"] == state

    def test_no_ignored_arguments_changes_nothing(self):
        from jdocmunch_mcp.tools import _arg_contract as ac
        result = {"_meta": {"verdict": {"state": "absent"}}}
        ac.degrade_absent_verdict(result, [])
        assert result["_meta"]["verdict"]["state"] == "absent"

    def test_degrade_runs_before_the_absence_block_reads_the_verdict(self):
        """Ordering is the whole fix. If the downgrade ran after the absence
        block, a citable ref would already have been minted."""
        import inspect
        from jdocmunch_mcp import server as S
        src = inspect.getsource(S.call_tool)
        assert "degrade_absent_verdict" in src
        assert src.index("degrade_absent_verdict") < src.index("note_absence"), (
            "the verdict downgrade must precede absence minting"
        )

    def test_top_level_disclosure_matches_the_suite_shape(self):
        """jdata documents top-level for a `_meta`-stripping server; jdoc is one."""
        from jdocmunch_mcp.tools import _arg_contract as ac
        result = {}
        ac.disclose(result, ["doc_path"])
        assert result["ignored_arguments"] == ["doc_path"]
        assert "ignored_arguments_note" in result

    def test_note_text_is_shared_with_the_siblings(self):
        """One sentence, three servers. Drift here is drift in an agent contract."""
        from jdocmunch_mcp.tools import _arg_contract as ac
        note = ac.note(["doc_path"])
        assert "cannot be read as evidence the target is absent" in note
