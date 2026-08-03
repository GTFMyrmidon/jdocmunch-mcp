"""Machine-readable, exact-SHA evidence receipts (jdoc#100).

Every evidence summary in the #95 lifecycle arc was assembled by hand. Two
problems with that, and the second is the dangerous one:

1. Transcription is lossy. This project has already shipped a wrong number
   copied from a terminal.
2. A hand-written summary cannot be tied to the commit it came from. It NAMES a
   SHA, which reads as provenance, while the numbers beside it could have come
   from any tree the author happened to have checked out.

A receipt is emitted by the run itself, so the SHA in it is the SHA the numbers
came from. Summaries are then generated from receipts rather than typed.

Usage:
    # after a test run, from the repo whose SHA should be recorded
    python scripts/evidence_receipt.py emit \
        --report report.json --out receipts/

    # roll every receipt in a directory into one summary
    python scripts/evidence_receipt.py summarize receipts/

`--report` takes a `pytest --json-report` file when available. Without it the
receipt still records the environment and SHA and marks counts unavailable,
which is honest rather than absent.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=True
        ).stdout.strip()
    except Exception:
        return ""


def _tree_is_clean() -> bool | None:
    """Did the TESTED tree differ from the commit it claims to be?

    Tracked modifications only. The run itself writes junit.xml, coverage data
    and this receipt directory into the working tree before the receipt is
    emitted, so counting untracked files reported every CI run as dirty. A
    signal that always fires is not a signal, and it would have hidden the real
    case it exists for: a tracked file edited after checkout, where the numbers
    genuinely do not describe the named commit.
    """
    if not _git("rev-parse", "HEAD"):
        return None
    return _git("status", "--porcelain", "--untracked-files=no") == ""


def build_receipt(report_path: Path | None) -> dict:
    sha = _git("rev-parse", "HEAD")
    clean = _tree_is_clean()
    # On a pull_request event, GitHub checks out a SYNTHETIC merge of the PR head
    # into the base. That commit is what ran, so it is the honest answer to "what
    # produced these numbers", but it exists nowhere in the branch history and a
    # reviewer looking it up finds nothing. Record both, and say which is which.
    import os

    ci = {
        k: v
        for k, v in {
            "event": os.environ.get("GITHUB_EVENT_NAME"),
            "workflow_sha": os.environ.get("GITHUB_SHA"),
            "ref": os.environ.get("GITHUB_REF_NAME"),
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            # NOT a default GitHub env var. The workflow passes it from the event
            # payload, because GITHUB_SHA is ALSO the merge commit on this event
            # and naming it "the branch head" was simply false.
            "head_sha": os.environ.get("PR_HEAD_SHA"),
        }.items()
        if v
    }
    if ci.get("event") == "pull_request":
        ci["note"] = (
            "checked-out sha is a synthetic PR merge commit and is not in the "
            "branch history. head_sha is the branch commit under test; "
            "workflow_sha is the same merge commit, not the head."
        )

    receipt = {
        "schema": "jdocmunch.evidence-receipt/v1",
        "sha": sha or None,
        "ci": ci or None,
        # A dirty tree means the numbers do NOT describe the named SHA. Recorded
        # rather than refused, so the receipt can say so out loud.
        "tree_clean": clean,
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or None,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "python": platform.python_version(),
        "counts": None,
        "counts_source": "unavailable",
        "failed_tests": [],
    }

    if report_path and report_path.is_file() and report_path.suffix == ".xml":
        # JUnit XML is pytest BUILT-IN (--junitxml). Preferred over a JSON
        # report plugin so producing evidence never adds a dependency.
        import xml.etree.ElementTree as ET

        try:
            root = ET.parse(report_path).getroot()
        except ET.ParseError:
            root = None
        if root is not None:
            suites = [root] if root.tag == "testsuite" else list(root)
            total = failed = errors = skipped = 0
            failing = []
            for suite in suites:
                total += int(suite.get("tests", 0))
                failed += int(suite.get("failures", 0))
                errors += int(suite.get("errors", 0))
                skipped += int(suite.get("skipped", 0))
                for case in suite.iter("testcase"):
                    if case.find("failure") is not None or case.find("error") is not None:
                        cls = case.get("classname", "")
                        failing.append(f"{cls}::{case.get('name', '')}" if cls else case.get("name", ""))
            receipt["counts"] = {
                "total": total,
                "passed": total - failed - errors - skipped,
                "failed": failed,
                "error": errors,
                "skipped": skipped,
            }
            receipt["counts_source"] = report_path.name
            receipt["failed_tests"] = sorted(failing)
        return receipt

    if report_path and report_path.is_file():
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except ValueError:
            data = None
        if isinstance(data, dict):
            summary = data.get("summary") or {}
            receipt["counts"] = {
                k: summary.get(k)
                for k in ("passed", "failed", "skipped", "error", "total")
                if k in summary
            }
            receipt["counts_source"] = str(report_path.name)
            receipt["failed_tests"] = sorted(
                t.get("nodeid", "")
                for t in data.get("tests", [])
                if t.get("outcome") in {"failed", "error"}
            )
    return receipt


def cmd_emit(args: argparse.Namespace) -> int:
    receipt = build_receipt(Path(args.report) if args.report else None)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sha = (receipt["sha"] or "unknown")[:12]
    stem = f"{sha}-{receipt['platform']['system']}-{receipt['python']}".lower()
    path = out_dir / f"{stem}.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {path}")
    if receipt["tree_clean"] is False:
        print(
            "WARNING: working tree was dirty, so these numbers do not describe "
            f"{receipt['sha']}. The receipt records that."
        )
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    receipts = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(Path(args.dir).glob("*.json"))
    ]
    if not receipts:
        print(f"no receipts in {args.dir}")
        return 1

    shas = {r.get("sha") for r in receipts}
    print(f"# Evidence summary ({len(receipts)} receipts)\n")
    if len(shas) != 1:
        # The failure this whole thing exists to make impossible.
        print("**MIXED SHAs. This is not evidence for a single candidate.**\n")
        for s in sorted(x or "unknown" for x in shas):
            print(f"- `{s}`")
        print()
    else:
        print(f"SHA: `{shas.pop()}`\n")

    # If these ran on a pull_request event the SHA above is a synthetic merge
    # commit that is not in the branch history. Name the branch head too, or a
    # reviewer looks up the only SHA on offer and finds nothing.
    heads = {
        (r.get("ci") or {}).get("head_sha")
        for r in receipts
        if (r.get("ci") or {}).get("head_sha")
    }
    events = {(r.get("ci") or {}).get("event") for r in receipts}
    if heads and events == {"pull_request"}:
        print(
            "Checked-out SHA is a synthetic PR merge commit. Branch head: "
            + ", ".join(f"`{h}`" for h in sorted(heads))
        )
        print()

    dirty = [r for r in receipts if r.get("tree_clean") is False]
    if dirty:
        print(
            f"**{len(dirty)} receipt(s) came from a dirty tree and do not "
            "describe their named SHA.**\n"
        )

    print("| Platform | Python | Passed | Failed | Skipped | Source |")
    print("|---|---|---|---|---|---|")
    for r in receipts:
        c = r.get("counts") or {}
        print(
            f"| {r['platform']['system']} | {r['python']} | "
            f"{c.get('passed', '?')} | {c.get('failed', '?')} | "
            f"{c.get('skipped', '?')} | {r.get('counts_source')} |"
        )

    failures = sorted({t for r in receipts for t in r.get("failed_tests", [])})
    if failures:
        print(f"\n## Failing tests ({len(failures)})\n")
        for nodeid in failures:
            print(f"- `{nodeid}`")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    emit = sub.add_parser("emit", help="write a receipt for the current run")
    emit.add_argument(
        "--report",
        help="pytest --junitxml=<f>.xml (preferred, built in) or a --json-report file",
    )
    emit.add_argument("--out", default="receipts", help="output directory")
    emit.set_defaults(func=cmd_emit)

    summ = sub.add_parser("summarize", help="roll receipts into one summary")
    summ.add_argument("dir", help="directory of receipt JSON files")
    summ.set_defaults(func=cmd_summarize)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
