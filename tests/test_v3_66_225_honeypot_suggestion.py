"""F3.4 — advisory per-site honeypot drop-threshold suggestion.

Pins the SURFACING-ONLY contract:
  * ``_m2_honeypot_suggestion`` is fail-soft and evidence-gated.
  * ``/api/sites/v2`` carries the two additive fields on every entry.
  * The suggestion NEVER changes drop behaviour — there is no write path
    here; the live filter remains the opt-in BD_HONEYPOT_SCORE_THRESHOLD.

Per the 224 test-hygiene learning (DB-seeded cross-test state is
unreliable under run_tests.py's per-test chdir), this injects a fake
runner into the module globals and restores them in ``finally`` rather
than seeding history rows. The threshold MATH is exercised against the
pure engine directly, where it is deterministic.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.bd_module_wipe


# ── helper: fail-soft + evidence gate ──────────────────────────────────

def test_helper_failsoft_unknown_site():
    """A site with no trap history (or a bogus id) yields (None, 0) —
    the chip stays hidden, nothing throws."""
    from bulk_downloader import app as a
    suggested, samples = a._m2_honeypot_suggestion("no-such-site-id")
    assert suggested is None
    assert samples == 0


# ── engine math (deterministic, no DB) ─────────────────────────────────

def test_engine_under_min_samples_returns_default():
    """Below DEFAULT_MIN_SAMPLES the learner must return the caller's
    default unchanged — that's what keeps the suggestion None in the
    helper's gate."""
    from bulk_downloader import honeypot_threshold as h
    assert h.learn_threshold([0.7, 0.8], default=0.8) == 0.8


def test_engine_learns_clamped_quantile():
    """With >= MIN_SAMPLES trap scores the learner returns a low-quantile
    threshold, clamped to [FLOOR, CEIL]. Advisory value sits at/under the
    production default (0.8) — it tightens, never loosens past the ceil."""
    from bulk_downloader import honeypot_threshold as h
    thr = h.learn_threshold(
        [0.7, 0.72, 0.8, 0.85, 0.9, 0.95], default=0.8
    )
    assert h.THRESHOLD_FLOOR <= thr <= h.THRESHOLD_CEIL
    assert thr <= 0.8


# ── additive route-entry contract ──────────────────────────────────────

class _FakeRunner:
    cookies: list = []
    _captcha_pending = False
    _event_log: list = []

    def state(self):
        return "idle"

    def get_status(self, light=False):
        return {"counts": {"done": 0}, "active": 0}


def test_sites_v2_entry_carries_additive_fields():
    """Every /api/sites/v2 entry must expose the two advisory fields so
    the SPA chip can branch on them. Cold DB => no traps => suggested is
    None, samples is an int. Inject a fake runner; restore globals."""
    from bulk_downloader import app as a

    saved_runners = dict(a.runners)
    saved_cfg = dict(a.s_cfg)
    try:
        a.runners["fake-site"] = _FakeRunner()
        a.s_cfg["fake-site"] = {"name": "Fake Site"}
        body = a.app.test_client().get("/api/sites/v2").get_json()
        assert body.get("ok") is True
        entry = next(
            (e for e in body.get("sites", []) if e["site_id"] == "fake-site"),
            None,
        )
        assert entry is not None, "injected site missing from /api/sites/v2"
        assert "honeypot_threshold_suggested" in entry
        assert "honeypot_threshold_samples" in entry
        # cold DB: no confirmed traps -> hidden chip, integer count
        assert entry["honeypot_threshold_suggested"] is None
        assert isinstance(entry["honeypot_threshold_samples"], int)
    finally:
        a.runners.clear()
        a.runners.update(saved_runners)
        a.s_cfg.clear()
        a.s_cfg.update(saved_cfg)
