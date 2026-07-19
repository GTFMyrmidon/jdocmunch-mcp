"""Index local folder tool — walk, parse, summarize, save."""

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Optional

import pathspec

from ..parser import parse_file, preprocess_content, ALL_EXTENSIONS
from ..retrieval.roles import annotate_sections as _annotate_roles
from ..retrieval.glossary import extract_glossary, write_terms
from ..security import (
    validate_path,
    is_symlink_escape,
    is_secret_file,
    DEFAULT_MAX_FILE_SIZE,
)
from ..storage import DocStore
from ..storage.doc_store import normalize_commit_sha
from ..summarizer import summarize_sections
from ..embeddings import embed_sections, get_provider_name, should_embed
from ._git import local_git_head, local_git_paths_dirty, local_git_paths_tracked, stable_local_git_state
from ._constants import SKIP_PATTERNS


def _default_local_name(folder_name: str, folder_path: Optional[str] = None) -> str:
    """Derive a storage-safe local index name from a folder basename (jdoc#72).

    Zero-config rule: if the basename is already a valid storage component,
    preserve it exactly (backward compatible). Otherwise slugify it to the
    allowed ``[A-Za-z0-9._-]`` charset and append a short hash of the folder's
    absolute path, so a label like ``"My Docs"`` becomes ``"my-docs-<hash>"``
    instead of failing storage validation downstream, and two
    differently-located folders that slugify to the same base don't silently
    collide.
    """
    if folder_name not in {".", ".."} and re.fullmatch(r"[A-Za-z0-9._-]+", folder_name):
        return folder_name
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", folder_name)
    slug = re.sub(r"-{2,}", "-", slug).strip("-.").lower()
    if not slug:
        slug = "local-docs"
    seed = folder_path if folder_path is not None else folder_name
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


def normalize_local_index_name(
    name: Optional[str], folder_name: str, folder_path: Optional[str] = None
) -> str:
    """Resolve the caller-supplied ``name`` to a bare local storage component.

    Local doc indexes are stored under owner ``local`` and surfaced by
    ``doc_list_repos`` as the durable handle ``local/<name>``. An agent that
    discovers a repo and reuses that handle as the refresh ``name`` previously
    hit ``Invalid name: 'local/<name>'`` because ``name`` is validated as a
    single storage component (jdoc#67). Accept and normalize the ``local/``
    round-trip while still rejecting other owner prefixes, nested slashes, and
    empty local names (the downstream ``_safe_repo_component`` check catches
    ``.``/``..``/illegal chars on the returned value).

    When ``name`` is omitted the default is derived from the folder basename
    via :func:`_default_local_name`, so a basename that isn't a valid storage
    component (e.g. one containing spaces) yields a deterministic safe handle
    instead of failing storage validation downstream (jdoc#72).
    """
    if not name:
        return _default_local_name(folder_name, folder_path)
    if name.startswith("local/"):
        _owner, _, local_name = name.partition("/")
        # Empty or further-nested local names are not a valid round trip
        # (e.g. "local/" or "local/a/b"); reject here for a clean error.
        if not local_name or "/" in local_name or "\\" in local_name:
            raise ValueError(f"Invalid name: {name!r}")
        return local_name
    if "/" in name or "\\" in name:
        raise ValueError(f"Invalid name: {name!r}")
    return name


def _load_gitignore(folder_path: Path) -> Optional[pathspec.PathSpec]:
    gitignore_path = folder_path / ".gitignore"
    if gitignore_path.is_file():
        try:
            content = gitignore_path.read_text(encoding="utf-8", errors="replace")
            return pathspec.PathSpec.from_lines("gitignore", content.splitlines())
        except Exception:
            pass
    return None


def _add_commit_fields(result: dict, index) -> None:
    if not index:
        return
    if index.head_sha:
        result["head_sha"] = index.head_sha
    result["source_dirty"] = bool(index.source_dirty)
    result["sha_certified"] = bool(index.sha_certified)
    if index.repo_at_sha:
        result["repo_at_sha"] = index.repo_at_sha


def _should_skip(rel_path: str) -> bool:
    normalized = "/" + rel_path.replace("\\", "/")
    for pat in SKIP_PATTERNS:
        if ("/" + pat) in normalized:
            return True
    return False


_DISCOVERY_HARD_CEILING_MULT = 20  # safety: stop counting at max_files * this


def _count_skip(skip_counts: Optional[dict], reason: str) -> None:
    """Tally one discovery-time skip (v1.103.0 coverage contract).

    No-op when the caller didn't ask for counts, so every existing
    ``discover_doc_files`` call keeps its exact prior behavior.
    """
    if skip_counts is not None:
        skip_counts[reason] = skip_counts.get(reason, 0) + 1


