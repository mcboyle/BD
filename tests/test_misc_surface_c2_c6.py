"""misc-surface C3-C6 (bughunt/misc-surface FINDINGS): one RED test per item.

C3  marketplace._redact recursed dicts only, so a secret inside a list of dicts
    (accounts=[{username, password}]) survived into federation.build_template_bundle
    and preview_import_template's config_preview.
C4  import_template / preview_import_template accepted an UNSIGNED bundle even
    when the caller supplied verify_with (a trust anchor) -- stripping the
    signature bypassed verification.
C5  push.add_subscription stored any non-empty endpoint string (http, loopback,
    link-local metadata, file://, 9000 chars) and send_push POSTed to each one
    with no timeout.
C6  /api/activity/v2/export.csv wrote site-controlled cells (=, +, @ leads) raw.

C2 (app_thumbnails existence oracle) is reproduce-only, owed to misc-surface-c1.

r2 (security REFUTE F1 + advisory A1 on push._endpoint_ok): non-canonical IPv4
(127.1, 0x7f.0.0.1, 0177.0.0.1, 10.1) and "localhost." passed the literal check
and resolve internal; a name resolving internal passed; send_push followed
redirects and re-resolved at connect. Name answers come from a fake
getaddrinfo (no network); assertion messages start with MSCC-C5R2.
"""
import csv
import http.server
import io
import json
import socket
import sys
import threading
import types

import pytest

pytestmark = pytest.mark.bd_module_wipe
BD_GATE_SCOPE = "module"


def _fake_dns(monkeypatch, answers):
    """getaddrinfo answering ``answers[host]`` (an address or a list); every
    other host goes to the real resolver (numeric forms, /etc/hosts)."""
    real = socket.getaddrinfo

    def fake(host, port, *a, **k):
        if host in answers:
            addrs = answers[host]
            addrs = [addrs] if isinstance(addrs, str) else addrs
            out = []
            for ad in addrs:
                fam = socket.AF_INET6 if ":" in ad else socket.AF_INET
                sa = (ad, port or 443, 0, 0) if fam == socket.AF_INET6 else (ad, port or 443)
                out.append((fam, socket.SOCK_STREAM, 6, "", sa))
            return out
        if host in answers.get("_fail", ()):
            raise socket.gaierror(socket.EAI_NONAME, "no such name (fake)")
        return real(host, port, *a, **k)
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_c3_redact_strips_secrets_inside_lists(monkeypatch):
    from bulk_downloader import marketplace as m
    from bulk_downloader import template_registry as tr
    from bulk_downloader import federation as f

    out = m._redact({"password": "TOP_C3", "start_urls": ["https://x.example/PUBLIC_C3"],
                     "accounts": [{"label": "acct_label_C3", "password": "ACCT_C3"}]})
    s = json.dumps(out)
    assert "TOP_C3" not in s, "MSCC-C3 control: top-level secret leaked"
    assert "PUBLIC_C3" in s and "acct_label_C3" in s, \
        "MSCC-C3 control: non-secret list values must survive redaction"
    assert "ACCT_C3" not in s, "MSCC-C3 list-nested password survived _redact"

    monkeypatch.setattr(tr, "load_templates", lambda: [{
        "host": "h.example", "password": "TOP_FED_C3", "selectors": {"a": "b"},
        "logins": [{"label": "fed_label_C3", "password": "FED_ACCT_C3"}]}])
    fs = json.dumps(f.build_template_bundle("h.example"))
    assert "fed_label_C3" in fs, "MSCC-C3 control: federation bundle lost the list"
    assert "FED_ACCT_C3" not in fs, "MSCC-C3 list-nested password in federation bundle"

    pv = m.preview_import_template({"schema": m.SCHEMA_VERSION, "site_id": "s",
                                    "template": {"accounts": [{"password": "PV_C3"}]}})
    assert pv["ok"], "MSCC-C3 control: unsigned preview without verify_with must pass"
    assert "PV_C3" not in json.dumps(pv["config_preview"]), \
        "MSCC-C3 list-nested password in preview config_preview"


