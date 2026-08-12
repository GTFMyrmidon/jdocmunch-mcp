"""jdoc#110 — an uncached embedding model must not block the MCP handshake.

Reported by @pnm-jgb with timings: `jdocmunch-mcp serve` initialized the
sentence-transformers provider *before* answering `initialize`, costing ~7.6 s
on every start with a cached model. With an **uncached** model the download
happened inside that same window, and a 440 MB model pushed the client past its
30 s connect timeout — the server never registered, and the error said only
"connection timed out", naming neither models nor downloads. Changing one env
var became a one-cycle outage.

⚠⚠ The fix is to SKIP the warmup for an uncached model, not to move it into a
background thread. The warmup exists so the model load finishes before
`stdio_server` owns stdout: `contextlib.redirect_stdout` is process-global, so
a load running concurrently with JSON-RPC cannot be redirected safely and its
progress chatter would corrupt framing for every request. Skipping is safe,
backgrounding is not — the failure mode of getting that wrong is worse than the
bug being fixed.

⚠ Full lazy-by-default (the report's literal ask) is therefore NOT done here.
This closes the outage and leaves the cached ~7.6 s, with
`JDOCMUNCH_EMBED_WARMUP=0` as the opt-out for anyone who wants lazy today.
"""

import pytest

from jdocmunch_mcp.embeddings import provider as prov


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in ("JDOCMUNCH_EMBED_WARMUP", "JDOCMUNCH_ST_MODEL",
              "HF_HOME", "HF_HUB_CACHE"):
        monkeypatch.delenv(v, raising=False)


def _hub(tmp_path, *repo_ids):
    """A HuggingFace-shaped cache containing the given repo ids."""
    hub = tmp_path / "hub"
    for rid in repo_ids:
        snap = hub / ("models--" + rid.replace("/", "--")) / "snapshots" / "abc123"
        snap.mkdir(parents=True)
        (snap / "config.json").write_text("{}", encoding="utf-8")
    return hub


# --- the cache probe -------------------------------------------------------

@pytest.fixture(autouse=True)
def _assume_the_provider_imports(monkeypatch):
    """jdoc#118: stub the import probe for every test in this file.

    The probe is a SECOND gate in front of warmup(), of the same shape as
    `_st_model_is_cached`. Unstubbed it shells out to the real
    sentence-transformers, so these tests would assert a property of the
    developer's site-packages rather than of warmup() -- two went red on a
    transformers/sentence-transformers pairing that raises ImportError.

    ⚠ The vacuity is the worse half, and it is why this is autouse rather than
    three targeted stubs. With the probe answering False, warmup() returns ""
    before reaching the cache gate at all, so a test like
    `test_a_users_own_progress_setting_is_not_overwritten` still PASSES -- for
    the wrong reason, having exercised nothing. The probe's own behaviour is
    covered in test_jdoc_118_import_probe.py; here it must be out of the way.
    """
    monkeypatch.setattr(prov, "_sentence_transformers_imports_cleanly", lambda: True)


def test_a_cached_model_is_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(_hub(tmp_path, "BAAI/bge-base-en-v1.5")))
    assert prov._st_model_is_cached("BAAI/bge-base-en-v1.5") is True


def test_an_uncached_model_is_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(_hub(tmp_path, "BAAI/bge-base-en-v1.5")))
    assert prov._st_model_is_cached("intfloat/e5-large-v2") is False


def test_a_bare_name_resolves_under_the_sentence_transformers_org(tmp_path, monkeypatch):
    """⚠ A bare name is not the cache key.

    sentence-transformers resolves `all-MiniLM-L6-v2` to
    `sentence-transformers/all-MiniLM-L6-v2`, so it lands in
    `models--sentence-transformers--all-MiniLM-L6-v2`. Checking only the literal
    name reported the DEFAULT model as uncached on every machine that has it,
    which would skip every warmup — the opposite of the intended change.
    """
    monkeypatch.setenv(
        "HF_HUB_CACHE",
        str(_hub(tmp_path, "sentence-transformers/all-MiniLM-L6-v2")),
    )
    assert prov._st_model_is_cached("all-MiniLM-L6-v2") is True
    assert prov._st_model_is_cached("sentence-transformers/all-MiniLM-L6-v2") is True


def test_an_org_qualified_id_is_not_mistaken_for_a_path(tmp_path, monkeypatch):
    """⚠⚠ Windows-only bug if `os.altsep` is treated as a path separator.

    `os.altsep` is "/" on Windows, so an ordinary hub id would be probed as a
    filesystem path, never found, and every org-qualified model would report
    uncached on Windows and nowhere else.
    """
    monkeypatch.setenv("HF_HUB_CACHE", str(_hub(tmp_path, "BAAI/bge-base-en-v1.5")))
    assert prov._st_model_is_cached("BAAI/bge-base-en-v1.5") is True


def test_a_local_directory_counts_as_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(_hub(tmp_path)))
    local = tmp_path / "my-model"
    local.mkdir()
    assert prov._st_model_is_cached(str(local)) is True
    assert prov._st_model_is_cached(str(tmp_path / "absent")) is False


def test_an_empty_model_name_fails_open():
    assert prov._st_model_is_cached("") is True


