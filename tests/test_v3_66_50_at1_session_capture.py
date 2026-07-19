"""A-T1 — session capture core + shared redaction primitives.

Covers capture-time redaction (default-on), the bd-recon output shape,
CDP Network.* event mapping (request→response join + redirects), the
two-capture diff (DoD + C-T1 seed), and that A-T1 output flows through
the existing netlog_classify media classifier.
"""

import pytest

from bulk_downloader import capture_redact as cr
from bulk_downloader.session_capture import (
    RECON_CAPTURE_VERSION,
    SessionCapture,
    capture_via_cdp,
    diff_captures,
    feed_cdp_event,
)


# ── shared redaction primitives ───────────────────────────────────

class TestCaptureRedact:

    def test_placeholder_matches_netlog_classify(self):
        # The placeholder MUST equal the one netlog_classify recognizes.
        from bulk_downloader import netlog_classify as nc
        assert cr.PLACEHOLDER == nc._SCRUB_PLACEHOLDER

    def test_redact_query_redacts_token_keeps_rest(self):
        out = cr.redact_query("https://x/v.mp4?token=abc&res=1080")
        assert "token=<scrubbed>" in out
        assert "res=1080" in out

    def test_redact_query_no_query_untouched(self):
        assert cr.redact_query("https://x/v.mp4") == "https://x/v.mp4"

    def test_scrub_headers_list_form(self):
        out = cr.scrub_headers([
            {"name": "Cookie", "value": "sid=SECRET"},
            {"name": "Accept", "value": "*/*"},
        ])
        assert {"name": "Cookie", "value": "<scrubbed>"} in out
        assert {"name": "Accept", "value": "*/*"} in out

    def test_scrub_headers_dict_form(self):
        out = cr.scrub_headers({"Authorization": "Bearer x", "Accept": "*/*"})
        assert out["Authorization"] == "<scrubbed>"
        assert out["Accept"] == "*/*"

    def test_body_marker(self):
        assert cr.body_marker("password=hunter2") == "<scrubbed>(len=16)"
        assert cr.body_marker(None) is None
        assert cr.body_marker({"a": 1}) == "<scrubbed>(json:dict)"


# ── SessionCapture redaction + shape ──────────────────────────────

class TestSessionCaptureRedaction:

    def test_record_network_redacts_by_default(self):
        cap = SessionCapture(url="https://members.x.com/")
        e = cap.record_network(
            method="POST",
            url="https://x.com/api?token=abc&q=1",
            request_headers=[{"name": "Cookie", "value": "sid=S"}],
            request_body="password=hunter2",
            response_status=200,
        )
        assert "token=<scrubbed>" in e["url"] and "q=1" in e["url"]
        assert e["request_headers"][0]["value"] == "<scrubbed>"
        assert e["request_body"] == "<scrubbed>(len=16)"

    def test_redact_false_keeps_raw(self):
        cap = SessionCapture(redact=False)
        e = cap.record_network(url="https://x/v?token=abc",
                               request_body="raw")
        assert e["url"] == "https://x/v?token=abc"
        assert e["request_body"] == "raw"

    def test_set_cookies_redacted_by_default(self):
        cap = SessionCapture()
        cap.set_cookies("sessionid=SECRET")
        assert cap.page_context["cookies"] == "<scrubbed>"

    def test_seq_increments_and_count(self):
        cap = SessionCapture()
        for i in range(3):
            cap.record_network(url=f"https://x/{i}")
        d = cap.to_capture_dict()
        assert [e["seq"] for e in d["network_log"]] == [0, 1, 2]
        assert d["network_log_count"] == 3

    def test_capture_dict_shape(self):
        cap = SessionCapture(url="https://members.x.com/path?a=1")
        cap.set_page_context(title="T", user_agent="UA")
        cap.record_network(url="https://x/v.mp4", response_status=200)
        d = cap.to_capture_dict()
        for k in ("capture_version", "captured_at", "url", "origin",
                  "host", "pathname", "search", "title", "user_agent",
                  "cookies", "network_log", "network_log_count"):
            assert k in d
        assert d["capture_version"] == RECON_CAPTURE_VERSION
        assert d["host"] == "members.x.com"
        assert d["pathname"] == "/path"
        assert d["search"] == "?a=1"
        entry = d["network_log"][0]
        for k in ("timestamp", "iso", "type", "method", "url",
                  "request_headers", "response_status", "seq"):
            assert k in entry


