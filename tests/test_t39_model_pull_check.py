"""T39 (Tier 4) — D-58 model-pull helper.

Verifies Ollama-installed models against the configured/requested
set. Read-only. NEVER hardcodes model tags — pulls them from
OllamaProvider.default_models + _app_cfg.

Tests use synthetic OllamaProvider patches so we don't hit a real
Ollama server.
"""
import json
import contextlib

from bulk_downloader import dev_suite as ds


# ── Patching helpers ────────────────────────────────────────────────

@contextlib.contextmanager
def _fake_ollama(installed_models):
    """Patch OllamaProvider.list_models to return a synthetic list.
    `installed_models` may be None (endpoint unreachable) or a list."""
    from bulk_downloader import ai_provider as ap
    original = ap.OllamaProvider.list_models
    ap.OllamaProvider.list_models = lambda self: installed_models
    try:
        yield
    finally:
        ap.OllamaProvider.list_models = original


def test_model_pull_check_explicit_models_all_present(clean_workdir):
    with _fake_ollama(["alpha:1b", "beta:7b"]):
        r = ds.model_pull_check(models=["alpha:1b", "beta:7b"])
    assert r["ok"] is True
    assert r["tool"] == "model_pull_check"
    assert r["missing"] == []
    assert "alpha:1b" in r["installed"]
    assert "all" in r["verdict"]


def test_model_pull_check_explicit_models_some_missing(clean_workdir):
    with _fake_ollama(["alpha:1b"]):
        r = ds.model_pull_check(models=["alpha:1b", "beta:7b"])
    assert r["ok"] is True
    assert "beta:7b" in r["missing"]
    assert "alpha:1b" not in r["missing"]


def test_model_pull_check_pull_command_for_each_missing(clean_workdir):
    with _fake_ollama([]):
        r = ds.model_pull_check(models=["delta:13b", "epsilon:7b"])
    assert len(r["pull_commands"]) == 2
    for cmd in r["pull_commands"]:
        assert cmd.startswith("ollama pull '")
        assert cmd.endswith("'")


def test_model_pull_check_endpoint_unreachable(clean_workdir):
    # list_models returns None on connect failure (per OllamaProvider
    # contract)
    with _fake_ollama(None):
        r = ds.model_pull_check(models=["x:1b"])
    assert r["ok"] is True
    assert r["installed"] == []
    assert "could not reach" in r["verdict"]


def test_model_pull_check_falls_back_to_provider_defaults(clean_workdir):
    # No explicit models → should use OllamaProvider.default_models
    with _fake_ollama([]):
        r = ds.model_pull_check()
    # default_models has "vision" and "text" keys → at least 2 reqs
    assert len(r["requested"]) >= 1
    # NOT hardcoded — comes from the provider class
    from bulk_downloader import ai_provider as ap
    defaults = list(ap.OllamaProvider.default_models.values())
    for d in defaults:
        assert d in r["requested"]


def test_model_pull_check_reports_extras(clean_workdir):
    with _fake_ollama(["alpha:1b", "beta:7b", "gamma:3b"]):
        r = ds.model_pull_check(models=["alpha:1b"])
    # gamma and beta are installed but not requested
    assert "beta:7b" in r["extra"]
    assert "gamma:3b" in r["extra"]


def test_model_pull_check_no_models_anywhere(clean_workdir, monkeypatch):
    # Force the default-derivation path to find nothing
    from bulk_downloader import ai_provider as ap
    monkeypatch.setattr(ap.OllamaProvider, "default_models", {})
    with _fake_ollama([]):
        r = ds.model_pull_check()
    assert r["ok"] is False
    assert "no models requested" in r["verdict"]


def test_model_pull_check_endpoint_uses_provider_default(clean_workdir):
    with _fake_ollama([]):
        r = ds.model_pull_check(models=["x:1b"])
    # Endpoint should be reported even on failure
    assert r["endpoint"]
    assert "://" in r["endpoint"]


def test_model_pull_check_is_json_serializable(clean_workdir):
    with _fake_ollama(["a:1b"]):
        r = ds.model_pull_check(models=["a:1b"])
    json.dumps(r)
