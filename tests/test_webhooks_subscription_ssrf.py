"""F-APP05-03 + F-CBD12-01 -- SSRF guard for the Phase-121 outgoing-webhook
subscription system (bulk_downloader/webhooks.py).

add_subscription stored the subscriber URL with only a non-empty check, and
_deliver_one POSTed to it verbatim -- so a subscription pointed at cloud
metadata / CGNAT / link-local was registered (F-APP05-03 entrypoint) and
dispatched (F-CBD12-01 delivery). The fix reuses the operator-chosen policy
already established for the plugin-hook webhooks in v3.66.553
(hooks._validate_webhook_url): reject the never-legitimate SSRF ranges while
ALLOWING RFC1918 LAN + loopback + public (Plex/Jellyfin/Home Assistant live on
the LAN). Validated at BOTH registration and delivery (delivery re-check =
DNS-rebind / hand-edited-DB defense).

WHY THIS FILE NO LONGER SKIPS ITSELF (v3.66.1229, backlog row 215).
`requests` is declared in no requirements manifest -- it arrives only
transitively, through requirements-cloak.txt's cloakbrowser[geoip] -> geoip2 ->
requests, and that install step is NON-FATAL by design -- so its absence is a
SUPPORTED posture, not a broken box. This file used to answer that posture with
a module-level `pytest.importorskip("requests")`, which threw away MORE than the
delivery tests: add_subscription never touches requests at all, so the two
registration guards -- the F-APP05-03 entrypoint itself -- were being skipped for
a dependency they do not have. A check that cannot see its subject must say so;
a WHOLE-FILE skip says nothing about four different guards at once (CLAUDE.md A7).
_deliver_one soft-imports requests inside its own body, so sys.modules is the
seam; the minimal API it uses (post, and a response carrying status_code) is
injected for the duration of each delivery test, and the missing-dependency
return is asserted directly instead of being skipped past.

Self-contained (no network); literal IPs resolve locally.
"""
import os
import json
import sys
import time
import tempfile
from types import SimpleNamespace

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import pytest

from bulk_downloader import webhooks

_BAD = [
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata / link-local
    "http://100.64.0.1/",                          # CGNAT
    "http://224.0.0.1/",                           # multicast
    "http://0.0.0.0/",                             # unspecified
    "file:///etc/passwd",                          # non-http scheme
]
_GOOD = [
    "http://192.168.1.50:32400/hook",  # Plex on LAN
    "http://127.0.0.1:8096/",           # single-box integration
    "http://93.184.216.34/webhook",     # public
]


# ---- F-APP05-03: entrypoint (add_subscription) ----
# These two need NO HTTP library at all: add_subscription calls
# hooks._validate_webhook_url and writes SQLite. They were collateral damage of
# the module-level importorskip this cut removed.
def test_add_subscription_rejects_ssrf_hosts():
    assert _BAD, "empty _BAD would make this pass over nothing"
    for bad in _BAD:
        assert webhooks.add_subscription(url=bad, events=["download.done"]) is None, \
            f"add_subscription must reject SSRF host: {bad}"


def test_add_subscription_allows_lan_and_public():
    assert _GOOD, "empty _GOOD would make this pass over nothing"
    for good in _GOOD:
        sid = webhooks.add_subscription(url=good, events=["download.done"])
        assert isinstance(sid, int), f"add_subscription must allow: {good}"


# ---- F-CBD12-01: delivery sink (_deliver_one) ----
def _insert_raw_sub(url):
    """Insert a subscription row directly (bypassing add_subscription's guard)
    to simulate a pre-guard or hand-edited-DB subscription."""
    webhooks._ensure_tables()
    from bulk_downloader import db as _db
    with _db.db_conn() as cx:
        cur = cx.execute(
            "INSERT INTO webhook_subscriptions(url,events,secret,created_at,enabled) "
            "VALUES(?,?,?,?,1)",
            (url, json.dumps(["download.done"]), "", time.time()))
        return cur.lastrowid


def _install_requests(monkeypatch, calls):
    """Inject the MINIMAL requests API _deliver_one actually uses.

    _deliver_one does `import requests` in its own body, so sys.modules is the
    seam. monkeypatch.setitem restores the previous entry whether the real
    package was present or absent, which is what makes this file
    posture-independent."""
    class _Resp:
        status_code = 200

    def fake_post(u, *a, **k):
        calls["n"] += 1
        calls["url"] = u
        calls["kwargs"] = k
        return _Resp()

    fake = SimpleNamespace(post=fake_post)
    monkeypatch.setitem(sys.modules, "requests", fake)
    # PRECONDITION: the seam is really the injected object, so a host that HAS
    # requests installed is measuring the same thing as one that does not.
    assert sys.modules["requests"] is fake
    return fake