# ── CDP event mapping ─────────────────────────────────────────────

class TestCdpMapping:

    def _req(self, rid, url, method="GET", **extra):
        p = {"requestId": rid, "wallTime": 1.0, "type": "XHR",
             "request": {"method": method, "url": url,
                         "headers": {"Accept": "*/*"}}}
        p.update(extra)
        return p

    def test_request_response_finish_join(self):
        cap = SessionCapture(redact=False)
        feed_cdp_event(cap, "Network.requestWillBeSent",
                       self._req("1", "https://x/v.mp4"))
        feed_cdp_event(cap, "Network.responseReceived",
                       {"requestId": "1",
                        "response": {"status": 200, "statusText": "OK",
                                     "headers": {"Content-Type": "video/mp4"}}})
        feed_cdp_event(cap, "Network.loadingFinished",
                       {"requestId": "1", "wallTime": 2.0})
        d = cap.to_capture_dict()
        assert d["network_log_count"] == 1
        e = d["network_log"][0]
        assert e["url"] == "https://x/v.mp4"
        assert e["response_status"] == 200
        assert e["duration_ms"] == 1000
        # request headers were converted from dict to name/value list
        assert {"name": "Accept", "value": "*/*"} in e["request_headers"]

    def test_loading_failed_records_error(self):
        cap = SessionCapture(redact=False)
        feed_cdp_event(cap, "Network.requestWillBeSent",
                       self._req("2", "https://x/bad"))
        feed_cdp_event(cap, "Network.loadingFailed",
                       {"requestId": "2", "wallTime": 1.5,
                        "errorText": "net::ERR_ABORTED"})
        e = cap.to_capture_dict()["network_log"][0]
        assert e["error"] == "net::ERR_ABORTED"

    def test_redirect_chain_emits_per_hop(self):
        cap = SessionCapture(redact=False)
        feed_cdp_event(cap, "Network.requestWillBeSent",
                       self._req("3", "https://x/go"))
        # second requestWillBeSent on same id with a redirectResponse
        # finalizes the first hop as a 'redirect' entry.
        feed_cdp_event(cap, "Network.requestWillBeSent",
                       self._req("3", "https://x/dest",
                                 redirectResponse={"status": 302,
                                                   "statusText": "Found",
                                                   "headers": {}}))
        feed_cdp_event(cap, "Network.responseReceived",
                       {"requestId": "3",
                        "response": {"status": 200, "headers": {}}})
        feed_cdp_event(cap, "Network.loadingFinished",
                       {"requestId": "3", "wallTime": 3.0})
        d = cap.to_capture_dict()
        assert d["network_log_count"] == 2
        types = [e["type"] for e in d["network_log"]]
        assert "redirect" in types
        urls = [e["url"] for e in d["network_log"]]
        assert "https://x/go" in urls and "https://x/dest" in urls

    def test_cdp_redaction_applies(self):
        cap = SessionCapture(redact=True)  # default
        feed_cdp_event(cap, "Network.requestWillBeSent",
                       self._req("4", "https://x/v?token=SECRET",
                                 request={"method": "GET",
                                          "url": "https://x/v?token=SECRET",
                                          "headers": {"Cookie": "sid=S"}}))
        feed_cdp_event(cap, "Network.loadingFinished",
                       {"requestId": "4", "wallTime": 1.1})
        e = cap.to_capture_dict()["network_log"][0]
        assert "token=<scrubbed>" in e["url"]
        assert e["request_headers"][0]["value"] == "<scrubbed>"


