"""jdoc#106 — the effective semantic_weight declares where it came from.

Reported by @faxik against a 5,315-section Markdown corpus: with the stock
`semantic_weight=0.5`, 0 of 15 paraphrased queries were answered in the top 5
— worse than disabling the semantic channel entirely (1/15) — while pure
cosine over the same vectors answered 5/15. The vectors were fine. The weight
was wrong, and nothing in the response said a weight was in play at all, so
the failure read as broken embeddings.

Three defects behind that, each pinned here:

  1. `_meta` carried the weight's VALUE but not its PROVENANCE, so a caller
     could not tell a learned weight from the stock default.
  2. `SEMANTIC_WEIGHT_BOUNDS` capped at 0.85 — below the measured optimum on
     that corpus — and `tuning.jsonc` was clamped to it SILENTLY.
  3. The sentinel for "caller did not pass a weight" was the literal default
     0.5, so a deliberate 0.5 was indistinguishable from an omitted argument
     and lost to the tuner.

Every e2e assertion below fails on v1.124.3.
"""

import json
from unittest.mock import patch

from jdocmunch_mcp.parser import parse_file
from jdocmunch_mcp.retrieval import tuning
from jdocmunch_mcp.storage.doc_store import DocStore
from jdocmunch_mcp.tools.search_sections import search_sections


SAMPLE_MD = """# Guide

## Authentication

To sign in users, configure OAuth 2.0 with your provider.

## Payments

We use Stripe for credit card processing.

## Notifications

Email alerts go through SendGrid.
"""

REPO = "local/weight_test"


def _make_index(tmp_path):
    """Real on-disk index with deterministic 3-dim unit-vector embeddings."""
    store = DocStore(base_path=str(tmp_path))
    sections = parse_file(SAMPLE_MD, "README.md", "test/repo")
    vecs = {
        "Authentication": [1.0, 0.0, 0.0],
        "Payments": [0.0, 1.0, 0.0],
        "Notifications": [0.0, 0.0, 1.0],
    }
    for sec in sections:
        sec.embedding = vecs.get(sec.title, [0.33, 0.33, 0.33])
    store.save_index(
        owner="local",
        name="weight_test",
        sections=sections,
        raw_files={"README.md": SAMPLE_MD},
        doc_types={".md": 1},
    )


def _write_tuning(tmp_path, weight, repo=REPO):
    tuning._tuning_path(str(tmp_path)).write_text(
        json.dumps({"repos": {repo: {"semantic_weight": weight}}}),
        encoding="utf-8",
    )
    tuning.reset_cache()


def _search(tmp_path, **kwargs):
    with patch("jdocmunch_mcp.storage.doc_store.embed_query") as mock_eq:
        mock_eq.return_value = [1.0, 0.0, 0.0]
        return search_sections(
            repo=REPO,
            query="authentication",
            storage_path=str(tmp_path),
            **kwargs,
        )


# ---------------------------------------------------------------------------
# resolve_semantic_weight — provenance
# ---------------------------------------------------------------------------

def test_unset_resolves_to_default_and_says_so(tmp_path):
    tuning.reset_cache()
    out = tuning.resolve_semantic_weight("r/x", base_path=str(tmp_path))
    assert out == {
        "weight": tuning.DEFAULT_SEMANTIC_WEIGHT,
        "source": "default",
        "clamped": False,
    }


def test_tuning_file_value_is_attributed_to_the_file(tmp_path):
    _write_tuning(tmp_path, 0.7, repo="r/x")
    out = tuning.resolve_semantic_weight("r/x", base_path=str(tmp_path))
    assert out == {"weight": 0.7, "source": "tuning.jsonc", "clamped": False}


def test_caller_value_is_attributed_to_the_caller(tmp_path):
    _write_tuning(tmp_path, 0.7, repo="r/x")
    out = tuning.resolve_semantic_weight(
        "r/x", explicit=0.2, base_path=str(tmp_path)
    )
    assert out == {"weight": 0.2, "source": "caller", "clamped": False}


def test_explicit_default_value_is_a_caller_value_not_unset(tmp_path):
    """The sentinel is None, and only None.

    Pre-fix, 0.5 WAS the sentinel: a caller who deliberately pinned the
    documented default silently got the learned weight instead.
    """
    _write_tuning(tmp_path, 0.8, repo="r/x")
    out = tuning.resolve_semantic_weight(
        "r/x", explicit=tuning.DEFAULT_SEMANTIC_WEIGHT, base_path=str(tmp_path)
    )
    assert out["weight"] == 0.5
    assert out["source"] == "caller"


def test_out_of_bounds_tuning_value_is_clamped_and_disclosed(tmp_path):
    _write_tuning(tmp_path, 1.5, repo="r/x")
    out = tuning.resolve_semantic_weight("r/x", base_path=str(tmp_path))
    assert out["weight"] == tuning.SEMANTIC_WEIGHT_BOUNDS[1]
    assert out["clamped"] is True


def test_in_bounds_tuning_value_is_not_reported_as_clamped(tmp_path):
    _write_tuning(tmp_path, tuning.SEMANTIC_WEIGHT_BOUNDS[1], repo="r/x")
    out = tuning.resolve_semantic_weight("r/x", base_path=str(tmp_path))
    assert out["clamped"] is False


