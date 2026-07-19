"""T15 (Tier 3) — D-45 FlareSolverr health + D-48 captcha-relay
status. Group E. Both read-only views; D-45 reuses the
flaresolverr_client.ping fail-open semantics, D-48 reads the
in-process captcha_relay queue and takeover-callback registration.
"""
import contextlib
import os

from bulk_downloader import dev_suite as ds


@contextlib.contextmanager
def _app_cfg(use=None, endpoint=None, timeout_s=None):
    """Patch flaresolverr-related app_cfg fields and restore after."""
    from bulk_downloader import app as bd_app
    saved = {k: bd_app._app_cfg.get(k) for k in (
        "use_flaresolverr", "flaresolverr_endpoint",
        "flaresolverr_timeout_s")}
    if use is not None:
        bd_app._app_cfg["use_flaresolverr"] = use
    if endpoint is not None:
        bd_app._app_cfg["flaresolverr_endpoint"] = endpoint
    if timeout_s is not None:
        bd_app._app_cfg["flaresolverr_timeout_s"] = timeout_s
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                bd_app._app_cfg.pop(k, None)
            else:
                bd_app._app_cfg[k] = v


@contextlib.contextmanager
def _captcha_state(pending=None, starter=None, ender=None):
    """Snapshot + restore the captcha_relay module-level state."""
    from bulk_downloader import captcha_relay as _cr
    saved_pending = dict(getattr(_cr, "_pending", {}))
    saved_starter = getattr(_cr, "_takeover_starter", None)
    saved_ender = getattr(_cr, "_takeover_ender", None)
    if pending is not None:
        _cr._pending.clear()
        for p in pending:
            _cr._pending[p.url] = p
    if starter is not None:
        _cr._takeover_starter = starter
    if ender is not None:
        _cr._takeover_ender = ender
    try:
        yield
    finally:
        _cr._pending.clear()
        _cr._pending.update(saved_pending)
        _cr._takeover_starter = saved_starter
        _cr._takeover_ender = saved_ender


# ── D-45 flaresolverr_health ───────────────────────────────────────

def test_flaresolverr_disabled(clean_workdir):
    with _app_cfg(use=False, endpoint="http://localhost:8191/v1"):
        r = ds.flaresolverr_health()
    assert r["ok"] is True
    assert r["tool"] == "flaresolverr_health"
    assert r["use_flaresolverr_setting"] is False
    assert "disabled" in r["verdict"].lower()


def test_flaresolverr_no_endpoint(clean_workdir):
    with _app_cfg(use=True, endpoint=""):
        r = ds.flaresolverr_health()
    assert r["is_configured"] is False
    assert r["ping"]["skipped"] is True
    assert r["ping"]["ok"] is False
    assert "not configured" in r["verdict"].lower()


def test_flaresolverr_unreachable_endpoint_fails_open(clean_workdir):
    # Sandbox has nothing on :8191 — ping must fail open, not raise
    with _app_cfg(use=True,
                   endpoint="http://localhost:8191/v1",
                   timeout_s=0.5):
        r = ds.flaresolverr_health()
    assert r["ok"] is True
    assert r["is_configured"] is True
    assert r["ping"]["ok"] is False
    # the verdict says "unreachable: ..." with the underlying error
    assert "unreachable" in r["verdict"].lower()


def test_flaresolverr_includes_cumulative_stats(clean_workdir):
    with _app_cfg(use=True, endpoint="http://localhost:8191/v1"):
        r = ds.flaresolverr_health()
    s = r["stats"]
    # the stats dict shape is fixed in flaresolverr_client.stats()
    for key in ("enabled", "solves_attempted", "solves_succeeded",
                "solves_failed", "last_error"):
        assert key in s, key


# ── D-48 captcha_relay_status ──────────────────────────────────────

def test_captcha_relay_empty(clean_workdir):
    with _captcha_state(pending=[],
                         starter=lambda *a, **k: {},
                         ender=lambda *a, **k: None):
        r = ds.captcha_relay_status()
    assert r["ok"] is True
    assert r["tool"] == "captcha_relay_status"
    assert r["total_known"] == 0
    assert r["pending"] == []
    assert r["takeover_starter_registered"] is True
    assert r["takeover_ender_registered"] is True


def test_captcha_relay_unwired_warns(clean_workdir):
    # callbacks not registered -> verdict surfaces that
    with _captcha_state(pending=[], starter=False, ender=False):
        # _captcha_state interprets starter=False as "leave alone" so
        # use None explicitly to force unwired
        pass
    from bulk_downloader import captcha_relay as _cr
    saved_s = _cr._takeover_starter
    saved_e = _cr._takeover_ender
    _cr._takeover_starter = None
    _cr._takeover_ender = None
    try:
        r = ds.captcha_relay_status()
    finally:
        _cr._takeover_starter = saved_s
        _cr._takeover_ender = saved_e
    assert r["takeover_starter_registered"] is False
    assert r["takeover_ender_registered"] is False
    assert "NOT wired" in r["verdict"]


def test_captcha_relay_groups_pending(clean_workdir):
    # Seed a couple of pending captchas of different types/statuses
    from bulk_downloader.captcha_relay import PendingCaptcha
    p1 = PendingCaptcha(url="https://a.example/1", site_id="siteA",
                         captcha_type="turnstile",
                         detected_at=0.0, status="pending")
    p2 = PendingCaptcha(url="https://b.example/2", site_id="siteB",
                         captcha_type="hcaptcha",
                         detected_at=0.0, status="resolved")
    p3 = PendingCaptcha(url="https://c.example/3", site_id="siteA",
                         captcha_type="turnstile",
                         detected_at=0.0, status="pending")
    with _captcha_state(pending=[p1, p2, p3],
                         starter=lambda *a, **k: {},
                         ender=lambda *a, **k: None):
        r = ds.captcha_relay_status()
    assert r["total_known"] == 3
    assert r["by_type"]["turnstile"] == 2
    assert r["by_type"]["hcaptcha"] == 1
    assert r["by_status"]["pending"] == 2
    assert r["by_status"]["resolved"] == 1
    assert len(r["pending"]) == 2  # excludes the resolved


def test_captcha_relay_lists_challenge_types(clean_workdir):
    with _captcha_state(pending=[],
                         starter=lambda *a, **k: {},
                         ender=lambda *a, **k: None):
        r = ds.captcha_relay_status()
    assert "turnstile" in r["challenge_types"]
    assert "hcaptcha" in r["challenge_types"]
    assert "cloudflare" in r["challenge_types"]


# ── dev routes ─────────────────────────────────────────────────────

def test_flaresolverr_route(fresh_app):
    j = fresh_app.get("/api/dev/flaresolverr_health").get_json()
    assert j["ok"] is True
    assert j["tool"] == "flaresolverr_health"


def test_captcha_relay_route(fresh_app):
    j = fresh_app.get("/api/dev/captcha_relay").get_json()
    assert j["ok"] is True
    assert j["tool"] == "captcha_relay_status"


def test_t15_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/flaresolverr_health",
                   "/api/dev/captcha_relay"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
