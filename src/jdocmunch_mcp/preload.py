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

⚠⚠ **This is HALF of the jdoc#118 remedy and the smaller half.** The other is
``provider._sentence_transformers_imports_cleanly``, which moves the risky
import out of this process entirely, into a subprocess that can be abandoned.
That closes the warmup trigger. This closes what is left: **any other path that
reaches numpy first**. Retraction 2 on the issue is the evidence that such
paths exist -- with the warmup thread disabled outright the wedge simply
reappeared under ``related_persist._semantic_edges_matrix``, a sidecar builder
nobody had connected to embeddings. A probe in front of one caller cannot
protect callers it does not sit in front of.

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
call. ``JDOCMUNCH_PRELOAD_NUMPY=0`` opts out for anyone who would rather keep
those milliseconds and take the race.

⚠⚠ **Windows only by default, and the narrowness is deliberate.** The stack
above is a Windows loader-lock wait; ELF and Mach-O loaders do not serialise
initialisers behind one process-wide critical section the same way, and the
hang has never been observed off Windows. Given the remedy is UNVERIFIED (see
above), it should not change startup on a platform where nobody has seen the
defect -- an unproven fix applied everywhere is a wider blast radius than the
bug it chases. ``JDOCMUNCH_PRELOAD_NUMPY=1`` forces it on anyway, which is the
switch to reach for if this ever gets reported on Linux or macOS.
"""

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
    setting = os.environ.get("JDOCMUNCH_PRELOAD_NUMPY", "").strip().lower()
    if setting in _DISABLED:
        return False
    if setting in _FORCED:
        return True
    return sys.platform == "win32"


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
            __import__(name)
        except Exception as exc:  # pragma: no cover - defensive
            report[name] = f"error: {type(exc).__name__}: {exc}"
            logger.warning("preload of %s failed: %s", name, exc, exc_info=True)
            continue
        report[name] = f"loaded in {time.time() - started:.2f}s"

    return report