def _hide_requests(monkeypatch):
    """Reproduce the supported posture in which the distribution is absent.
    A None entry makes `import requests` raise ImportError, which is what
    _deliver_one catches."""
    monkeypatch.setitem(sys.modules, "requests", None)


def _drive_deliver(url, sid_offset, monkeypatch):
    sid = _insert_raw_sub(url)
    calls = {"n": 0, "url": None, "kwargs": None}
    _install_requests(monkeypatch, calls)
    res = webhooks._deliver_one({"subscription_id": sid, "payload": "{}",
                                 "event_name": "download.done", "id": sid_offset})
    return calls, res


def test_deliver_blocks_ssrf_even_if_stored(monkeypatch):
    calls, res = _drive_deliver("http://169.254.169.254/latest/meta-data/", 9001,
                                monkeypatch)
    assert calls["n"] == 0, "delivery must NOT POST to a cloud-metadata URL"
    assert res.get("ok") is False, res
    # The missing-dependency return is also ok=False, so name the refusal: a
    # blocked target must not be laundered by an absent package.
    assert "blocked url" in (res.get("error") or ""), \
        f"refused for the wrong reason -- the guard never ran: {res!r}"
    assert res.get("permanent") is True, res


def test_deliver_allows_lan_receiver(monkeypatch):
    calls, res = _drive_deliver("http://192.168.1.50/hook", 9002, monkeypatch)
    assert calls["n"] == 1 and calls["url"] == "http://192.168.1.50/hook", calls
    assert res.get("ok") is True, res


def test_deliver_allows_public_receiver(monkeypatch):
    """Over-sensitivity control on the other side of the policy, plus the
    redirect contract.

    LAN is allowed here by operator decision, so a LAN-only positive control
    could not tell "policy applied" from "policy inverted". A public receiver
    must also be delivered to -- and with allow_redirects=False, because
    following a redirect would hand the delivery back to requests' own engine
    and let a public receiver bounce the POST inward."""
    calls, res = _drive_deliver("http://93.184.216.34/webhook", 9003, monkeypatch)
    assert calls["n"] == 1 and calls["url"] == "http://93.184.216.34/webhook", calls
    assert res.get("ok") is True, res
    assert calls["kwargs"].get("allow_redirects") is False, \
        f"delivery must not follow redirects: {calls['kwargs']!r}"


def test_deliver_reports_supported_missing_requests_posture(monkeypatch):
    """The one genuinely requests-dependent behaviour, asserted rather than
    skipped. It says the seam DEGRADES -- not that any guard holds."""
    sid = _insert_raw_sub("http://93.184.216.34/webhook")
    _hide_requests(monkeypatch)
    res = webhooks._deliver_one({"subscription_id": sid, "payload": "{}",
                                 "event_name": "download.done", "id": 9004})
    assert res == {"ok": False, "error": "requests not installed"}, res


def test_requests_seam_restores_sys_modules_in_either_order(monkeypatch):
    """The injection must not leak into the rest of the session, in EITHER
    order. A leaked sys.modules['requests'] is a suite-wide hazard: every later
    test importing requests would silently get a one-method SimpleNamespace."""
    sentinel = object()
    original_present = "requests" in sys.modules
    original_value = sys.modules.get("requests", sentinel)
    sid = _insert_raw_sub("http://93.184.216.34/webhook")

    def assert_restored():
        assert ("requests" in sys.modules) is original_present, \
            "sys.modules['requests'] presence was not restored"
        assert sys.modules.get("requests", sentinel) is original_value, \
            "sys.modules['requests'] was replaced beyond the test that injected it"

    def available():
        with monkeypatch.context() as scoped:
            calls = {"n": 0, "url": None, "kwargs": None}
            _install_requests(scoped, calls)
            res = webhooks._deliver_one({"subscription_id": sid, "payload": "{}",
                                         "event_name": "download.done", "id": 9005})
            assert res.get("ok") is True and calls["n"] == 1, (res, calls)
        assert_restored()

    def missing():
        with monkeypatch.context() as scoped:
            _hide_requests(scoped)
            res = webhooks._deliver_one({"subscription_id": sid, "payload": "{}",
                                         "event_name": "download.done", "id": 9006})
            assert res == {"ok": False, "error": "requests not installed"}, res
        assert_restored()

    assert_restored()
    available()
    missing()
    missing()
    available()


if __name__ == "__main__":
    import inspect
    import traceback
    for n in [k for k in sorted(dict(globals())) if k.startswith("test_")]:
        fn = globals()[n]
        # Fixture-taking tests are RUN here too, with a real MonkeyPatch, rather
        # than quietly passed over: a script runner that silently drops half its
        # subjects is the same defect this file was cut to remove.
        mp = pytest.MonkeyPatch() if "monkeypatch" in inspect.signature(fn).parameters else None
        try:
            fn(mp) if mp is not None else fn()
            print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception:
            print(f"ERROR {n}"); traceback.print_exc()
        finally:
            if mp is not None:
                mp.undo()