def discover_doc_files(
    folder_path: Path,
    max_files: int = 10_000,
    max_size: int = DEFAULT_MAX_FILE_SIZE,
    extra_ignore_patterns: Optional[list] = None,
    follow_symlinks: bool = False,
    sort_by: str = "newest",
    skip_counts: Optional[dict] = None,
) -> tuple:
    """Discover doc files (.md, .txt, .rst) with security filtering.

    ``skip_counts`` (v1.103.0): optional dict the walk tallies per-reason skip
    counts into (``unsupported_extension``, ``oversize``, ``gitignored``, ...)
    at the existing skip sites — the index-time half of the coverage contract
    an absent verdict discloses. Omitted = no counting, behavior unchanged.

    Returns ``(files, warnings, discovered_count)``. ``files`` is capped at
    ``max_files``; ``discovered_count`` is the total that matched all filters
    (capped at ``max_files * _DISCOVERY_HARD_CEILING_MULT`` so a pathological
    directory tree cannot run forever). When ``discovered_count > max_files``
    the caller is responsible for surfacing truncation (jdoc#15).

    ``sort_by`` (jdoc#16) controls truncation order:
      * ``"newest"`` (default): when the cap is hit, the indexed subset is
        the ``max_files`` files with the most recent mtime. So a freshly-
        edited file is always in the index regardless of where it sits in
        the filesystem walk.
      * ``"walk_order"``: take the first ``max_files`` in filesystem-walk
        order (the pre-jdoc#16 behavior). Useful for deterministic
        reproducible builds where mtimes can shift.
    """
    discovered_items: list = []  # [(file_path, mtime_or_zero), ...]
    warnings = []
    hard_ceiling = max_files * _DISCOVERY_HARD_CEILING_MULT
    root = folder_path.resolve()

    gitignore_spec = _load_gitignore(root)
    extra_spec = None
    if extra_ignore_patterns:
        try:
            extra_spec = pathspec.PathSpec.from_lines("gitignore", extra_ignore_patterns)
        except Exception:
            pass

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        dir_path = Path(dirpath)
        try:
            dir_rel = dir_path.relative_to(root).as_posix()
        except ValueError:
            dirnames.clear()
            continue

        # Prune skipped directories in-place so os.walk won't descend into them
        dirnames[:] = [
            d for d in dirnames
            if not _should_skip(f"{dir_rel}/{d}/".lstrip("./"))
            and not (gitignore_spec and gitignore_spec.match_file(f"{dir_rel}/{d}/".lstrip("./")))
            and not (extra_spec and extra_spec.match_file(f"{dir_rel}/{d}/".lstrip("./")))
        ]

        for filename in filenames:
            file_path = dir_path / filename

            if not follow_symlinks and file_path.is_symlink():
                _count_skip(skip_counts, "symlink_not_followed")
                continue
            if file_path.is_symlink() and is_symlink_escape(root, file_path):
                warnings.append(f"Skipped symlink escape: {file_path}")
                _count_skip(skip_counts, "symlink_escape")
                continue

            if not validate_path(root, file_path):
                warnings.append(f"Skipped path traversal: {file_path}")
                _count_skip(skip_counts, "path_traversal")
                continue

            rel_path = f"{dir_rel}/{filename}".lstrip("./") if dir_rel != "." else filename

            if _should_skip(rel_path):
                _count_skip(skip_counts, "skip_pattern")
                continue

            if gitignore_spec and gitignore_spec.match_file(rel_path):
                _count_skip(skip_counts, "gitignored")
                continue

            if extra_spec and extra_spec.match_file(rel_path):
                _count_skip(skip_counts, "extra_ignore_pattern")
                continue

            if is_secret_file(rel_path):
                warnings.append(f"Skipped secret file: {rel_path}")
                _count_skip(skip_counts, "secret_file")
                continue

            ext = file_path.suffix.lower()
            if ext not in ALL_EXTENSIONS:
                _count_skip(skip_counts, "unsupported_extension")
                continue

            try:
                st = file_path.stat()
                if st.st_size > max_size:
                    _count_skip(skip_counts, "oversize")
                    continue
                mtime = st.st_mtime
            except OSError:
                _count_skip(skip_counts, "stat_error")
                continue

            discovered_items.append((file_path, mtime))

        # Stop walking entirely when the safety ceiling is reached so an
        # adversarial / runaway directory tree can't churn forever.
        if len(discovered_items) >= hard_ceiling:
            break

    discovered = len(discovered_items)
    if sort_by == "newest" and discovered > max_files:
        # Only sort on the truncation path; the un-truncated case
        # preserves walk order so callers see no behavior change.
        discovered_items.sort(key=lambda item: item[1], reverse=True)
    files = [fp for fp, _ in discovered_items[:max_files]]
    return files, warnings, discovered


