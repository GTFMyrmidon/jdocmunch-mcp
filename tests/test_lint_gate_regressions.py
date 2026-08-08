"""Defects the new lint job would have caught, and the gate that keeps them out.

v1.124.3. This repo had no lint job until now. Adding one surfaced a latent
`NameError` in code shipped in v1.124.0.

⚠⚠ `_declared_properties` logged through a module-level `logger` that
**server.py has never defined**. The call sits inside `except Exception:` -- the
handler that exists to swallow a failure -- so any real failure of `_all_tools()`
raised `NameError` out of the handler and converted a handled error into a crash.
Ruff reports it as F821 and would have caught it on the day it shipped.

The lesson is not "add a linter". jcodemunch-mcp HAD the check, it failed on four
consecutive releases, and nobody read it. A gate is worth exactly as much as the
habit of reading it; see the release checklist, which now names both.
"""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from jdocmunch_mcp import server as S


class TestTheHandlerSurvivesItsOwnErrorPath:
    def test_a_failure_inside_the_cache_build_is_swallowed(self, monkeypatch):
        """The whole point of the `except` block. Before the fix this raised
        NameError instead of returning None."""
        monkeypatch.setattr(S, "_SCHEMA_PROPS_CACHE", None)

        def _boom():
            raise RuntimeError("catalog exploded")

        monkeypatch.setattr(S, "_all_tools", _boom)
        assert S._declared_properties("get_toc") is None  # must not raise

    def test_it_logs_rather_than_dying(self, monkeypatch, caplog):
        monkeypatch.setattr(S, "_SCHEMA_PROPS_CACHE", None)
        monkeypatch.setattr(S, "_all_tools", lambda: (_ for _ in ()).throw(RuntimeError("x")))
        with caplog.at_level(logging.DEBUG, logger="jdocmunch_mcp.server"):
            S._declared_properties("get_toc")
        assert any("schema property cache" in r.message for r in caplog.records)

    def test_the_module_can_resolve_its_logger(self):
        """`logging` must be importable at module scope, not only inside the one
        function that happened to import it locally."""
        assert hasattr(S, "logging"), "server.py cannot reach `logging`"
        assert S.logging.getLogger("x") is not None


class TestNoUndefinedNames:
    """A cheap in-suite mirror of the CI lint job.

    ⚠ Deliberately narrow: F821 only. The repo carries 65 E402 and 14 F401
    findings that reflect deliberate patterns, and a gate that fails on those
    would be switched off within a week. This asserts the ONE rule that
    represents a runtime crash.
    """

    def test_src_has_no_undefined_names(self):
        if shutil.which("ruff") is None:
            try:
                import ruff  # noqa: F401
            except ImportError:
                pytest.skip("ruff not installed; CI runs the same check")
        root = Path(__file__).resolve().parents[1]
        out = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--select", "F821",
             "--output-format=concise", "src/"],
            cwd=root, capture_output=True, text=True, timeout=300,
        )
        assert out.returncode == 0, (
            "undefined name(s) in src/ -- each is a NameError waiting for its "
            "code path:\n" + out.stdout
        )

