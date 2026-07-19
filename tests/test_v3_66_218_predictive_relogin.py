"""F1.4 predictive relogin — tests (v3.66.218).

Two layers:
  1. The pure decision (`relogin_predict.predictive_relogin_due`): 0.8*median
     math, the MIN_OBSERVATIONS gate, fraction clamping, and fail-safe input
     handling.
  2. The runner hook (`SiteRunner.maybe_preemptive_relogin`): default-off is
     byte-identical to the old fixed-age behaviour; opt-in + enough data makes
     the prediction authoritative; thin data falls back; the predictor is
     fail-open.

run_tests.py-compatible: zero-arg functions, no pytest fixtures, module globals
restored in try/finally.
"""
import bulk_downloader.relogin_predict as rp
from bulk_downloader import db


# ───────────────────────── pure decision ─────────────────────────

def test_due_when_age_past_fraction_of_median():
    # median of [100,200,300] = 200; 0.8*200 = 160. age 170 >= 160 -> due.
    due, reason = rp.predictive_relogin_due(170, [100, 200, 300], fraction=0.8)
    assert due is True, reason


def test_not_due_when_age_below_fraction():
    # 0.8*200 = 160; age 150 < 160 -> not due (and that's authoritative).
    due, reason = rp.predictive_relogin_due(150, [100, 200, 300], fraction=0.8)
    assert due is False, reason


def test_exactly_at_threshold_is_due():
    due, reason = rp.predictive_relogin_due(160, [100, 200, 300], fraction=0.8)
    assert due is True, reason


def test_insufficient_observations_returns_none():
    # 2 < MIN_OBSERVATIONS(3) -> no opinion -> caller falls back.
    due, reason = rp.predictive_relogin_due(99999, [100, 200])
    assert due is None, reason
    assert "insufficient" in reason


def test_exactly_min_observations_forms_opinion():
    due, _ = rp.predictive_relogin_due(99999, [100, 200, 300])
    assert due is True


def test_median_even_count():
    # [100,200,300,400] median = 250; 0.8*250 = 200; age 199 -> not due.
    due, _ = rp.predictive_relogin_due(199, [100, 200, 300, 400], fraction=0.8)
    assert due is False
    due2, _ = rp.predictive_relogin_due(200, [100, 200, 300, 400], fraction=0.8)
    assert due2 is True


def test_fraction_clamped_high():
    # fraction 5.0 clamps to ceil 1.0 -> threshold = median = 200; age 199 not due.
    due, reason = rp.predictive_relogin_due(199, [100, 200, 300], fraction=5.0)
    assert due is False, reason
    due2, _ = rp.predictive_relogin_due(200, [100, 200, 300], fraction=5.0)
    assert due2 is True


def test_fraction_clamped_low():
    # fraction 0.0 clamps to floor 0.1 -> threshold = 0.1*200 = 20; age 25 due.
    due, reason = rp.predictive_relogin_due(25, [100, 200, 300], fraction=0.0)
    assert due is True, reason


def test_bad_observations_are_filtered():
    # None / strings / non-positive dropped; remaining 3 valid -> opinion formed.
    due, _ = rp.predictive_relogin_due(
        99999, [100, None, "x", -5, 0, 200, 300])
    assert due is True


def test_empty_observations_none():
    due, reason = rp.predictive_relogin_due(99999, [])
    assert due is None and "insufficient" in reason


def test_bad_age_returns_none_not_raise():
    due, reason = rp.predictive_relogin_due("not-a-number", [100, 200, 300])
    assert due is None and "bad age" in reason


def test_negative_age_returns_none():
    due, reason = rp.predictive_relogin_due(-10, [100, 200, 300])
    assert due is None and "negative" in reason


def test_all_nonpositive_observations_none():
    due, reason = rp.predictive_relogin_due(99999, [0, -1, -2, -3])
    assert due is None  # nothing survives the filter -> insufficient


# ───────────────────────── runner hook integration ─────────────────────────

