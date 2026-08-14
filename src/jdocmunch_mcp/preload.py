"""Load the deadlock-prone native extensions on the main thread, while it is alone.

jdoc#118. ``import numpy`` inside a running server wedges **indefinitely** on
Windows. The native stack, identical across dumps minutes apart, is::

    ZwWaitForAlertByThreadId (ntdll)
    RtlSleepConditionVariableCS
    RtlEnterCriticalSection
    <libopenblas64_...>            <- DllMain
    LdrLoadDll / LoadLibraryExW

That is the Windows **loader lock**, not our code and not the GIL. numpy's
``_multiarray_umath`` pulls in the bundled OpenBLAS, whose ``DllMain`` runs
while the loader lock is held; if any other thread is doing loader work at that
moment -- another extension import, or simply ``threading.Thread.start()``,
whose new thread must take the loader lock to run ``DLL_THREAD_ATTACH`` -- the
two can wait on each other forever.

⚠⚠ **The wedge is a race, and losing it costs the whole process.** Measured 7 of
8 runs on a `tools/call` issued right after `initialize`; the one that got
through had simply finished the import first. There is no timeout and no error:
the loader waits, so the event loop keeps polling and every subsequent call
hangs behind it. An idle server never wedges -- with nothing else running there
is no second party to deadlock against.

⚠⚠ **The subprocess probe does NOT close this, and believing it did was a
mistake worth recording.** ``provider._sentence_transformers_imports_cleanly``
answers "would this import raise?", and the deadlock is not a raise. A probe
subprocess is single-threaded, so on a healthy install it returns True in a
few seconds -- and ``warmup`` then runs ``embed_query``, which does
``from sentence_transformers import SentenceTransformer`` (provider.py:442) on
the **warmup thread**, concurrently with the live server. That is the wedge
condition exactly. The probe rescued the machine it was written on only because
sentence-transformers genuinely *raises* there; repair that version skew and
the hang returns.

⚠⚠ **Preloading numpy alone does not close it either.** The chain loads scipy,
sklearn and torch too, and the issue's FIRST native dump is wedged in
``scipy/sparse/linalg/_svdp.py`` under ``sklearn`` under
``sentence_transformers/util/similarity.py`` -- a different DLL from the numpy
one in the second dump. Any of them can be the unlucky loader.

So there are two jobs here, and they are not the same job:

1. :func:`preload_native_deps` -- numpy, for the paths that reach it WITHOUT
   sentence-transformers. Retraction 2 on the issue is the evidence those
   exist: with the warmup thread disabled outright the wedge reappeared under
   ``related_persist._semantic_edges_matrix``, a sidecar builder nobody had
   connected to embeddings.
2. :func:`preload_embedding_stack` -- the whole sentence-transformers import,
   for the warmup path. ⚠ **This adds no work.** warmup already pays that
   import; it just pays it on a thread where it can deadlock. Moving it here
   serialises it ahead of the transport instead of adding to the total.

⚠ **This is not a numpy bug we can wait out.** Standalone, on the same
interpreter, ``import numpy`` is ~0.11 s. It is the *concurrency* that is
lethal, so the remedy is to remove the concurrency rather than to bound, retry
or supervise the import.

⚠ **Not fixed by `OPENBLAS_NUM_THREADS=1`** -- tested, still wedges. The thread
pool is not what blocks; the loader is.

So: import it here, on the main thread, before the event loop exists and before
any worker or warmup thread is started. At that point the process is
single-threaded by construction and there is no second party to deadlock with.
Everything downstream -- the warmup thread, ``sentence_transformers``, the
related-graph sidecars -- then finds ``numpy`` already in ``sys.modules`` and
never touches the loader for it again.

⚠ **Ordering is the whole contract.** Called after the first thread starts this
is not merely useless, it is the bug. ``main()`` calls it before
``threading.Thread(...).start()``; keep it that way.
``test_preload.py`` pins the order against ``server.py``'s source, because the
property that matters is not "preload exists" but "preload runs first", and a
later edit that moves the thread start up would leave every other test green.

⚠⚠ **VERIFICATION STATUS: mechanism measured, remedy NOT demonstrated.** The
stack above is real and was captured twice. This module is derived from it by
reasoning. What is missing is an A/B: the wedge reproduced 7 of 8 times, then
**stopped reproducing on that machine in BOTH arms** -- 16 runs with the
preload and 16 without, none wedged, including 6-way concurrent servers. The
most likely reason is that ~30 server starts left the ~35 MB OpenBLAS image in
the OS file cache, which narrows the DllMain window the race needs. So the
16/16 pass here is **not** evidence: the control passed too. Do not write this
up as fixed on the strength of a green run; it needs a cold machine, or a
reporter who can still reproduce, to say anything at all.

⚠ numpy stays an **optional** runtime dependency. Absent, this is a no-op and
the lexical paths are unaffected -- so the cost is paid only by installs that
were going to pay it anyway, and it is ~0.11 s once per server start, not per
call. ``JDOCMUNCH_PRELOAD=0`` opts out of BOTH preloads for anyone who would
rather keep the milliseconds and take the race.

⚠⚠ **The embedding preload contradicts jdoc#110, so it is OPT-IN and the
default keeps #110 intact.** #110 moved the provider import to a background
thread to keep the handshake fast, and has a named test asserting it. Both
contracts cannot hold in one process: the import is either on the main thread
(slow handshake) or on a background thread (possible indefinite hang).

⚠ **The measurement that settled it.** Handshake against a ~1.0 s baseline grew
to 6.5 s and 11.4 s in two runs -- and then a cold run of the same import took
**73.77 s**, against 5.71 / 5.51 / 5.53 s warm. torch is gigabytes of DLLs and
the first import after a boot pays for all of them. The slow end of a 13x
spread lands on the FIRST server start after a reboot, i.e. past a 30 s client
connect timeout, i.e. jdoc#110's original outage: the server never registers.

Defaulting this on would swap a probabilistic hang for a fairly reliable
cold-start failure. Neither is acceptable to impose, so the switch belongs to
the person who knows which one they are suffering:
``JDOCMUNCH_PRELOAD_EMBEDDINGS=1``.

⚠ torch/scipy/sklearn are NOT new cost -- they are declared dependencies of
sentence-transformers and warmup has always loaded them (verified: all five of
numpy/scipy/sklearn/transformers/torch land in ``sys.modules`` even on the
failing import). Nothing here adds work; it only moves WHERE the work happens.

⚠ **One-off, per process, not per request.** It runs once in ``run_server``
before the transport starts. Everything after the handshake is unaffected, and
the first semantic query is warm rather than paying the load mid-call -- which
is where the wedge used to land. On a host whose MCP client leaks stdio servers
it is once per leaked spawn, which is a reason to fix the leak.

⚠⚠ **Windows only by default, and the narrowness is deliberate.** The stack
above is a Windows loader-lock wait; ELF and Mach-O loaders do not serialise
initialisers behind one process-wide critical section the same way, and the
hang has never been observed off Windows. Given the remedy is UNVERIFIED (see
above), it should not change startup on a platform where nobody has seen the
defect -- an unproven fix applied everywhere is a wider blast radius than the
bug it chases. ``JDOCMUNCH_PRELOAD=1`` forces it on anyway, which is the
switch to reach for if this ever gets reported on Linux or macOS.
"""

