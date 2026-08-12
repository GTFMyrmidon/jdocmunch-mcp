"""jdoc#118: the native preloads must run on the main thread, before any other.

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
    thread_line = _first_line_matching(body, "threading.Thread")
    # ⚠ BOTH preloads, named separately. They currently share one statement, so
    # checking either would pass — and a later split that moved only one below
    # the thread start is exactly the regression this exists to catch.
    for fn in ("preload_native_deps", "preload_embedding_stack"):
        line = _first_line_matching(body, fn)
        assert line < thread_line, (
            f"{fn}() must run before the warmup thread starts; found it at line "
            f"{line} and Thread(...).start() at {thread_line}"
        )


def test_preload_precedes_the_event_loop_too():
    """Nothing awaited may run before the preload, or a task can race it."""
    body = _async_main_body()
    preload_line = _first_line_matching(body, "preload_native_deps")
    transport_line = _first_line_matching(body, "stdio_server")
    assert preload_line < transport_line


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", " False "])
def test_explicit_off_values_disable(monkeypatch, value):
    monkeypatch.setenv("JDOCMUNCH_PRELOAD", value)
    assert preload.preload_enabled() is False
    assert preload.preload_native_deps() == {}


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "ON"])
def test_explicit_on_values_force_it_on_every_platform(monkeypatch, value):
    monkeypatch.setenv("JDOCMUNCH_PRELOAD", value)
    for platform in ("win32", "linux", "darwin"):
        monkeypatch.setattr(preload.sys, "platform", platform)
        assert preload.preload_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "off"])
def test_explicit_off_wins_on_every_platform(monkeypatch, value):
    monkeypatch.setenv("JDOCMUNCH_PRELOAD", value)
    for platform in ("win32", "linux", "darwin"):
        monkeypatch.setattr(preload.sys, "platform", platform)
        assert preload.preload_enabled() is False


@pytest.mark.parametrize("value", ["", "flase", "no thanks", "2"])
def test_unrecognised_falls_back_to_the_platform_default(monkeypatch, value):
    """A typo must not silently reintroduce the hang on Windows...

    ...nor silently add startup cost anywhere else. Unrecognised means
    "unset", not "on".
    """
    monkeypatch.setenv("JDOCMUNCH_PRELOAD", value)
    monkeypatch.setattr(preload.sys, "platform", "win32")
    assert preload.preload_enabled() is True
    monkeypatch.setattr(preload.sys, "platform", "linux")
    assert preload.preload_enabled() is False


def test_unset_is_windows_only(monkeypatch):
    """The stack in jdoc#118 is a Windows loader wait; scope the remedy to it."""
    monkeypatch.delenv("JDOCMUNCH_PRELOAD", raising=False)
    monkeypatch.setattr(preload.sys, "platform", "win32")
    assert preload.preload_enabled() is True
    for platform in ("linux", "darwin"):
        monkeypatch.setattr(preload.sys, "platform", platform)
        assert preload.preload_enabled() is False
        assert preload.preload_native_deps() == {}


def test_absent_module_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_PRELOAD", "1")
    monkeypatch.setattr(preload, "_PRELOAD", ("jdoc_no_such_module_118",))
    report = preload.preload_native_deps()
    assert report == {"jdoc_no_such_module_118": "absent"}


def test_import_failure_is_reported_not_raised(monkeypatch):
    """numpy stays optional; a broken install must not stop the server."""
    monkeypatch.setenv("JDOCMUNCH_PRELOAD", "1")
    monkeypatch.setattr(preload, "_PRELOAD", ("jdoc_boom_118",))
    monkeypatch.setattr(
        preload.importlib.util, "find_spec", lambda name: object()
    )
    report = preload.preload_native_deps()
    assert report["jdoc_boom_118"].startswith("error: ")


def test_numpy_is_actually_imported_when_present(monkeypatch):
    pytest.importorskip("numpy")
    monkeypatch.setenv("JDOCMUNCH_PRELOAD", "1")
    report = preload.preload_native_deps()
    assert "loaded in" in report["numpy"]
    assert "numpy" in sys.modules


