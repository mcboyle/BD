"""O1789 session-misc-fix r1: F8 F13 F17(partial) F20 F21 F22 F23 (session-auth-capture findings).

Each test asserts the sound behaviour and carries a MISC-F<n> diagnostic; every
fix test has a control beside it that proves the probe can see a yes.
"""

import os
import stat
import threading
import types
import logging

import pytest

from bulk_downloader import login, runner_auth, runner_browser
from bulk_downloader import session_capture as sc
from bulk_downloader import session_keeper as sk
from bulk_downloader import takeover
from bulk_downloader.login_impl import replay

BD_GATE_SCOPE = "module"
CB = "https://site.test/oauth/callback?code=CODE-SECRET&state=ST&token=TOK-SECRET"


# ── F13: the URL a capture starts on is scrubbed under redact=True ──────────
def _leaks(d):
    return {k: d.get(k) for k in ("url", "search")
            if "CODE-SECRET" in str(d.get(k)) or "TOK-SECRET" in str(d.get(k))}


def test_f13_start_url_redacted_in_capture_dict_and_diff():
    d = sc.SessionCapture(url=CB).to_capture_dict()
    assert not _leaks(d), f"MISC-F13: redact=True capture keeps callback secrets at top level: {_leaks(d)}"
    assert d["host"] == "site.test" and d["pathname"] == "/oauth/callback"
    out = str(sc.diff_captures(d, sc.SessionCapture(url="https://site.test/").to_capture_dict()))
    assert "CODE-SECRET" not in out and "TOK-SECRET" not in out, "MISC-F13: diff_captures carries the callback secret"


def test_f13_set_page_context_url_redacted():
    cap = sc.SessionCapture(url=None)
    cap.set_page_context(url=CB, title="t")
    d = cap.to_capture_dict()
    assert not _leaks(d), f"MISC-F13: set_page_context(url=) keeps callback secrets: {_leaks(d)}"
    assert d["title"] == "t"


def test_f13_control_redact_false_keeps_raw_url():
    d = sc.SessionCapture(url=CB, redact=False).to_capture_dict()
    assert d["url"] == CB and "CODE-SECRET" in d["search"]


# ── F22: a non-finite or out-of-range CDP timestamp never raises ────────────
@pytest.mark.parametrize("wt", [float("nan"), float("inf"), float("-inf"), 1e300])
def test_f22_bad_walltime_does_not_raise(wt):
    cap = sc.SessionCapture(url=None)
    try:
        sc.feed_cdp_event(cap, "Network.requestWillBeSent",
                          {"requestId": "1", "request": {"url": "https://x.test/"}, "wallTime": wt})
        sc.feed_cdp_event(cap, "Network.loadingFinished", {"requestId": "1", "timestamp": wt})
        err = None
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    assert err is None, f"MISC-F22: wallTime={wt!r} -> {err}"
    assert len(cap.network_log) == 1 and cap.network_log[0]["iso"]


def test_f22_control_finite_walltime_kept():
    cap = sc.SessionCapture(url=None)
    sc.feed_cdp_event(cap, "Network.requestWillBeSent",
                      {"requestId": "1", "request": {"url": "https://x.test/"}, "wallTime": 1.7e9})
    assert cap._pending["1"]["start_ms"] == 1_700_000_000_000


# r2 (R210-D F22): a negative wallTime inside abs() <= _TS_MAX_S passed the r1 guard and
# reached _iso below datetime's year 1 ("ValueError: year -1199 is out of range").
@pytest.mark.parametrize("wt", [-1e11, -2.5e11, -7e10, -1.0])
def test_f22_r2_negative_walltime_does_not_raise(wt):
    cap = sc.SessionCapture(url=None)
    try:
        sc.feed_cdp_event(cap, "Network.requestWillBeSent",
                          {"requestId": "1", "request": {"url": "https://x.test/"}, "wallTime": wt})
        sc.feed_cdp_event(cap, "Network.loadingFinished", {"requestId": "1", "timestamp": wt})
        err = None
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    assert err is None, f"MISC-F22-NEG: wallTime={wt!r} -> {err}"
    assert sc._ts_ms({"wallTime": wt, "timestamp": wt}) is None, (
        f"MISC-F22-NEG: negative wallTime {wt!r} accepted as a timestamp")
    assert len(cap.network_log) == 1 and cap.network_log[0]["iso"]


@pytest.mark.parametrize("wt,ms", [(0, 0), (0.5, 500), (253402300799, 253402300799000)])  # year 9999 max
def test_f22_r2_control_bounds_kept(wt, ms):
    assert sc._ts_ms({"wallTime": wt}) == ms
    assert sc._ts_ms({"timestamp": wt}) == ms
    assert sc._iso(ms)  # both ends render


