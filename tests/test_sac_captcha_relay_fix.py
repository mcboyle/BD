"""session-auth-capture captcha-relay-fix: F5 F6 F9 F15 F16 F18 (finder probes S01 S02 S06 S05 S07 S03).

F5  a starter reporting ok:false left the entry `solving` under an invented session id; route 200 ok:true.
F6  two concurrent start_solve calls both reached the (unlocked) starter: two browsers for one captcha.
F9  the cached start_solve reply echoed the clearance value to the client.
F15 /api/captcha/resolved took ttl_seconds inf / nan / negative / "abc" and answered 200.
F16 starter / route exception text (paths, URLs) went back to the client.
F18 a second site marking the same pending URL was folded into the first site's entry.
Each assertion carries a distinctive token; the *_control tests show the probe can see the sound outcome.
"""

import threading
import time

import pytest
from flask import Flask

from bulk_downloader import app_captcha_relay as acr
from bulk_downloader import captcha_relay as cr
from bulk_downloader.login_impl import token_manager as tm

BD_GATE_SCOPE = "module"
URL = "https://shop.example.test/item/1"


@pytest.fixture
def relay(monkeypatch):
    monkeypatch.setattr(cr, "_pending", {})
    monkeypatch.setattr(cr, "_starting", set(), raising=False)
    monkeypatch.setattr(cr, "_maybe_push", lambda *a, **k: None)
    monkeypatch.setattr(cr, "_takeover_starter", None)
    tm.reset_cache_for_tests()
    return cr


@pytest.fixture
def client(relay):
    app = Flask("sac_captcha_relay_fix")
    app.testing = True
    app.register_blueprint(acr.captcha_bp)
    return app.test_client()


def _starter(monkeypatch, fn):
    monkeypatch.setattr(cr, "_takeover_starter", fn)


def _failing(calls):
    def fn(site, url):
        calls.append(url)
        return {"ok": False, "error": "browser launch failed at /home/op/.profile"}
    return fn


# ─── F5 (S01) ───────────────────────────────────────────────────────

def test_f5_failed_starter_leaves_entry_pending(relay, monkeypatch):
    _starter(monkeypatch, _failing([]))
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    out = cr.start_solve(URL)
    p = cr.get_pending(URL)
    assert (p["status"], p["solve_session_id"], out.get("ok"), out.get("session_id")) == ("pending", None, False, None), \
        f"F5_FAILED_STARTER_WENT_SOLVING: {p['status']} {p['solve_session_id']} reply={out}"
    assert "/home/op" not in str(out), f"F16_EXCEPTION_TEXT_TO_CLIENT: {out}"


def test_f5_failed_starter_opens_no_driverless_session(client, monkeypatch):
    # AMEND 0910Z (F17's driverless half): no takeover channel for a browser that never opened.
    _starter(monkeypatch, _failing([]))
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    sid = cr.start_solve(URL)["session_id"]
    codes = [client.post(f"/cockpit/api/takeover/{sid}/input", json={"type": "insertText", "text": "a"}).status_code
             for _ in range(3)]
    assert 200 not in codes, f"F5_DRIVERLESS_SESSION_TOOK_INPUT: sid={sid} {codes}"


def test_f5_control_ok_starter_session_takes_input(client, monkeypatch):
    _starter(monkeypatch, lambda s, u: {"ok": True, "session_id": "sess-ctl"})
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    cr.start_solve(URL)
    ok = client.post("/cockpit/api/takeover/sess-ctl/input", json={"type": "insertText", "text": "a"})
    bad = client.post("/cockpit/api/takeover/sess-ctl/input", json={"type": "mouse"})
    assert (ok.status_code, bad.status_code) == (200, 400)


