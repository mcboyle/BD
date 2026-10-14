"""dn-fix (O1789 deep-notifications, finder bd-worker-S16-A): webhook delivery rows D1-D12.

Global subscriptions (bulk_downloader/webhooks.py) are driven through a fake
`requests` injected at sys.modules (the seam _deliver_one imports), so no
network is used. Per-site hooks (bulk_downloader/hooks.py) POST through urllib
to a loopback stub receiver. Every assertion message starts with DN-FIX-D<n>.
"""
import hashlib
import hmac
import http.server
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import pytest

from bulk_downloader import hooks, webhooks

BD_GATE_SCOPE = "module"

PKG = Path(webhooks.__file__).resolve().parent


@pytest.fixture(autouse=True)
def _clean_queue():
    webhooks._ensure_tables()
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute("DELETE FROM webhook_queue")
        cx.execute("DELETE FROM webhook_subscriptions")
    yield


def _q(sql, args=()):
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        return [dict(r) for r in cx.execute(sql, args).fetchall()]


def _x(sql, args=()):
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cx.execute(sql, args)


class _Fake:
    """requests stand-in: route(url) -> (status, headers, sleep_seconds)."""

    def __init__(self, route):
        self.route = route
        self.calls = []
        self.lock = threading.Lock()

    def post(self, url, data=None, headers=None, **kw):
        status, rheaders, sleep = self.route(url)
        with self.lock:
            self.calls.append({"url": url, "data": data, "headers": dict(headers or {}),
                               "t": time.time()})
        if sleep:
            time.sleep(sleep)
        return SimpleNamespace(status_code=status, headers=rheaders or {})


def _install(monkeypatch, route):
    fake = _Fake(route)
    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=fake.post))
    return fake


def _sub(url, event="download.done", secret=""):
    sid = webhooks.add_subscription(url=url, events=[event], secret=secret)
    assert isinstance(sid, int), f"DN-FIX setup: add_subscription({url!r}) refused"
    return sid


# ---- D1: 408/429 are retried, Retry-After honoured ----

@pytest.mark.parametrize("status", [429, 408])
def test_d1_rate_limited_delivery_is_retried_after_retry_after(monkeypatch, status):
    _install(monkeypatch, lambda u: (status, {"Retry-After": "120"}, 0))
    _sub("http://127.0.0.1:9/h")
    webhooks.fire("download.done", {"k": 1})
    t0 = time.time()
    webhooks._drain_once()
    left = _q("SELECT attempt, next_retry_ts FROM webhook_queue")
    assert len(left) == 1 and left[0]["attempt"] == 1, \
        f"DN-FIX-D1 HTTP {status} must be retried, not dropped: queue={left}"
    assert left[0]["next_retry_ts"] - t0 >= 119, \
        f"DN-FIX-D1 Retry-After: 120 not honoured: next in {left[0]['next_retry_ts'] - t0:.1f}s"


def test_d1_control_other_4xx_still_dropped(monkeypatch):
    _install(monkeypatch, lambda u: (404, {}, 0))
    _sub("http://127.0.0.1:9/h")
    webhooks.fire("download.done", {"k": 1})
    webhooks._drain_once()
    assert _q("SELECT id FROM webhook_queue") == [], "DN-FIX-D1 control: 404 must drop at once"


# ---- D2: one item, two concurrent drain passes -> one POST ----

