"""jdoc#106 follow-up — the weight tuner could not reach a better weight,
and when it did run it walked the wrong way.

Opened while answering #106. We had told the reporter the tuner "exists
precisely to move" the weight. Three things were wrong with that:

1. ⚠⚠ **The signal was scale-corrupted, so the tuner moved DOWNWARD on data
   where the semantic channel was the one answering the queries.**
   `confidence`'s `strength` term reads a RAW top-1 score through a curve
   hardcoded to the BM25 scale. BM25 tops out in the tens; an RRF fused
   score tops out at `1/(k+1)` ≈ 0.0164. Measured on identical ranking
   quality: lexical 0.6205, hybrid 0.0872. The tuner subtracts those, reads
   `semantic_hurts` with delta −0.53, and steps down every round to the 0.10
   floor. Not slow — backwards.

2. **A flat ±0.05 step** meant 0.5 → 0.95 was nine successful rounds at 50+
   qualifying events each.

3. **No signal exists at all on a single-mode workload.** The tuner learns by
   comparing WITH vs WITHOUT the semantic channel; a user who always runs
   hybrid gets `no_signal_split` forever, however long they wait.

⚠ (1) is not confined to the tuner. `build_verdict`'s `low_confidence` state
refuses to mint a citable absence claim, so hybrid searches were also being
disqualified from evidence they had earned.
"""

import math
import tempfile

import pytest

from jdocmunch_mcp.retrieval import confidence as conf
from jdocmunch_mcp.retrieval import tuning


# ---------------------------------------------------------------------------
# The scale bug in confidence
# ---------------------------------------------------------------------------

def _rows(top1, top2):
    return [
        {"title": "a", "_score": top1, "_freshness": "fresh"},
        {"title": "b", "_score": top2, "_freshness": "fresh"},
        {"title": "c", "_score": top2 * 0.9, "_freshness": "fresh"},
    ]


def test_lexical_confidence_is_byte_identical_to_the_old_curve():
    """⚠ The BM25 path must not move. `1-exp(-3t/12)` == `1-exp(-t/4)`."""
    for top1 in (0.5, 2.0, 5.586, 12.0, 20.0, 40.0):
        assert conf._strength(top1, conf.BM25_CEILING) == pytest.approx(
            1.0 - math.exp(-top1 / 4.0)
        )


def test_identical_ranking_quality_scores_alike_across_scorers():
    """The whole defect in one assertion: same separation, same verdict."""
    lex = conf.compute_confidence(
        "q", _rows(20.0, 14.0), score_ceiling=conf.BM25_CEILING
    )["value"]
    hyb = conf.compute_confidence(
        "q", _rows(0.0146, 0.01022), score_ceiling=conf.rrf_ceiling(60)
    )["value"]
    assert hyb == pytest.approx(lex, abs=0.15), (
        f"lexical {lex} vs hybrid {hyb} on identical relative separation"
    )


def test_hybrid_confidence_was_crushed_on_the_bm25_curve():
    """Pins the magnitude of the bug so the fix cannot quietly regress."""
    rows = _rows(0.0146, 0.01022)
    old = conf.compute_confidence("q", rows, score_ceiling=conf.BM25_CEILING)["value"]
    new = conf.compute_confidence("q", rows, score_ceiling=conf.rrf_ceiling(60))["value"]
    assert old < 0.15
    assert new > 0.55
    assert new / old > 5


def test_rrf_ceiling_is_independent_of_the_weight_split():
    """Why 1/(k+1) is a usable ceiling: an item ranked 1st in both channels
    scores `(1-w)/(k+1) + w/(k+1)` for ANY w, since the weights sum to 1."""
    k = 60
    for w in (0.0, 0.1, 0.5, 0.85, 0.95, 1.0):
        best = (1.0 - w) / (k + 1) + w / (k + 1)
        assert best == pytest.approx(conf.rrf_ceiling(k))


def test_semantic_only_uses_the_cosine_ceiling():
    assert conf.ceiling_for_mode("semantic_only") == 1.0
    strong = conf.compute_confidence(
        "q", _rows(0.82, 0.55), score_ceiling=conf.ceiling_for_mode("semantic_only")
    )["value"]
    assert strong > 0.5, "a 0.82 cosine is a strong hit, not a 0.06 one"


def test_unknown_mode_falls_back_to_bm25():
    """⚠ An un-updated caller must be unchanged, not newly wrong."""
    assert conf.ceiling_for_mode("lexical") == conf.BM25_CEILING
    assert conf.ceiling_for_mode("something_new") == conf.BM25_CEILING
    assert conf._strength(5.0, 0.0) == conf._strength(5.0, conf.BM25_CEILING)