def test_boolean_in_tuning_file_is_not_read_as_a_weight(tmp_path):
    """`true` is an int subclass in Python; it must not become weight 1.0."""
    _write_tuning(tmp_path, True, repo="r/x")
    out = tuning.resolve_semantic_weight("r/x", base_path=str(tmp_path))
    assert out["source"] == "default"


# ---------------------------------------------------------------------------
# SEMANTIC_WEIGHT_BOUNDS — the ceiling
# ---------------------------------------------------------------------------

def test_ceiling_is_095_not_085(tmp_path):
    """0.95 was strictly better than 0.85 on every slice of #106's answer key;
    1.0 is the value worth excluding (keyword recall 93.3% -> 86.7% there)."""
    assert tuning.SEMANTIC_WEIGHT_BOUNDS == (0.10, 0.95)


def test_hand_written_095_survives_resolution(tmp_path):
    _write_tuning(tmp_path, 0.95, repo="r/x")
    out = tuning.resolve_semantic_weight("r/x", base_path=str(tmp_path))
    assert out["weight"] == 0.95
    assert out["clamped"] is False


def test_tuner_can_step_above_085(tmp_path):
    assert tuning._clamp(0.90) == 0.90
    assert tuning._clamp(1.00) == 0.95
    assert tuning._clamp(0.05) == 0.10


# ---------------------------------------------------------------------------
# get_semantic_weight — legacy scalar form keeps its quirk
# ---------------------------------------------------------------------------

def test_legacy_resolver_still_treats_05_as_unset(tmp_path):
    """Deliberately unchanged: existing callers must not shift behaviour."""
    _write_tuning(tmp_path, 0.7, repo="r/x")
    assert tuning.get_semantic_weight(
        "r/x", explicit=0.5, base_path=str(tmp_path)
    ) == 0.7


def test_legacy_resolver_returns_a_bare_float(tmp_path):
    tuning.reset_cache()
    assert tuning.get_semantic_weight("r/x", base_path=str(tmp_path)) == 0.5


# ---------------------------------------------------------------------------
# search_sections — the surface the reporter was actually reading
# ---------------------------------------------------------------------------

def test_meta_names_the_default_as_the_source(tmp_path):
    _make_index(tmp_path)
    tuning.reset_cache()
    meta = _search(tmp_path)["_meta"]
    assert meta["search_mode"] == "hybrid"
    assert meta["semantic_weight"] == 0.5
    assert meta["semantic_weight_source"] == "default"


def test_meta_names_the_tuning_file_as_the_source(tmp_path):
    _make_index(tmp_path)
    _write_tuning(tmp_path, 0.7)
    meta = _search(tmp_path)["_meta"]
    assert meta["semantic_weight"] == 0.7
    assert meta["semantic_weight_source"] == "tuning.jsonc"


def test_meta_names_the_caller_as_the_source(tmp_path):
    _make_index(tmp_path)
    _write_tuning(tmp_path, 0.7)
    meta = _search(tmp_path, semantic_weight=0.9)["_meta"]
    assert meta["semantic_weight"] == 0.9
    assert meta["semantic_weight_source"] == "caller"


def test_explicit_05_beats_a_learned_weight_end_to_end(tmp_path):
    _make_index(tmp_path)
    _write_tuning(tmp_path, 0.8)
    meta = _search(tmp_path, semantic_weight=0.5)["_meta"]
    assert meta["semantic_weight"] == 0.5
    assert meta["semantic_weight_source"] == "caller"


def test_clamped_tuning_value_is_surfaced_in_meta(tmp_path):
    _make_index(tmp_path)
    _write_tuning(tmp_path, 1.5)
    meta = _search(tmp_path)["_meta"]
    assert meta["semantic_weight"] == 0.95
    assert meta["semantic_weight_clamped_to"] == [0.10, 0.95]


def test_unclamped_weight_carries_no_clamp_key(tmp_path):
    _make_index(tmp_path)
    tuning.reset_cache()
    assert "semantic_weight_clamped_to" not in _search(tmp_path)["_meta"]


def test_lexical_mode_reports_no_weight_or_source(tmp_path):
    _make_index(tmp_path)
    tuning.reset_cache()
    meta = _search(tmp_path, semantic=False)["_meta"]
    assert meta["search_mode"] == "lexical"
    assert "semantic_weight" not in meta
    assert "semantic_weight_source" not in meta


def test_caller_weight_of_zero_is_honoured_as_lexical(tmp_path):
    """0.0 is falsy but is a real caller value — it must not read as unset."""
    _make_index(tmp_path)
    _write_tuning(tmp_path, 0.8)
    meta = _search(tmp_path, semantic_weight=0.0)["_meta"]
    assert meta["search_mode"] == "lexical"


# ---------------------------------------------------------------------------
# The MCP surface must not fill the sentinel in for the caller
# ---------------------------------------------------------------------------

def test_tool_schema_declares_no_default_for_semantic_weight():
    """A schema default that clients materialize would make every call look
    explicit and permanently silence the tuner."""
    from jdocmunch_mcp import server

    schema = None
    for tool in server._all_tools():
        if tool.name == "search_sections":
            schema = tool.inputSchema
            break
    assert schema is not None
    prop = schema["properties"]["semantic_weight"]
    assert "default" not in prop


def test_dispatcher_passes_no_default_for_semantic_weight():
    import inspect

    from jdocmunch_mcp import server

    src = inspect.getsource(server)
    assert 'semantic_weight=arguments.get("semantic_weight")' in src
    assert 'arguments.get("semantic_weight", 0.5)' not in src