def test_a_missing_cache_root_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "nothing-here"))
    assert prov._st_model_is_cached("BAAI/bge-base-en-v1.5") is False


def test_an_empty_snapshots_dir_is_not_cached(tmp_path, monkeypatch):
    hub = tmp_path / "hub"
    (hub / "models--BAAI--bge-base-en-v1.5" / "snapshots").mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    assert prov._st_model_is_cached("BAAI/bge-base-en-v1.5") is False


# --- the warmup gate -------------------------------------------------------

def test_warmup_is_skipped_for_an_uncached_model(tmp_path, monkeypatch):
    """The reported outage: this is the call that used to download 440 MB
    inside the client's 30 s connect window."""
    monkeypatch.setenv("HF_HUB_CACHE", str(_hub(tmp_path)))
    monkeypatch.setenv("JDOCMUNCH_ST_MODEL", "BAAI/bge-base-en-v1.5")
    monkeypatch.setattr(prov, "get_provider_name", lambda: "sentence-transformers")

    called = []
    monkeypatch.setattr(prov, "embed_query", lambda q: called.append(q))

    assert prov.warmup() == ""
    assert called == [], "an uncached model was loaded during startup"


def test_warmup_still_runs_for_a_cached_model(tmp_path, monkeypatch):
    """⚠ The gate must not disable warmup wholesale — that would move every
    model load into a tool call, where chatter can corrupt JSON-RPC framing."""
    monkeypatch.setenv(
        "HF_HUB_CACHE", str(_hub(tmp_path, "BAAI/bge-base-en-v1.5")))
    monkeypatch.setenv("JDOCMUNCH_ST_MODEL", "BAAI/bge-base-en-v1.5")
    monkeypatch.setattr(prov, "get_provider_name", lambda: "sentence-transformers")

    called = []
    monkeypatch.setattr(prov, "embed_query", lambda q: called.append(q))

    assert prov.warmup() == "sentence-transformers"
    assert len(called) == 1


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "N", "F"])
def test_warmup_can_be_declined_outright(tmp_path, monkeypatch, value):
    monkeypatch.setenv(
        "HF_HUB_CACHE", str(_hub(tmp_path, "sentence-transformers/all-MiniLM-L6-v2")))
    monkeypatch.setenv("JDOCMUNCH_EMBED_WARMUP", value)
    monkeypatch.setattr(prov, "get_provider_name", lambda: "sentence-transformers")
    monkeypatch.setattr(prov, "embed_query",
                        lambda q: pytest.fail("warmup ran despite the opt-out"))
    assert prov.warmup() == ""


@pytest.mark.parametrize("provider", ["gemini", "openai", "openai-compatible", None])
def test_network_providers_are_still_never_warmed(monkeypatch, provider):
    monkeypatch.setattr(prov, "get_provider_name", lambda: provider)
    monkeypatch.setattr(prov, "embed_query",
                        lambda q: pytest.fail("a network provider was warmed"))
    assert prov.warmup() == ""


def test_a_failing_warmup_is_still_swallowed(tmp_path, monkeypatch):
    """Startup must not die because a model would not load."""
    monkeypatch.setenv(
        "HF_HUB_CACHE", str(_hub(tmp_path, "sentence-transformers/all-MiniLM-L6-v2")))
    monkeypatch.setattr(prov, "get_provider_name", lambda: "sentence-transformers")

    def boom(_q):
        raise RuntimeError("model is corrupt")

    monkeypatch.setattr(prov, "embed_query", boom)
    assert prov.warmup() == ""


def test_deferring_a_load_silences_progress_bars(tmp_path, monkeypatch):
    """⚠⚠ Skipping hands the chatter problem to the first tool call.

    By then stdout belongs to JSON-RPC and cannot be redirected, so a download
    printing a progress bar would corrupt framing — the exact hazard warmup was
    built to avoid. Silence it at the source when we choose to defer.
    """
    monkeypatch.setenv("HF_HUB_CACHE", str(_hub(tmp_path)))
    monkeypatch.setenv("JDOCMUNCH_ST_MODEL", "BAAI/bge-base-en-v1.5")
    monkeypatch.delenv("HF_HUB_DISABLE_PROGRESS_BARS", raising=False)
    monkeypatch.delenv("TQDM_DISABLE", raising=False)
    monkeypatch.setattr(prov, "get_provider_name", lambda: "sentence-transformers")

    assert prov.warmup() == ""
    import os
    assert os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"
    assert os.environ["TQDM_DISABLE"] == "1"


def test_a_users_own_progress_setting_is_not_overwritten(tmp_path, monkeypatch):
    """⚠ Only set what is unset — a user who configured these owns them."""
    monkeypatch.setenv("HF_HUB_CACHE", str(_hub(tmp_path)))
    monkeypatch.setenv("JDOCMUNCH_ST_MODEL", "BAAI/bge-base-en-v1.5")
    monkeypatch.setenv("HF_HUB_DISABLE_PROGRESS_BARS", "0")
    monkeypatch.setattr(prov, "get_provider_name", lambda: "sentence-transformers")

    prov.warmup()
    import os
    assert os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] == "0"
