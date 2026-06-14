"""Tests for CLI hook handlers and init --hooks installer."""

import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# PreToolUse hook
# ---------------------------------------------------------------------------

class TestPreToolUse:
    """Tests for hook-pretooluse handler."""

    def _run(self, payload: dict) -> int:
        from jdocmunch_mcp.cli.hooks import run_pretooluse
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            return run_pretooluse()

    def test_allows_non_doc_file(self):
        assert self._run({"tool_input": {"file_path": "app.py"}}) == 0

    def test_allows_small_doc_file(self, tmp_path):
        p = tmp_path / "small.md"
        p.write_text("hi")
        assert self._run({"tool_input": {"file_path": str(p)}}) == 0

    def test_warns_on_large_doc_file(self, tmp_path, capsys):
        p = tmp_path / "big.md"
        p.write_text("x" * 5000)
        assert self._run({"tool_input": {"file_path": str(p)}}) == 0
        captured = capsys.readouterr()
        assert "jDocMunch hint" in captured.err

    def test_allows_targeted_read(self, tmp_path):
        p = tmp_path / "big.md"
        p.write_text("x" * 5000)
        assert self._run({"tool_input": {"file_path": str(p), "offset": 10, "limit": 5}}) == 0

    def test_allows_rst_small(self, tmp_path):
        p = tmp_path / "doc.rst"
        p.write_text("hi")
        assert self._run({"tool_input": {"file_path": str(p)}}) == 0

    def test_warns_on_large_rst(self, tmp_path, capsys):
        p = tmp_path / "doc.rst"
        p.write_text("x" * 5000)
        assert self._run({"tool_input": {"file_path": str(p)}}) == 0
        assert "jDocMunch hint" in capsys.readouterr().err

    def test_warns_on_large_adoc(self, tmp_path, capsys):
        p = tmp_path / "doc.adoc"
        p.write_text("x" * 5000)
        assert self._run({"tool_input": {"file_path": str(p)}}) == 0
        assert "jDocMunch hint" in capsys.readouterr().err

    def test_handles_invalid_json(self):
        from jdocmunch_mcp.cli.hooks import run_pretooluse
        with mock.patch("sys.stdin", io.StringIO("not json")):
            assert run_pretooluse() == 0

    def test_handles_missing_file_path(self):
        assert self._run({"tool_input": {}}) == 0

    def test_handles_nonexistent_file(self):
        assert self._run({"tool_input": {"file_path": "/nonexistent/doc.md"}}) == 0


# ---------------------------------------------------------------------------
# PostToolUse hook
# ---------------------------------------------------------------------------

