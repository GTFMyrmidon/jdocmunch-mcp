"""Run the sentence-transformers embedder in a child process (jdoc#118).

⚠⚠ **This module exists to resolve a collision, not to be faster.** jdoc#110
requires that the sentence-transformers import must not delay the MCP
handshake; jdoc#118 requires that it must not race another thread's native
loader activity. In one process those cannot both hold — the import is either
on the main thread (a cold ``torch`` load measured **73.77 s**, past a client's
30 s connect timeout) or on a background thread (Windows loader lock, observed
wedged indefinitely by a native stack taken twice 25 s apart). v1.132.0 shipped
a switch between the two outages because there was no third option *in one
process*.

A child process is the third option. The import runs concurrently with the live
server **and** alone in its own loader, so backgrounding becomes safe again.

What crosses the boundary is deliberately one method::

    embed_texts(texts, task_type) -> list[list[float]]

Everything else — provider detection, the HF-cache probe, cache keys, the
sidecar, identity headers, rotation detection — needs no import and stays in
the server. ⚠ **numpy stays in the server too**: ``doc_store`` and
``related_persist`` use it to *score*, and shipping a section matrix down a
pipe per query would be absurd. Those two sites remain covered by
:mod:`jdocmunch_mcp.preload`. After this, the server process loads numpy and
nothing else native; ``torch``/``transformers``/``scipy``/``sklearn`` never
enter it at all.

⚠ **Phase 1 is opt-in and changes no default.** Set ``JDOCMUNCH_EMBED_WORKER=1``.

Protocol — one JSON object per line, in both directions::

    -> {"op": "ready"}
    <- {"op": "ready", "ok": true, "dim": 384, "stdout_private": true}
    -> {"op": "embed", "id": 7, "texts": [...], "task": "retrieval_document"}
    <- {"id": 7, "ok": true, "dim": 384, "vecs": "<base64 little-endian float32>"}
    -> {"op": "shutdown"}

⚠ Vectors are base64 float32, not JSON floats. A 5,300-section corpus at 384
dims is ~30 MB as JSON text and ~8 MB as bytes, and the child was already
paying a ``.tolist()`` conversion that this replaces rather than adds.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import queue
import subprocess
import sys
import threading
from array import array
from typing import Optional

logger = logging.getLogger(__name__)

_ENABLED = ("1", "true", "yes", "on")

#: Texts per request. Bounds both the child's peak memory and, more
#: importantly, the per-request timeout: ``embed_sections`` hands over every
#: cache miss in ONE call, so an unchunked request on a full corpus has no
#: timeout that is both generous enough to succeed and tight enough to mean
#: anything.
CHUNK_SIZE = 256

#: Seconds to wait for the child to finish importing and load the model. Sized
#: off the measured cold-import figure in jdoc#118 (73.77 s) with headroom: the
#: whole point is that this wait is BOUNDED, not that it is short.
READY_TIMEOUT_DEFAULT = 300.0

#: Seconds to wait for one chunk of ``CHUNK_SIZE`` texts.
REQUEST_TIMEOUT_DEFAULT = 120.0


class EmbedWorkerError(RuntimeError):
    """The embedding worker could not answer.

    ⚠ Raised rather than returning empty vectors, deliberately. ``embed_sections``
    reads an exception as ``embed_failed`` and preserves the existing sidecar;
    a list of empty vectors reads as "this corpus legitimately has none", which
    is the jdoc#107 / jdoc#109 data-loss shape.
    """


def worker_enabled() -> bool:
    """Whether sentence-transformers should run out of process.

    Off unless explicitly on. Phase 1 changes no default: the in-process
    provider, the jdoc#118 probe and the jdoc#132 preload switches all behave
    exactly as they did until someone sets this.
    """
    return os.environ.get("JDOCMUNCH_EMBED_WORKER", "").strip().lower() in _ENABLED


def _timeout(var: str, default: float) -> float:
    """Read a positive float from ``var``, falling back to ``default``.

    A garbage value falls back rather than raising: this is read on the embed
    path, and failing an index over a typo'd env var is worse than using the
    documented default.
    """
    raw = os.environ.get(var, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def ready_timeout() -> float:
    return _timeout("JDOCMUNCH_EMBED_WORKER_READY_TIMEOUT", READY_TIMEOUT_DEFAULT)


def request_timeout() -> float:
    return _timeout("JDOCMUNCH_EMBED_WORKER_TIMEOUT", REQUEST_TIMEOUT_DEFAULT)


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------

def encode_vectors(rows: list) -> tuple[str, int]:
    """Pack ``rows`` (equal-length float lists) into base64 little-endian float32."""
    if not rows:
        return "", 0
    dim = len(rows[0])
    flat = array("f")
    for row in rows:
        if len(row) != dim:
            raise ValueError(f"ragged embedding rows: {len(row)} != {dim}")
        flat.extend(row)
    if sys.byteorder != "little":
        flat.byteswap()
    return base64.b64encode(flat.tobytes()).decode("ascii"), dim


def decode_vectors(payload: str, dim: int) -> list:
    """Unpack :func:`encode_vectors` output back into a list of float lists."""
    if not payload or dim <= 0:
        return []
    flat = array("f")
    flat.frombytes(base64.b64decode(payload))
    if sys.byteorder != "little":
        flat.byteswap()
    values = flat.tolist()
    return [values[i:i + dim] for i in range(0, len(values), dim)]


# ---------------------------------------------------------------------------
# Parent side
# ---------------------------------------------------------------------------

class WorkerProvider:
    """Parent-side stand-in for ``_SentenceTransformersProvider``.

    Interface-compatible with the in-process providers: ``embed_texts`` is the
    only method the rest of the codebase calls.

    ⚠ Construction **spawns but does not wait**. The whole gain would be lost
    if building the provider blocked on the child's import — that is jdoc#110's
    slow handshake wearing a different hat. The wait happens in ``embed_texts``,
    bounded, on whatever thread actually needs a vector.
    """

    #: One respawn, then the provider is done for this session. A child that
    #: dies twice is a broken install, and an unbounded respawn loop would turn
    #: that into a fork bomb on the embed path.
    MAX_SPAWNS = 2

    def __init__(self, model_name: str, *, command: Optional[list] = None, spawn: bool = True):
        self._model_name = model_name
        self._command = command
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._replies: "queue.Queue" = queue.Queue()
        self._ready: Optional[bool] = None
        self._error = ""
        self._dim: Optional[int] = None
        self._next_id = 0
        self._spawns = 0
        self._permanently_failed = False
        if spawn:
            try:
                self._spawn()
            except Exception as exc:  # pragma: no cover - defensive
                self._permanently_failed = True
                self._error = f"{type(exc).__name__}: {exc}"[:300]
                logger.warning("embedding worker could not be started: %s", self._error)

    # -- lifecycle ---------------------------------------------------------

    def _build_command(self) -> list:
        if self._command is not None:
            return list(self._command)
        return [
            sys.executable,
            "-m", "jdocmunch_mcp.embeddings.worker",
            "--model", self._model_name,
        ]

    def _spawn(self) -> None:
        env = dict(os.environ)
        # Only set what is unset: a user who configured these owns them. The
        # child's stdout carries the protocol, so a progress bar written there
        # would be a parse error rather than cosmetic noise — the child also
        # swaps fd 1 to stderr for exactly this reason, and this is the cheap
        # belt to that braces.
        env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
        env.setdefault("TQDM_DISABLE", "1")

        kwargs: dict = {}
        if sys.platform == "win32":
            # No console flash when the server itself runs windowless.
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        self._spawns += 1
        # ⚠ Orphan prevention rides on the stdin pipe rather than on a job
        # object: when this process dies, however it dies, the write end closes,
        # the child's `for raw in sys.stdin.buffer` sees EOF and it exits. An
        # orphaned torch process is a support ticket, so the mechanism should be
        # the one that survives a hard kill of the parent.
        #
        # ⚠ It is not instant, and the comment should not pretend otherwise: the
        # child notices at its next read, so one killed mid-import lingers until
        # that import returns. It cannot linger forever only because the child's
        # import is the single-threaded one that does not wedge — which is the
        # premise of this whole module, so if that premise is ever wrong this is
        # one of the places it shows up.
        #
        # ⚠ stderr is INHERITED on purpose: the child's chatter belongs in the
        # server's log stream. Capturing it into a pipe nobody drains would
        # deadlock the child on a full buffer, which is a fine way to
        # reintroduce a hang while fixing one.
        self._proc = subprocess.Popen(
            self._build_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=env,
            **kwargs,
        )
        self._replies = queue.Queue()
        self._ready = None
        self._dim = None
        reader = threading.Thread(
            target=self._read_replies,
            args=(self._proc, self._replies),
            name="jdocmunch-embed-worker-reader",
            daemon=True,
        )
        reader.start()
        self._send({"op": "ready", "model": self._model_name})

    @staticmethod
    def _read_replies(proc: subprocess.Popen, replies: "queue.Queue") -> None:
        """Drain the child's stdout onto a queue; enqueue None at EOF.

        A reader thread rather than a poll loop because ``select`` does not
        work on pipes on Windows, which is the platform this whole issue is
        about.
        """
        try:
            stream = proc.stdout
            if stream is None:  # pragma: no cover - defensive
                return
            for raw in stream:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    replies.put(json.loads(line))
                except ValueError:
                    logger.debug("embedding worker sent an unparseable line: %.200s", line)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("embedding worker reader stopped: %s", exc)
        finally:
            replies.put(None)

    def _send(self, message: dict) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise EmbedWorkerError("embedding worker is not running")
        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            proc.stdin.write(payload)
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise EmbedWorkerError(f"embedding worker stdin is closed: {exc}") from exc

    def _await(self, predicate, timeout: float, what: str) -> dict:
        """Wait for a reply satisfying ``predicate``; kill the child on timeout.

        Replies that do not match are dropped rather than requeued: requests are
        serialised under ``self._lock``, so a non-matching reply is a stale
        answer to a request that already timed out and nothing will ever want it.
        """
        import time as _time

        deadline = _time.monotonic() + timeout
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                self._kill(f"{what} timed out after {timeout:.0f}s")
                raise EmbedWorkerError(
                    f"embedding worker {what} timed out after {timeout:.0f}s "
                    "(the worker was killed; lexical search is unaffected)"
                )
            try:
                reply = self._replies.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if reply is None:
                self._kill(f"the worker exited during {what}")
                raise EmbedWorkerError(f"embedding worker exited during {what}")
            if predicate(reply):
                return reply

    def _kill(self, reason: str) -> None:
        """Terminate the child. ⚠ This is the property a thread cannot offer.

        A wedged thread inside ``LdrLoadDll`` is a kernel-mode wait: a timeout,
        a thread kill and a try/except are all equally useless against it. A
        wedged *process* is killable, and that is what turns this design from a
        relocation of jdoc#118 into a fix for it.
        """
        proc, self._proc = self._proc, None
        self._ready = False
        self._error = reason
        if proc is None:
            return
        logger.warning("killing the embedding worker: %s", reason)
        for step in (proc.terminate, proc.kill):
            try:
                step()
                proc.wait(timeout=5)
                return
            except Exception:
                continue

    def close(self) -> None:
        """Ask the child to exit, then make sure it did."""
        with self._lock:
            proc = self._proc
            if proc is None:
                return
            try:
                self._send({"op": "shutdown"})
                proc.wait(timeout=5)
                self._proc = None
                return
            except Exception:
                pass
            self._kill("shutdown requested")

    def _ensure_ready(self) -> None:
        if self._permanently_failed:
            raise EmbedWorkerError(self._error or "embedding worker is unavailable")
        if self._ready:
            if self._proc is not None and self._proc.poll() is None:
                return
            # Was ready, has since died — respawn once and re-handshake.
            # ⚠ Drop the dead handle here. Leaving it in place sends the next
            # handshake down a closed pipe and burns one of MAX_SPAWNS on a
            # failure we already knew about.
            self._proc = None
            self._ready = None
        if self._proc is None:
            if self._spawns >= self.MAX_SPAWNS:
                self._permanently_failed = True
                raise EmbedWorkerError(
                    f"embedding worker died {self._spawns} times "
                    f"({self._error or 'no detail'}); not restarting it again"
                )
            self._spawn()
        if self._ready is None:
            reply = self._await(
                lambda m: m.get("op") == "ready", ready_timeout(), "startup"
            )
            if not reply.get("ok"):
                # ⚠ An import that RAISES is a broken install, not a race.
                # Respawning would fail identically and cost another cold
                # import, so this is terminal for the session.
                self._permanently_failed = True
                self._ready = False
                self._error = str(reply.get("error") or "unknown")[:300]
                raise EmbedWorkerError(
                    f"embedding worker could not load the model: {self._error}"
                )
            self._ready = True
            self._dim = reply.get("dim") or None
            if reply.get("stdout_private") is False:
                logger.warning(
                    "the embedding worker could not give itself a private stdout; "
                    "library chatter may corrupt its protocol stream"
                )
            logger.info(
                "embedding worker ready (model=%s, dim=%s)", self._model_name, self._dim
            )
        if not self._ready:
            raise EmbedWorkerError(self._error or "embedding worker is unavailable")

    # -- the one method that crosses the boundary --------------------------

    def embed_texts(self, texts: list, task_type: str = "retrieval_document") -> list:
        if not texts:
            return []
        out: list = []
        for start in range(0, len(texts), CHUNK_SIZE):
            out.extend(self._embed_chunk(texts[start:start + CHUNK_SIZE], task_type))
        return out

    def _embed_chunk(self, texts: list, task_type: str) -> list:
        with self._lock:
            self._ensure_ready()
            self._next_id += 1
            request_id = self._next_id
            self._send({
                "op": "embed", "id": request_id, "texts": texts, "task": task_type,
            })
            reply = self._await(
                lambda m: m.get("id") == request_id, request_timeout(), "an embed request"
            )
            if not reply.get("ok"):
                raise EmbedWorkerError(
                    f"embedding worker failed: {str(reply.get('error') or 'unknown')[:300]}"
                )
            vectors = decode_vectors(reply.get("vecs") or "", int(reply.get("dim") or 0))
            if len(vectors) != len(texts):
                raise EmbedWorkerError(
                    f"embedding worker returned {len(vectors)} vectors for {len(texts)} texts"
                )
            return vectors


# ---------------------------------------------------------------------------
# Child side
# ---------------------------------------------------------------------------

def _child_send(out, message: dict) -> bool:
    try:
        out.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        out.flush()
        return True
    except Exception:
        return False


def _child_main(argv: Optional[list] = None) -> int:
    """Entry point for ``python -m jdocmunch_mcp.embeddings.worker``.

    ⚠ The import of sentence-transformers happens HERE, on the only thread in
    a process that does nothing else. That is the entire design.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    model_name = ""
    if "--model" in argv:
        index = argv.index("--model")
        if index + 1 < len(argv):
            model_name = argv[index + 1]

    # Same reasoning as jdoc#110's transport guard, applied to this protocol:
    # the child's stdout carries framed JSON, and a C extension writing to
    # fd 1 would corrupt it in a way `redirect_stdout` cannot prevent.
    try:
        from jdocmunch_mcp.stdio_guard import claim_stdout
        stream, swapped = claim_stdout()
    except Exception:  # pragma: no cover - defensive
        stream, swapped = None, False
    out = stream.buffer if stream is not None else sys.stdout.buffer

    model = None
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        op = message.get("op")

        if op == "shutdown":
            return 0

        if op == "ready":
            requested = message.get("model") or model_name
            ok, error, dim = True, "", None
            try:
                model, dim = _load_model(requested)
            except BaseException as exc:
                # ⚠ BaseException: a broken native dependency can raise things
                # Exception does not cover, and the parent must get an answer
                # either way — a child that dies silently looks exactly like
                # the hang this replaces.
                ok, error = False, f"{type(exc).__name__}: {exc}"[:300]
            if not _child_send(out, {
                "op": "ready", "ok": ok, "error": error,
                "dim": dim, "stdout_private": swapped,
            }):
                return 1
            continue

        if op == "embed":
            request_id = message.get("id")
            texts = message.get("texts") or []
            if model is None:
                _child_send(out, {
                    "id": request_id, "ok": False, "error": "model is not loaded",
                })
                continue
            try:
                rows = _encode_texts(model, texts)
                payload, dim = encode_vectors(rows)
                sent = _child_send(out, {
                    "id": request_id, "ok": True, "dim": dim, "vecs": payload,
                })
            except BaseException as exc:
                sent = _child_send(out, {
                    "id": request_id, "ok": False,
                    "error": f"{type(exc).__name__}: {exc}"[:300],
                })
            if not sent:
                return 1
            continue

    return 0


def _load_model(model_name: str):
    """Import sentence-transformers and load ``model_name``. Returns (model, dim)."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    dim = None
    try:
        dim = int(model.get_sentence_embedding_dimension())
    except Exception:
        dim = None
    return model, dim


def _encode_texts(model, texts: list) -> list:
    """Encode ``texts`` to a list of equal-length float lists."""
    if not texts:
        return []
    encoded = model.encode(texts, batch_size=64, show_progress_bar=False)
    return [list(map(float, row)) for row in encoded]


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(_child_main())