def test_f5_route_reports_failure_and_retry_reaches_starter(client, monkeypatch):
    calls = []
    _starter(monkeypatch, _failing(calls))
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    r1 = client.post("/api/captcha/start_solve", json={"url": URL, "bypass_cache": True})
    r2 = client.post("/api/captcha/start_solve", json={"url": URL, "bypass_cache": True})
    got = [(r.status_code, r.get_json()["ok"]) for r in (r1, r2)]
    assert got == [(409, False), (409, False)] and len(calls) == 2, \
        f"F5_ROUTE_OK_FOR_FAILED_STARTER: {got} starter_calls={len(calls)} {r2.get_json()}"


def test_f5_control_ok_starter_goes_solving(relay, monkeypatch):
    _starter(monkeypatch, lambda s, u: {"ok": True, "session_id": "sess-1"})
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    assert cr.start_solve(URL)["session_id"] == "sess-1"
    p = cr.get_pending(URL)
    assert (p["status"], p["solve_session_id"]) == ("solving", "sess-1")


# ─── F6 (S02) ───────────────────────────────────────────────────────

def test_f6_concurrent_start_solve_opens_one_browser(relay, monkeypatch):
    calls, gate, refusals = [], threading.Event(), []

    def slow(site, url):
        calls.append(url)
        gate.wait(5)
        return {"ok": True, "session_id": f"s{len(calls)}"}

    _starter(monkeypatch, slow)
    cr.mark_captcha_needed("siteA", URL, "turnstile")

    def run():
        try:
            cr.start_solve(URL)
        except RuntimeError as e:
            refusals.append(str(e))

    ts = [threading.Thread(target=run) for _ in range(2)]
    for t in ts:
        t.start()
    deadline = time.time() + 5
    while not (calls and refusals) and time.time() < deadline:
        time.sleep(0.01)
    gate.set()
    for t in ts:
        t.join(5)
    assert len(calls) == 1 and len(refusals) == 1, \
        f"F6_TWO_BROWSERS_FOR_ONE_URL: starter_calls={len(calls)} refusals={refusals}"
    assert cr.get_pending(URL)["status"] == "solving" and not cr._starting


def test_f6_reservation_released_after_starter_raises(relay, monkeypatch):
    calls = []

    def boom(site, url):
        calls.append(url)
        raise OSError("launch failed")

    _starter(monkeypatch, boom)
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    for _ in range(2):
        with pytest.raises(RuntimeError, match="starter raised"):
            cr.start_solve(URL)
    assert len(calls) == 2 and not cr._starting, f"F6_RESERVATION_LEAKED: calls={len(calls)} {cr._starting}"


# ─── F9 (S06) ───────────────────────────────────────────────────────

def test_f9_cached_reply_does_not_echo_clearance(client, monkeypatch):
    _starter(monkeypatch, lambda s, u: {"ok": True, "session_id": "sess-1"})
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    client.post("/api/captcha/resolved", json={"url": URL, "clearance": "CLR-SECRET-1"})
    other = "https://shop.example.test/item/2"
    cr.mark_captcha_needed("siteB", other, "turnstile")
    r = client.post("/api/captcha/start_solve", json={"url": other})
    body = r.get_json()
    assert body["cached"] is True and body["session"]["status"] == "resolved"     # control: cache path taken
    assert "CLR-SECRET-1" not in r.get_data(as_text=True), f"F9_CLEARANCE_ECHOED: {body}"


# ─── F15 (S05) ──────────────────────────────────────────────────────

@pytest.mark.parametrize("ttl", ["inf", "1e400", "abc", -5, "nan", 0, True, 86400 * 365, [60]])
def test_f15_bad_clearance_ttl_refused_before_resolve(client, ttl):
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    r = client.post("/api/captcha/resolved", json={"url": URL, "clearance": "CLR-X", "ttl_seconds": ttl})
    stored = list(tm.get_default_cache()._clearance.values())
    status = cr.get_pending(URL)["status"]
    assert (r.status_code, stored, status) == (400, [], "pending"), \
        f"F15_TTL_ACCEPTED: ttl={ttl!r} -> {r.status_code} stored={stored} status={status}"


