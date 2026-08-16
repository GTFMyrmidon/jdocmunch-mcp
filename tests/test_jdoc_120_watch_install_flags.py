"""jdoc#120: `watch-install` can express the flags the watcher it installs accepts.

Two halves, reported together by @williamblair333:

1. `watch --no-ai-summaries` works in the foreground; `watch-install` had no
   arguments at all and `_exec_cmd()` was a constant, so the installed service
   always ran the daemon with `use_ai_summaries=True`. A corpus deliberately
   indexed without summaries regained them on the watcher's first refresh.
2. Every installer rewrites its whole service definition on each run, so the
   documented workaround — hand-editing `ExecStart` — was reverted by the next
   `watch-install`, silently.

The revert still happens (a merge would make the installed argv unpredictable);
what these tests pin is that it is now REPORTED, and that hand-editing is no
longer the only way to express the setting.
"""
import json
import plistlib
import subprocess
import sys

import pytest

from jdocmunch_mcp import server, service_installer


# ── the argv the service installs ────────────────────────────────────────────


class TestExecCmd:
    def test_no_args_is_unchanged(self):
        # Control: an existing caller installs exactly what it installed before.
        assert service_installer._exec_cmd() == [
            sys.executable, "-m", "jdocmunch_mcp", "watch",
        ]

    def test_flags_are_appended_verbatim(self):
        assert service_installer._exec_cmd(["--no-ai-summaries"]) == [
            sys.executable, "-m", "jdocmunch_mcp", "watch", "--no-ai-summaries",
        ]

    def test_multiple_flags_keep_order(self):
        cmd = service_installer._exec_cmd(["--no-ai-summaries", "--quiet"])
        assert cmd[-2:] == ["--no-ai-summaries", "--quiet"]


# ── systemd ──────────────────────────────────────────────────────────────────


