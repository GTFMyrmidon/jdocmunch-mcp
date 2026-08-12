"""CLI harness: run fixtures through search_sections, compute metrics, gate.

Usage:
    PYTHONPATH=src python -m benchmarks.replay.run_replay \
        --fixture self_v1_11_0 \
        [--baseline 1.10.0] [--gate 0.02] [--write-results]

Fixture format (JSON, in benchmarks/replay/fixtures/<name>.json):
    {
      "name": "self_v1_11_0",
      "repo_path": ".",                  # local path to index
      "repo_id": "local/jdocmunch-mcp",  # for search_sections
      "indexed_at": "2026-04-26T...",
      "queries": [
        {"query": "...", "expected_top_k": ["section_id_1", ...], "k": 5}
      ]
    }

Saved results land at benchmarks/replay/results/<name>-v<version>.json. With
--baseline X.Y.Z the harness reads <name>-vX.Y.Z.json and exits non-zero when
any aggregate metric drops by more than --gate (default 0.02 = 2%). Missing
baseline ⇒ pass with a "first_run" note.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

REPLAY_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = REPLAY_DIR / "fixtures"
RESULTS_DIR = REPLAY_DIR / "results"


def _resolve_version() -> str:
    """Read jdocmunch-mcp version, falling back to pyproject.toml when the
    installed package metadata is stale (matters during a freshly bumped release)."""
    try:
        from jdocmunch_mcp import __version__
        if __version__ and __version__ != "unknown":
            return __version__
    except Exception:
        pass
    pyproject = REPLAY_DIR.parent.parent / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("version"):
                # version = "1.11.0"
                parts = line.split("=", 1)
                if len(parts) == 2:
                    return parts[1].strip().strip('"').strip("'")
    return "unknown"


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"fixture not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _index_fixture_repo(fixture: dict, storage_path: str) -> None:
    """Index the fixture's repo_path into a temp store before running queries."""
    from jdocmunch_mcp.tools.index_local import index_local

    repo_path = fixture["repo_path"]
    name = fixture.get("repo_id", "").split("/", 1)[-1] or None
    extra_ignore = fixture.get("extra_ignore_patterns") or None

    result = index_local(
        path=repo_path,
        name=name,
        use_ai_summaries=False,   # keep replay deterministic + fast
        use_embeddings=False,
        storage_path=storage_path,
        incremental=False,
        extra_ignore_patterns=extra_ignore,
    )
    if not result.get("success"):
        raise RuntimeError(f"indexing failed for fixture {fixture['name']}: {result}")


def _run_query(repo_id: str, query: str, k: int, storage_path: str, lexical_engine: str = "bm25") -> list[str]:
    from jdocmunch_mcp.tools.search_sections import search_sections

    out = search_sections(
        repo=repo_id,
        query=query,
        max_results=max(k, 10),
        semantic=False,
        lexical_engine=lexical_engine,
        storage_path=storage_path,
    )
    if "results" not in out:
        return []
    return [r.get("id", "") for r in out["results"]][:k]


def run_fixture(
    fixture_name: str,
    *,
    baseline: Optional[str] = None,
    gate: float = 0.02,
    write_results: bool = False,
    lexical_engine: str = "bm25",
) -> dict:
    from .metrics import aggregate, mrr_at_k, ndcg_at_k, recall_at_k

    fixture = _load_fixture(fixture_name)
    queries = fixture["queries"]

    # Run indexing + queries in an isolated temp store so the user's home is untouched.
    with tempfile.TemporaryDirectory(prefix="jdoc_replay_") as tmp:
        storage_path = tmp
        _index_fixture_repo(fixture, storage_path)

        per_query = []
        for q in queries:
            k = int(q.get("k", 5))
            predicted = _run_query(
                fixture["repo_id"], q["query"], k=max(k, 10), storage_path=storage_path, lexical_engine=lexical_engine,
            )
            expected = q.get("expected_top_k", [])
            per_query.append(
                {
                    "query": q["query"],
                    "predicted": predicted[:k],
                    "expected": expected,
                    "ndcg": ndcg_at_k(predicted, expected, k),
                    "mrr": mrr_at_k(predicted, expected, k),
                    "recall": recall_at_k(predicted, expected, k),
                }
            )

    aggregates = aggregate(per_query)

    version = _resolve_version()
    report = {
        "fixture": fixture_name,
        "version": version,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_queries": len(per_query),
        "aggregates": aggregates,
        "per_query": per_query,
    }

    if write_results:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = RESULTS_DIR / f"{fixture_name}-v{version}.json"
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report["written_to"] = str(out_path)

    if baseline:
        baseline_path = RESULTS_DIR / f"{fixture_name}-v{baseline}.json"
        if not baseline_path.exists():
            report["gate"] = {
                "status": "first_run",
                "reason": f"baseline {baseline_path.name} not found; treating as first run",
                "gate_pct": gate,
            }
        else:
            baseline_report = json.loads(baseline_path.read_text(encoding="utf-8"))
            base_agg = baseline_report.get("aggregates", {})
            regressions = []
            for key, current in aggregates.items():
                base = float(base_agg.get(key, 0.0))
                drop = base - float(current)
                if drop > gate:
                    regressions.append(
                        {"metric": key, "baseline": base, "current": current, "drop": drop}
                    )
            report["gate"] = {
                "status": "fail" if regressions else "pass",
                "baseline": baseline,
                "gate_pct": gate,
                "regressions": regressions,
            }

    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="replay")
    parser.add_argument("--fixture", required=True, help="fixture name (without .json)")
    parser.add_argument("--baseline", help="baseline version (e.g. 1.10.0) for gate comparison")
    parser.add_argument("--gate", type=float, default=0.02, help="max allowed metric drop (default 0.02)")
    parser.add_argument("--write-results", action="store_true", help="persist report under results/")
    parser.add_argument("--engine", choices=["bm25", "legacy"], default="bm25", help="lexical engine to use")
    args = parser.parse_args(argv)

    report = run_fixture(
        args.fixture,
        baseline=args.baseline,
        gate=args.gate,
        write_results=args.write_results,
        lexical_engine=args.engine,
    )

    print(json.dumps(report, indent=2))

    gate_info = report.get("gate")
    if gate_info and gate_info.get("status") == "fail":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