class _FakeRunner:
    """Minimal stand-in exposing exactly what maybe_preemptive_relogin touches.
    Avoids booting the app; we call the unbound method with this as self."""
    def __init__(self, config, age_hours):
        self.config = config
        self.site_id = "site-x"
        self._active_account_idx = 0
        self._age_hours = age_hours
        self._preemptive_login_attempted_at = 0.0
        self.login_called = 0
        self.events = []

    def _cookie_age_hours(self):
        return self._age_hours

    def login_async(self):
        self.login_called += 1

    def log_event(self, kind, msg):
        self.events.append((kind, msg))


def _call_hook(config, age_hours):
    from bulk_downloader.runner import SiteRunner
    fr = _FakeRunner(config, age_hours)
    result = SiteRunner.maybe_preemptive_relogin(fr)
    return fr, result


def test_hook_opt_out_returns_false_no_login():
    # auto_preemptive_relogin off -> never acts (default behaviour).
    fr, result = _call_hook({"auto_preemptive_relogin": False,
                             "username": "u", "password": "p"}, age_hours=999)
    assert result is False and fr.login_called == 0


def test_hook_predictive_off_uses_fixed_threshold_below():
    # predictive off, age below fixed 168h -> no relogin (byte-identical old path).
    cfg = {"auto_preemptive_relogin": True, "username": "u", "password": "p",
           "cookie_max_age_hours": 168.0}
    fr, result = _call_hook(cfg, age_hours=100)
    assert result is False and fr.login_called == 0


def test_hook_predictive_off_uses_fixed_threshold_above():
    # predictive off, age above fixed 168h -> relogin fires (old path).
    cfg = {"auto_preemptive_relogin": True, "username": "u", "password": "p",
           "cookie_max_age_hours": 168.0}
    fr, result = _call_hook(cfg, age_hours=200)
    assert result is True and fr.login_called == 1


def test_hook_predictive_on_with_data_overrides_fixed():
    # Predictive ON + enough observations. median([2h,2h,2h]) in sec = 7200;
    # 0.8*7200 = 5760s = 1.6h. Cookie age 2h (=7200s) >= 5760 -> due, even though
    # the FIXED threshold (168h) would NOT have fired. Prediction is authoritative.
    saved = db.session_lifetime_observations
    try:
        db.session_lifetime_observations = lambda site, acct=None: [7200, 7200, 7200]
        cfg = {"auto_preemptive_relogin": True, "username": "u", "password": "p",
               "cookie_max_age_hours": 168.0, "predictive_relogin_enabled": True,
               "predictive_relogin_fraction": 0.8}
        fr, result = _call_hook(cfg, age_hours=2.0)
        assert result is True and fr.login_called == 1, fr.events
    finally:
        db.session_lifetime_observations = saved


def test_hook_predictive_on_not_yet_due_blocks_fixed():
    # Predictive ON, enough data, but age below 0.8*median -> NOT due, and that
    # is authoritative: even if fixed threshold were tiny, predictor says no.
    saved = db.session_lifetime_observations
    try:
        db.session_lifetime_observations = lambda site, acct=None: [7200, 7200, 7200]
        cfg = {"auto_preemptive_relogin": True, "username": "u", "password": "p",
               "cookie_max_age_hours": 0.0,  # fixed would fire immediately...
               "predictive_relogin_enabled": True, "predictive_relogin_fraction": 0.8}
        # age 1h = 3600s < 5760s threshold -> predictor returns False -> blocked.
        fr, result = _call_hook(cfg, age_hours=1.0)
        assert result is False and fr.login_called == 0, fr.events
    finally:
        db.session_lifetime_observations = saved


def test_hook_predictive_on_thin_data_falls_back_to_fixed():
    # Predictive ON but < MIN_OBSERVATIONS -> None -> fixed-age fallback used.
    saved = db.session_lifetime_observations
    try:
        db.session_lifetime_observations = lambda site, acct=None: [7200]  # 1 obs
        cfg = {"auto_preemptive_relogin": True, "username": "u", "password": "p",
               "cookie_max_age_hours": 168.0, "predictive_relogin_enabled": True}
        # age 100h < 168h fixed -> no relogin (fell back to fixed, didn't fire).
        fr, result = _call_hook(cfg, age_hours=100)
        assert result is False and fr.login_called == 0, fr.events
        # age 200h > 168h fixed -> fires via fallback.
        fr2, result2 = _call_hook(cfg, age_hours=200)
        assert result2 is True and fr2.login_called == 1, fr2.events
    finally:
        db.session_lifetime_observations = saved


