"""jdoc#118: warmup proves the provider can import before importing it here.

On Windows, `import sentence_transformers` pulls in numpy's bundled OpenBLAS,
whose ``DllMain`` runs while the process-wide loader lock is held. Observed
deadlocked in `RtlEnterCriticalSection` under `LdrLoadDll`, indefinitely, via a
native stack taken twice 25 s apart.

⚠ It is NOT the OpenBLAS thread pool. `OPENBLAS_NUM_THREADS=1` was measured
against a reproduction that wedged 7 runs in 8 and it **still wedged**; at one
thread the pool is never spawned. See `_sentence_transformers_imports_cleanly`
for the full note -- the remedy is unaffected, the mechanism sentence was not.

⚠⚠ The reason this needs a SUBPROCESS and not a try/except is that the failure
is not confined to the caller: once a thread wedges inside `LdrLoadDll` the
loader lock is never released, so every later `LoadLibrary` in the process
blocks too. It is a kernel-mode wait, so a timeout, a thread kill and an
exception handler are all equally useless. The probe has to run somewhere we can
abandon.

⚠ The check also pays for itself without any deadlock: a provider whose import
merely RAISES is unusable, and the version pairing that produced
``ImportError: cannot import name 'HybridCache' from 'transformers'`` was not
chosen by the user.
"""

import subprocess

import pytest

from jdocmunch_mcp.embeddings import provider as prov


@pytest.fixture(autouse=True)
def _reset_probe_cache(monkeypatch):
    monkeypatch.setattr(prov, "_import_probe_result", None, raising=False)
    monkeypatch.setattr(prov, "_import_probe_detail", "", raising=False)


def _fake_run(returncode=0, stderr="", raises=None):
    def run(*a, **k):
        if raises is not None:
            raise raises
        return subprocess.CompletedProcess(a[0] if a else [], returncode, "", stderr)
    return run


@pytest.fixture()
def _installed(monkeypatch):
    """Pretend the package IS installed, so the probe reaches the subprocess."""
    monkeypatch.setattr(prov, "_sentence_transformers_available", lambda: True)


class TestAbsentPackageIsNotProbed:
    def test_an_absent_package_is_safe_to_import_and_costs_no_subprocess(self, monkeypatch):
        """⚠ The probe answers 'is importing here SAFE', not 'will it succeed'.

        An absent package raises ImportError immediately — fast, catchable, and
        already handled by every caller. Only a native loader deadlock is
        unrecoverable, and an absent package cannot produce one. Answering False
        would also make warmup decline before the uncached-model branch that
        silences download progress bars (jdoc#110).
        """
        monkeypatch.setattr(prov, "_sentence_transformers_available", lambda: False)

        def run(*a, **k):  # pragma: no cover - asserted not to run
            raise AssertionError("spawned a probe for an absent package")

        monkeypatch.setattr(subprocess, "run", run)
        assert prov._sentence_transformers_imports_cleanly() is True


@pytest.mark.usefixtures("_installed")
class TestProbe:
    def test_clean_import_reports_importable(self, monkeypatch):
        monkeypatch.setattr(subprocess, "run", _fake_run(0))
        assert prov._sentence_transformers_imports_cleanly() is True

    def test_failing_import_reports_not_importable_and_keeps_the_reason(self, monkeypatch):
        stderr = (
            "Traceback (most recent call last):\n"
            "ImportError: cannot import name 'HybridCache' from 'transformers'\n"
        )
        monkeypatch.setattr(subprocess, "run", _fake_run(1, stderr))
        assert prov._sentence_transformers_imports_cleanly() is False
        assert "HybridCache" in prov._import_probe_detail

    def test_a_hanging_import_is_bounded_not_awaited(self, monkeypatch):
        monkeypatch.setattr(
            subprocess, "run",
            _fake_run(raises=subprocess.TimeoutExpired(cmd="x", timeout=30)),
        )
        assert prov._sentence_transformers_imports_cleanly() is False
        assert "did not finish" in prov._import_probe_detail

    def test_probe_that_cannot_run_does_not_condemn_the_provider(self, monkeypatch):
        """A broken probe must not be read as a broken provider."""
        monkeypatch.setattr(subprocess, "run", _fake_run(raises=OSError("no exec")))
        assert prov._sentence_transformers_imports_cleanly() is True

    def test_result_is_cached_so_the_probe_runs_once(self, monkeypatch):
        calls = {"n": 0}

        def run(*a, **k):
            calls["n"] += 1
            return subprocess.CompletedProcess([], 0, "", "")

        monkeypatch.setattr(subprocess, "run", run)
        prov._sentence_transformers_imports_cleanly()
        prov._sentence_transformers_imports_cleanly()
        prov._sentence_transformers_imports_cleanly()
        assert calls["n"] == 1

    def test_probe_never_inherits_this_process_stdin(self, monkeypatch):
        """jdoc#110 gave JSON-RPC a private stdout; a child must not reach it."""
        seen = {}

        def run(*a, **k):
            seen.update(k)
            return subprocess.CompletedProcess([], 0, "", "")

        monkeypatch.setattr(subprocess, "run", run)
        prov._sentence_transformers_imports_cleanly()
        assert seen.get("stdin") is subprocess.DEVNULL
        assert seen.get("capture_output") is True
        assert seen.get("timeout") == prov._IMPORT_PROBE_TIMEOUT


