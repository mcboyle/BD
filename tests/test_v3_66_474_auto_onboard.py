"""A4 -- auto-onboard prep on site create/update (v3.66.474).

api_template_onboard exists but is operator-triggered. A4 adds a GATED site
lifecycle hook that, on create/update, classifies onboarding (via the pure
plan_site) and STAGES a draft intent -- PREP ONLY: never enables a template and
never launches the live capture here (first-time enable stays the one manual
atom). Default OFF -> byte-identical no-op.

Contract proven here (RED-first -- auto_onboard does not exist yet):
  1. toggle OFF -> skipped, cfg untouched.
  2. toggle ON + a capture-required site -> onboarding=capture_required,
     a pending-capture intent staged, NEVER enabled, teach-first-run forced off.
  3. toggle ON + no usable URL -> staged False.
  4. the hook NEVER sets status="enabled".

Zero-arg + tempfile so this runs under run_tests.py AND pytest.
"""
import contextlib

from bulk_downloader import auto_onboard as ao
from bulk_downloader import lifecycle_automation as la


@contextlib.contextmanager
def _toggles(on_set):
    orig = la._read_toggle
    on_keys = {la.AUTOMATION_TOGGLES[n] for n in on_set}
    la._read_toggle = lambda key: key in on_keys
    try:
        yield
    finally:
        la._read_toggle = orig


def _site(url="https://brand-new-site.example/videos"):
    return {"id": "newsite", "url": url, "status": "active"}


def test_off_is_noop():
    cfg = _site()
    before = dict(cfg)
    r = ao.auto_onboard_on_site_change(cfg)
    assert r.get("skipped") == "auto_onboard disabled", r
    assert cfg == before, "cfg must be untouched when the toggle is off"


def test_on_capture_required_stages_intent_never_enables():
    cfg = _site()
    with _toggles(on_set={"auto_onboard"}):
        r = ao.auto_onboard_on_site_change(cfg)
    assert r["ok"] is True, r
    assert r["onboarding"] == "capture_required", r
    assert r["staged"] is True, r
    assert r["enabled"] is False, r
    # the staged intent is recorded on the cfg, prep-only
    assert cfg.get("onboard_pending", {}).get("state") == "capture_required", cfg
    assert cfg.get("auto_teach_first_run") is False, cfg
    # NEVER enabled
    assert cfg.get("status") != "enabled"


def test_on_no_usable_url_stages_nothing():
    cfg = {"id": "x", "url": "", "status": "active"}
    with _toggles(on_set={"auto_onboard"}):
        r = ao.auto_onboard_on_site_change(cfg)
    assert r["ok"] is True, r
    assert r.get("staged") is False, r


def test_hook_never_enables_a_template():
    cfg = _site()
    with _toggles(on_set={"auto_onboard"}):
        ao.auto_onboard_on_site_change(cfg)
    # the cfg's template-onboarding mode must be a review/capture path, not enabled
    assert cfg.get("template_onboarding") in ("capture_required", "approved_template_found")
    assert cfg.get("template_auto_detect_mode") != "enabled"