def test_hook_predictive_failopen_on_db_error():
    # Predictor raises -> caught -> falls back to fixed threshold.
    saved = db.session_lifetime_observations
    try:
        def _boom(site, acct=None):
            raise RuntimeError("db down")
        db.session_lifetime_observations = _boom
        cfg = {"auto_preemptive_relogin": True, "username": "u", "password": "p",
               "cookie_max_age_hours": 168.0, "predictive_relogin_enabled": True}
        fr, result = _call_hook(cfg, age_hours=200)  # above fixed -> fires via fallback
        assert result is True and fr.login_called == 1, fr.events
        # and a diagnostic event was logged
        assert any("predictive check failed" in m for _, m in fr.events), fr.events
    finally:
        db.session_lifetime_observations = saved


def test_hook_no_cookie_age_returns_false():
    cfg = {"auto_preemptive_relogin": True, "username": "u", "password": "p"}
    fr, result = _call_hook(cfg, age_hours=None)
    assert result is False and fr.login_called == 0


# ─────────────── FINDING 7 (v3.66.267): predictive implies the gate ───────────────
# RED-first: on pristine 266 `maybe_preemptive_relogin` gates ONLY on
# `auto_preemptive_relogin`, so setting `predictive_relogin_enabled` alone
# silently no-ops (the OPV-F1.4 footgun). After the fix, either flag arms it.

def test_hook_predictive_enabled_alone_arms_gate():
    # predictive_relogin_enabled True, auto_preemptive_relogin ABSENT. With creds,
    # >=3 obs, and age past 0.8*median, the preemptive relogin must FIRE.
    # median 7200s, 0.8*7200=5760s=1.6h; age 2h(=7200s) >= threshold -> due.
    saved = db.session_lifetime_observations
    try:
        db.session_lifetime_observations = lambda site, acct=None: [7200, 7200, 7200]
        cfg = {"username": "u", "password": "p",
               "cookie_max_age_hours": 168.0,
               "predictive_relogin_enabled": True,
               "predictive_relogin_fraction": 0.8}
        # auto_preemptive_relogin intentionally NOT set (default off).
        fr, result = _call_hook(cfg, age_hours=2.0)
        assert result is True and fr.login_called == 1, fr.events
    finally:
        db.session_lifetime_observations = saved


def test_hook_predictive_enabled_alone_respects_not_due():
    # Gate opens via predictive flag, but the predictive decision still governs:
    # age below 0.8*median -> NOT due -> no fire.
    saved = db.session_lifetime_observations
    try:
        db.session_lifetime_observations = lambda site, acct=None: [7200, 7200, 7200]
        cfg = {"username": "u", "password": "p",
               "cookie_max_age_hours": 0.0,  # fixed would fire, but predictive governs
               "predictive_relogin_enabled": True, "predictive_relogin_fraction": 0.8}
        fr, result = _call_hook(cfg, age_hours=1.0)  # 3600s < 5760s -> not due
        assert result is False and fr.login_called == 0, fr.events
    finally:
        db.session_lifetime_observations = saved


def test_hook_neither_flag_with_data_still_noop():
    # Pins that the feature is FLAG-gated, not data-gated: observations present
    # but both flags off -> no fire (default-off stays byte-identical).
    saved = db.session_lifetime_observations
    try:
        db.session_lifetime_observations = lambda site, acct=None: [7200, 7200, 7200]
        cfg = {"username": "u", "password": "p", "cookie_max_age_hours": 168.0,
               "auto_preemptive_relogin": False, "predictive_relogin_enabled": False}
        fr, result = _call_hook(cfg, age_hours=200)  # even above fixed -> still no-op
        assert result is False and fr.login_called == 0, fr.events
    finally:
        db.session_lifetime_observations = saved