@pytest.fixture
def systemd_host(tmp_path, monkeypatch):
    """A fake systemd host: real files on disk, no systemctl."""
    unit = tmp_path / "systemd" / "jdocmunch-watch.service"
    monkeypatch.setattr(service_installer, "_systemd_unit_path", lambda: unit)
    monkeypatch.setattr(service_installer, "_log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(service_installer.shutil, "which", lambda _: "/usr/bin/systemctl")
    monkeypatch.setattr(service_installer.subprocess, "run", lambda *a, **k: None)
    return unit


def _exec_start(unit_path):
    for line in unit_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("ExecStart="):
            return line[len("ExecStart="):]
    raise AssertionError("no ExecStart in unit")


class TestSystemd:
    def test_flag_reaches_execstart(self, systemd_host):
        service_installer._install_systemd(["--no-ai-summaries"])
        assert "--no-ai-summaries" in _exec_start(systemd_host)

    def test_no_flag_leaves_execstart_bare(self, systemd_host):
        service_installer._install_systemd()
        assert "--no-ai-summaries" not in _exec_start(systemd_host)

    def test_reverting_a_hand_edit_is_reported(self, systemd_host):
        # The user's workaround: install, then hand-edit the unit.
        service_installer._install_systemd()
        systemd_host.write_text(
            systemd_host.read_text(encoding="utf-8").replace(
                "watch\n", "watch --no-ai-summaries\n"
            ),
            encoding="utf-8",
        )
        # ...then upgrade, which re-runs watch-install.
        out = service_installer._install_systemd()
        assert "replaced_exec" in out
        assert "--no-ai-summaries" in out["replaced_exec"]["previous"]
        assert "--no-ai-summaries" not in out["replaced_exec"]["installed"]

    def test_reinstalling_the_same_unit_reports_nothing(self, systemd_host):
        # Control: an idempotent re-install must not cry wolf.
        service_installer._install_systemd(["--no-ai-summaries"])
        out = service_installer._install_systemd(["--no-ai-summaries"])
        assert "replaced_exec" not in out

    def test_first_install_reports_nothing(self, systemd_host):
        # Control: there is no previous definition to have overwritten.
        assert "replaced_exec" not in service_installer._install_systemd()

    def test_reading_an_absent_unit_is_none(self, systemd_host):
        assert service_installer._installed_systemd_exec() is None

    def test_a_backslash_in_the_interpreter_path_still_compares_equal(
        self, systemd_host, monkeypatch
    ):
        # ⚠ Regression on the first cut of this fix, which split ExecStart back
        # into an argv with `shlex.split`. POSIX-mode splitting eats backslashes,
        # so a Windows-shaped interpreter path never round-tripped and EVERY
        # re-install reported a customisation nobody had made. The comparison
        # target is a string this module wrote — so it is compared as a string.
        monkeypatch.setattr(service_installer.sys, "executable", r"C:\py\python.exe")
        service_installer._install_systemd()
        assert "replaced_exec" not in service_installer._install_systemd()


# ── launchd ──────────────────────────────────────────────────────────────────


@pytest.fixture
def launchd_host(tmp_path, monkeypatch):
    plist = tmp_path / "LaunchAgents" / "us.gravelle.jdocmunch-watch.plist"
    monkeypatch.setattr(service_installer, "_launchd_plist_path", lambda: plist)
    monkeypatch.setattr(service_installer, "_log_dir", lambda: tmp_path / "logs")
    monkeypatch.setattr(service_installer.subprocess, "run", lambda *a, **k: None)
    return plist


class TestLaunchd:
    def test_flag_reaches_program_arguments(self, launchd_host):
        service_installer._install_launchd(["--no-ai-summaries"])
        with launchd_host.open("rb") as fh:
            data = plistlib.load(fh)
        assert data["ProgramArguments"][-1] == "--no-ai-summaries"

    def test_reverting_a_hand_edit_is_reported(self, launchd_host):
        service_installer._install_launchd(["--quiet"])
        out = service_installer._install_launchd()
        assert out["replaced_exec"]["previous"][-1] == "--quiet"

    def test_reinstalling_the_same_plist_reports_nothing(self, launchd_host):
        service_installer._install_launchd(["--quiet"])
        assert "replaced_exec" not in service_installer._install_launchd(["--quiet"])

    def test_unreadable_plist_is_none_not_a_crash(self, launchd_host):
        launchd_host.parent.mkdir(parents=True, exist_ok=True)
        launchd_host.write_text("not a plist", encoding="utf-8")
        assert service_installer._installed_launchd_exec() is None


# ── Task Scheduler ───────────────────────────────────────────────────────────


class TestWindows:
    def test_flag_reaches_the_task_command(self, tmp_path, monkeypatch):
        seen = {}

        def fake_run(args, **kwargs):
            if args[:2] == ["schtasks", "/Create"]:
                seen["tr"] = args[args.index("/TR") + 1]
            return subprocess.CompletedProcess(args, 0, "", "")

        monkeypatch.setattr(service_installer, "_log_dir", lambda: tmp_path / "logs")
        monkeypatch.setattr(service_installer.subprocess, "run", fake_run)
        service_installer._install_windows(["--no-ai-summaries"])
        assert "--no-ai-summaries" in seen["tr"]

    def test_query_failure_reads_as_unknown_not_as_changed(self, monkeypatch):
        # ⚠ schtasks labels its output in the system's display language. An
        # unreadable answer must produce no warning at all — a false "we
        # overwrote your customisation" is worse than staying quiet.
        monkeypatch.setattr(
            service_installer.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "ERROR:"),
        )
        assert service_installer._installed_windows_exec() is None

    def test_localised_labels_read_as_unknown(self, monkeypatch):
        monkeypatch.setattr(
            service_installer.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a, 0, "Tâche à exécuter:  python -m jdocmunch_mcp watch\n", ""
            ),
        )
        assert service_installer._installed_windows_exec() is None

    def test_task_to_run_is_read_when_labelled_in_english(self, monkeypatch):
        monkeypatch.setattr(
            service_installer.subprocess, "run",
            lambda *a, **k: subprocess.CompletedProcess(
                a, 0, "TaskName:  \\jdocmunch-watch\n"
                      "Task To Run:  python -m jdocmunch_mcp watch --quiet\n", ""
            ),
        )
        assert service_installer._installed_windows_exec().endswith("--quiet")