def test_d2_concurrent_drains_deliver_once(monkeypatch):
    fake = _install(monkeypatch, lambda u: (200, {}, 0.5))
    _sub("http://127.0.0.1:9/slow")
    webhooks.fire("download.done", {"once": True})
    ts = [threading.Thread(target=webhooks._drain_once) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    ids = [c["headers"].get("X-BD-Delivery-Id") for c in fake.calls]
    assert len(ids) == 1, f"DN-FIX-D2 duplicate delivery from concurrent drains: ids={ids}"


def test_d2_control_two_items_two_posts(monkeypatch):
    fake = _install(monkeypatch, lambda u: (200, {}, 0))
    _sub("http://127.0.0.1:9/ok")
    webhooks.fire("download.done", {"n": 1})
    webhooks.fire("download.done", {"n": 2})
    webhooks._drain_once()
    assert len(fake.calls) == 2, f"DN-FIX-D2 control: {len(fake.calls)} POSTs"


# ---- D4: head-of-line ----

def test_d4_hanging_receiver_does_not_delay_healthy_one(monkeypatch):
    fake = _install(monkeypatch, lambda u: (200, {}, 1.5 if "/hang" in u else 0))
    _sub("http://127.0.0.1:9/hang", event="download.failed")
    _sub("http://127.0.0.1:9/okhol", event="download.done")
    webhooks.fire("download.failed", {"i": 1})
    webhooks.fire("download.failed", {"i": 2})
    webhooks.fire("download.done", {"i": 3})
    t0 = time.time()
    webhooks._drain_once()
    ok = [c for c in fake.calls if "/okhol" in c["url"]]
    assert ok and ok[0]["t"] - t0 < 1.0, \
        f"DN-FIX-D4 healthy delivered after {ok[0]['t'] - t0 if ok else None} s (head-of-line)"


def test_d4_backlog_of_one_subscription_does_not_starve_another(monkeypatch):
    fake = _install(monkeypatch, lambda u: (500 if "/dead" in u else 200, {}, 0))
    _sub("http://127.0.0.1:9/dead", event="download.failed")
    _sub("http://127.0.0.1:9/live", event="download.done")
    for i in range(60):
        webhooks.fire("download.failed", {"i": i})
    webhooks.fire("download.done", {"i": "live"})
    webhooks._drain_once()
    assert [c for c in fake.calls if "/live" in c["url"]], \
        "DN-FIX-D4 60 queued items of a dead receiver kept the live one out of the pass"


# ---- D5: delays match the documented 1s -> 2s -> 5s -> 30s -> drop ----

def test_d5_retry_schedule_matches_doc(monkeypatch):
    fake = _install(monkeypatch, lambda u: (500, {}, 0))
    _sub("http://127.0.0.1:9/500")
    webhooks.fire("download.done", {})
    delays = []
    for _ in range(10):
        now = time.time()
        webhooks._drain_once()
        rows = _q("SELECT next_retry_ts FROM webhook_queue")
        if not rows:
            break
        delays.append(round(rows[0]["next_retry_ts"] - now))
        _x("UPDATE webhook_queue SET next_retry_ts = 0")
    assert delays == [1, 2, 5, 30] and len(fake.calls) == 5, \
        f"DN-FIX-D5 schedule {delays} attempts {len(fake.calls)} (doc: 1,2,5,30 then drop)"


def test_d5_drain_tick_wakes_for_a_short_retry():
    assert hasattr(webhooks, "_next_wait"), "DN-FIX-D5 drain loop sleeps a fixed 10 s tick"
    _sub("http://127.0.0.1:9/h")
    webhooks.fire("download.done", {})
    _x("UPDATE webhook_queue SET next_retry_ts = ?", (time.time() + 1,))
    assert webhooks._next_wait() <= 1.0, "DN-FIX-D5 a 1 s retry waits for the 10 s tick"
    _x("DELETE FROM webhook_queue")
    assert webhooks._next_wait() == 10.0, "DN-FIX-D5 control: empty queue waits the full tick"


# ---- D6: removed / disabled subscription ----

def test_d6_remove_subscription_drops_its_queue(monkeypatch):
    sid = _sub("http://127.0.0.1:9/h")
    keep = _sub("http://127.0.0.1:9/k")
    webhooks.fire("download.done", {})
    assert webhooks.remove_subscription(sid) is True
    left = _q("SELECT subscription_id FROM webhook_queue")
    assert left == [{"subscription_id": keep}], \
        f"DN-FIX-D6 removed subscription's items stay queued: {left}"


def test_d6_disabled_subscription_items_drop_in_one_pass(monkeypatch):
    fake = _install(monkeypatch, lambda u: (200, {}, 0))
    sid = _sub("http://127.0.0.1:9/h")
    webhooks.fire("download.done", {})
    _x("UPDATE webhook_subscriptions SET enabled = 0 WHERE id = ?", (sid,))
    webhooks._drain_once()
    assert _q("SELECT id FROM webhook_queue") == [] and fake.calls == [], \
        "DN-FIX-D6 disabled subscription's item is retried instead of dropped"


# ---- D7: event names ----

def test_d7_unknown_event_name_refused():
    sid = webhooks.add_subscription(url="http://127.0.0.1:9/h", events=["download.completed"])
    assert sid is None, f"DN-FIX-D7 subscription to a never-fired event accepted: id={sid}"
    assert webhooks.add_subscription(url="http://127.0.0.1:9/h", events=[7]) is None, \
        "DN-FIX-D7 non-string event name accepted"


def test_d7_vocabulary_is_every_fired_name():
    names = getattr(webhooks, "WEBHOOK_EVENTS", None)
    assert names, "DN-FIX-D7 no WEBHOOK_EVENTS vocabulary"
    def src(rel):
        return (PKG / rel).read_text(encoding="utf-8")

    fired = set()
    # runner maps its terminal status to the name between the import and the call
    for block in re.findall(r"import webhooks as _wh(.*?)_wh\.fire\(", src("runner.py"), re.S):
        fired |= set(re.findall(r'"(download\.[a-z_]+)"', block))
    fired |= set(re.findall(r'_wh\.fire\("([^"]+)"', src("alerts_engine.py")))
    fired |= set(re.findall(r'_fire_webhook\("([^"]+)"', src("maintenance.py")))
    assert len(fired) >= 6, f"DN-FIX-D7 probe found too few fired names: {fired}"
    assert fired == set(names), f"DN-FIX-D7 vocabulary {sorted(names)} != fired {sorted(fired)}"
    for name in names:  # control: every real name still subscribes
        assert isinstance(webhooks.add_subscription(url="http://127.0.0.1:9/h", events=[name]), int)


# ---- D8: signature binds timestamp and delivery id ----

def test_d8_signature_covers_timestamp_and_delivery_id(monkeypatch):
    fake = _install(monkeypatch, lambda u: (200, {}, 0))
    _sub("http://127.0.0.1:9/sig", secret="s3cret")
    webhooks.fire("download.done", {"window_id": 1})
    webhooks._drain_once()
    c = fake.calls[0]
    h, body = c["headers"], c["data"]
    signed = f"{h['X-BD-Timestamp']}.{h['X-BD-Delivery-Id']}.".encode() + body
    want = "sha256=" + hmac.new(b"s3cret", signed, hashlib.sha256).hexdigest()
    body_only = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    assert h["X-BD-Signature"] != body_only, \
        "DN-FIX-D8 signature covers the body only; timestamp/delivery id replayable"
    assert h["X-BD-Signature"] == want, "DN-FIX-D8 signature is not HMAC(ts.id.body)"


# ---- per-site hooks: loopback receiver ----

class _Recv(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    hits = []
    flaky = {"n": 0}
    ra400 = {"n": 0}

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        _Recv.hits.append(self.path)
        code = 200
        if self.path.startswith("/flaky"):
            _Recv.flaky["n"] += 1
            code = 503 if _Recv.flaky["n"] == 1 else 200
        if self.path.startswith("/404"):
            code = 404
        extra = {}
        if self.path.startswith("/always503"):
            code = 503
        if self.path.startswith("/ra-sup"):
            code, extra = 503, {"Retry-After": "\u00b2"}  # isdigit() True, float() refuses
        if self.path.startswith("/ra400"):
            _Recv.ra400["n"] += 1
            if _Recv.ra400["n"] == 1:
                code, extra = 503, {"Retry-After": "400"}
        self.send_response(code)
        for k, v in extra.items():
            self.send_header(k, v)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def recv():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recv)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _dispatch(url_tmpl, job, event="completed"):
    cfg = {"name": "S", "webhook_urls": url_tmpl, "webhook_events": event}
    hooks._dispatch(event, cfg, hooks.build_vars(cfg, event, job), job)


def test_d9_template_values_are_url_encoded(recv):
    _Recv.hits.clear()
    _dispatch(f"http://{recv}/hk/{{filename}}", {"filename": "a?x=1#b/../c"})
    _dispatch(f"http://{recv}/hk/{{filename}}", {"filename": "clip.mp4"})
    assert _Recv.hits[-1] == "/hk/clip.mp4", f"DN-FIX-D9 control: {_Recv.hits}"
    assert _Recv.hits[0] == "/hk/a%3Fx%3D1%23b%2F..%2Fc", \
        f"DN-FIX-D9 filename injected into the webhook URL: path={_Recv.hits[0]!r}"


def test_d10_log_line_has_no_userinfo_or_path(recv, capsys, monkeypatch):
    monkeypatch.setattr(hooks, "_SITE_WEBHOOK_DELAYS", (0, 0, 0, 0), raising=False)
    _dispatch(f"http://user:pa55word@{recv}/ok18/TOKENPATH", {"filename": "f"})
    _dispatch(f"http://{recv}/ok18b/TOKENPATH", {"filename": "f"})
    err = capsys.readouterr().err
    assert f"webhook http://{recv}" in err, f"DN-FIX-D10 control: no webhook line: {err!r}"
    assert "pa55word" not in err and "TOKENPATH" not in err, \
        f"DN-FIX-D10 webhook URL secret in log: {err!r}"


def test_d11_per_site_send_retries_5xx(recv, monkeypatch):
    monkeypatch.setattr(hooks, "_SITE_WEBHOOK_DELAYS", (0, 0, 0, 0), raising=False)
    _Recv.hits.clear()
    _Recv.flaky["n"] = 0
    _dispatch(f"http://{recv}/flaky", {"filename": "f"})
    assert _Recv.hits.count("/flaky") == 2, \
        f"DN-FIX-D11 per-site 503 not retried: POSTs={_Recv.hits.count('/flaky')}"
    _dispatch(f"http://{recv}/404", {"filename": "f"})
    assert _Recv.hits.count("/404") == 1, "DN-FIX-D11 control: 404 must not be retried"


def test_d12_hook_threads_are_bounded(monkeypatch):
    gate = threading.Event()
    done = []
    lock = threading.Lock()

    def slow_dispatch(event, site_config, vars, job):
        gate.wait(20)
        with lock:
            done.append(job.get("filename"))

    monkeypatch.setattr(hooks, "_dispatch", slow_dispatch)
    for i in range(50):
        hooks.fire_event("completed", {"name": "S"}, job={"filename": f"f{i}"})
    time.sleep(0.3)
    live = [t for t in threading.enumerate() if t.name == "hook-completed"]
    gate.set()
    deadline = time.time() + 10
    while len(done) < 50 and time.time() < deadline:
        time.sleep(0.05)
    assert len(live) <= 8, f"DN-FIX-D12 {len(live)} hook threads for 50 events (cap 8)"
    assert len(done) == 50, f"DN-FIX-D12 backlog lost events: {len(done)}/50 dispatched"
    time.sleep(0.2)
    got = sum(hooks._HOOK_SEMAPHORE.acquire(blocking=False) for _ in range(9))
    for _ in range(got):
        hooks._HOOK_SEMAPHORE.release()
    assert got == 8, f"DN-FIX-D12 semaphore slots after drain: {got} (want 8)"


# ---- r2: REFUTE F1 (D204) and the advisories taken ----

def _record_sleeps(monkeypatch):
    waits = []
    monkeypatch.setattr(hooks, "_site_retry_sleep", waits.append, raising=False)
    monkeypatch.setattr(hooks.time, "sleep", waits.append)  # r1 slept here directly
    return waits


def test_f1_retry_after_parser_never_raises():
    for bad in ("\u00b2", "\u0661\u0662", "abc", "", " 1 2"):
        assert hooks._retry_after_seconds(bad) is None, f"DN-FIX-F1 Retry-After {bad!r} not refused"
    assert hooks._retry_after_seconds("120") == 120.0, "DN-FIX-F1 control: delta-seconds"
    assert 0 < hooks._retry_after_seconds("Fri, 01 Jan 2100 00:00:00 GMT"), "DN-FIX-F1 control: HTTP-date"


def test_f1_non_ascii_retry_after_does_not_abort_dispatch(recv, monkeypatch):
    _record_sleeps(monkeypatch)
    _Recv.hits.clear()
    cfg = {"name": "S", "webhook_events": "completed",
           "webhook_urls": f"http://{recv}/ra-sup\nhttp://{recv}/after-ra"}
    job = {"filename": "f"}
    try:
        hooks._dispatch("completed", cfg, hooks.build_vars(cfg, "completed", job), job)
    except Exception as e:
        pytest.fail(f"DN-FIX-F1 Retry-After '\u00b2' aborted dispatch: {type(e).__name__}: {e}")
    assert "/after-ra" in _Recv.hits, f"DN-FIX-F1 later webhook skipped: {_Recv.hits}"
    assert _Recv.hits.count("/ra-sup") == 5, \
        f"DN-FIX-F1 unparseable Retry-After must fall back to the schedule: {_Recv.hits.count('/ra-sup')} POSTs"


def test_d11_five_attempts_then_give_up(recv, monkeypatch):
    waits = _record_sleeps(monkeypatch)
    _Recv.hits.clear()
    _dispatch(f"http://{recv}/always503", {"filename": "f"})
    assert _Recv.hits.count("/always503") == 5 and waits == [1, 2, 5, 30], \
        f"DN-FIX-D11 attempts={_Recv.hits.count('/always503')} waits={waits} (want 5, [1, 2, 5, 30])"


def test_d11_retry_after_capped_at_last_delay(recv, monkeypatch):
    waits = _record_sleeps(monkeypatch)
    _Recv.hits.clear()
    _Recv.ra400["n"] = 0
    _dispatch(f"http://{recv}/ra400", {"filename": "f"})
    assert _Recv.hits.count("/ra400") == 2 and waits == [30], \
        f"DN-FIX-D11 Retry-After 400 per-site wait {waits} (cap 30), POSTs {_Recv.hits.count('/ra400')}"


def test_d10_invalid_url_error_text_scrubbed(recv, capsys, monkeypatch):
    waits = _record_sleeps(monkeypatch)
    _dispatch(f"http://{recv}/ok19/TOK EN19", {"filename": "f"})
    err = capsys.readouterr().err
    assert "webhook http://" in err and "FAIL" in err, f"DN-FIX-D10 control: no FAIL line: {err!r}"
    assert "TOK" not in err, f"DN-FIX-D10 URL path echoed by the error text: {err!r}"
    assert waits == [], f"DN-FIX-D10 malformed URL retried: waits={waits}"