def test_c4_unsigned_bundle_refused_when_verify_with_given():
    from bulk_downloader import marketplace as m
    unsigned = m.export_template("s", {"name": "S", "start_urls": ["https://x.example/"]})
    good = m.export_template("s", {"name": "S"}, sign_with="shared-k")
    bad = m.export_template("s", {"name": "S"}, sign_with="other-k")

    assert m.import_template(dict(good), verify_with="shared-k")["ok"], \
        "MSCC-C4 control: right-key signed bundle must import"
    assert not m.import_template(dict(bad), verify_with="shared-k")["ok"], \
        "MSCC-C4 control: wrong-key bundle must be refused"
    assert m.import_template(dict(unsigned))["ok"], \
        "MSCC-C4 control: unsigned import with no verify_with stays allowed"

    r = m.import_template(dict(unsigned), verify_with="shared-k")
    assert not r["ok"], "MSCC-C4 unsigned bundle imported despite verify_with"
    assert r["errors"] == ["unsigned bundle refused"], r
    pv = m.preview_import_template(dict(unsigned), verify_with="shared-k")
    assert not pv["ok"], "MSCC-C4 unsigned bundle previewed ok despite verify_with"
    assert pv["errors"] == ["unsigned bundle refused"], pv


def test_c5_push_endpoint_validated_and_send_has_timeout(monkeypatch):
    from bulk_downloader import push as p, db
    db.db_init()
    calls = []

    class WPE(Exception):
        response = None

    def webpush(**kw):
        calls.append(kw)
    monkeypatch.setitem(sys.modules, "pywebpush",
                        types.SimpleNamespace(webpush=webpush, WebPushException=WPE))
    monkeypatch.setattr(p, "_ensure_vapid_keys", lambda: ("priv", "pub"))
    _fake_dns(monkeypatch, {"fcm.googleapis.com": "142.250.1.1"})
    keys = {"p256dh": "k", "auth": "a"}
    good = "https://fcm.googleapis.com/fcm/send/abc_C5"
    assert p.add_subscription({"endpoint": good, "keys": keys}) is True, \
        "MSCC-C5 control: a real https push-service endpoint must be accepted"
    bad = ["http://127.0.0.1:9/internal", "http://169.254.169.254/latest",
           "file:///etc/passwd", "https://127.0.0.1/x", "https://localhost/x",
           "https://intranet/x", "https://u:p@fcm.googleapis.com/x",
           "https://x.example/" + "A" * 9000]
    accepted = [ep[:40] for ep in bad if p.add_subscription({"endpoint": ep, "keys": keys})]
    assert accepted == [], "MSCC-C5 internal endpoint accepted: %r" % accepted

    # A row stored before validation existed must not be POSTed to.
    with p._conn() as cx:
        cx.execute("INSERT INTO push_subscriptions (endpoint, p256dh, auth, user_agent, "
                   "created_at, last_sent_at) VALUES (?, 'k', 'a', '', '', 0)",
                   ("http://169.254.169.254/legacy_C5",))
    out = p.send_push("t", "b", throttle_seconds=0)
    posted = [c["subscription_info"]["endpoint"] for c in calls]
    assert posted == [good], "MSCC-C5 send_push posted to an invalid endpoint: %r" % posted
    assert out["sent"] == 1 and out["failed"] == 1, out
    assert all(c.get("timeout") for c in calls), \
        "MSCC-C5 webpush called with no timeout: %r" % [c.get("timeout") for c in calls]


@pytest.mark.parametrize("host", ["127.1", "0x7f.0.0.1", "0x7f.1", "0177.0.0.1", "10.1",
                                  "0x7f000001", "localhost.", "127.0.0.1.",
                                  "fcm.googleapis.com."])
def test_c5r2_non_canonical_ipv4_and_trailing_dot_refused(monkeypatch, host):
    from bulk_downloader import push as p
    # the attacker-friendly case: every lookup says "public" (a resolver that
    # does not parse numeric forms, DoH, a proxy) -- the FORM must be refused.
    _fake_dns(monkeypatch, {host: "142.250.1.1", host.rstrip("."): "142.250.1.1"})
    ep = "https://%s/push-sink" % host
    if host == "fcm.googleapis.com.":
        assert p._endpoint_ok(ep) is True, \
            "MSCC-C5R2 control: a trailing dot on a public name is still that name"
        return
    if p._endpoint_ok(ep):
        pytest.fail("MSCC-C5R2 F1: _endpoint_ok accepted %r" % ep)


@pytest.mark.parametrize("answers", [["10.0.0.5"], ["142.250.1.1", "127.0.0.1"],
                                     ["169.254.169.254"], ["::1"], ["100.64.0.1"]])