def test_compute_confidence_defaults_to_bm25_ceiling():
    import inspect

    sig = inspect.signature(conf.compute_confidence)
    assert sig.parameters["score_ceiling"].default == conf.BM25_CEILING


# ---------------------------------------------------------------------------
# search_sections passes the right ceiling for its mode
# ---------------------------------------------------------------------------

def test_hybrid_search_reports_a_credible_confidence(tmp_path):
    from unittest.mock import patch

    from jdocmunch_mcp.parser import parse_file
    from jdocmunch_mcp.storage.doc_store import DocStore
    from jdocmunch_mcp.tools.search_sections import search_sections

    md = (
        "# Guide\n\n## Authentication\n\nConfigure OAuth tokens for sign in.\n"
        "\n## Payments\n\nStripe billing and invoices.\n"
        "\n## Notifications\n\nEmail alerts via SendGrid.\n"
    )
    store = DocStore(base_path=str(tmp_path))
    secs = parse_file(md, "README.md", "t/r")
    vecs = {
        "Authentication": [1.0, 0.0, 0.0],
        "Payments": [0.0, 1.0, 0.0],
        "Notifications": [0.0, 0.0, 1.0],
    }
    for s in secs:
        s.embedding = vecs.get(s.title, [0.33, 0.33, 0.33])
    store.save_index(owner="local", name="conf", sections=secs,
                     raw_files={"README.md": md}, doc_types={".md": 1})

    with patch("jdocmunch_mcp.storage.doc_store.embed_query") as m:
        m.return_value = [1.0, 0.0, 0.0]
        res = search_sections(repo="local/conf", query="authentication",
                              storage_path=str(tmp_path))
    assert res["_meta"]["search_mode"] == "hybrid"
    assert res["_meta"]["confidence"] > 0.3, (
        "hybrid confidence is being scored on the BM25 curve again"
    )


# ---------------------------------------------------------------------------
# The tuner no longer walks the wrong way
# ---------------------------------------------------------------------------

@pytest.fixture
def ledger(monkeypatch):
    monkeypatch.setenv("JDOCMUNCH_PERF_TELEMETRY", "1")
    tuning.reset_cache()
    return tempfile.mkdtemp()


def _seed(base, *, n, semantic_used, confidence, repo="r/x"):
    from jdocmunch_mcp.storage.token_tracker import record_ranking_event

    for i in range(n):
        record_ranking_event(
            repo=repo, tool="search_sections", query=f"q{i}{semantic_used}",
            mode="hybrid" if semantic_used else "lexical",
            semantic_used=semantic_used, semantic_weight=0.5,
            top1_score=1.0, top2_score=0.7, confidence=confidence,
            result_count=5, base_path=base,
        )


def test_equal_quality_across_modes_no_longer_reads_as_semantic_hurts(ledger):
    """With the scale fixed, comparable retrieval yields comparable numbers
    and the tuner correctly declines to move."""
    _seed(ledger, n=60, semantic_used=True, confidence=0.60)
    _seed(ledger, n=60, semantic_used=False, confidence=0.62)
    out = tuning.tune_one_repo(repo="r/x", base_path=ledger)
    assert out["status"] == "no_significant_signal"


def test_step_scales_with_the_measured_gap(ledger):
    _seed(ledger, n=60, semantic_used=True, confidence=0.90)
    _seed(ledger, n=60, semantic_used=False, confidence=0.30)
    out = tuning.tune_one_repo(repo="r/x", base_path=ledger)
    assert out["status"] == "semantic_helps"
    moved = out["new_semantic_weight"] - out["previous_semantic_weight"]
    assert moved > tuning.STEP, "a decisive delta must move more than the old flat step"
    assert moved <= tuning.MAX_STEP + 1e-9, "and must still be bounded"


def test_a_marginal_gap_still_takes_the_small_step(ledger):
    _seed(ledger, n=60, semantic_used=True, confidence=0.66)
    _seed(ledger, n=60, semantic_used=False, confidence=0.60)
    out = tuning.tune_one_repo(repo="r/x", base_path=ledger)
    assert out["status"] == "semantic_helps"
    moved = out["new_semantic_weight"] - out["previous_semantic_weight"]
    assert moved == pytest.approx(tuning.STEP, abs=0.02)