class _FakeProv:
    """Stands in for embeddings.provider so no real import chain is touched."""

    def __init__(self, provider="sentence-transformers", cached=True):
        self._provider = provider
        self._cached = cached
        self.recorded = []

    def get_provider_name(self):
        return self._provider

    def _st_model_name(self):
        return "BAAI/bge-base-en-v1.5"

    def _st_model_is_cached(self, _m):
        return self._cached

    def record_import_probe(self, ok, detail=""):
        self.recorded.append((ok, detail))


@pytest.fixture
def fake_prov(monkeypatch):
    import jdocmunch_mcp.embeddings.provider as real

    fake = _FakeProv()
    monkeypatch.setattr(real, "get_provider_name", fake.get_provider_name)
    monkeypatch.setattr(real, "_st_model_name", fake._st_model_name)
    monkeypatch.setattr(real, "_st_model_is_cached", fake._st_model_is_cached)
    monkeypatch.setattr(real, "record_import_probe", fake.record_import_probe)
    monkeypatch.setenv("JDOCMUNCH_PRELOAD", "1")
    return fake


def test_embedding_stack_is_skipped_when_the_provider_is_not_selected(
    monkeypatch, fake_prov
):
    fake_prov._provider = "gemini"
    report = preload.preload_embedding_stack()
    assert report["sentence_transformers"].startswith("absent")
    assert fake_prov.recorded == []


def test_embedding_stack_is_skipped_when_the_model_is_not_cached(
    monkeypatch, fake_prov
):
    """jdoc#110's gate still holds: never pay a download at startup."""
    fake_prov._cached = False
    report = preload.preload_embedding_stack()
    assert report["sentence_transformers"] == "absent: model not cached"
    assert fake_prov.recorded == []


def test_a_successful_import_is_recorded_as_the_probe_answer(monkeypatch, fake_prov):
    """The subprocess probe must not re-ask what the main thread just proved."""
    monkeypatch.setattr(preload, "_import_module", lambda n: None)
    report = preload.preload_embedding_stack()
    assert "loaded in" in report["sentence_transformers"]
    assert fake_prov.recorded == [(True, "")]


def test_a_failing_import_is_recorded_and_does_not_raise(monkeypatch, fake_prov):
    def boom(name):
        raise ImportError("cannot import name 'HybridCache' from 'transformers'")

    monkeypatch.setattr(preload, "_import_module", boom)
    report = preload.preload_embedding_stack()
    assert report["sentence_transformers"].startswith("error: ImportError")
    assert len(fake_prov.recorded) == 1
    ok, detail = fake_prov.recorded[0]
    assert ok is False
    assert "HybridCache" in detail


def test_a_non_exception_failure_is_still_survived(monkeypatch, fake_prov):
    """⚠ BaseException: a broken native dep can raise outside Exception, and a
    server that will not start is worse than the hang this prevents."""
    def boom(name):
        raise KeyboardInterrupt("simulated native abort")

    monkeypatch.setattr(preload, "_import_module", boom)
    report = preload.preload_embedding_stack()
    assert report["sentence_transformers"].startswith("error: KeyboardInterrupt")
    assert fake_prov.recorded == [(False, "KeyboardInterrupt: simulated native abort")]


def test_embedding_stack_respects_the_off_switch(monkeypatch, fake_prov):
    monkeypatch.setenv("JDOCMUNCH_PRELOAD", "0")
    assert preload.preload_embedding_stack() == {}
    assert fake_prov.recorded == []


def test_recorded_probe_result_short_circuits_the_subprocess(monkeypatch):
    """End of the contract: record_import_probe must make the probe answer
    without shelling out. Otherwise the main-thread import buys nothing."""
    import jdocmunch_mcp.embeddings.provider as prov

    monkeypatch.setattr(prov, "_import_probe_result", None, raising=False)
    monkeypatch.setattr(prov, "_import_probe_detail", "", raising=False)
    monkeypatch.setattr(
        prov.subprocess,
        "run",
        lambda *a, **k: pytest.fail("probe shelled out despite a recorded result"),
        raising=False,
    )
    prov.record_import_probe(True, "")
    assert prov._sentence_transformers_imports_cleanly() is True


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
        env={**os.environ, "JDOCMUNCH_PRELOAD": "1"},
    )
    assert out.returncode == 0, out.stderr
    n_before, on_main = out.stdout.split()
    assert n_before == "1", f"expected a single-threaded process, saw {n_before} threads"
    assert on_main == "True"
