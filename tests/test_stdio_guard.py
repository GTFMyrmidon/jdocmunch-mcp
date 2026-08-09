"""jdoc#110 — JSON-RPC owns a private stdout; everything else lands on stderr.

Until now "nothing writes to stdout" was maintained by discipline: warm the
model before the transport starts (jdoc#19), wrap JSON-producing CLI commands
in ``redirect_stdout`` (jdoc#65), remember to do it at every new site. That is
why provider init sat on the startup path at all, costing ~7.6 s per launch and
turning an uncached model into a connect-timeout outage.

⚠⚠ ``redirect_stdout`` only rebinds ``sys.stdout``. It cannot catch a C
extension calling ``write(1, ...)``, a subprocess that inherited fd 1, or
another thread — and those are exactly the writers a model download produces.
Duplicating stdout and pointing fd 1 at stderr makes the guarantee structural,
so warming can move off the critical path.

The subprocess tests here are the ones that matter: they assert against real
file descriptors, which is the whole point. An in-process test of a
descriptor-level swap would be testing the mock.
"""

import json
import os
import subprocess
import sys
import textwrap

import pytest


def _run(body: str, env_extra=None) -> tuple[str, str]:
    """Execute `body` in a child process; return its (stdout, stderr)."""
    src = str((__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
    env = dict(os.environ, PYTHONPATH=src, PYTHONIOENCODING="utf-8")
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(body)],
        # ⚠ encoding is explicit: `text=True` alone decodes with the parent's
        # locale, which is cp1252 on Windows, so UTF-8 output would arrive
        # mojibaked and look like a product bug.
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=120,
    )
    return proc.stdout, proc.stderr


def test_every_kind_of_write_is_diverted_to_stderr():
    """⚠⚠ The `os.write(1, ...)` case is the one `redirect_stdout` cannot catch.

    That is a C extension writing to the descriptor directly — tqdm, tokenizers,
    torch — which is precisely what appears while a model downloads.
    """
    out, err = _run("""
        import os, sys
        from jdocmunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert swapped
        print("PYTHON PRINT")
        sys.stdout.write("SYS STDOUT WRITE\\n"); sys.stdout.flush()
        os.write(1, b"NATIVE FD WRITE\\n")
        stream.write("JSONRPC\\n"); stream.flush()
    """)
    assert out.strip() == "JSONRPC"
    for expected in ("PYTHON PRINT", "SYS STDOUT WRITE", "NATIVE FD WRITE"):
        assert expected in err


def test_a_child_process_cannot_reach_the_private_stream():
    """A subprocess inherits fd 1, so the swap has to cover it too."""
    out, err = _run("""
        import subprocess, sys
        from jdocmunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert swapped
        subprocess.run([sys.executable, "-c", "print('CHILD ON STDOUT')"])
        stream.write("JSONRPC\\n"); stream.flush()
    """)
    assert out.strip() == "JSONRPC"
    assert "CHILD ON STDOUT" in err


def test_a_background_thread_cannot_reach_it_either():
    """⚠ The warmup now runs in a thread. `redirect_stdout` is process-global
    and unscoped, so it could never have made this safe."""
    out, err = _run("""
        import threading
        from jdocmunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert swapped
        t = threading.Thread(target=lambda: print("THREAD OUTPUT"))
        t.start(); t.join()
        stream.write("JSONRPC\\n"); stream.flush()
    """)
    assert out.strip() == "JSONRPC"
    assert "THREAD OUTPUT" in err


def test_buffered_output_is_flushed_before_the_swap():
    """⚠ Bytes buffered on stdout belong to stdout. Swapping without flushing
    first delivers them to stderr, silently moving output the caller already
    committed to."""
    out, _ = _run("""
        import sys
        sys.stdout.write("EARLY")           # buffered, not yet written
        from jdocmunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert swapped
        stream.write("\\nJSONRPC\\n"); stream.flush()
    """)
    assert out.startswith("EARLY")
    assert "JSONRPC" in out


def test_the_private_stream_is_unbuffered():
    """A framed message must reach the client when written, not when a buffer
    fills — the client is waiting on it before it will send anything else."""
    out, _ = _run("""
        from jdocmunch_mcp.stdio_guard import claim_stdout
        stream, _ = claim_stdout()
        stream.write("JSONRPC\\n")     # deliberately no flush
        import os; os._exit(0)         # hard exit: no interpreter cleanup
    """)
    assert out.strip() == "JSONRPC"


def test_it_fails_open_when_stderr_has_no_descriptor():
    """⚠⚠ pythonw, or a harness that replaced sys.stderr with a buffer. A
    server that starts with the old hazard beats one that will not start."""
    out, _ = _run("""
        import io, sys
        sys.stderr = io.StringIO()
        from jdocmunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert stream is None and swapped is False
        print("STILL RUNNING")
    """)
    assert "STILL RUNNING" in out


def test_it_fails_open_when_stdout_has_no_descriptor():
    out, _ = _run("""
        import io, sys
        sys.stdout = io.StringIO()
        from jdocmunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
        assert stream is None and swapped is False
        sys.stderr.write("FAILED OPEN\\n")
    """)
    assert out == ""


def test_unicode_survives_the_private_stream():
    """The transport wraps stdout as UTF-8; the replacement must match."""
    out, _ = _run("""
        from jdocmunch_mcp.stdio_guard import claim_stdout
        stream, _ = claim_stdout()
        stream.write('{"t":"caf\\u00e9 \\u2014 \\u65e5\\u672c\\u8a9e"}\\n'); stream.flush()
    """)
    assert json.loads(out)["t"] == "café — 日本語"


# ---------------------------------------------------------------------------
# End to end: the handshake
# ---------------------------------------------------------------------------

def _handshake(provider: str) -> tuple[float, dict, str]:
    import time
    src = str((__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
    env = dict(os.environ, PYTHONPATH=src, JDOCMUNCH_EMBEDDING_PROVIDER=provider,
               PYTHONIOENCODING="utf-8")
    req = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "probe", "version": "1"}},
    }) + "\n"
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        [sys.executable, "-m", "jdocmunch_mcp", "serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=env,
    )
    try:
        proc.stdin.write(req)
        proc.stdin.flush()
        line = proc.stdout.readline()
        elapsed = time.perf_counter() - t0
    finally:
        proc.kill()
        proc.wait(timeout=30)
    return elapsed, json.loads(line), line


def test_the_handshake_answers_with_clean_json():
    _, payload, raw = _handshake("none")
    assert payload["id"] == 1
    assert raw.count("\n") == 1, "more than one line reached stdout"


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("sentence_transformers"),
    reason="sentence-transformers not installed",
)
def test_the_embedding_provider_no_longer_delays_the_handshake():
    """jdoc#110's measurement, as a test.

    ⚠ Asserts the DELTA between providers in this same tree, not an absolute
    time — an absolute bound would be a runner-speed assertion, which is the
    mistake jdoc#114 was about. Before this change the provider added ~7.0 s
    here (measured 6062 ms -> 13109 ms on v1.128.0); the reporter measured
    ~7.6 s on his own machine.
    """
    baseline, _, _ = _handshake("none")
    with_provider, payload, _ = _handshake("sentence-transformers")
    assert payload["id"] == 1
    assert with_provider < baseline + 4.0, (
        f"provider init added {with_provider - baseline:.1f}s to the handshake; "
        "it should now be warming in the background"
    )
