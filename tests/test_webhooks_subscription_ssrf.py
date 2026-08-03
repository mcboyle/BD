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

Self-contained (no pytest fixtures); literal IPs resolve locally, no network.
"""
import os
import json
import time
import tempfile

os.environ.setdefault("BD_HOME", tempfile.mkdtemp())
os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")

import pytest

from bulk_downloader import webhooks

# The module under test SOFT-imports requests and returns
# {"ok": False, "error": "requests not installed"} without it, so the guard this
# file exercises never runs on an install that lacks it. requests is declared in
# no requirements manifest -- it arrives only transitively, through
# requirements-cloak.txt's cloakbrowser[geoip] -> geoip2 -> requests, and that
# install step is NON-FATAL by design -- so its absence is a supported posture,
# not a broken box. MEASURED before this line existed: with requests blocked,
# this file and its sibling SSRF file went to 7 failed / 2 passed (control, same
# command, blocker removed: 9 passed) -- a missing dependency presenting as an
# SSRF-guard failure. A check that cannot see its subject must SAY so (CLAUDE.md
# section 0). A skip says so; a failure lies about which thing is broken.
requests = pytest.importorskip("requests")

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
def test_add_subscription_rejects_ssrf_hosts():
    for bad in _BAD:
        assert webhooks.add_subscription(url=bad, events=["download.done"]) is None, \
            f"add_subscription must reject SSRF host: {bad}"


def test_add_subscription_allows_lan_and_public():
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


def _drive_deliver(url, sid_offset):
    sid = _insert_raw_sub(url)
    calls = {"n": 0, "url": None}

    class _Resp:
        status_code = 200

    def fake_post(u, *a, **k):
        calls["n"] += 1
        calls["url"] = u
        return _Resp()

    orig = requests.post
    requests.post = fake_post
    try:
        res = webhooks._deliver_one({"subscription_id": sid, "payload": "{}",
                                     "event_name": "download.done", "id": sid_offset})
    finally:
        requests.post = orig
    return calls, res


def test_deliver_blocks_ssrf_even_if_stored():
    calls, res = _drive_deliver("http://169.254.169.254/latest/meta-data/", 9001)
    assert calls["n"] == 0, "delivery must NOT POST to a cloud-metadata URL"
    assert res.get("ok") is False, res


def test_deliver_allows_lan_receiver():
    calls, res = _drive_deliver("http://192.168.1.50/hook", 9002)
    assert calls["n"] == 1 and calls["url"] == "http://192.168.1.50/hook", calls
    assert res.get("ok") is True, res


if __name__ == "__main__":
    import traceback
    for n in [k for k in sorted(dict(globals())) if k.startswith("test_")]:
        try:
            globals()[n](); print(f"PASS  {n}")
        except AssertionError as e:
            print(f"FAIL  {n}: {e}")
        except Exception:
            print(f"ERROR {n}"); traceback.print_exc()