# ── F23: _pending and network_log are bounded ───────────────────────────────
def test_f23_pending_and_network_log_bounded():
    cap = sc.SessionCapture(url=None)
    n = 20000
    for i in range(n):
        sc.feed_cdp_event(cap, "Network.requestWillBeSent",
                          {"requestId": f"p{i}", "request": {"url": f"https://x.test/{i}"}, "wallTime": 1.7e9})
    for i in range(n):
        rid = f"f{i}"
        sc.feed_cdp_event(cap, "Network.requestWillBeSent",
                          {"requestId": rid, "request": {"url": f"https://x.test/f{i}"}, "wallTime": 1.7e9})
        sc.feed_cdp_event(cap, "Network.loadingFinished", {"requestId": rid, "timestamp": 1.0})
    assert len(cap._pending) <= getattr(sc, "_PENDING_MAX", n - 1) < n, (
        f"MISC-F23: {n} unfinished requests -> _pending {len(cap._pending)} (no cap)")
    assert len(cap.network_log) <= getattr(sc, "_NETWORK_LOG_MAX", n - 1) < n, (
        f"MISC-F23: {n} finished requests -> network_log {len(cap.network_log)} (no cap)")
    d = cap.to_capture_dict()
    assert d.get("network_log_capped") is True and d.get("network_log_dropped") == n - len(cap.network_log), (
        f"MISC-F23: capped network_log not reported: capped={d.get('network_log_capped')} "
        f"dropped={d.get('network_log_dropped')}")
    # the oldest unfinished request is the one evicted
    assert "p0" not in cap._pending and f"f{n - 1}" not in cap._pending


def test_f23_control_small_capture_untouched():
    cap = sc.SessionCapture(url=None)
    for i in range(10):
        sc.feed_cdp_event(cap, "Network.requestWillBeSent",
                          {"requestId": str(i), "request": {"url": f"https://x.test/{i}"}, "wallTime": 1.7e9})
        sc.feed_cdp_event(cap, "Network.loadingFinished", {"requestId": str(i), "timestamp": 1.0})
    d = cap.to_capture_dict()
    assert d["network_log_count"] == 10 and "network_log_capped" not in d and not cap._pending


# ── F17 (partial): a takeover channel's input queue is bounded ──────────────
_EV = {"type": "mouseMoved", "x": 1, "y": 1}


@pytest.fixture
def channel(monkeypatch):
    monkeypatch.setattr(takeover, "_shared_bucket", takeover.InputRateBucket(capacity=100_000, refill_per_s=0))
    sid = "misc-f17-sid"
    ch = takeover.open_channel(sid)
    yield sid, ch
    takeover.close_channel(sid)


def test_f17_input_queue_bounded(channel):
    sid, ch = channel
    cap = ch.inputs.maxsize
    assert 0 < cap < 100_000, f"MISC-F17: takeover input queue is unbounded (maxsize={cap})"
    got = [takeover.enqueue_input(sid, dict(_EV)) for _ in range(cap)]
    assert set(got) == {"ok"}
    over = takeover.enqueue_input(sid, dict(_EV))
    assert over == "closed" and ch.inputs.qsize() == cap, (
        f"MISC-F17: input past the bound -> {over!r}, queue {ch.inputs.qsize()}/{cap}")


def test_f17_control_drain_frees_room(channel):
    sid, ch = channel
    assert takeover.enqueue_input(sid, dict(_EV)) == "ok"
    assert takeover.drain_inputs(sid) == [_EV] and ch.inputs.qsize() == 0


# ── F20: two sites, same tag, same second keep two evidence files ───────────
class _Page:
    def __init__(self, body):
        self.body = body
        self.shots = []

    def content(self):
        return f"<html><body>{self.body}</body></html>"

    def screenshot(self, path):
        self.shots.append(path)


@pytest.fixture
def same_second(monkeypatch, tmp_path):
    shim = types.SimpleNamespace(strftime=lambda f, t=None: "20261014T090000Z", gmtime=lambda: None)
    monkeypatch.setattr(replay, "time", shim)
    return {"login_evidence_dir": str(tmp_path / "ev")}