def test_c5r2_name_resolving_internal_refused(monkeypatch, answers):
    from bulk_downloader import push as p
    _fake_dns(monkeypatch, {"push.attacker.example": answers,
                            "fcm.googleapis.com": "142.250.1.1",
                            "_fail": ("gone.example",)})
    assert p._endpoint_ok("https://fcm.googleapis.com/x") is True, \
        "MSCC-C5R2 control: a name resolving public only must be accepted"
    if p._endpoint_ok("https://push.attacker.example/x"):
        pytest.fail("MSCC-C5R2 A1: name resolving %r accepted" % answers)
    # a name that does not resolve reaches nothing; the send re-checks it and
    # connects only through the pinned session (next tests), so it is stored.
    assert p._endpoint_ok("https://gone.example/x") is True, \
        "MSCC-C5R2 control: an unresolvable name is left to the connect-time pin"


def test_c5r2_send_push_uses_pinned_session_without_redirects(monkeypatch):
    from bulk_downloader import push as p, db, deep_http
    db.db_init()
    calls = []

    class WPE(Exception):
        response = None

    monkeypatch.setitem(sys.modules, "pywebpush", types.SimpleNamespace(
        webpush=lambda **kw: calls.append(kw), WebPushException=WPE))
    monkeypatch.setattr(p, "_ensure_vapid_keys", lambda: ("priv", "pub"))
    _fake_dns(monkeypatch, {"fcm.googleapis.com": "142.250.1.1"})
    assert p.add_subscription({"endpoint": "https://fcm.googleapis.com/x_R2",
                               "keys": {"p256dh": "k", "auth": "a"}})
    p.send_push("t", "b", throttle_seconds=0)
    sess = [c.get("requests_session") for c in calls]
    if not sess or sess[0] is None:
        pytest.fail("MSCC-C5R2 A1: send_push passed no pinned requests_session")
    adapter = sess[0].get_adapter("https://fcm.googleapis.com/x_R2")
    assert isinstance(adapter, deep_http._PinnedAdapter), type(adapter)
    assert sess[0].max_redirects == 0, "MSCC-C5R2 A1: push session follows redirects"


def test_c5r2_push_session_refuses_a_connect_time_rebind(monkeypatch):
    """The name the guard judged public answers loopback at connect time:
    the pinned push session must refuse, and the listener must see nothing."""
    from bulk_downloader import push as p
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.send_response(201)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        port = srv.server_address[1]
        _fake_dns(monkeypatch, {"rebind.example": "127.0.0.1"})
        import requests
        requests.post("http://rebind.example:%d/ctl" % port, data=b"x", timeout=5)
        assert hits == ["/ctl"], "MSCC-C5R2 control: an unpinned post must reach the listener"
        hits.clear()
        sess = p._push_session()
        with pytest.raises(Exception):
            sess.post("http://rebind.example:%d/x" % port, data=b"x", timeout=5)
        assert hits == [], "MSCC-C5R2 A1: pinned push session reached loopback %r" % hits
        _fake_dns(monkeypatch, {"_fail": ("gone.example",)})
        with pytest.raises(Exception):
            sess.post("https://gone.example/x", data=b"x", timeout=5)
    finally:
        srv.shutdown()
        srv.server_close()


def test_c6_activity_export_csv_neutralizes_formulas():
    from bulk_downloader import app as A, db
    A.app.config["TESTING"] = True
    c = A.app.test_client()
    db.db_init()
    with db.db_conn() as cx:
        cx.execute("INSERT INTO history(site_id, site_name, url, status, filename, file_size, "
                   "message) VALUES(?,?,?,?,?,?,?)",
                   ("c6site", "c6", "https://x/1", "done", '=HYPERLINK("http://evil/C6")', 1,
                    "@SUM(1+1)C6"))
        cx.execute("INSERT INTO history(site_id, site_name, url, status, filename, file_size, "
                   "message) VALUES(?,?,?,?,?,?,?)",
                   ("c6site", "c6", "https://x/2", "done", "plain_C6.mp4", 1, "ok"))
    A.s_cfg["c6site"] = {"name": "+cmd|C6"}
    r = c.get("/api/activity/v2/export.csv?window=all")
    assert r.status_code == 200
    rows = list(csv.reader(io.StringIO(r.get_data(as_text=True))))
    data = [row for row in rows[1:] if row and row[2] == "c6site"]
    assert any("plain_C6.mp4" in row for row in data), \
        "MSCC-C6 control: plain row missing from export (probe cannot see rows)"
    assert len(data) == 2, data
    live = [cell for row in data for cell in row if cell[:1] in ("=", "+", "-", "@")]
    assert live == [], "MSCC-C6 formula cell emitted raw: %r" % live
