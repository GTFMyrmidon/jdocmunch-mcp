"""Generate CHANGELOG.md from git release commits (v1.35.0+).

Walks ``git log`` for every commit whose subject starts with ``release:``
and produces a Keep-a-Changelog-formatted file. The first paragraph of
each release commit message is used as the entry summary.

Usage:

    python scripts/generate_changelog.py [--out CHANGELOG.md]

Idempotent: rewrites the entire file from git state. Safe to run on
every release; keeps history canonical to git rather than drifting
between hand edits.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_RELEASE_RE = re.compile(r"^release:\s*v(?P<ver>\d+\.\d+\.\d+)\s*[—–-]\s*(?P<title>.+)$")


def _git_log(repo_root: Path) -> list[dict]:
    """Return list of {hash, date, subject, body} for every commit."""
    sep = "<<<JDOCMUNCH_LOG_SEP>>>"
    field = "<<<JDOCMUNCH_FIELD_SEP>>>"
    fmt = field.join(["%H", "%ad", "%s", "%b"]) + sep
    proc = subprocess.run(
        ["git", "log", f"--pretty=format:{fmt}", "--date=short"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    out: list[dict] = []
    for raw in proc.stdout.split(sep):
        raw = raw.strip("\n")
        if not raw:
            continue
        parts = raw.split(field)
        if len(parts) < 4:
            continue
        out.append({
            "hash": parts[0],
            "date": parts[1],
            "subject": parts[2],
            "body": parts[3],
        })
    return out


def _first_paragraph(body: str) -> str:
    body = body.strip("\n")
    if not body:
        return ""
    # Drop trailing Co-Authored-By signoff blocks.
    body = re.split(r"\n\s*Co-Authored-By:.*$", body, flags=re.MULTILINE | re.DOTALL)[0]
    para = body.split("\n\n", 1)[0]
    return para.strip()


def render(commits: list[dict]) -> str:
    """Render the CHANGELOG.md text from a list of commits."""
    lines: list[str] = [
        "# Changelog",
        "",
        "All notable changes to jdocmunch-mcp by release. Generated from git "
        "history via `scripts/generate_changelog.py`. See "
        "[README.md](./README.md) for the 1.x compatibility commitment.",
        "",
    ]
    for c in commits:
        m = _RELEASE_RE.match(c["subject"])
        if not m:
            continue
        ver = m.group("ver")
        title = m.group("title").strip()
        date = c["date"]
        summary = _first_paragraph(c["body"])
        lines.append(f"## v{ver} — {date}")
        lines.append("")
        lines.append(f"**{title}**")
        lines.append("")
        if summary:
            lines.append(summary)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate CHANGELOG.md from git history.")
    parser.add_argument("--out", default="CHANGELOG.md", help="Output path (default CHANGELOG.md).")
    parser.add_argument("--repo", default=".", help="Repo root (default cwd).")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    commits = _git_log(repo_root)
    text = render(commits)
    out_path = (repo_root / args.out).resolve()
    out_path.write_text(text, encoding="utf-8")
    print(f"wrote {out_path} ({sum(1 for c in commits if _RELEASE_RE.match(c['subject']))} releases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