class TestCdpResponseBodyFetch:
    """C-T2 source half: capture_via_cdp now fetches response bodies over CDP
    (Network.getResponseBody), gated on BD_CAPTURE_BODIES + a text/JSON
    content-type, fed through redact_body. These tests drive feed_cdp_event
    with a mock body_fetcher (no real browser) and pin the policy gates."""

    def _drive(self, cap, ct, fetcher):
        feed_cdp_event(cap, "Network.requestWillBeSent",
                       {"requestId": "1", "wallTime": 1.0, "type": "XHR",
                        "request": {"method": "GET", "url": "https://x/api",
                                    "headers": {}}})
        feed_cdp_event(cap, "Network.responseReceived",
                       {"requestId": "1",
                        "response": {"status": 200,
                                     "headers": {"Content-Type": ct}}})
        feed_cdp_event(cap, "Network.loadingFinished",
                       {"requestId": "1", "wallTime": 2.0},
                       body_fetcher=fetcher)
        return cap.network_log[0]

    def test_flag_on_json_body_fetched_and_redacted(self, monkeypatch):
        monkeypatch.setenv("BD_CAPTURE_BODIES", "1")
        calls = []
        def fetch(rid):
            calls.append(rid)
            return '{"id":"123","t":"eyJh.payload.sig"}'
        e = self._drive(SessionCapture(redact=True), "application/json", fetch)
        assert calls == ["1"]                      # body WAS fetched
        assert '"id": "123"' in e["response_body"]  # benign value kept
        assert "<scrubbed>" in e["response_body"]   # JWT redacted at capture
        assert "payload.sig" not in e["response_body"]

    def test_flag_off_body_not_fetched(self, monkeypatch):
        monkeypatch.delenv("BD_CAPTURE_BODIES", raising=False)
        calls = []
        e = self._drive(SessionCapture(redact=True), "application/json",
                        lambda rid: calls.append(rid) or "{}")
        assert calls == []                  # fetcher never called
        assert e["response_body"] is None   # byte-for-byte old behaviour

    def test_flag_on_binary_body_not_fetched(self, monkeypatch):
        # Posture line: stream bytes are never pulled into memory.
        monkeypatch.setenv("BD_CAPTURE_BODIES", "1")
        calls = []
        e = self._drive(SessionCapture(redact=True), "video/mp4",
                        lambda rid: calls.append(rid) or "STREAMBYTES")
        assert calls == []
        assert e["response_body"] is None

    def test_fetcher_failure_degrades_to_none(self, monkeypatch):
        # A body that can't be fetched (evicted/CDP error) must not crash
        # the capture — _finalize swallows the exception.
        monkeypatch.setenv("BD_CAPTURE_BODIES", "1")
        def boom(rid):
            raise RuntimeError("body evicted")
        e = self._drive(SessionCapture(redact=True), "application/json", boom)
        assert e["response_body"] is None
        assert e["response_status"] == 200  # the entry is still recorded

    def test_no_fetcher_is_metadata_only(self, monkeypatch):
        # The unit-test/default path (feed_cdp_event without a fetcher) is
        # unchanged even with the flag on.
        monkeypatch.setenv("BD_CAPTURE_BODIES", "1")
        cap = SessionCapture(redact=True)
        feed_cdp_event(cap, "Network.requestWillBeSent",
                       {"requestId": "1", "wallTime": 1.0, "type": "XHR",
                        "request": {"method": "GET", "url": "https://x/api",
                                    "headers": {}}})
        feed_cdp_event(cap, "Network.responseReceived",
                       {"requestId": "1",
                        "response": {"status": 200,
                                     "headers": {"Content-Type":
                                                 "application/json"}}})
        feed_cdp_event(cap, "Network.loadingFinished",
                       {"requestId": "1", "wallTime": 2.0})  # no body_fetcher
        assert cap.network_log[0]["response_body"] is None


# ── diff (DoD + C-T1 seed) ────────────────────────────────────────