import importlib
import importlib.util
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)

#: Modules loaded eagerly on the main thread. Each entry is here because its
#: import performs native library loading that has been observed to deadlock
#: when it races other loader activity -- add to this list only with a stack.
_PRELOAD = ("numpy",)

#: Indirection so tests can substitute the import without touching
#: ``builtins.__import__`` -- patching that globally breaks pytest's own
#: internals mid-run, which is how the first version of the tests failed.
_import_module = importlib.import_module

_DISABLED = ("0", "false", "no", "off")
_FORCED = ("1", "true", "yes", "on")


def preload_enabled() -> bool:
    """Whether the eager main-thread preload should run.

    Off by an explicit off-switch anywhere; on by default on Windows; on
    elsewhere only when explicitly forced.

    ⚠ The unset case is the one that matters and it is asymmetric on purpose:
    a typo must not silently reintroduce the hang on the platform that has it,
    and must not silently add startup cost on the platforms that do not.
    """
    setting = os.environ.get("JDOCMUNCH_PRELOAD", "").strip().lower()
    if setting in _DISABLED:
        return False
    if setting in _FORCED:
        return True
    return sys.platform == "win32"


def embedding_preload_enabled() -> bool:
    """Whether to import the sentence-transformers chain on the main thread.

    ⚠⚠ **OPT-IN on every platform, including Windows, and the asymmetry with
    :func:`preload_enabled` is the whole point.** numpy costs ~0.11 s, so
    defaulting it on where the bug lives is free. This chain costs SECONDS, and
    measured cold on Windows it cost **73.77 s** -- torch alone is gigabytes of
    DLLs, and the first import after a boot pays for all of them. Three warm
    runs on the same box took 5.5 s. A 13x spread whose slow end lands on the
    first server start after a reboot is exactly jdoc#110's outage: past a
    client's 30 s connect timeout, the server never registers at all.

    So defaulting this on would swap a PROBABILISTIC hang for a fairly RELIABLE
    cold-start failure, which is not a trade worth making on the user's behalf.
    Anyone actually wedged sets ``JDOCMUNCH_PRELOAD_EMBEDDINGS=1`` and pays a
    slow handshake instead of an unbounded one.

    ⚠ ``JDOCMUNCH_PRELOAD=0`` is the master off-switch and wins over this.
    """
    if os.environ.get("JDOCMUNCH_PRELOAD", "").strip().lower() in _DISABLED:
        return False
    return (
        os.environ.get("JDOCMUNCH_PRELOAD_EMBEDDINGS", "").strip().lower() in _FORCED
    )


