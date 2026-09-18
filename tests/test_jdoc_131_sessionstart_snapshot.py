"""#131: the session snapshot must travel on SessionStart, not PreCompact.

From 1.73.0 through 1.140.0 ``run_precompact`` wrote the doc session snapshot
as a top-level ``systemMessage`` on the PreCompact event. PreCompact has no
``hookSpecificOutput.additionalContext`` and Claude Code discards its
``systemMessage``, so the snapshot went into a field nobody received (same
class as #129, different event). The snapshot now reaches the model through
``run_sessionstart`` on ``source`` compact / resume / fork.

The PreCompact subcommand STAYS and is silent: a settings.json written by an
earlier ``init`` still names it, and an unknown subcommand would turn every
compaction into a hook error.
"""

from __future__ import annotations

import ast
import inspect
import io
import json
from unittest import mock

import pytest

from jdocmunch_mcp.cli.hooks import run_precompact, run_sessionstart

_REPOS = {
    "repos": [{"name": "docs-repo", "section_count": 7, "doc_count": 2, "source_root": "/tmp/docs"}],
    "count": 1,
}


def _run_sessionstart(payload) -> tuple[int, str, str]:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    with mock.patch("sys.stdin", io.StringIO(raw)):
        with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos", return_value=_REPOS):
            rc = run_sessionstart()
    return rc


class TestSessionStart:
    @pytest.mark.parametrize("source,label", [
        ("compact", "restored after compaction"),
        ("resume", "restored on resume"),
        ("fork", "carried into this fork"),
    ])
    def test_injects_on_prior_state_sources(self, source, label, capsys):
        assert _run_sessionstart({"source": source}) == 0
        payload = json.loads(capsys.readouterr().out)
        out = payload["hookSpecificOutput"]
        assert out["hookEventName"] == "SessionStart"
        assert out["additionalContext"].startswith(f"## jDocMunch session state ({label})")
        assert "docs-repo" in out["additionalContext"]
        assert "systemMessage" not in payload

    @pytest.mark.parametrize("source", ["startup", "clear", "", None, 42])
    def test_silent_on_fresh_sessions(self, source, capsys):
        """A fresh session has no prior doc state; injecting would present unrelated repos as focus."""
        payload = {"source": source} if source is not None else {}
        assert _run_sessionstart(payload) == 0
        assert capsys.readouterr().out == ""

    def test_source_is_case_and_whitespace_tolerant(self, capsys):
        assert _run_sessionstart({"source": "  Compact "}) == 0
        assert "hookSpecificOutput" in capsys.readouterr().out

    def test_invalid_stdin_never_blocks(self, capsys):
        assert _run_sessionstart("not json") == 0
        assert _run_sessionstart("[1, 2]") == 0
        assert capsys.readouterr().out == ""

    def test_silent_when_nothing_indexed(self, capsys):
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"source": "compact"}))):
            with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos",
                            return_value={"repos": [], "count": 0}):
                assert run_sessionstart() == 0
        assert capsys.readouterr().out == ""

    def test_snapshot_failure_never_blocks(self, capsys):
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"source": "compact"}))):
            with mock.patch("jdocmunch_mcp.cli.hooks._build_snapshot", side_effect=RuntimeError("boom")):
                assert run_sessionstart() == 0
        assert capsys.readouterr().out == ""


class TestPreCompactIsSilent:
    def test_writes_nothing(self, capsys):
        with mock.patch("sys.stdin", io.StringIO(json.dumps({"cwd": "/tmp"}))):
            with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos", return_value=_REPOS):
                assert run_precompact() == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_ratchet_no_system_message_in_any_hook(self):
        """Source-level: no hook handler builds a top-level ``systemMessage`` dict.

        Proven non-vacuous by restoring the 1.140.0 ``{"systemMessage": snapshot}``
        line in run_precompact and watching it fire.
        """
        from jdocmunch_mcp.cli import hooks
        offenders = []
        for name in ("run_pretooluse", "run_posttooluse", "run_precompact", "run_sessionstart"):
            tree = ast.parse(inspect.getsource(getattr(hooks, name)))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and node.value == "systemMessage":
                    offenders.append(name)
        assert not offenders, f"top-level systemMessage never reaches the model (#131): {offenders}"


class TestInstall:
    def test_init_adds_sessionstart_beside_an_existing_precompact_entry(self, tmp_path):
        """A 1.x settings.json from an earlier init gains SessionStart on re-run."""
        from jdocmunch_mcp.cli.init import _enforcement_hooks, install_hooks
        exe_hooks = _enforcement_hooks()
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"hooks": {
            "PreToolUse": exe_hooks["PreToolUse"],
            "PostToolUse": exe_hooks["PostToolUse"],
            "PreCompact": exe_hooks["PreCompact"],
        }}))
        with mock.patch("jdocmunch_mcp.cli.init._settings_json_path", return_value=settings):
            install_hooks(backup=False)
        hooks = json.loads(settings.read_text())["hooks"]
        assert len(hooks["PreCompact"]) == 1
        assert [r["matcher"] for r in hooks["SessionStart"]] == ["compact|resume|fork"]
        assert hooks["SessionStart"][0]["hooks"][0]["command"].endswith(" hook-sessionstart")

    def test_cli_dispatch(self):
        from jdocmunch_mcp.server import main
        with mock.patch("jdocmunch_mcp.cli.hooks.run_sessionstart", return_value=0) as m:
            with pytest.raises(SystemExit):
                main(["hook-sessionstart"])
        m.assert_called_once()