# ── dispatch + CLI, i.e. the user's entry point ──────────────────────────────


class TestDispatch:
    @staticmethod
    def _capture(monkeypatch, name, seen):
        def fake(watch_args=None):
            seen["args"] = watch_args
            return {}

        monkeypatch.setattr(service_installer, name, fake)

    @pytest.mark.parametrize("system,installer", [
        ("Linux", "_install_systemd"),
        ("Darwin", "_install_launchd"),
        ("Windows", "_install_windows"),
    ])
    def test_flags_reach_every_platform(self, system, installer, monkeypatch):
        seen = {}
        monkeypatch.setattr(service_installer.platform, "system", lambda: system)
        self._capture(monkeypatch, installer, seen)
        service_installer.install_service(["--no-ai-summaries"])
        assert seen["args"] == ["--no-ai-summaries"]

    def test_default_is_no_flags(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(service_installer.platform, "system", lambda: "Linux")
        self._capture(monkeypatch, "_install_systemd", seen)
        service_installer.install_service()
        assert seen["args"] is None


class TestCli:
    def _run(self, argv, monkeypatch, result=None):
        seen = {}

        def fake_install(watch_args=None):
            seen["args"] = watch_args
            return result or {}

        monkeypatch.setattr(service_installer, "install_service", fake_install)
        server.main(argv)
        return seen.get("args")

    def test_no_ai_summaries_is_passed_through(self, monkeypatch, capsys):
        assert self._run(["watch-install", "--no-ai-summaries"], monkeypatch) == [
            "--no-ai-summaries"
        ]

    def test_quiet_is_passed_through(self, monkeypatch, capsys):
        assert self._run(["watch-install", "--quiet"], monkeypatch) == ["--quiet"]

    def test_both_flags(self, monkeypatch, capsys):
        assert self._run(
            ["watch-install", "--no-ai-summaries", "--quiet"], monkeypatch
        ) == ["--no-ai-summaries", "--quiet"]

    def test_bare_install_passes_an_empty_list(self, monkeypatch, capsys):
        assert self._run(["watch-install"], monkeypatch) == []

    def test_an_invented_spelling_is_rejected(self, monkeypatch, capsys):
        # One source of truth: `watch-install` accepts `watch`'s flag names and
        # nothing else, so a near-miss fails loudly instead of installing the
        # default under a name the user thought they had changed.
        with pytest.raises(SystemExit):
            self._run(["watch-install", "--no-summaries"], monkeypatch)

    def test_a_reverted_hand_edit_is_announced_on_stderr(self, monkeypatch, capsys):
        self._run(
            ["watch-install"], monkeypatch,
            result={
                "platform": "systemd",
                "replaced_exec": {
                    "previous": ["python", "-m", "jdocmunch_mcp", "watch", "--no-ai-summaries"],
                    "installed": ["python", "-m", "jdocmunch_mcp", "watch"],
                },
            },
        )
        err = capsys.readouterr().err
        assert "replaced a customised service definition" in err
        assert "--no-ai-summaries" in err

    def test_an_ordinary_install_says_nothing_about_replacement(self, monkeypatch, capsys):
        self._run(["watch-install"], monkeypatch, result={"platform": "systemd"})
        assert "customised" not in capsys.readouterr().err

    def test_result_json_still_prints(self, monkeypatch, capsys):
        self._run(["watch-install"], monkeypatch, result={"platform": "systemd"})
        assert json.loads(capsys.readouterr().out)["platform"] == "systemd"
