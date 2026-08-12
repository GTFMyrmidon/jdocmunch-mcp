"""Public-surface smoke test against the INSTALLED package (jdoc#99).

Every lifecycle test in `tests/` runs against an editable source tree, so a
packaging fault cannot fail them: a module left out of the wheel, a stale file,
or a broken entry point all pass CI and reach PyPI. This exercises the public
surface through whatever `import jdocmunch_mcp` actually resolves to.

Run it from a directory with NO source tree reachable, against a wheel installed
into a clean environment. It refuses to run otherwise, because a smoke test that
silently imports `src/` proves nothing and would be worse than not having one.

Exit code is the result: 0 pass, 1 fail, with the reason on stdout.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def main() -> None:
    import jdocmunch_mcp

    origin = Path(jdocmunch_mcp.__file__).resolve()
    # The guard that makes this test mean anything.
    if "site-packages" not in origin.parts and "dist-packages" not in origin.parts:
        _fail(
            "imported jdocmunch_mcp from a source tree, not an installed "
            f"package: {origin}. Run this from a directory with no src/ on the "
            "path, against an installed wheel."
        )
    print(f"OK: importing installed package from {origin}")

    from jdocmunch_mcp.tools.delete_index import delete_index
    from jdocmunch_mcp.tools.index_local import index_local

    with tempfile.TemporaryDirectory() as tmp:
        storage = str(Path(tmp) / "store")
        docs = Path(tmp) / "docs"
        docs.mkdir()
        (docs / "README.md").write_text(
            "# Smoke\n\nInstalled-package smoke test for jdoc#99.\n",
            encoding="utf-8",
        )

        indexed = index_local(
            str(docs), use_ai_summaries=False, storage_path=storage
        )
        if not indexed.get("success"):
            _fail(f"index_local failed against the installed package: {indexed}")
        repo = indexed.get("repo")
        if not repo:
            _fail(f"index_local returned no repo id: {indexed}")
        print(f"OK: indexed {repo}")

        # Deleting something absent must be a typed answer, not a crash.
        missing = delete_index("owner/definitely-not-indexed", storage_path=storage)
        if missing.get("success"):
            _fail(f"deleting an unindexed repo reported success: {missing}")
        if not missing.get("reason_code"):
            _fail(
                "delete of an unindexed repo carried no reason_code; the public "
                f"vocabulary did not survive packaging: {missing}"
            )
        print(f"OK: absent delete returned reason_code={missing['reason_code']}")

        deleted = delete_index(repo, storage_path=storage)
        if not deleted.get("success"):
            _fail(f"delete_index failed against the installed package: {deleted}")
        print(f"OK: deleted {repo}")

        again = delete_index(repo, storage_path=storage)
        if again.get("success"):
            _fail(f"second delete of the same repo reported success: {again}")
        print(f"OK: repeat delete refused with reason_code={again.get('reason_code')}")

    print("PASS: installed-package public surface is intact")


if __name__ == "__main__":
    main()