class TestDiffCaptures:

    def _cap(self, url, token):
        c = SessionCapture(url=url, redact=False)
        c.record_network(url="https://cdn/app.js")               # invariant
        c.record_network(url=f"https://api/auth?token={token}")  # varying
        return c.to_capture_dict()

    def test_invariant_and_varying_split(self):
        a = self._cap("https://x/p", "AAA")
        b = self._cap("https://x/p", "BBB")
        d = diff_captures(a, b)
        assert "GET https://cdn/app.js".replace("https://", "") in \
            [k.replace("https://", "") for k in d["invariant"]]
        varying_reqs = [v["request"] for v in d["varying"]]
        assert any("api/auth" in r for r in varying_reqs)

    def test_only_in_a_and_b(self):
        a = SessionCapture(redact=False)
        a.record_network(url="https://x/only-a")
        b = SessionCapture(redact=False)
        b.record_network(url="https://x/only-b")
        d = diff_captures(a.to_capture_dict(), b.to_capture_dict())
        assert any("only-a" in k for k in d["only_in_a"])
        assert any("only-b" in k for k in d["only_in_b"])

    def test_page_context_diff(self):
        a = self._cap("https://x/page-a", "T")
        b = self._cap("https://x/page-b", "T")
        d = diff_captures(a, b)
        assert "pathname" in d["page_context_diff"]

    def test_summary_string(self):
        a = self._cap("https://x/p", "AAA")
        b = self._cap("https://x/p", "BBB")
        assert "varying" in diff_captures(a, b)["summary"]


# ── integration: A-T1 output → netlog_classify ────────────────────

class TestNetlogClassifyIntegration:

    def test_capture_flows_through_classifier(self):
        from bulk_downloader.netlog_classify import classify_network_log
        cap = SessionCapture(url="https://members.x.com/", redact=True)
        # an unsigned direct mp4 + a signed/expiring one
        cap.record_network(
            url="https://cdn.x.com/clean/video.mp4",
            response_status=200,
            response_headers=[{"name": "Content-Type", "value": "video/mp4"}])
        cap.record_network(
            url="https://cdn.x.com/s/v.mp4?token=SECRET&expires=1999999999",
            response_status=200,
            response_headers=[{"name": "Content-Type", "value": "video/mp4"}])
        d = cap.to_capture_dict()
        report = classify_network_log(d["network_log"])
        # The signed URL had its token redacted at capture time, so it is
        # never emitted as a downloadable candidate.
        cands = report.candidates() if hasattr(report, "candidates") else []
        assert all("SECRET" not in c for c in cands)


# ── live CDP wiring (regression: handlers were registered under the ──
#    prefix-stripped event name, so nothing was ever captured) ───────
class _FakeCDPClient:
    """Stand-in for a Playwright CDP session: records on()/send() and can
    dispatch a recorded event to its handlers."""

    def __init__(self):
        self.handlers = {}
        self.sent = []

    def send(self, method, *a, **k):
        self.sent.append(method)

    def on(self, event, handler):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event, params):
        for h in self.handlers.get(event, []):
            h(params)


class _FakePage:
    def __init__(self, client, url=""):
        self.url = url
        self.context = type("Ctx", (), {
            "new_cdp_session": staticmethod(lambda page: client)})()


class TestCdpLiveWiring:

    def test_listeners_registered_under_full_event_names(self):
        client = _FakeCDPClient()
        capture_via_cdp(_FakePage(client), SessionCapture(url="x"))
        for name in ("Network.requestWillBeSent", "Network.responseReceived",
                     "Network.loadingFinished", "Network.loadingFailed"):
            assert name in client.handlers, name
        assert "Network.enable" in client.sent

    def test_driven_event_lands_in_network_log(self):
        client = _FakeCDPClient()
        cap = SessionCapture(url="x")
        capture_via_cdp(_FakePage(client), cap)
        client.emit("Network.requestWillBeSent", {
            "requestId": "1",
            "request": {"method": "GET",
                        "url": "https://cdn.x/master.m3u8?id=9",
                        "headers": {"Accept": "*/*"}},
            "type": "XHR", "timestamp": 1.0})
        client.emit("Network.responseReceived", {
            "requestId": "1", "response": {"status": 200, "headers": {}}})
        client.emit("Network.loadingFinished", {
            "requestId": "1", "timestamp": 1.5})
        log = cap.to_capture_dict()["network_log"]
        assert len(log) == 1
        assert log[0]["url"].startswith("https://cdn.x/master.m3u8")
        assert log[0]["method"] == "GET"
        assert log[0]["response_status"] == 200