class TestProviderDetectionIsTheRealFixSite:
    """⚠ The first in-process import was NOT in warmup.

    `get_provider_name()` reaches `_sentence_transformers_available()` on the
    auto-detect path, long before warmup or any embedding call. Guarding only
    warmup left the deadlock fully reachable — verified end-to-end: the server
    still wedged with the warmup guard in place, and stopped wedging only once
    this function stopped importing in-process.
    """

    def test_availability_check_does_not_import_in_process(self, monkeypatch):
        """⚠ Asserts the MECHANISM, not the machine.

        An earlier version of this test asserted the return value was True,
        which silently required sentence-transformers to be installed on the
        test runner. It passed locally and failed on every CI job that does not
        install it — the assumption-is-not-a-fixture trap, again.
        """
        import builtins

        real_import = builtins.__import__

        def guard(name, *a, **k):
            if name == "sentence_transformers" or name.startswith("sentence_transformers."):
                raise AssertionError("imported sentence_transformers in-process")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", guard)
        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", lambda: True)
        # Must not raise, and must answer from metadata either way.
        assert prov._sentence_transformers_available() in (True, False)

    def test_availability_uses_metadata_and_never_spawns_a_probe(self, monkeypatch):
        """⚠ Detection must stay CHEAP. Probing here would put a subprocess —
        up to the full timeout on a broken install — on the startup path."""
        called = {"n": 0}

        def probe():  # pragma: no cover - asserted not to run
            called["n"] += 1
            return True

        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", probe)
        prov._sentence_transformers_available()
        assert called["n"] == 0

    def test_provider_construction_is_gated_by_the_probe(self, monkeypatch):
        monkeypatch.setattr(prov, "get_provider_name", lambda: "sentence-transformers")
        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", lambda: False)

        def boom():  # pragma: no cover - must never run
            raise AssertionError("provider factory ran despite a failed probe")

        monkeypatch.setitem(prov._PROVIDER_FACTORIES, "sentence-transformers", boom)
        assert prov._get_provider() is None

    def test_a_working_provider_is_still_constructed(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(prov, "get_provider_name", lambda: "sentence-transformers")
        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", lambda: True)
        monkeypatch.setitem(prov._PROVIDER_FACTORIES, "sentence-transformers", lambda: sentinel)
        monkeypatch.setattr(prov, "_PROVIDER_CACHE", {})
        assert prov._get_provider() is sentinel

    def test_other_providers_are_not_gated_by_it(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(prov, "get_provider_name", lambda: "openai")
        called = {"n": 0}

        def probe():  # pragma: no cover - asserted not to run
            called["n"] += 1
            return False

        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", probe)
        monkeypatch.setitem(prov._PROVIDER_FACTORIES, "openai", lambda: sentinel)
        monkeypatch.setattr(prov, "_PROVIDER_CACHE", {})
        assert prov._get_provider() is sentinel
        assert called["n"] == 0


class TestWarmupHonoursTheProbe:
    def test_warmup_declines_when_the_provider_cannot_import(self, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_EMBED_WARMUP", raising=False)
        monkeypatch.setattr(prov, "get_provider_name", lambda: "sentence-transformers")
        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", lambda: False)

        def boom(*a, **k):  # pragma: no cover - must never run
            raise AssertionError("embed_query was called despite a failed probe")

        monkeypatch.setattr(prov, "embed_query", boom)
        assert prov.warmup() == ""

    def test_warmup_proceeds_when_the_probe_passes(self, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_EMBED_WARMUP", raising=False)
        monkeypatch.setattr(prov, "get_provider_name", lambda: "sentence-transformers")
        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", lambda: True)
        monkeypatch.setattr(prov, "_st_model_is_cached", lambda *_a, **_k: True)
        monkeypatch.setattr(prov, "_st_model_name", lambda: "some-model")
        monkeypatch.setattr(prov, "embed_query", lambda *_a, **_k: [0.0])
        assert prov.warmup() == "sentence-transformers"

    def test_probe_is_not_run_for_non_sentence_transformers_providers(self, monkeypatch):
        """A network provider is not warmed, so it must not pay for a probe."""
        monkeypatch.delenv("JDOCMUNCH_EMBED_WARMUP", raising=False)
        monkeypatch.setattr(prov, "get_provider_name", lambda: "openai")
        called = {"n": 0}

        def probe():  # pragma: no cover - asserted not to run
            called["n"] += 1
            return True

        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", probe)
        assert prov.warmup() == ""
        assert called["n"] == 0

    def test_env_opt_out_still_short_circuits_before_the_probe(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_EMBED_WARMUP", "0")
        called = {"n": 0}

        def probe():  # pragma: no cover - asserted not to run
            called["n"] += 1
            return True

        monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", probe)
        assert prov.warmup() == ""
        assert called["n"] == 0