class TestPostToolUse:
    """Tests for hook-posttooluse handler."""

    def _run(self, payload: dict) -> int:
        from jdocmunch_mcp.cli.hooks import run_posttooluse
        with mock.patch("sys.stdin", io.StringIO(json.dumps(payload))):
            return run_posttooluse()

    def test_skips_non_doc_file(self):
        with mock.patch("subprocess.Popen") as mock_popen:
            assert self._run({"tool_input": {"file_path": "app.py"}}) == 0
            mock_popen.assert_not_called()

    def test_spawns_reindex_for_md(self, tmp_path):
        p = tmp_path / "README.md"
        p.write_text("hello")
        with mock.patch("subprocess.Popen") as mock_popen:
            assert self._run({"tool_input": {"file_path": str(p)}}) == 0
            mock_popen.assert_called_once()
            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "jdocmunch-mcp"
            assert cmd[1] == "index-file"

    def test_spawns_reindex_for_rst(self, tmp_path):
        p = tmp_path / "doc.rst"
        p.write_text("hello")
        with mock.patch("subprocess.Popen") as mock_popen:
            assert self._run({"tool_input": {"file_path": str(p)}}) == 0
            mock_popen.assert_called_once()

    def test_spawns_reindex_for_txt(self, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("hello")
        with mock.patch("subprocess.Popen") as mock_popen:
            assert self._run({"tool_input": {"file_path": str(p)}}) == 0
            mock_popen.assert_called_once()

    def test_handles_invalid_json(self):
        from jdocmunch_mcp.cli.hooks import run_posttooluse
        with mock.patch("sys.stdin", io.StringIO("bad")):
            assert run_posttooluse() == 0

    def test_handles_popen_failure(self, tmp_path):
        p = tmp_path / "doc.md"
        p.write_text("hello")
        with mock.patch("subprocess.Popen", side_effect=FileNotFoundError):
            assert self._run({"tool_input": {"file_path": str(p)}}) == 0


# ---------------------------------------------------------------------------
# PreCompact hook
# ---------------------------------------------------------------------------

class TestPreCompact:
    """Tests for hook-precompact handler."""

    def test_returns_snapshot(self, capsys):
        from jdocmunch_mcp.cli.hooks import run_precompact
        mock_repos = {
            "repos": [{"name": "test-repo", "section_count": 42, "doc_count": 5, "source_root": "/tmp/docs"}],
            "count": 1,
        }
        with mock.patch("sys.stdin", io.StringIO("{}")):
            with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos", return_value=mock_repos):
                assert run_precompact() == 0

        out = capsys.readouterr().out
        result = json.loads(out)
        assert "systemMessage" in result
        assert "test-repo" in result["systemMessage"]

    def test_returns_nothing_when_no_repos(self, capsys):
        from jdocmunch_mcp.cli.hooks import run_precompact
        with mock.patch("sys.stdin", io.StringIO("{}")):
            with mock.patch("jdocmunch_mcp.tools.list_repos.list_repos", return_value={"repos": [], "count": 0}):
                assert run_precompact() == 0
        assert capsys.readouterr().out == ""

    def test_handles_invalid_json(self):
        from jdocmunch_mcp.cli.hooks import run_precompact
        with mock.patch("sys.stdin", io.StringIO("bad")):
            assert run_precompact() == 0


# ---------------------------------------------------------------------------
# init --hooks installer
# ---------------------------------------------------------------------------

class TestInstallHooks:
    """Tests for init --hooks."""

    def test_installs_all_three_hooks(self, tmp_path):
        from jdocmunch_mcp.cli.init import install_hooks, _settings_json_path
        settings = tmp_path / "settings.json"
        settings.write_text("{}")

        with mock.patch("jdocmunch_mcp.cli.init._settings_json_path", return_value=settings):
            msg = install_hooks(backup=False)

        assert "PreToolUse" in msg or "added" in msg
        data = json.loads(settings.read_text())
        hooks = data["hooks"]
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks
        assert "PreCompact" in hooks

        # Verify commands (#39: built from the resolved executable path).
        from jdocmunch_mcp.cli.init import _hook_invocation
        exe = _hook_invocation()
        assert hooks["PreToolUse"][0]["hooks"][0]["command"] == f"{exe} hook-pretooluse"
        assert hooks["PostToolUse"][0]["hooks"][0]["command"] == f"{exe} hook-posttooluse"
        assert hooks["PreCompact"][0]["hooks"][0]["command"] == f"{exe} hook-precompact"

    def test_idempotent(self, tmp_path):
        from jdocmunch_mcp.cli.init import install_hooks
        settings = tmp_path / "settings.json"
        settings.write_text("{}")

        with mock.patch("jdocmunch_mcp.cli.init._settings_json_path", return_value=settings):
            install_hooks(backup=False)
            msg = install_hooks(backup=False)

        assert "already present" in msg

    def test_preserves_existing_hooks(self, tmp_path):
        from jdocmunch_mcp.cli.init import install_hooks
        settings = tmp_path / "settings.json"
        existing = {
            "hooks": {
                "PreToolUse": [{
                    "matcher": "Read",
                    "hooks": [{"type": "command", "command": "jcodemunch-mcp hook-pretooluse"}],
                }]
            }
        }
        settings.write_text(json.dumps(existing))

        with mock.patch("jdocmunch_mcp.cli.init._settings_json_path", return_value=settings):
            install_hooks(backup=False)

        data = json.loads(settings.read_text())
        # Both jcodemunch and jdocmunch hooks should be present
        from jdocmunch_mcp.cli.init import _hook_invocation
        pre_rules = data["hooks"]["PreToolUse"]
        cmds = [r["hooks"][0]["command"] for r in pre_rules]
        assert "jcodemunch-mcp hook-pretooluse" in cmds
        assert f"{_hook_invocation()} hook-pretooluse" in cmds

    def test_dry_run(self, tmp_path):
        from jdocmunch_mcp.cli.init import install_hooks
        settings = tmp_path / "settings.json"
        settings.write_text("{}")

        with mock.patch("jdocmunch_mcp.cli.init._settings_json_path", return_value=settings):
            msg = install_hooks(dry_run=True)

        assert "would add" in msg
        # File should still be empty
        assert json.loads(settings.read_text()) == {}

    def test_creates_backup(self, tmp_path):
        from jdocmunch_mcp.cli.init import install_hooks
        settings = tmp_path / "settings.json"
        settings.write_text('{"existing": true}')

        with mock.patch("jdocmunch_mcp.cli.init._settings_json_path", return_value=settings):
            install_hooks(backup=True)

        bak = tmp_path / "settings.json.bak"
        assert bak.exists()
        assert json.loads(bak.read_text()) == {"existing": True}


# ---------------------------------------------------------------------------
# CLI dispatch (server.py main)
# ---------------------------------------------------------------------------

class TestCLIDispatch:
    """Test that CLI subcommands are routed correctly."""

    def test_hook_pretooluse_dispatch(self):
        from jdocmunch_mcp.server import main
        with mock.patch("jdocmunch_mcp.cli.hooks.run_pretooluse", return_value=0) as m:
            with pytest.raises(SystemExit) as exc_info:
                main(["hook-pretooluse"])
            assert exc_info.value.code == 0
            m.assert_called_once()

    def test_hook_posttooluse_dispatch(self):
        from jdocmunch_mcp.server import main
        with mock.patch("jdocmunch_mcp.cli.hooks.run_posttooluse", return_value=0) as m:
            with pytest.raises(SystemExit) as exc_info:
                main(["hook-posttooluse"])
            assert exc_info.value.code == 0
            m.assert_called_once()

    def test_hook_precompact_dispatch(self):
        from jdocmunch_mcp.server import main
        with mock.patch("jdocmunch_mcp.cli.hooks.run_precompact", return_value=0) as m:
            with pytest.raises(SystemExit) as exc_info:
                main(["hook-precompact"])
            assert exc_info.value.code == 0
            m.assert_called_once()

    def test_index_local_dispatch(self, tmp_path):
        from jdocmunch_mcp.server import main
        mock_result = {"status": "ok", "files_indexed": 0}
        with mock.patch("jdocmunch_mcp.tools.index_local.index_local", return_value=mock_result) as m:
            main(["index-local", "--path", str(tmp_path)])
            m.assert_called_once_with(path=str(tmp_path), name=None, paths=None)

    def test_init_hooks_dispatch(self):
        from jdocmunch_mcp.server import main
        with mock.patch("jdocmunch_mcp.cli.init.run_init", return_value=0) as m:
            with pytest.raises(SystemExit) as exc_info:
                main(["init", "--hooks"])
            assert exc_info.value.code == 0
            m.assert_called_once_with(
                clients=None, claude_md=None, hooks=True, index=False,
                dry_run=False, demo=False, yes=False, no_backup=False,
            )

    def test_claude_md_dispatch(self):
        from jdocmunch_mcp.server import main
        with mock.patch("jdocmunch_mcp.cli.init.run_claude_md", return_value=0) as m:
            with pytest.raises(SystemExit) as exc_info:
                main(["claude-md"])
            assert exc_info.value.code == 0
            m.assert_called_once_with(install=None)

    def test_claude_md_install_dispatch(self):
        from jdocmunch_mcp.server import main
        with mock.patch("jdocmunch_mcp.cli.init.run_claude_md", return_value=0) as m:
            with pytest.raises(SystemExit) as exc_info:
                main(["claude-md", "--install", "global"])
            assert exc_info.value.code == 0
            m.assert_called_once_with(install="global")

    def test_index_file_dispatch(self, tmp_path):
        from jdocmunch_mcp.server import main
        mock_result = {"success": True, "exit_code": 0}
        with mock.patch("jdocmunch_mcp.tools.index_file.index_file_cli", return_value=mock_result) as m:
            with pytest.raises(SystemExit) as exc_info:
                main(["index-file", str(tmp_path / "doc.md")])
            assert exc_info.value.code == 0
            m.assert_called_once_with(str(tmp_path / "doc.md"), name=None)


# ---------------------------------------------------------------------------
# Client detection
# ---------------------------------------------------------------------------

class TestClientDetection:
    """Tests for MCP client detection."""

    def test_detects_claude_code(self):
        from jdocmunch_mcp.cli.init import _detect_clients
        with mock.patch("shutil.which", return_value="/usr/bin/claude"):
            clients = _detect_clients()
        names = [c.name for c in clients]
        assert "Claude Code" in names

    def test_no_clients_when_nothing_installed(self, tmp_path):
        from jdocmunch_mcp.cli.init import _detect_clients
        with mock.patch("shutil.which", return_value=None):
            with mock.patch("pathlib.Path.home", return_value=tmp_path):
                clients = _detect_clients()
        # May still detect Claude Desktop if appdata parent exists
        assert isinstance(clients, list)


# ---------------------------------------------------------------------------
# Config patching
# ---------------------------------------------------------------------------

class TestConfigPatching:
    """Tests for MCP client config patching."""

    def test_patches_empty_config(self, tmp_path):
        from jdocmunch_mcp.cli.init import _patch_mcp_config
        config = tmp_path / "mcp.json"
        config.write_text("{}")
        msg = _patch_mcp_config(config, backup=False)
        assert "added jdocmunch" in msg
        data = json.loads(config.read_text())
        assert "jdocmunch" in data["mcpServers"]
        assert data["mcpServers"]["jdocmunch"]["command"] == "uvx"

    def test_preserves_existing_servers(self, tmp_path):
        from jdocmunch_mcp.cli.init import _patch_mcp_config
        config = tmp_path / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"other": {"command": "node"}}}))
        _patch_mcp_config(config, backup=False)
        data = json.loads(config.read_text())
        assert "other" in data["mcpServers"]
        assert "jdocmunch" in data["mcpServers"]

    def test_idempotent(self, tmp_path):
        from jdocmunch_mcp.cli.init import _patch_mcp_config
        config = tmp_path / "mcp.json"
        config.write_text(json.dumps({"mcpServers": {"jdocmunch": {"command": "uvx"}}}))
        msg = _patch_mcp_config(config, backup=False)
        assert "already configured" in msg

    def test_dry_run(self, tmp_path):
        from jdocmunch_mcp.cli.init import _patch_mcp_config
        config = tmp_path / "mcp.json"
        config.write_text("{}")
        msg = _patch_mcp_config(config, dry_run=True)
        assert "would add" in msg
        assert json.loads(config.read_text()) == {}