def preload_native_deps() -> dict:
    """Import the deadlock-prone extensions now, on the calling thread.

    Returns a report ``{module: "loaded in 0.11s" | "absent" | "error: ..."}``,
    which the caller may log. Never raises: a preload that fails must not stop
    a server from starting, it just leaves the original race in place.
    """
    report: dict = {}
    if not preload_enabled():
        return report

    for name in _PRELOAD:
        # find_spec avoids paying an import error's cost for an absent optional
        # dependency, which is the common case on a lexical-only install.
        try:
            if importlib.util.find_spec(name) is None:
                report[name] = "absent"
                continue
        except (ImportError, ValueError):
            report[name] = "absent"
            continue

        started = time.time()
        try:
            _import_module(name)
        except Exception as exc:  # pragma: no cover - defensive
            report[name] = f"error: {type(exc).__name__}: {exc}"
            logger.warning("preload of %s failed: %s", name, exc, exc_info=True)
            continue
        report[name] = f"loaded in {time.time() - started:.2f}s"

    return report


def preload_embedding_stack() -> dict:
    """Import sentence-transformers here, on the main thread, if warmup will need it.

    Returns ``{"sentence_transformers": <outcome>}``, or ``{}`` when disabled.

    ⚠ **Gated to exactly what warmup would have loaded anyway** -- the
    sentence-transformers provider, selected, with its model already in the
    local HuggingFace cache. Absent any of those, warmup declines too (jdoc#110
    for the cache gate), so importing here would be pure added startup cost for
    a feature the session may never touch.

    ⚠⚠ **No probe subprocess in front of this, deliberately.** The probe exists
    because an in-process import can deadlock; that reasoning does not apply on
    a single-threaded main thread, which is the one place the import is safe.
    Running it here anyway would make a broken install pay the failing import
    twice. Instead the outcome is handed to
    :func:`provider.record_import_probe`, so the later gate reads a fact
    observed in THIS process rather than shelling out to infer it.

    ⚠ The startup cost is real and lands on `initialize`: the import is seconds,
    not milliseconds. That is the trade -- a bounded, one-off, disclosed delay
    for Windows sentence-transformers users, against a hang with no timeout
    that takes every later tool call with it.
    """
    if not embedding_preload_enabled():
        return {}

    # Imported here, not at module scope: preload runs before anything else and
    # must not drag the embedding package in for installs that never use it.
    from jdocmunch_mcp.embeddings import provider as prov

    try:
        # ⚠⚠ jdoc#118 phase 1: the worker exists so this process NEVER imports
        # the stack. Preloading it here anyway would import it into the very
        # process the worker is keeping clean — paying the slow handshake the
        # opt-in exists to trade for, and getting nothing back for it.
        from jdocmunch_mcp.embeddings import worker as _worker
        if _worker.worker_enabled():
            return {"sentence_transformers": "absent: embedding worker owns the import"}
        if prov.get_provider_name() != "sentence-transformers":
            return {"sentence_transformers": "absent: provider not selected"}
        if not prov._st_model_is_cached(prov._st_model_name()):
            return {"sentence_transformers": "absent: model not cached"}
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("embedding preload gate failed: %s", exc, exc_info=True)
        return {"sentence_transformers": f"absent: gate error: {type(exc).__name__}"}

    started = time.time()
    try:
        _import_module("sentence_transformers")
    except BaseException as exc:
        # ⚠ BaseException, not Exception. A broken native dependency can raise
        # things Exception does not cover, and a server that fails to start
        # because a preload was strict is worse than the hang it prevents.
        detail = f"{type(exc).__name__}: {exc}"[:300]
        prov.record_import_probe(False, detail)
        logger.warning(
            "sentence-transformers could not be imported (%s); embeddings are "
            "unavailable. Lexical search is unaffected.", detail,
        )
        return {"sentence_transformers": f"error: {detail}"}

    prov.record_import_probe(True, "")
    return {"sentence_transformers": f"loaded in {time.time() - started:.2f}s"}
