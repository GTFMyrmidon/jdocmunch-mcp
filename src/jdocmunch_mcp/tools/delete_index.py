"""Delete a repo index."""

import time
from typing import Optional

from ..storage import DocStore
from ..storage.doc_store import DELETE_REASON_CODES

_DELETED = DELETE_REASON_CODES["deleted"]
_NOT_FOUND = DELETE_REASON_CODES["not_found"]
_LIFECYCLE_BUSY = DELETE_REASON_CODES["lifecycle_busy"]

# jdoc#93 QA-20: published outcome vocabulary. `success` alone cannot tell an
# agent whether to retry — lifecycle contention and a genuinely missing index
# both arrived as success:false, "Index not found.", so a caller that hit a
# retirement mid-flight concluded the index never existed and re-indexed. That
# duplicate creation is the exact failure this arc exists to prevent.
DELETE_RESULT_VOCABULARY = {
    _DELETED: {
        "outcome": "Deleted",
        "success": True,
        "retryable": False,
    },
    _NOT_FOUND: {
        "outcome": "Missing",
        "success": False,
        "retryable": False,
    },
    _LIFECYCLE_BUSY: {
        "outcome": "Lifecycle contention",
        "success": False,
        "retryable": True,
    },
}

_MESSAGES = {
    _DELETED: "Index deleted.",
    _NOT_FOUND: "Index not found.",
    _LIFECYCLE_BUSY: (
        "Index is busy completing a retirement. The index still exists; "
        "retry shortly."
    ),
}


def delete_index(repo: str, storage_path: Optional[str] = None) -> dict:
    """Remove a repo index and its raw content cache."""
    t0 = time.perf_counter()
    store = DocStore(base_path=storage_path)
    owner, name = store._resolve_repo(repo)
    outcome: dict = {}
    # jdoc#93 QA-23: zero-wait on the PUBLIC path. Contention comes back as a
    # typed, retryable answer instead of a silent block — an MCP call that
    # waits on a lock is indistinguishable from a hang to its caller. Internal
    # coordinated operations (the retirement's guarded delete) keep the
    # blocking acquisition; they are mid-protocol, where waiting is correct.
    deleted = store.delete_index(owner, name, outcome=outcome, lock_wait=False)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    # Fall back rather than assume: a store that ignored `outcome` still
    # yields a truthful code from the boolean it did return.
    fallback_reason = _DELETED if deleted else _NOT_FOUND
    reason_code = outcome.get("reason_code", fallback_reason)
    result_contract = DELETE_RESULT_VOCABULARY.get(reason_code)
    return {
        "success": (
            result_contract["success"] if result_contract is not None else deleted
        ),
        "repo": f"{owner}/{name}",
        "reason_code": reason_code,
        "retryable": (
            result_contract["retryable"]
            if result_contract is not None
            else False
        ),
        "message": _MESSAGES.get(reason_code, _MESSAGES[fallback_reason]),
        "_meta": {"latency_ms": latency_ms},
    }