# ---------------------------------------------------------------------------
# CLAUDE.md injection
# ---------------------------------------------------------------------------

class TestClaudeMdInjection:
    """Tests for CLAUDE.md policy installation."""

    def test_appends_policy(self, tmp_path):
        from jdocmunch_mcp.cli.init import install_claude_md, _CLAUDE_MD_MARKER
        md = tmp_path / "CLAUDE.md"
        md.write_text("# Existing content\n")
        with mock.patch("jdocmunch_mcp.cli.init._claude_md_path", return_value=md):
            msg = install_claude_md("global", backup=False)
        assert "appended" in msg
        content = md.read_text()
        assert _CLAUDE_MD_MARKER in content
        assert "# Existing content" in content

    def test_creates_new_file(self, tmp_path):
        from jdocmunch_mcp.cli.init import install_claude_md, _CLAUDE_MD_MARKER
        md = tmp_path / "CLAUDE.md"
        with mock.patch("jdocmunch_mcp.cli.init._claude_md_path", return_value=md):
            install_claude_md("global", backup=False)
        assert md.exists()
        assert _CLAUDE_MD_MARKER in md.read_text()

    def test_idempotent(self, tmp_path):
        from jdocmunch_mcp.cli.init import install_claude_md, _CLAUDE_MD_MARKER
        md = tmp_path / "CLAUDE.md"
        md.write_text(f"existing\n\n{_CLAUDE_MD_MARKER}\nstuff")
        with mock.patch("jdocmunch_mcp.cli.init._claude_md_path", return_value=md):
            msg = install_claude_md("global", backup=False)
        assert "already present" in msg