@pytest.mark.parametrize("ttl,expect", [(None, 7200.0), (60, 60.0), ("90.5", 90.5)])
def test_f15_control_valid_ttl_stored(client, ttl, expect):
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    body = {"url": URL, "clearance": "CLR-X"}
    if ttl is not None:
        body["ttl_seconds"] = ttl
    r = client.post("/api/captcha/resolved", json=body)
    cache = tm.get_default_cache()
    exp = [v[1] for v in cache._clearance.values()]
    assert r.status_code == 200 and len(exp) == 1 and abs(exp[0] - cache._clock() - expect) < 5
    assert cr.get_pending(URL)["status"] == "resolved"


# ─── F16 (S07) ──────────────────────────────────────────────────────

def test_f16_starter_exception_text_not_returned(client, monkeypatch):
    def boom(s, u):
        raise OSError("/home/op/.config/secret-profile: permission denied")

    _starter(monkeypatch, boom)
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    r = client.post("/api/captcha/start_solve", json={"url": URL, "bypass_cache": True})
    assert r.status_code == 409 and "/home/op" not in r.get_data(as_text=True), \
        f"F16_EXCEPTION_TEXT_TO_CLIENT: {r.status_code} {r.get_json()}"
    assert cr.get_pending(URL)["status"] == "pending"


def test_f16_route_unexpected_error_text_not_returned(client, monkeypatch):
    def broken(url):
        raise ValueError("/home/op/db.sqlite locked")

    monkeypatch.setattr(cr, "start_solve", broken)
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    r = client.post("/api/captcha/start_solve", json={"url": URL, "bypass_cache": True})
    assert r.status_code == 500 and "/home/op" not in r.get_data(as_text=True), \
        f"F16_EXCEPTION_TEXT_TO_CLIENT: {r.status_code} {r.get_json()}"


# ─── F18 (S03) ──────────────────────────────────────────────────────

def test_f18_second_site_takes_pending_url(relay):
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    cr.mark_captcha_needed("siteB", URL, "turnstile")
    assert cr.get_pending(URL)["site_id"] == "siteB", "F18_SECOND_SITE_FOLDED_INTO_FIRST"


def test_f18_second_site_reaches_starter(relay, monkeypatch):
    seen = []
    _starter(monkeypatch, lambda s, u: seen.append(s) or {"ok": True, "session_id": "s1"})
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    cr.mark_captcha_needed("siteB", URL, "turnstile")
    cr.start_solve(URL)
    assert seen == ["siteB"], f"F18_SECOND_SITE_FOLDED_INTO_FIRST: starter got {seen}"


def test_f18_control_solving_entry_kept(relay, monkeypatch):
    _starter(monkeypatch, lambda s, u: {"ok": True, "session_id": "s1"})
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    cr.start_solve(URL)
    cr.mark_captcha_needed("siteB", URL, "turnstile")
    p = cr.get_pending(URL)
    assert (p["site_id"], p["status"]) == ("siteA", "solving")


def test_f18_second_site_cannot_take_url_while_its_solve_starts(relay, monkeypatch):
    # A browser is opening for siteA: siteB's mark must not swap in an entry that then goes `solving`
    # on siteA's browser.
    def starter(site, url):
        cr.mark_captcha_needed("siteB", URL, "turnstile")
        return {"ok": True, "session_id": "s1"}

    _starter(monkeypatch, starter)
    cr.mark_captcha_needed("siteA", URL, "turnstile")
    cr.start_solve(URL)
    p = cr.get_pending(URL)
    assert (p["site_id"], p["status"]) == ("siteA", "solving"), f"F18_ENTRY_SWAPPED_MID_START: {p['site_id']} {p['status']}"


def test_f18_control_same_site_remark_keeps_entry(relay):
    cr.mark_captcha_needed("siteA", URL, "turnstile", worker_idx=1)
    cr.mark_captcha_needed("siteA", URL, "turnstile", worker_idx=2)
    p = cr.get_pending(URL)
    assert (p["site_id"], p["worker_idx"]) == ("siteA", 1)
