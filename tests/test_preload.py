"""jdoc#118: the numpy preload must run on the main thread, before any other.

⚠ These tests pin the CONTRACT (ordering, fail-open, opt-out), not the wedge.
The wedge is a Windows loader race that needs a cold machine to reproduce; see
``preload.py`` for the captured stack and for why the remedy is not yet
demonstrated. A test that could assert "no longer deadlocks" would have to
reproduce the deadlock first, and it cannot do that reliably.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jdocmunch_mcp import preload

SERVER_PY = Path(preload.__file__).with_name("server.py")


def _async_main_body() -> list:
    """Statements of the async ``run_server()`` in server.py, in source order."""
    tree = ast.parse(SERVER_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "run_server":
            return node.body
    raise AssertionError("async run_server() not found in server.py")


def _first_line_matching(body: list, needle: str) -> int:
    """First non-import statement mentioning ``needle``.

    ⚠ Imports are skipped deliberately. The first draft of this helper did not
    skip them, so both ordering tests matched ``from ... import
    preload_native_deps`` and ``from mcp.server.stdio import stdio_server``
    instead of the call and the ``async with`` — and passed on the accident
    that run_server()'s import block happens to sit above everything it guards.
    A test that reads an import as the thing being ordered cannot see a
    reordering of the things themselves.
    """
    for stmt in body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            continue
        if needle in ast.unparse(stmt):
            return stmt.lineno
    raise AssertionError(
        f"no non-import statement in async run_server() contains {needle!r}"
    )


def test_preload_runs_before_any_thread_is_started():
    """The ordering IS the fix — a preload after the first thread is the bug.

    Asserted against source order rather than by running the server, because
    the failure this guards is silent: move the thread start up and every
    behavioural test still passes.
    """
    body = _async_main_body()
    preload_line = _first_line_matching(body, "preload_native_deps")
    thread_line = _first_line_matching(body, "threading.Thread")
    assert preload_line < thread_line, (
        "preload_native_deps() must run before the warmup thread starts; "
        f"found preload at line {preload_line} and Thread(...).start() at {thread_line}"
    )


def test_preload_precedes_the_event_loop_too():
    """Nothing awaited may run before the preload, or a task can race it."""
    body = _async_main_body()
    preload_line = _first_line_matching(body, "preload_native_deps")
    transport_line = _first_line_matching(body, "stdio_server")
    assert preload_line < transport_line


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", " False "])
def test_explicit_off_values_disable(monkeypatch, value):
    monkeypatch.setenv("JDOCMUNCH_PRELOAD_NUMPY", value)
    assert preload.preload_enabled() is False
    assert preload.preload_native_deps() == {}


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON"])
def test_explicit_on_values_force_it_on_every_platform(monkeypatch, value):
    monkeypatch.setenv("JDOCMUNCH_PRELOAD_NUMPY", value)
    for platform in ("win32", "linux", "darwin"):
        monkeypatch.setattr(preload.sys, "platform", platform)
        assert preload.preload_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_explicit_off_wins_on_every_platform(monkeypatch, value):
    monkeypatch.setenv("JDOCMUNCH_PRELOAD_NUMPY", value)
    for platform in ("win32", "linux", "darwin"):
        monkeypatch.setattr(preload.sys, "platform", platform)
        assert preload.preload_enabled() is False


@pytest.mark.parametrize("value", ["", "flase", "no thanks", "2"])
def test_unrecognised_falls_back_to_the_platform_default(monkeypatch, value):
    """A typo must not silently reintroduce the hang on Windows...

    ...nor silently add startup cost anywhere else. Unrecognised means
    "unset", not "on".
    """
    monkeypatch.setenv("JDOCMUNCH_PRELOAD_NUMPY", value)
    monkeypatch.setattr(preload.sys, "platform", "win32")
    assert preload.preload_enabled() is True
    monkeypatch.setattr(preload.sys, "platform", "linux")
    assert preload.preload_enabled() is False


def test_unset_is_windows_only(monkeypatch):
    """The stack in jdoc#118 is a Windows loader wait; scope the remedy to it."""
    monkeypatch.delenv("JDOCMUNCH_PRELOAD_NUMPY", raising=False)
    monkeypatch.setattr(preload.sys, "platform", "win32")
    assert preload.preload_enabled() is True
    for platform in ("linux", "darwin"):
        monkeypatch.setattr(preload.sys, "platform", platform)
        assert preload.preload_enabled() is False
        assert preload.preload_native_deps() == {}


def test_absent_module_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_PRELOAD_NUMPY", "1")
    monkeypatch.setattr(preload, "_PRELOAD", ("jdoc_no_such_module_118",))
    report = preload.preload_native_deps()
    assert report == {"jdoc_no_such_module_118": "absent"}


def test_import_failure_is_reported_not_raised(monkeypatch):
    """numpy stays optional; a broken install must not stop the server."""
    monkeypatch.setenv("JDOCMUNCH_PRELOAD_NUMPY", "1")
    monkeypatch.setattr(preload, "_PRELOAD", ("jdoc_boom_118",))
    monkeypatch.setattr(
        preload.importlib.util, "find_spec", lambda name: object()
    )
    report = preload.preload_native_deps()
    assert report["jdoc_boom_118"].startswith("error: ")


def test_numpy_is_actually_imported_when_present(monkeypatch):
    pytest.importorskip("numpy")
    monkeypatch.setenv("JDOCMUNCH_PRELOAD_NUMPY", "1")
    report = preload.preload_native_deps()
    assert "loaded in" in report["numpy"]
    assert "numpy" in sys.modules


def test_preload_is_single_threaded_when_it_runs():
    """The guarantee is 'alone on the main thread', so prove it in a fresh process.

    Running it inside pytest cannot show this — the test session already has
    threads. A subprocess that mirrors run_server()'s ordering can.
    """
    code = (
        "import threading, sys;"
        "sys.path.insert(0, r'%s');"
        "from jdocmunch_mcp.preload import preload_native_deps;"
        "n_before = threading.active_count();"
        "preload_native_deps();"
        "print(n_before, threading.current_thread() is threading.main_thread())"
        % str(Path(preload.__file__).parents[1])
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "JDOCMUNCH_PRELOAD_NUMPY": "1"},
    )
    assert out.returncode == 0, out.stderr
    n_before, on_main = out.stdout.split()
    assert n_before == "1", f"expected a single-threaded process, saw {n_before} threads"
    assert on_main == "True"