def _resolve_explicit_paths(
    folder_path: Path,
    paths: list,
    max_files: int,
    follow_symlinks: bool,
) -> tuple:
    """Resolve a caller-supplied list of paths into the doc-file shape that the
    downstream pipeline expects. Each entry may be:

      * an absolute path under ``folder_path``, or
      * a path relative to ``folder_path``,
      * a directory (recursed via ``discover_doc_files`` against that subtree),
      * a file (validated and added when its extension is known).

    Returns ``(files, warnings, requested)``. ``requested`` is the list of
    root-relative POSIX paths for every entry that resolved inside
    ``folder_path`` — including entries that no longer exist on disk — so the
    caller can scope an incremental diff to exactly what was asked for
    (jdoc#31). Mirrors ``discover_doc_files`` semantics for security: rejects
    symlink escapes, path-traversal attempts, and entries outside
    ``folder_path``. Skips entries with unknown extensions silently (caller
    gets a `warnings` entry per skip).
    """
    files: list = []
    warnings: list = []
    requested: list = []
    seen: set = set()

    for raw in paths:
        if not isinstance(raw, str) or not raw.strip():
            warnings.append(f"Skipped empty/non-string path: {raw!r}")
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (folder_path / p)
        try:
            p = p.resolve()
        except OSError as e:
            warnings.append(f"Skipped unresolvable path {raw!r}: {e}")
            continue

        try:
            requested.append(p.relative_to(folder_path).as_posix())
        except ValueError:
            warnings.append(f"Skipped path outside folder: {raw!r}")
            continue

        if not p.exists():
            warnings.append(f"Skipped non-existent path: {raw!r}")
            continue

        if p.is_dir():
            sub_files, sub_warnings, _sub_discovered = discover_doc_files(
                p,
                max_files=max_files - len(files),
                follow_symlinks=follow_symlinks,
            )
            warnings.extend(sub_warnings)
            for f in sub_files:
                fr = f.resolve()
                if fr not in seen:
                    seen.add(fr)
                    files.append(f)
                    if len(files) >= max_files:
                        break
        elif p.is_file():
            if not follow_symlinks and p.is_symlink():
                warnings.append(f"Skipped symlink (follow_symlinks=False): {raw!r}")
                continue
            if p.is_symlink() and is_symlink_escape(folder_path, p):
                warnings.append(f"Skipped symlink escape: {raw!r}")
                continue
            if not validate_path(folder_path, p):
                warnings.append(f"Skipped path traversal: {raw!r}")
                continue
            ext = p.suffix.lower()
            if ext not in ALL_EXTENSIONS:
                warnings.append(f"Skipped unsupported extension: {raw!r}")
                continue
            pr = p.resolve()
            if pr not in seen:
                seen.add(pr)
                files.append(p)
        else:
            warnings.append(f"Skipped non-file/non-dir entry: {raw!r}")

        if len(files) >= max_files:
            break

    return files[:max_files], warnings, requested