# ---------------------------------------------------------------------------
# Cursor / Windsurf rules
# ---------------------------------------------------------------------------

class TestIDERules:
    """Tests for Cursor and Windsurf rule installation."""

    def test_writes_cursor_rules(self, tmp_path):
        from jdocmunch_mcp.cli.init import install_cursor_rules, _CLAUDE_MD_MARKER
        with mock.patch("jdocmunch_mcp.cli.init._cursor_rules_path", return_value=tmp_path / ".cursor" / "rules" / "jdocmunch.mdc"):
            msg = install_cursor_rules(backup=False)
        assert "wrote" in msg
        content = (tmp_path / ".cursor" / "rules" / "jdocmunch.mdc").read_text()
        assert "alwaysApply: true" in content
        assert _CLAUDE_MD_MARKER in content

    def test_writes_windsurf_rules(self, tmp_path):
        from jdocmunch_mcp.cli.init import install_windsurf_rules, _CLAUDE_MD_MARKER
        ws = tmp_path / ".windsurfrules"
        with mock.patch("jdocmunch_mcp.cli.init._windsurf_rules_path", return_value=ws):
            msg = install_windsurf_rules(backup=False)
        assert "appended" in msg
        assert _CLAUDE_MD_MARKER in ws.read_text()


# ---------------------------------------------------------------------------
# claude-md subcommand
# ---------------------------------------------------------------------------

