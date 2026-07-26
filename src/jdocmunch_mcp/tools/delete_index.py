"""Delete a repo index."""

import time
from typing import Optional

from ..storage import DocStore

# jdoc#93 QA-20: published outcome vocabulary. `success` alone cannot tell an
# agent whether to retry — lifecycle contention and a genuinely missing index
# both arrived as success:false, "Index not found.", so a caller that hit a
# retirement mid-flight concluded the index never existed and re-indexed. That
# duplicate creation is the exact failure this arc exists to prevent.
DELETE_RESULT_VOCABULARY = {
    "index_deleted": {
        "outcome": "Deleted",
        "success": True,
        "retryable": False,
    },
    "index_not_found": {
        "outcome": "Missing",
        "success": False,
        "retryable": False,
    },
    "index_lifecycle_busy": {
        "outcome": "Lifecycle contention",
        "success": False,
        "retryable": True,
    },
}

_MESSAGES = {
    "index_deleted": "Index deleted.",
    "index_not_found": "Index not found.",
    "index_lifecycle_busy": (
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
    reason_code = outcome.get(
        "reason_code", "index_deleted" if deleted else "index_not_found"
    )
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
        "message": _MESSAGES.get(
            reason_code, "Index deleted." if deleted else "Index not found."
        ),
        "_meta": {"latency_ms": latency_ms},
    }