def index_local(
    path: str,
    name: Optional[str] = None,
    use_ai_summaries: bool = True,
    use_embeddings="auto",
    storage_path: Optional[str] = None,
    extra_ignore_patterns: Optional[list] = None,
    follow_symlinks: bool = False,
    incremental: bool = True,
    max_files: int = 10_000,
    sort_by: str = "newest",
    autotune: bool = False,
    paths: Optional[list] = None,
    worktree_mode: str = "reuse_equivalent",
) -> dict:
    """Index a local folder containing documentation files.

    Args:
        path: Path to local folder.
        name: Optional repo identifier override. Use when two folders share the same
              name (e.g. two libraries both with a 'docs' folder). Defaults to the
              folder name.
        use_ai_summaries: Whether to use AI for section summaries.
        use_embeddings: True/False/"auto". "auto" (default) enables embeddings when
                        an embedding provider is configured (GOOGLE_API_KEY,
                        OPENAI_API_KEY, openai-compatible
                        + JDOCMUNCH_OPENAI_COMPAT_URL + JDOCMUNCH_OPENAI_COMPAT_MODEL,
                        or sentence-transformers installed).
        storage_path: Custom storage path (default: ~/.doc-index/).
        extra_ignore_patterns: Additional gitignore-style patterns to exclude.
        follow_symlinks: Whether to follow symlinks.
        incremental: When True and an existing index exists, only re-index changed files.
        max_files: Maximum number of doc files to index. Default 10000.
                   When hit, response includes truncated/discovered/indexed
                   top-level fields (jdoc#15).
        sort_by: "newest" (default) or "walk_order". Controls which subset
                 is indexed when discovered > max_files. "newest" sorts by
                 mtime descending so recently-edited files always make it
                 into the index regardless of filesystem-walk position
                 (jdoc#16). "walk_order" preserves the pre-1.65 behavior
                 for callers needing deterministic reproducible builds.
                 No effect when the corpus fits under the cap.
        paths: Optional list of explicit paths to index. When provided, the tree
            walk is skipped; only these files (and the contents of any directories
            in the list) are indexed. Each entry may be absolute or relative to
            ``path``. Useful for batch-indexing exactly the files an agent already
            knows about — e.g. the doc files git just touched.
            On an existing index with ``incremental=True`` the diff is scoped to
            the listed subset (jdoc#31): listed files are added/updated, a listed
            file that no longer exists on disk is removed, and indexed files NOT
            in the list are left untouched (never treated as deleted).
        worktree_mode: ``"reuse_equivalent"`` (default, jdoc#83) recognizes when
            an equivalent corpus in a linked Git worktree already has an
            established index — a proven-fresh equivalent is reused (returned,
            not refreshed) instead of creating a duplicate; stale, dirty,
            ambiguous, or evidence-incomplete outcomes return a bounded
            decision with no write. ``"branch_local"`` opts out: intentionally
            create or refresh an exact-path index for THIS worktree.

    Returns:
        Dict with indexing results.
    """
    t0 = time.perf_counter()
    folder_path = Path(path).expanduser().resolve()

    if not folder_path.exists():
        return {"success": False, "error": f"Folder not found: {path}"}
    if not folder_path.is_dir():
        return {"success": False, "error": f"Path is not a directory: {path}"}

    use_embeddings = should_embed(use_embeddings)
    warnings = []

    # jdoc#67: normalize a discovered `local/<name>` handle back to its bare
    # storage component so the doc_list_repos -> index_local refresh round trip
    # works. Done before the broad try below so an invalid name returns a clean
    # error rather than the "Indexing failed: ..." wrapper.
    try:
        repo_name = normalize_local_index_name(name, folder_path.name, str(folder_path))
    except ValueError as e:
        return {"success": False, "error": str(e)}
    owner = "local"
    repo_id = f"{owner}/{repo_name}"

    # jdoc#72: when name was omitted and the folder basename wasn't a valid
    # storage component, a safe local name was derived. Surface both labels so
    # the caller can record the durable handle, and warn that an explicit
    # name= overrides it.
    derivation_fields: dict = {}
    if not name and repo_name != folder_path.name:
        derivation_fields = {
            "original_folder_label": folder_path.name,
            "derived_local_name": repo_name,
        }
        warnings.append(
            f"Folder label {folder_path.name!r} is not a valid storage name; "
            f"indexed under the derived handle local/{repo_name}. Pass "
            f'name="<your-name>" to choose your own.'
        )

    # jdoc#81: creation-claim state, initialized outside the broad try so the
    # exception path can release an acquired claim (a failed create must not
    # leave a claim that blocks the retry).
    claim_token: Optional[str] = None
    claim_store = None

    try:
        requested_rels: list = []
        # v1.103.0: per-reason skip tally from the full discovery walk — the
        # index-time half of the coverage contract. Only a full walk (no
        # explicit `paths`) records it; a subset call is not a coverage claim.
        walk_skip_counts: dict = {}
        if paths:
            doc_files, discover_warnings, requested_rels = _resolve_explicit_paths(
                folder_path,
                list(paths),
                max_files=max_files,
                follow_symlinks=follow_symlinks,
            )
            discovered_count = len(doc_files)
        else:
            doc_files, discover_warnings, discovered_count = discover_doc_files(
                folder_path,
                max_files=max_files,
                extra_ignore_patterns=extra_ignore_patterns,
                follow_symlinks=follow_symlinks,
                sort_by=sort_by,
                skip_counts=walk_skip_counts,
            )
        warnings.extend(discover_warnings)

        initial_git_state = (local_git_head(folder_path), False)
        store = DocStore(base_path=storage_path)
        existing_index = store.load_index(owner, repo_name)

        # --- jdoc#81: corpus-identity resolution BEFORE any persistent write.
        # An equivalent local source (same normalized root + a covering durable
        # selection) must not gain a second physical index just because a
        # different name was supplied. Explicitly selecting an existing handle
        # stays refreshable; an explicit *conflicting* name returns a conflict;
        # several equivalent legacy indexes return bounded ambiguity — never a
        # guess, never another copy.
        from ._corpus_identity import (
            candidate_rows,
            corpus_norm_root,
            find_equivalent_indexes,
            selection_descriptor,
        )
        from ..storage.corpus_claims import claim_key, release_claim, try_claim

        explicit_name = bool(name and str(name).strip())
        call_selection = selection_descriptor(
            requested_rels if paths else None,
            extra_ignore_patterns=extra_ignore_patterns,
            follow_symlinks=follow_symlinks,
        )
        root_key = corpus_norm_root(folder_path)
        # jdoc#82: two relations, deliberately distinct. Identity (symmetric,
        # order-independent) drives conflict and ambiguity; refresh coverage
        # (directional — a full index also absorbs a temporary subset call)
        # drives only omitted-name routing to an established handle.
        identity_matches = find_equivalent_indexes(
            store, root_key, call_selection, exclude_repo=repo_id, mode="identity"
        )
        equivalents = identity_matches if explicit_name else find_equivalent_indexes(
            store, root_key, call_selection, exclude_repo=repo_id, mode="refresh"
        )
        reuse_fields: dict = {}

        if existing_index is None and equivalents:
            if explicit_name:
                # jdoc#82 invariant 2: several identity matches are NEVER
                # ordered into a winner — registry order must not promote an
                # established handle. Only a single match names one.
                if len(equivalents) > 1:
                    return {
                        "success": False,
                        "error": "ambiguous_corpus_identity",
                        "requested_handle": repo_id,
                        "candidates": candidate_rows(equivalents),
                        "total_matches": len(equivalents),
                        "hint": (
                            "Multiple existing indexes cover this source "
                            "equally well. Pass name= selecting one of the "
                            "candidates to refresh it, or delete_index the "
                            "duplicates. No new index was created."
                        ),
                    }
                est = equivalents[0]
                return {
                    "success": False,
                    "error": "corpus_already_indexed",
                    "requested_handle": repo_id,
                    "established_handle": est.get("repo", ""),
                    "candidates": candidate_rows(equivalents),
                    "total_matches": len(equivalents),
                    "hint": (
                        f"This documentation source is already indexed as "
                        f"'{est.get('repo', '')}'. Refresh it with "
                        f"index_local(path=..., name='{est.get('name') or est.get('repo', '')}'), "
                        f"or delete_index it first if you intend to re-home the "
                        f"corpus. No new index was created."
                    ),
                }
            if len(equivalents) == 1:
                est = equivalents[0]
                reuse_fields = {
                    "reused_established_handle": True,
                    "requested_handle": repo_id,
                    "established_handle": est.get("repo", ""),
                }
                repo_name = est.get("name") or est.get("repo", "").partition("/")[2]
                repo_id = f"{owner}/{repo_name}"
                existing_index = store.load_index(owner, repo_name)
            else:
                return {
                    "success": False,
                    "error": "ambiguous_corpus_identity",
                    "candidates": candidate_rows(equivalents),
                    "total_matches": len(equivalents),
                    "hint": (
                        "Multiple existing indexes cover this source equally "
                        "well. Pass name= selecting one of the candidates to "
                        "refresh it. No new index was created."
                    ),
                }
        elif existing_index is not None and not explicit_name and equivalents:
            # The derived handle exists AND other equivalent indexes exist —
            # the caller hasn't selected one, so guessing is not allowed.
            all_candidates = [{
                "repo": repo_id,
                "source_root": str(folder_path),
                "indexed_at": existing_index.indexed_at,
            }] + candidate_rows(equivalents)
            return {
                "success": False,
                "error": "ambiguous_corpus_identity",
                "candidates": all_candidates[:5],
                "total_matches": 1 + len(equivalents),
                "hint": (
                    "Multiple existing indexes cover this source equally well. "
                    "Pass name= selecting one of the candidates to refresh it. "
                    "No index was created or modified."
                ),
            }


        # jdoc#31: when explicit `paths` target an existing incremental index,
        # an empty resolution is not a dead end — every listed file may have
        # been deleted from disk, and the subset-scoped diff below must still
        # run to remove them. Every other shape keeps the early return.
        can_diff_subset = bool(
            paths and requested_rels and incremental and existing_index is not None
        )
        if not doc_files and not can_diff_subset:
            err: dict = {"success": False, "error": "No documentation files found"}
            if warnings:
                err["warnings"] = warnings
            return err

        # jdoc#83 (Item B): collect Git evidence ONCE for this request —
        # used by the worktree gate below and persisted as identity metadata
        # on every save path. Non-Git folders come back in_git=False and keep
        # exactly the pre-#83 behavior.
        from ._worktree_corpus import (
            ResolutionRequest,
            collect_git_evidence,
            filter_lineage_candidates,
            resolve_worktree_corpus,
            worktree_claim_key,
            CORPUS_IDENTITY_VERSION,
        )

        wt_evidence = collect_git_evidence(folder_path)
        branch_local = str(worktree_mode or "reuse_equivalent") == "branch_local"
        lineage_kwargs: dict = {}
        if wt_evidence.lineage_state == "confirmed":
            lineage_kwargs = {
                "worktree_lineage_key": wt_evidence.lineage_key,
                "repo_relative_root": wt_evidence.relative_root,
                "corpus_identity_version": CORPUS_IDENTITY_VERSION,
            }

        def _wt_decision() -> "object":
            candidates = filter_lineage_candidates(
                store.list_repos(), wt_evidence, allow_containment=False
            )
            # The requesting root's own same-root indexes were handled by the
            # Item A block; exclude the target handle itself defensively.
            candidates = [c for c in candidates if c.get("repo") != repo_id]
            return resolve_worktree_corpus(
                ResolutionRequest(
                    tool="index_local",
                    evidence=wt_evidence,
                    selection=call_selection,
                    branch_local=branch_local,
                ),
                candidates,
            )

        if existing_index is None and not branch_local and wt_evidence.in_git:
            decision = _wt_decision()
            if decision.status == "reusable":
                # PRD 9.3: reuse returns the established handle. It never
                # refreshes, retargets, or writes anything.
                latency_ms = int((time.perf_counter() - t0) * 1000)
                return {
                    "success": True,
                    "repo": decision.established_handle,
                    "reused_established_handle": True,
                    "requested_path": str(folder_path),
                    "message": (
                        "An established index for this documentation corpus "
                        "already exists in a linked worktree and is proven "
                        "fresh; it was reused. No index was created."
                    ),
                    "worktree_resolution": decision.to_public(did_write=False),
                    "_meta": {"latency_ms": latency_ms},
                }
            if decision.status in ("reference_only", "related", "ambiguous", "unknown"):
                out = {
                    "success": False,
                    "error": decision.reason_code,
                    "worktree_resolution": decision.to_public(did_write=False),
                }
                if decision.next_action:
                    out["hint"] = decision.next_action
                return out
            # decision.status == "created": creation may proceed under claim.

        # jdoc#81: about to create a brand-new index — close the concurrent-
        # create race with an atomic claim. The loser of the race routes to the
        # winner's handle instead of creating a duplicate physical index.
        # jdoc#83: worktree-translated identities contend on ONE claim keyed by
        # lineage + relative location + selection, so two worktrees racing to
        # create the same logical corpus still produce a single winner;
        # branch-local and non-Git creation keep the exact-path key.
        if existing_index is None:
            wt_key = None if branch_local else worktree_claim_key(
                wt_evidence, call_selection
            )
            key = wt_key or claim_key(root_key, call_selection)
            acquired, existing_claim = try_claim(
                store.base_path, key, repo_id, root_key, call_selection,
                index_exists=lambda r: store.load_index(
                    r.partition("/")[0], r.partition("/")[2]
                ) is not None,
            )
            if acquired:
                claim_token = key
                claim_store = store
                # jdoc#83 R11: resolve AGAIN while holding the claim — a
                # competitor may have established the corpus between the
                # first resolution and the claim. A changed decision returns
                # the new result; creation proceeds only if still permitted.
                if not branch_local and wt_evidence.in_git:
                    recheck = _wt_decision()
                    if recheck.status != "created":
                        release_claim(store.base_path, claim_token)
                        claim_token = None
                        if recheck.status == "reusable":
                            latency_ms = int((time.perf_counter() - t0) * 1000)
                            return {
                                "success": True,
                                "repo": recheck.established_handle,
                                "reused_established_handle": True,
                                "requested_path": str(folder_path),
                                "message": (
                                    "An established index for this corpus was "
                                    "created concurrently and is proven fresh; "
                                    "it was reused. No index was created."
                                ),
                                "worktree_resolution": recheck.to_public(did_write=False),
                                "_meta": {"latency_ms": latency_ms},
                            }
                        out = {
                            "success": False,
                            "error": recheck.reason_code,
                            "worktree_resolution": recheck.to_public(did_write=False),
                        }
                        if recheck.next_action:
                            out["hint"] = recheck.next_action
                        return out
            elif existing_claim is None:
                # jdoc#82 invariant 1: a claim exists but its ownership payload
                # is not readable — a winner is mid-creation and its identity is
                # unknown. Creating anyway is exactly the two-physical-indexes
                # race; refuse with no persistent write.
                return {
                    "success": False,
                    "error": "corpus_creation_in_progress",
                    "requested_handle": repo_id,
                    "hint": (
                        "Another process is currently creating an index for "
                        "this documentation source. Retry shortly, or call "
                        "doc_resolve_repo to find the established handle once "
                        "creation completes. No new index was created."
                    ),
                }
            elif existing_claim.get("repo") not in ("", repo_id):
                claimed_repo = existing_claim["repo"]
                if explicit_name:
                    return {
                        "success": False,
                        "error": "corpus_already_indexed",
                        "requested_handle": repo_id,
                        "established_handle": claimed_repo,
                        "hint": (
                            f"This documentation source is already established "
                            f"(or being created) as '{claimed_repo}'. Refresh it "
                            f"through that handle, or delete_index it first. "
                            f"No new index was created."
                        ),
                    }
                reuse_fields = {
                    "reused_established_handle": True,
                    "requested_handle": repo_id,
                    "established_handle": claimed_repo,
                }
                repo_name = claimed_repo.partition("/")[2] or repo_name
                repo_id = f"{owner}/{repo_name}"
                existing_index = store.load_index(owner, repo_name)

        # Read all discovered files
        current_files: dict = {}
        for file_path in doc_files:
            if not validate_path(folder_path, file_path):
                continue
            try:
                rel_path = file_path.relative_to(folder_path).as_posix()
            except ValueError:
                continue
            try:
                # newline="" preserves CRLF/CR so byte offsets and hashes
                # address the real on-disk bytes, matching the GitHub leg and
                # the disk file (#52). Path.read_text lacks newline before 3.13,
                # so use open(). errors="replace" stays for invalid UTF-8.
                with open(file_path, encoding="utf-8", errors="replace", newline="") as fh:
                    content = fh.read()
                parsed_content = preprocess_content(content, rel_path)
                current_files[rel_path] = parsed_content
            except Exception as e:
                warnings.append(f"Failed to read {file_path}: {e}")
                if not paths:
                    _count_skip(walk_skip_counts, "read_error")

        final_git_state = (local_git_head(folder_path), False)
        head_sha, source_dirty = stable_local_git_state(initial_git_state, final_git_state)
        cert_paths = set(current_files.keys())
        if existing_index is not None:
            cert_paths.update(existing_index.doc_paths)
        if local_git_paths_dirty(folder_path, cert_paths):
            source_dirty = True
        paths_tracked = local_git_paths_tracked(folder_path, current_files.keys())
        if head_sha and not paths_tracked:
            source_dirty = True
        sha_certified = bool(head_sha and not source_dirty and paths_tracked)

        # --- Incremental path ---
        if incremental and existing_index is not None:
            changed, new, deleted = store.detect_changes(owner, repo_name, current_files)

            # jdoc#31: `paths` narrows current_files to a subset, so the
            # corpus-wide diff above marks every unlisted indexed file as
            # deleted. Rescope deletions to what the caller actually listed:
            # an indexed file is deleted only when a requested entry covers it
            # (exact file, or under a listed directory) and it was not read
            # back from disk. Unlisted files are never pruned.
            if paths:
                old_files = set(existing_index.file_hashes)
                if any(req in ("", ".") for req in requested_rels):
                    covered = old_files  # the root itself was listed
                else:
                    covered = {
                        fp for fp in old_files
                        if any(fp == req or fp.startswith(req + "/")
                               for req in requested_rels)
                    }
                deleted = sorted(covered - set(current_files))

            # jdoc#81: a full-corpus refresh (re)asserts the durable selection;
            # a subset-scoped `paths` refresh never redefines it.
            selection_kwargs = {} if paths else {"corpus_selection": call_selection}
            # jdoc#83: a full-corpus refresh of the established handle also
            # backfills/validates the worktree identity metadata (PRD 11 —
            # backfill happens on explicit refresh, never during discovery).
            if not paths and lineage_kwargs:
                selection_kwargs.update(lineage_kwargs)
            # jdoc#82 invariant 4: when a corpus-shaping input changed the
            # durable selection, the identity change is reconciled and
            # DISCLOSED — stored coverage never shifts under an unchanged
            # identity. (Legacy "" normalizes to "full": backfill is silent.)
            selection_changed_fields: dict = {}
            if not paths:
                stored_sel = getattr(existing_index, "corpus_selection", "") or "full"
                if stored_sel != call_selection:
                    selection_changed_fields = {
                        "corpus_selection_changed": {
                            "from": stored_sel,
                            "to": call_selection,
                        }
                    }
            if not changed and not new and not deleted:
                updated = existing_index
                if (
                    normalize_commit_sha(existing_index.head_sha) != head_sha
                    or bool(existing_index.source_dirty) != bool(source_dirty)
                    or bool(existing_index.sha_certified) != bool(sha_certified)
                    or getattr(existing_index, "source_root", "") != str(folder_path)
                    or (not paths and getattr(existing_index, "corpus_selection", "") != call_selection)
                    or (
                        not paths
                        and bool(lineage_kwargs)
                        and getattr(existing_index, "worktree_lineage_key", "")
                        != lineage_kwargs.get("worktree_lineage_key", "")
                    )
                ):
                    updated = store.incremental_save(
                        owner=owner, name=repo_name,
                        changed_files=[], new_files=[], deleted_files=[],
                        new_sections=[], raw_files={}, doc_types={},
                        head_sha=head_sha,
                        source_dirty=source_dirty,
                        sha_certified=sha_certified,
                        source_root=str(folder_path),
                        **selection_kwargs,
                    ) or existing_index
                latency_ms = int((time.perf_counter() - t0) * 1000)
                nochange_result: dict = {
                    "success": True,
                    "message": "No changes detected",
                    "repo": f"{owner}/{repo_name}",
                    "folder_path": str(folder_path),
                    "incremental": True,
                    "changed": 0, "new": 0, "deleted": 0,
                    "_meta": {"latency_ms": latency_ms},
                }
                nochange_result.update(derivation_fields)
                nochange_result.update(reuse_fields)
                nochange_result.update(selection_changed_fields)
                _add_commit_fields(nochange_result, updated)
                # jdoc#15: report truncation even when nothing changed,
                # since the visible-corpus boundary is unchanged.
                if discovered_count > max_files:
                    nochange_result["truncated"] = True
                    nochange_result["discovered"] = discovered_count
                    nochange_result["indexed"] = len(doc_files)
                else:
                    nochange_result["truncated"] = False
                return nochange_result

            files_to_parse = set(changed) | set(new)
            new_sections = []
            raw_subset: dict = {}
            doc_types: dict = {}

            for rel_path in files_to_parse:
                content = current_files[rel_path]
                raw_subset[rel_path] = content
                ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
                try:
                    sections = parse_file(content, rel_path, repo_id)
                    if sections:
                        new_sections.extend(sections)
                        doc_types[f".{ext}"] = doc_types.get(f".{ext}", 0) + 1
                except Exception as e:
                    warnings.append(f"Failed to parse {rel_path}: {e}")

            new_sections = summarize_sections(new_sections, use_ai=use_ai_summaries)
            _annotate_roles(new_sections)
            if use_embeddings:
                new_sections = embed_sections(
                    new_sections,
                    owner=owner, name=repo_name, storage_path=storage_path,
                )

            updated = store.incremental_save(
                owner=owner, name=repo_name,
                changed_files=changed, new_files=new, deleted_files=deleted,
                new_sections=new_sections, raw_files=raw_subset, doc_types=doc_types,
                head_sha=head_sha,
                source_dirty=source_dirty,
                sha_certified=sha_certified,
                source_root=str(folder_path),
                **selection_kwargs,
            )

            latency_ms = int((time.perf_counter() - t0) * 1000)
            result = {
                "success": True,
                "repo": f"{owner}/{repo_name}",
                "folder_path": str(folder_path),
                "incremental": True,
                "changed": len(changed), "new": len(new), "deleted": len(deleted),
                "section_count": len(updated.sections) if updated else 0,
                "indexed_at": updated.indexed_at if updated else "",
                "semantic_search": use_embeddings and get_provider_name() is not None,
                "_meta": {"latency_ms": latency_ms},
            }
            result.update(derivation_fields)
            result.update(reuse_fields)
            result.update(selection_changed_fields)
            _add_commit_fields(result, updated)
            # jdoc#15: surface truncation on the incremental path too.
            if discovered_count > max_files:
                result["truncated"] = True
                result["discovered"] = discovered_count
                result["indexed"] = len(doc_files)
                warnings.append(
                    f"max_files cap hit: indexed {len(doc_files)} of "
                    f"{discovered_count} discovered files. Raise max_files "
                    f"to capture the rest."
                )
            else:
                result["truncated"] = False
            if warnings:
                result["warnings"] = warnings
            return result

        # --- Full index path ---
        all_sections = []
        doc_types = {}
        raw_files: dict = {}
        parsed_files = []
        # v1.103.0: files that were read and parsed but yielded zero sections
        # are invisible to search — an absence claim must disclose them.
        no_sections_count = 0

        for rel_path, content in current_files.items():
            ext = f".{rel_path.rsplit('.', 1)[-1].lower()}" if "." in rel_path else ""
            try:
                sections = parse_file(content, rel_path, repo_id)
                if sections:
                    all_sections.extend(sections)
                    doc_types[ext] = doc_types.get(ext, 0) + 1
                    raw_files[rel_path] = content
                    parsed_files.append(rel_path)
                else:
                    no_sections_count += 1
            except Exception as e:
                warnings.append(f"Failed to parse {rel_path}: {e}")
                no_sections_count += 1

        if not all_sections:
            if claim_token and claim_store is not None:
                release_claim(claim_store.base_path, claim_token)
            return {"success": False, "error": "No sections extracted from files"}

        all_sections = summarize_sections(all_sections, use_ai=use_ai_summaries)
        _annotate_roles(all_sections)
        if use_embeddings:
            all_sections = embed_sections(
                all_sections,
                owner=owner, name=repo_name, storage_path=storage_path,
            )

        # v1.103.0: coverage contract from the full discovery walk. Recorded
        # only when the walk covered the whole corpus (no explicit `paths`);
        # a full re-walk overwrites the prior block (self-heals), incremental
        # saves carry it forward unchanged. Empty = unknown, never fabricated.
        coverage_block: dict = {}
        if not paths:
            from datetime import datetime, timezone
            coverage_block = {
                "walk": "full",
                "files_indexed": len(parsed_files),
                "no_sections_count": no_sections_count,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            nonzero_skips = {k: v for k, v in walk_skip_counts.items() if v}
            if nonzero_skips:
                coverage_block["skip_counts"] = nonzero_skips

        # jdoc#62: persist the core index BEFORE the optional sidecars. The
        # sidecars (the related-graph build especially) are best-effort
        # enrichment; a slow or failing one must never delay or block the
        # index that retrieval actually needs.
        saved = store.save_index(
            owner=owner,
            name=repo_name,
            sections=all_sections,
            raw_files=raw_files,
            doc_types=doc_types,
            head_sha=head_sha,
            source_dirty=source_dirty,
            sha_certified=sha_certified,
            source_root=str(folder_path),
            corpus_selection=call_selection,
            coverage=coverage_block or None,
            **lineage_kwargs,
        )

        # v1.19.0: glossary sidecar built from final section content.
        try:
            entries = extract_glossary(all_sections)
            write_terms(storage_path, owner, repo_name, entries)
        except Exception:
            pass  # glossary is best-effort; never fail indexing

        # v1.24.0: related-graph adjacency list sidecar.
        try:
            from ..retrieval.related_persist import write as _write_related
            _write_related(storage_path, owner, repo_name, all_sections)
        except Exception:
            pass

        # v1.24.0: boilerplate detector sidecar.
        try:
            from ..retrieval.boilerplate import write as _write_boilerplate
            _write_boilerplate(storage_path, owner, repo_name, all_sections)
        except Exception:
            pass

        # v1.34.0: section near-duplicate detector sidecar.
        try:
            from ..retrieval.dedup import write as _write_dedup
            _write_dedup(storage_path, owner, repo_name,
                         [s.to_dict() | {"content": getattr(s, "content", "") or ""} for s in all_sections])
        except Exception:
            pass

        # v1.29.0: opt-in autotune. Runs the v1.23 weight tuner on this
        # repo's accumulated ranking events; no-op when telemetry isn't
        # enabled. Failures swallowed.
        autotune_result = None
        if autotune:
            try:
                from .tune_weights import tune_weights as _tune_weights
                autotune_result = _tune_weights(
                    repo=f"{owner}/{repo_name}",
                    storage_path=storage_path,
                )
            except Exception:
                autotune_result = None

        latency_ms = int((time.perf_counter() - t0) * 1000)
        result = {
            "success": True,
            "repo": f"{owner}/{repo_name}",
            "folder_path": str(folder_path),
            "indexed_at": saved.indexed_at,
            "file_count": len(parsed_files),
            "section_count": len(all_sections),
            "doc_types": doc_types,
            "files": parsed_files[:20],
            "semantic_search": use_embeddings and get_provider_name() is not None,
            "_meta": {"latency_ms": latency_ms},
        }
        result.update(derivation_fields)
        result.update(reuse_fields)
        # jdoc#82 invariant 4 disclosure on the full-replace path too.
        if existing_index is not None:
            _stored_sel = getattr(existing_index, "corpus_selection", "") or "full"
            if _stored_sel != call_selection:
                result["corpus_selection_changed"] = {
                    "from": _stored_sel,
                    "to": call_selection,
                }
        _add_commit_fields(result, saved)
        if autotune_result is not None:
            result["autotune"] = autotune_result

        # jdoc#15: surface truncation as structured top-level fields so
        # callers can detect it programmatically, not just from a free-text
        # note string. `truncated` is False when the corpus fit entirely
        # under the cap; True when the cap was hit. `discovered` is the
        # full match count (capped at max_files * safety ceiling).
        if discovered_count > max_files:
            result["truncated"] = True
            result["discovered"] = discovered_count
            result["indexed"] = len(doc_files)
            warnings.append(
                f"max_files cap hit: indexed {len(doc_files)} of "
                f"{discovered_count} discovered files. Raise max_files to "
                f"capture the rest."
            )
            result["note"] = (
                f"Folder has many files; indexed first {max_files} of "
                f"{discovered_count}. Raise max_files to include the rest."
            )
        else:
            result["truncated"] = False

        if warnings:
            result["warnings"] = warnings

        return result

    except Exception as e:
        if claim_token and claim_store is not None:
            try:
                from ..storage.corpus_claims import release_claim as _release
                _release(claim_store.base_path, claim_token)
            except Exception:
                pass
        return {"success": False, "error": f"Indexing failed: {str(e)}"}