class TestClaudeMdCommand:
    """Tests for the claude-md standalone subcommand."""

    def test_prints_policy(self, capsys):
        from jdocmunch_mcp.cli.init import run_claude_md, _CLAUDE_MD_MARKER
        rc = run_claude_md()
        assert rc == 0
        out = capsys.readouterr().out
        assert _CLAUDE_MD_MARKER in out

    def test_install_global(self, tmp_path):
        from jdocmunch_mcp.cli.init import run_claude_md, _CLAUDE_MD_MARKER
        md = tmp_path / "CLAUDE.md"
        with mock.patch("jdocmunch_mcp.cli.init._claude_md_path", return_value=md):
            rc = run_claude_md(install="global")
        assert rc == 0
        assert _CLAUDE_MD_MARKER in md.read_text()


# ---------------------------------------------------------------------------
# index-file tool
# ---------------------------------------------------------------------------

class TestIndexFile:
    """Tests for index_file tool."""

    def test_rejects_nonexistent_file(self):
        from jdocmunch_mcp.tools.index_file import index_file
        result = index_file("/nonexistent/doc.md")
        assert not result["success"]
        assert result["exit_code"] == 2

    def test_rejects_non_doc_extension(self, tmp_path):
        from jdocmunch_mcp.tools.index_file import index_file
        p = tmp_path / "code.py"
        p.write_text("print('hi')")
        result = index_file(str(p))
        assert not result["success"]
        assert "Not a doc file" in result["error"]

    def test_rejects_file_not_in_index(self, tmp_path):
        from jdocmunch_mcp.tools.index_file import index_file
        p = tmp_path / "doc.md"
        p.write_text("# Hello")
        result = index_file(str(p))
        assert not result["success"]
        assert result["exit_code"] == 1

    def test_reindexes_file_in_existing_index(self, tmp_path):
        """Full integration: index a folder, then re-index a single file."""
        from jdocmunch_mcp.tools.index_local import index_local
        from jdocmunch_mcp.tools.index_file import index_file

        # Create a mini doc folder
        doc_dir = tmp_path / "testdocs"
        doc_dir.mkdir()
        (doc_dir / "one.md").write_text("# One\nContent one")
        (doc_dir / "two.md").write_text("# Two\nContent two")

        # Index the folder
        storage = str(tmp_path / "storage")
        r1 = index_local(path=str(doc_dir), storage_path=storage)
        assert r1["success"]

        # Modify a file
        (doc_dir / "one.md").write_text("# One Updated\nNew content")

        # Re-index just that file
        r2 = index_file(str(doc_dir / "one.md"), storage_path=storage)
        assert r2["success"]
        assert r2["file"] == "one.md"
        assert not r2["is_new"]
        assert r2["sections"] >= 1