def test_f20_same_tag_same_second_two_sites(same_second):
    pa, pb = _Page("SITE-A account page"), _Page("SITE-B account page")
    a = replay.write_login_evidence(pa, same_second, "https://a.test/", "login-failed")
    b = replay.write_login_evidence(pb, same_second, "https://b.test/", "login-failed")
    assert a != b and "SITE-A" in open(a).read() and "SITE-B" in open(b).read(), (
        f"MISC-F20: site A's evidence overwritten by site B: a={a} b={b}")
    assert pa.shots != pb.shots, f"MISC-F20: both screenshots go to {pb.shots}"
    for p in (a, b):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600, f"MISC-F20: {p} mode {oct(os.stat(p).st_mode)}"


def test_f20_control_first_name_unchanged(same_second):
    a = replay.write_login_evidence(_Page("A"), same_second, "https://a.test/", "login-failed")
    assert os.path.basename(a) == f"login-failed-20261014T090000Z-{os.getpid()}.html"


# ── F8: a second manual browser never deletes a live browser's profile lock ─
class _Carrier:
    def __init__(self, config, sid="siteA"):
        self.config = config
        self.site_id = sid
        self.log = logging.getLogger("misc-f8")
        self._manual_login_handle = None
        self._manual_cookie_snapshot = []
        self._login_thread = None
        self._lock = threading.RLock()
        self.jobs = {}
        self.events = []

    def log_event(self, *a, **k):
        self.events.append(a)


def _runner():
    cls = type("_H", (runner_auth.AuthMixin, runner_browser.BrowserMixin, _Carrier), {})
    return cls({})


@pytest.fixture
def rig(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sk, "pause_site_keepers", lambda sid: None)
    monkeypatch.setattr(runner_auth, "_admit_takeover", lambda cfg, n: (False, "local", ""))
    seen = {"profiles": [], "lock": []}

    def opener(cfg, manual_profile_dir=None, headless=False):
        lock = os.path.join(manual_profile_dir, "SingletonLock")
        seen["lock"].append(os.path.lexists(lock))
        seen["profiles"].append(manual_profile_dir)
        open(lock, "w").close()  # a live Chromium holds this
        return types.SimpleNamespace(cancel=lambda timeout=None: None)
    monkeypatch.setattr(login, "open_manual_login_browser", opener)
    return seen


def test_f8_second_solve_session_keeps_live_lock(rig, capsys):
    r = _runner()
    r.start_captcha_solve_session("https://site.test/a")
    r.start_captcha_solve_session("https://site.test/b")
    assert rig["lock"] == [False, True], (
        f"MISC-F8: 2nd browser on profile {rig['profiles'][-1]} deleted the live 1st browser's "
        f"SingletonLock (lock present at open: {rig['lock']})")
    assert "keeping SingletonLock" in capsys.readouterr().err


def test_f8_control_stale_lock_still_cleared(rig):
    r = _runner()
    d = os.path.join("profiles", "siteA", "manual")
    os.makedirs(d)
    open(os.path.join(d, "SingletonLock"), "w").close()  # left by a crashed run
    r.start_captcha_solve_session("https://site.test/a")
    assert rig["lock"] == [False], f"MISC-F8: stale lock not cleared with no browser open: {rig['lock']}"


def test_f8_control_closed_handle_does_not_keep_lock(rig):
    r = _runner()
    r.start_captcha_solve_session("https://site.test/a")
    closed = threading.Event()
    closed.set()
    r._captcha_solve_sessions["https://site.test/a"]["handle"] = types.SimpleNamespace(_closed=closed)
    r.start_captcha_solve_session("https://site.test/b")
    assert rig["lock"] == [False, False], f"MISC-F8: closed browser's lock kept: {rig['lock']}"


# ── F21: reconnect with a non-integer account_idx is a 400, not a 500 ───────
@pytest.mark.parametrize("bad", ["x", [1], {"a": 1}])
def test_f21_reconnect_bad_account_idx_is_400(fresh_app, bad):
    sid = fresh_app.post("/api/sites", json={"name": "rc-site"}).get_json()["id"]
    rr = fresh_app.post(f"/api/sites/{sid}/reconnect", json={"account_idx": bad})
    assert rr.status_code == 400 and rr.get_json().get("ok") is False, (
        f"MISC-F21: account_idx={bad!r} -> {rr.status_code} {rr.get_data(as_text=True)[:160]}")


@pytest.mark.parametrize("ok", [1, 1.5, "2"])
def test_f21_control_integer_like_account_idx_is_200(fresh_app, ok):
    sid = fresh_app.post("/api/sites", json={"name": "rc-site"}).get_json()["id"]
    rr = fresh_app.post(f"/api/sites/{sid}/reconnect", json={"account_idx": ok})
    assert rr.status_code == 200 and rr.get_json()["ok"] is True
