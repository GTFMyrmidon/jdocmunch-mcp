"""Give JSON-RPC a private stdout so nothing else can corrupt the framing.

jdoc#110. The MCP stdio transport writes framed JSON to stdout, and **any**
other write to that stream breaks a response. Until now the guarantee was
maintained by discipline: warm the embedding model before the transport starts
(jdoc#19), wrap the JSON-producing CLI commands in ``redirect_stdout``
(jdoc#65), and remember to do the same at every new site.

⚠⚠ ``contextlib.redirect_stdout`` only rebinds ``sys.stdout``. It cannot catch:

  - a C extension calling ``write(1, ...)`` directly (tqdm, tokenizers, torch),
  - a subprocess that inherited fd 1,
  - any thread, since the rebinding is process-global and unscoped.

Those are exactly the writers that show up while a model downloads, which is
why the warmup had to finish *before* the transport existed and could not be
moved off the startup path.

The fix is structural rather than disciplinary. Duplicate the real stdout,
point file descriptor 1 at stderr, and hand the duplicate to the transport.
Afterwards fd 1 *is* stderr for the entire process — Python, native code and
child processes alike — and the JSON-RPC stream is reachable only through the
handle the transport holds.

⚠ This is a safety net, not a licence to remove the existing guards. Keep them:
belt and braces cost nothing, and a stderr banner is still the right place for
human-readable output.
"""

import os
import sys
from io import TextIOWrapper
from typing import Optional, TextIO


def claim_stdout() -> tuple[Optional[TextIO], bool]:
    """Hand back a private stdout stream, and point fd 1 at stderr.

    Returns ``(stream, swapped)``. ``stream`` is a UTF-8 text handle on the
    original stdout, or None when the swap could not be performed — in which
    case the caller should let the transport use ``sys.stdout`` as before.

    ⚠⚠ Fails OPEN. Under pythonw, a harness that replaced ``sys.stderr`` with a
    non-file object, or any environment without real file descriptors, this
    returns ``(None, False)`` and changes nothing. A server that starts with
    the old hazard beats a server that will not start.
    """
    try:
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        return None, False

    try:
        # Flush first: buffered bytes belong to the real stdout, and after the
        # swap they would be delivered to stderr instead.
        sys.stdout.flush()
    except (OSError, ValueError):
        pass

    try:
        private_fd = os.dup(stdout_fd)
    except OSError:
        return None, False

    try:
        os.dup2(stderr_fd, stdout_fd)
    except OSError:
        os.close(private_fd)
        return None, False

    try:
        # buffering=0 on the binary layer: a framed message must reach the
        # client when written, not when a buffer happens to fill.
        stream = TextIOWrapper(
            os.fdopen(private_fd, "wb", buffering=0),
            encoding="utf-8",
            write_through=True,
        )
    except (OSError, ValueError):
        try:
            os.close(private_fd)
        except OSError:
            pass
        return None, False

    return stream, True
