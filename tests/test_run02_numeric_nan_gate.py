"""F-RUN02-01 -- the config-sourced numeric safety gates in runner_transport and
runner_auth must reject non-finite (NaN/inf) so they cannot be silently disabled.

(1) runner_transport size-sanity gate (Phase 17.20): min_size_pct feeds
    ``ratio < min_pct``. A NaN min_size_pct makes ``ratio < nan`` False, so a
    wildly-undersized download (2MB served for an advertised 100MB -- an error
    page / login wall) is ACCEPTED as done instead of quarantined.

(2) runner_auth fixed-age pre-emptive-relogin gate: cookie_max_age_hours feeds
    ``age < max_age``. An inf value makes ``age < inf`` always True, so relogin
    is silently DISABLED (cookies never refreshed); a NaN value makes
    ``age < nan`` False, so relogin over-fires regardless of cookie age.

RED on the pinned (pre-fix) source; GREEN once each float() is coerced through a
math.isfinite backstop (_finite_config_float) that falls back to the default.
"""
import math
import types

import bulk_downloader.runner_transport as rt
import bulk_downloader.runner_auth as ra


# ── (2) runner_auth relogin gate: behavioral via maybe_preemptive_relogin ─────
def _relogin_self(cookie_max_age_hours, age_hours):
    """Minimal fake SiteRunner for AuthMixin.maybe_preemptive_relogin, forced
    onto the FIXED-AGE fallback path (predictive relogin left disabled)."""
    calls = {"login_async": 0}
    fake = types.SimpleNamespace(
        config={
            "auto_preemptive_relogin": True,
            "username": "u", "password": "p",
            "cookie_max_age_hours": cookie_max_age_hours,
            # predictive_relogin_enabled unset -> due stays None -> fixed-age path
        },
        _preemptive_login_attempted_at=0.0,
        log_event=lambda *a, **k: None,
        login_async=lambda: calls.__setitem__("login_async", calls["login_async"] + 1),
    )
    fake._cookie_age_hours = lambda: age_hours
    fired = ra.AuthMixin.maybe_preemptive_relogin(fake)
    return fired, calls


def test_inf_cookie_max_age_still_relogins_when_old():
    # cookies 200h old; a finite/coerced 168h threshold -> relogin fires.
    fired, calls = _relogin_self(float("inf"), 200.0)
    assert fired is True, "inf cookie_max_age_hours silently DISABLED pre-emptive relogin"
    assert calls["login_async"] == 1


def test_nan_cookie_max_age_does_not_overfire_when_fresh():
    # cookies only 50h old; a coerced 168h threshold -> relogin must NOT fire.
    fired, calls = _relogin_self(float("nan"), 50.0)
    assert fired is False, "NaN cookie_max_age_hours over-fired relogin on fresh cookies"
    assert calls["login_async"] == 0


def test_finite_cookie_max_age_relogins_when_old():
    # control: finite 168h threshold, 200h-old cookies -> relogin fires.
    fired, calls = _relogin_self(168.0, 200.0)
    assert fired is True
    assert calls["login_async"] == 1


def test_finite_cookie_max_age_skips_when_fresh():
    # control: finite 168h threshold, 50h-old cookies -> no relogin.
    fired, calls = _relogin_self(168.0, 50.0)
    assert fired is False
    assert calls["login_async"] == 0


# ── (1) runner_transport size-sanity gate: gate semantics via the coercion ────
def test_size_sanity_coerced_min_pct_rejects_undersized():
    # 2MB served for an advertised 100MB -> ratio ~2%. A NaN min_size_pct evades
    # the gate (ratio < nan is False); the finite-coerced default (5%) rejects it.
    expected = 100 * 1024 * 1024
    downloaded = 2 * 1024 * 1024
    ratio = (downloaded / expected) * 100
    assert not (ratio < float("nan")), "sanity: NaN min_size_pct evades ratio<min_pct"
    min_pct = rt._finite_config_float("nan", 5.0)
    assert ratio < min_pct, "coerced min_size_pct must reject a ~2% undersized download"


# ── coercion helper unit tests (duplicated helper, one per module) ────────────
def _helper_cases(fn):
    assert fn(float("nan"), 5.0) == 5.0
    assert fn("nan", 5.0) == 5.0
    assert fn(float("inf"), 168.0) == 168.0
    assert fn(float("-inf"), 168.0) == 168.0
    assert fn("bogus", 7.0) == 7.0
    assert fn(None, 9.0) == 9.0
    # finite passthrough (int / float / numeric string)
    assert fn(3.5, 5.0) == 3.5
    assert fn(10, 5.0) == 10.0
    assert fn("12.5", 5.0) == 12.5
    for v, d in ((float("nan"), 5.0), (float("inf"), 5.0), (3.5, 5.0)):
        assert math.isfinite(fn(v, d))


def test_runner_transport_finite_helper():
    _helper_cases(rt._finite_config_float)


def test_runner_auth_finite_helper():
    _helper_cases(ra._finite_config_float)
