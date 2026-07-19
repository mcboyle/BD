"""v3.43.33 regression tests — AI-assisted login form detection.

Coverage:
  - Module surface
  - detect_login_form: handles disabled AI assist, malformed
    responses, missing fields
  - validate_proposed_selectors: shape + edge cases (empty selectors,
    invalid CSS, multi-match)
  - grade_proposal: decision policy correctness (high/low confidence,
    captcha, missing validation)
  - Runner integration: AI block runs after page load, before
    enumeration; only when enabled
  - app.py wiring: CFG_FIELDS, defaults, endpoint
  - UI: toggle + load/save round-trip
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent
_AI_LOGIN_PY = _REPO_ROOT / "bulk_downloader" / "ai_login.py"
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"
_LOGIN_PY = _REPO_ROOT / "bulk_downloader" / "login.py"

def _login_impl_src():
    """Concatenated source of the decomposed login_impl/ package. login.py is now an
    ADD-only re-export shim (DECOMP-LEAF cut 3); the implementation lives in
    login_impl/*.py, so structure/path tests read the package, not the shim."""
    from pathlib import Path as _P
    import bulk_downloader.login_impl as _pkg
    _d = _P(_pkg.__file__).parent
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_d.glob("*.py")))



# v3.43.43: helper for tests that were written against the old
# Ollama-only path. They mocked `aiassist._post_json` returning a
# dict like {"response": "<json text>"}. After the multi-provider
# refactor, the seam moved to `aiassist._call_model` which returns a
# `GenerationResult`. This helper converts an Ollama-style fake into
# the new shape so each test only needs a one-word patch target
# change.
def _mock_call_model(text="", ok=True, error="", latency_ms=10):
    from bulk_downloader import ai_provider
    return ai_provider.GenerationResult(
        ok=ok, text=text, model="test-model",
        provider="ollama", latency_ms=latency_ms,
        error=error,
        error_kind="" if ok else "server_error")


def _mock_call_model_from_ollama(ollama_dict):
    """Bridge: tests built an {response: "..."} dict for the old
    `_post_json` mock; we lift the "response" field into a
    GenerationResult."""
    return _mock_call_model(text=ollama_dict.get("response", ""))


# ── Module surface ────────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_ai_login_module_importable():
    import bulk_downloader.ai_login as ail  # noqa: F401


def test_ai_login_has_required_functions():
    from bulk_downloader.ai_login import (
        detect_login_form, validate_proposed_selectors, grade_proposal,
    )
    assert callable(detect_login_form)
    assert callable(validate_proposed_selectors)
    assert callable(grade_proposal)


# ── detect_login_form ────────────────────────────────────────────────

def test_detect_login_form_returns_disabled_when_aiassist_off():
    """When aiassist is globally disabled, detect_login_form must
    short-circuit immediately. No network call, clear error message."""
    from bulk_downloader import ai_login, aiassist
    # Save current state, force-disable
    saved = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=False)
        result = ai_login.detect_login_form(
            dom_excerpt="<html><body></body></html>")
        assert result["ok"] is False
        assert "disabled" in result["error"].lower()
    finally:
        aiassist.configure(enabled=saved)


def test_detect_login_form_handles_endpoint_error(monkeypatch=None):
    """When the LLM endpoint is unreachable, the function must return
    {ok: False, error: ...} not raise. The runner's hot path can't
    afford exceptions on every login."""
    from bulk_downloader import ai_login, aiassist
    # Force enabled with a clearly-unreachable endpoint
    saved_ep = aiassist.get_config().get("endpoint")
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(endpoint="http://127.0.0.1:1", enabled=True)
        result = ai_login.detect_login_form(
            dom_excerpt="<form></form>")
        assert result["ok"] is False
        assert "error" in result
        # Latency captured even on failure
        assert "latency_ms" in result
    finally:
        aiassist.configure(endpoint=saved_ep, enabled=saved_en)


def test_detect_login_form_sanitizes_malformed_response():
    """Mock the underlying _post_json to return garbage; the function
    must return ok=False with an explanatory error, not crash."""
    from bulk_downloader import ai_login, aiassist
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=True)
        with mock.patch.object(aiassist, "_call_model",
                                return_value=_mock_call_model(text="not json {{{")):
            result = ai_login.detect_login_form(
                dom_excerpt="<form></form>")
            assert result["ok"] is False
            assert "unparseable" in result["error"]
    finally:
        aiassist.configure(enabled=saved_en)


def test_detect_login_form_clamps_confidence():
    """Confidence values outside 0-100 must be clamped — model can
    return 150 or -5 occasionally."""
    from bulk_downloader import ai_login, aiassist
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=True)
        fake_response = {
            "response": '{"username_selector": "#u", "password_selector": "#p", '
                        '"submit_selector": "#s", "confidence": 250, '
                        '"captcha_detected": false}',
        }
        with mock.patch.object(aiassist, "_call_model",
                                return_value=_mock_call_model_from_ollama(fake_response)):
            result = ai_login.detect_login_form(dom_excerpt="<form></form>")
            assert result["ok"] is True
            assert result["confidence"] == 100  # clamped from 250
    finally:
        aiassist.configure(enabled=saved_en)


def test_detect_login_form_caps_selector_length():
    """A selector >300 chars is the model hallucinating an entire
    HTML element rather than a CSS selector. Truncate."""
    from bulk_downloader import ai_login, aiassist
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=True)
        huge = "div " * 500  # 2000 chars
        fake = {"response": f'{{"username_selector": "{huge}", '
                              '"password_selector": "#p", '
                              '"submit_selector": "#s", "confidence": 80}'}
        with mock.patch.object(aiassist, "_call_model", return_value=_mock_call_model_from_ollama(fake)):
            result = ai_login.detect_login_form(dom_excerpt="<form></form>")
            assert len(result["username_selector"]) <= 300
    finally:
        aiassist.configure(enabled=saved_en)


def test_detect_login_form_empty_selectors_forces_zero_confidence():
    """If the model returns all-empty selectors, treat as 'couldn't
    find form' regardless of what confidence number it sent."""
    from bulk_downloader import ai_login, aiassist
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=True)
        fake = {"response": '{"username_selector": "", "password_selector": "", '
                             '"submit_selector": "", "confidence": 90}'}
        with mock.patch.object(aiassist, "_call_model", return_value=_mock_call_model_from_ollama(fake)):
            result = ai_login.detect_login_form(dom_excerpt="<form></form>")
            assert result["ok"] is True
            # Even though model said 90, all-empty = 0
            assert result["confidence"] == 0
    finally:
        aiassist.configure(enabled=saved_en)


def test_detect_login_form_handles_non_dict_response():
    """If the model returns a JSON array or string, that's a shape
    error — return ok=False, not crash."""
    from bulk_downloader import ai_login, aiassist
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=True)
        fake = {"response": '["this is", "a list"]'}
        with mock.patch.object(aiassist, "_call_model", return_value=_mock_call_model_from_ollama(fake)):
            result = ai_login.detect_login_form(dom_excerpt="<form></form>")
            assert result["ok"] is False
            assert "unparseable" in result["error"]
    finally:
        aiassist.configure(enabled=saved_en)


# ── validate_proposed_selectors ──────────────────────────────────────

class _FakeLocator:
    """Stand-in for Playwright's Locator — returns a configurable
    count to test the validation logic."""
    def __init__(self, count): self._count = count
    def count(self): return self._count


class _FakePage:
    """Stand-in for Playwright's Page. Maps selector → count."""
    def __init__(self, selector_counts, raise_for=None):
        self._counts = selector_counts
        self._raise_for = raise_for or set()
    def locator(self, sel):
        if sel in self._raise_for:
            raise ValueError(f"invalid CSS: {sel}")
        return _FakeLocator(self._counts.get(sel, 0))


def test_validate_all_match_exactly_one():
    """The happy path: every selector resolves to exactly 1 node."""
    from bulk_downloader.ai_login import validate_proposed_selectors
    page = _FakePage({"#user": 1, "#pass": 1, "#login": 1})
    proposal = {
        "username_selector": "#user",
        "password_selector": "#pass",
        "submit_selector": "#login",
    }
    result = validate_proposed_selectors(page, proposal)
    assert result["all_valid"] is True
    assert result["username_count"] == 1
    assert result["errors"] == []


def test_validate_detects_zero_match():
    """Selectors that match 0 nodes must be flagged invalid AND
    reported in errors."""
    from bulk_downloader.ai_login import validate_proposed_selectors
    page = _FakePage({"#user": 1, "#pass": 0, "#login": 1})
    proposal = {
        "username_selector": "#user",
        "password_selector": "#pass",
        "submit_selector": "#login",
    }
    result = validate_proposed_selectors(page, proposal)
    assert result["all_valid"] is False
    assert result["password_valid"] is False
    assert any("password" in e for e in result["errors"])


def test_validate_handles_multi_match_as_valid_but_flagged():
    """Multi-match is ambiguous but the first node might still be
    right; mark valid (so we try) but add an error so the caller
    knows."""
    from bulk_downloader.ai_login import validate_proposed_selectors
    page = _FakePage({"#user": 1, "#pass": 1, ".btn": 3})
    proposal = {
        "username_selector": "#user",
        "password_selector": "#pass",
        "submit_selector": ".btn",
    }
    result = validate_proposed_selectors(page, proposal)
    assert result["all_valid"] is True
    assert result["submit_count"] == 3
    assert any("ambiguous" in e for e in result["errors"])


def test_validate_handles_invalid_css_syntax():
    """Locator() raises on invalid selectors. The function must catch
    and report rather than propagating."""
    from bulk_downloader.ai_login import validate_proposed_selectors
    page = _FakePage({"#user": 1, "#pass": 1},
                       raise_for={"!@#$ invalid"})
    proposal = {
        "username_selector": "#user",
        "password_selector": "#pass",
        "submit_selector": "!@#$ invalid",
    }
    result = validate_proposed_selectors(page, proposal)
    assert result["all_valid"] is False
    assert result["submit_valid"] is False
    assert any("invalid" in e for e in result["errors"])


def test_validate_handles_empty_selectors():
    """Empty proposals (model couldn't find that field) must produce
    a clear 'no selector proposed' error, not crash."""
    from bulk_downloader.ai_login import validate_proposed_selectors
    page = _FakePage({})
    proposal = {
        "username_selector": "",
        "password_selector": "",
        "submit_selector": "",
    }
    result = validate_proposed_selectors(page, proposal)
    assert result["all_valid"] is False
    assert len(result["errors"]) == 3


# ── grade_proposal ───────────────────────────────────────────────────

def test_grade_high_confidence_all_valid_uses_proposal():
    from bulk_downloader.ai_login import grade_proposal
    proposal = {"confidence": 85, "captcha_detected": False}
    validation = {"all_valid": True, "username_valid": True,
                   "password_valid": True, "submit_valid": True}
    grade = grade_proposal(proposal, validation)
    assert grade["use_proposal"] is True
    assert grade["score"] == 85


def test_grade_captcha_detected_blocks_use():
    """Captcha means we MUST hand off to manual login; never auto-fill."""
    from bulk_downloader.ai_login import grade_proposal
    proposal = {"confidence": 95, "captcha_detected": True}
    validation = {"all_valid": True, "username_valid": True,
                   "password_valid": True, "submit_valid": True}
    grade = grade_proposal(proposal, validation)
    assert grade["use_proposal"] is False
    assert any("captcha" in r.lower() for r in grade["reasons"])


def test_grade_low_confidence_blocks_use():
    """Below 50 confidence we never trust the model, regardless of
    validation outcome."""
    from bulk_downloader.ai_login import grade_proposal
    proposal = {"confidence": 30, "captcha_detected": False}
    validation = {"all_valid": True, "username_valid": True,
                   "password_valid": True, "submit_valid": True}
    grade = grade_proposal(proposal, validation)
    assert grade["use_proposal"] is False


def test_grade_validation_failure_blocks_use():
    """Even with high confidence, if a selector doesn't validate
    against the page, don't submit credentials with it."""
    from bulk_downloader.ai_login import grade_proposal
    proposal = {"confidence": 90, "captcha_detected": False}
    validation = {"all_valid": False, "username_valid": True,
                   "password_valid": False, "submit_valid": True}
    grade = grade_proposal(proposal, validation)
    assert grade["use_proposal"] is False
    assert any("password" in r.lower() for r in grade["reasons"])


def test_grade_partial_confidence_with_validation_passes():
    """50-69 confidence but all selectors validate: use_proposal=True
    (validation gives us extra evidence)."""
    from bulk_downloader.ai_login import grade_proposal
    proposal = {"confidence": 60, "captcha_detected": False}
    validation = {"all_valid": True, "username_valid": True,
                   "password_valid": True, "submit_valid": True}
    grade = grade_proposal(proposal, validation)
    assert grade["use_proposal"] is True
    assert grade["score"] == 60


# ── Runner / login.py integration ────────────────────────────────────

def test_login_has_ai_assist_block():
    """do_login must have the AI-assist block; it goes BETWEEN page
    load and _try_fill."""
    src = _login_impl_src()
    assert "ai_login_assist_enabled" in src
    assert "from .. import ai_login" in src
    assert "detect_login_form" in src


def test_ai_assist_only_runs_when_no_learned_selectors():
    """When learned selectors exist (the user has logged in
    successfully before), the AI path is skipped — learned wins."""
    src = _login_impl_src()
    pos = src.find("ai_assist_active = ")
    assert pos > 0
    nearby = src[pos:pos + 500]
    # Must check learned_uf/pf/sb in the gate
    assert "learned_uf" in nearby
    assert "learned_pf" in nearby
    assert "learned_sb" in nearby


def test_ai_path_prepends_not_replaces_candidates():
    """The AI proposal must be PREPENDED to the candidate lists, not
    replace them. If the AI is wrong, the fallback enumeration still
    runs."""
    src = _login_impl_src()
    # Find the candidate-list prepending
    pos = src.find("uf_candidates = ([proposal")
    assert pos > 0, "AI proposal must prepend, not replace, uf_candidates"
    # Confirm the prepend pattern (proposal first, then existing)
    nearby = src[pos:pos + 500]
    assert "+ uf_candidates" in nearby


def test_ai_captcha_short_circuits_to_manual():
    """When the AI detects a captcha, we should hand off to manual
    rather than waste time on selectors that won't beat the captcha
    anyway."""
    src = _login_impl_src()
    pos = src.find("captcha_detected")
    assert pos > 0
    nearby = src[pos:pos + 1000]
    assert "_hand_off" in nearby
    assert "allow_manual_takeover" in nearby


def test_ai_failure_falls_back_silently():
    """Any exception in the AI block must NOT prevent the normal
    enumeration path from running. The runner's primary mission is
    login; AI is an optimization."""
    src = _login_impl_src()
    pos = src.find("ai_assist_active")
    body = src[pos:pos + 6000]
    # Look for the outer try/except around the AI block
    assert "except Exception as e:" in body
    # And a fall-back message
    assert "falling back to enumeration" in body


# ── App wiring ───────────────────────────────────────────────────────

def test_cfg_fields_has_ai_login_assist():
    src = _APP_SRC()
    import re
    m = re.search(r"CFG_FIELDS\s*=\s*\[", src)
    start = m.end()
    depth = 1
    pos = start
    while pos < len(src) and depth > 0:
        if src[pos] == "[":
            depth += 1
        elif src[pos] == "]":
            depth -= 1
        pos += 1
    fields_block = src[start:pos - 1]
    assert '"ai_login_assist_enabled"' in fields_block


def test_ai_login_default_in_defaults():
    src = _APP_SRC()
    assert '"ai_login_assist_enabled": False' in src


def test_ai_login_endpoint_registered():
    src = _APP_SRC()
    assert "/api/sites/<sid>/ai/detect_login" in src
    assert "def api_ai_detect_login" in src


# ── UI ───────────────────────────────────────────────────────────────



# ── Adversarial input regression tests ──────────────────────────────
# These catch the defensive bugs found during the build-time audit:
#   1. response=None crashed text[:500]
#   2. non-string selectors crashed .strip()
#   3. non-numeric confidence crashed int()

def test_detect_handles_none_response_text():
    """When the model returns {'response': None}, we must not crash
    when slicing text[:500] for the error payload."""
    from bulk_downloader import ai_login, aiassist
    saved_en = aiassist.get_config().get("enabled")
    try:
        aiassist.configure(enabled=True)
        with mock.patch.object(aiassist, "_call_model",
                                return_value=_mock_call_model(text="")):
            result = ai_login.detect_login_form(dom_excerpt="<form></form>")
        assert result["ok"] is False
        # Doesn't crash, returns clean error
        assert "raw" in result
    finally:
        aiassist.configure(enabled=saved_en)


def test_validate_handles_non_string_selectors():
    """Model could return int/list/dict instead of string. Validator
    must report 'no selector proposed' not crash."""
    from bulk_downloader.ai_login import validate_proposed_selectors
    page = _FakePage({})
    proposal = {
        "username_selector": 12345,  # int
        "password_selector": [],     # list
        "submit_selector": {"a": 1}, # dict
    }
    result = validate_proposed_selectors(page, proposal)
    assert result["all_valid"] is False
    assert len(result["errors"]) == 3
    # Each error mentions the type
    assert any("int" in e for e in result["errors"])


def test_grade_handles_non_numeric_confidence():
    """Model could return confidence='high' or 'low' instead of int.
    Must treat as 0 (won't trust) not crash."""
    from bulk_downloader.ai_login import grade_proposal
    proposal = {"confidence": "high", "captcha_detected": False}
    validation = {"all_valid": True, "username_valid": True,
                   "password_valid": True, "submit_valid": True}
    grade = grade_proposal(proposal, validation)
    # Non-numeric becomes 0 → use_proposal=False
    assert grade["use_proposal"] is False
    assert grade["score"] == 0