def test_the_ceiling_is_reachable_in_a_few_rounds(ledger):
    """The reachability claim itself: decisive evidence must not need seven
    rounds of 50 events to cross the range."""
    _seed(ledger, n=60, semantic_used=True, confidence=0.95)
    _seed(ledger, n=60, semantic_used=False, confidence=0.25)
    rounds = 0
    while rounds < 10:
        out = tuning.tune_one_repo(repo="r/x", base_path=ledger)
        rounds += 1
        if out["status"] != "semantic_helps":
            break
        if out["new_semantic_weight"] >= tuning.SEMANTIC_WEIGHT_BOUNDS[1]:
            break
    assert rounds <= 3, f"took {rounds} rounds to reach the ceiling"


def test_step_for_is_signed_and_bounded():
    assert tuning._step_for(0.0) == 0.0
    assert tuning._step_for(0.04) == 0.0
    assert tuning._step_for(0.05) == pytest.approx(tuning.STEP)
    assert tuning._step_for(-0.05) == pytest.approx(-tuning.STEP)
    assert tuning._step_for(0.9) == pytest.approx(tuning.MAX_STEP)
    assert tuning._step_for(-0.9) == pytest.approx(-tuning.MAX_STEP)


# ---------------------------------------------------------------------------
# Single-mode workloads: name the remedy instead of stalling forever
# ---------------------------------------------------------------------------

def test_single_mode_ledger_explains_itself(ledger):
    _seed(ledger, n=60, semantic_used=True, confidence=0.7)
    out = tuning.tune_one_repo(repo="r/x", base_path=ledger)
    assert out["status"] == "no_signal_split"
    assert "semantic-only" in out["reason"]
    assert "set_weight" in out["remedy"]
    assert out["current_semantic_weight"] == 0.5


# ---------------------------------------------------------------------------
# set_weight — the supported way to persist a measured value
# ---------------------------------------------------------------------------

def test_set_weight_persists_and_resolves(tmp_path):
    tuning.reset_cache()
    out = tuning.set_semantic_weight("r/x", 0.9, base_path=str(tmp_path))
    assert out["status"] == "set" and out["semantic_weight"] == 0.9
    assert out["clamped"] is False
    resolved = tuning.resolve_semantic_weight("r/x", base_path=str(tmp_path))
    assert resolved["weight"] == 0.9
    assert resolved["source"] == "tuning.jsonc"


def test_set_weight_clamps_and_says_so(tmp_path):
    tuning.reset_cache()
    out = tuning.set_semantic_weight("r/x", 1.5, base_path=str(tmp_path))
    assert out["semantic_weight"] == tuning.SEMANTIC_WEIGHT_BOUNDS[1]
    assert out["clamped"] is True
    assert out["requested"] == 1.5


def test_set_weight_drops_a_stale_learned_provenance(tmp_path):
    tuning.reset_cache()
    tuning._persist(
        {"repos": {"r/x": {"semantic_weight": 0.6, "learned_from_events": 312}}},
        str(tmp_path),
    )
    tuning.set_semantic_weight("r/x", 0.85, base_path=str(tmp_path))
    tuning.reset_cache()
    entry = tuning._load(str(tmp_path))["repos"]["r/x"]
    assert entry["semantic_weight"] == 0.85
    assert "learned_from_events" not in entry, "a hand-set value was never learned"


def test_tune_weights_tool_set_weight_needs_no_telemetry(tmp_path, monkeypatch):
    """⚠ The telemetry gate exists because there is nothing to LEARN from
    without a ledger. Writing down a measured value needs no ledger."""
    from jdocmunch_mcp.tools.tune_weights import tune_weights

    monkeypatch.delenv("JDOCMUNCH_PERF_TELEMETRY", raising=False)
    tuning.reset_cache()
    out = tune_weights(repo="r/x", set_weight=0.9, storage_path=str(tmp_path))
    assert out["_meta"]["mode"] == "set_weight"
    assert out["results"][0]["semantic_weight"] == 0.9


def test_tune_weights_tool_set_weight_requires_a_repo(tmp_path):
    from jdocmunch_mcp.tools.tune_weights import tune_weights

    out = tune_weights(set_weight=0.9, storage_path=str(tmp_path))
    assert out["status"] == "repo_required"


def test_tune_weights_schema_exposes_set_weight():
    from jdocmunch_mcp import server

    schema = None
    for tool in server._all_tools():
        if tool.name == "tune_weights":
            schema = tool.inputSchema
            break
    assert schema is not None
    assert "set_weight" in schema["properties"]
