"""Tests for C-T1 static 2-capture synthesis (bulk_downloader.capture_synth).

Captures are built through SessionCapture with redact=True so the real
capture-time redaction path is exercised — the synthesizer must behave
correctly on redacted captures (the only kind that hit disk).
"""

import pytest

from bulk_downloader.capture_synth import (
    classify_value,
    synthesize,
    cross_check,
    CAPTURE_SYNTH_VERSION,
)
from bulk_downloader.session_capture import SessionCapture
from bulk_downloader.capture_redact import PLACEHOLDER


# ── shape classification ───────────────────────────────────────────
class TestClassifyValue:

    @pytest.mark.parametrize("value,expected", [
        ("550e8400-e29b-41d4-a716-446655440000", "uuid"),
        ("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
         "sha256"),
        ("9e107d9d372bb6826bd81d3542a419d6", "md5"),
        ("2026-05-27T18:42:11Z", "iso8601"),
        ("1716835331000", "unix_ts"),
        ("12345", "id"),
        ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc", "jwt"),
        ("master.m3u8", "filename"),
        ("xKQ7nP2vR9wThisIsLongEnough", "token"),
        ("ab", "opaque"),
        ("", "empty"),
        (None, "empty"),
    ])
    def test_shapes(self, value, expected):
        assert classify_value(value) == expected

    def test_placeholder_is_redacted(self):
        assert classify_value(PLACEHOLDER) == "redacted"

    def test_body_marker_is_redacted(self):
        assert classify_value(f"{PLACEHOLDER}(len=42)") == "redacted"


# ── synthesis fixtures ─────────────────────────────────────────────
def _build_capture(item_id, secret, expires_val, *, with_only_a=False,
                   with_only_b=False):
    host = "members.ex.com"
    cap = SessionCapture(url=f"https://{host}/watch?v={item_id}")
    cap.set_page_context(host=host)
    # req1: API lookup, id is a non-sensitive varying param (page_url source)
    cap.record_network(type="xhr", method="GET",
                       url=f"https://{host}/api/item?id={item_id}")
    # req2: signed media manifest — id (page_url) + token/expires (credentials)
    cap.record_network(
        type="xhr", method="GET",
        url=f"https://cdn.ex.com/v/master.m3u8?id={item_id}"
            f"&token={secret}&expires={expires_val}",
        request_headers=[{"name": "Cookie", "value": "sid=topsecret"},
                         {"name": "Accept", "value": "*/*"}])
    # req3: a varying non-sensitive param sourced from nowhere traceable
    cap.record_network(type="xhr", method="GET",
                       url=f"https://{host}/api/extra?z=ZZZ_{item_id}")
    if with_only_a:
        cap.record_network(type="xhr", method="GET",
                           url="https://analytics.ex.com/beacon?s=1")
    if with_only_b:
        cap.record_network(type="xhr", method="GET",
                           url="https://ads.ex.com/imp?c=2")
    return cap.to_capture_dict()


class TestSynthesizeStructure:

    def setup_method(self):
        self.a = _build_capture("10001", "SECRETA", "111", with_only_a=True)
        self.b = _build_capture("10002", "SECRETB", "222", with_only_b=True)
        self.syn = synthesize(self.a, self.b)

    def test_envelope(self):
        assert self.syn["capture_synth_version"] == CAPTURE_SYNTH_VERSION
        assert self.syn["synthesized"] is True
        assert self.syn["needs_review"] is True
        assert self.syn["confidence"] == "low"  # N=2 is always low
        assert self.syn["host"] == "members.ex.com"

    def test_skeleton_is_common_requests_in_order(self):
        keys = [r["key"] for r in self.syn["requests"]]
        assert keys == [
            "GET members.ex.com/api/item",
            "GET cdn.ex.com/v/master.m3u8",
            "GET members.ex.com/api/extra",
        ]

    def test_session_specific_requests_excluded(self):
        ss = self.syn["session_specific"]
        assert ss["only_in_a"] == ["GET analytics.ex.com/beacon"]
        assert ss["only_in_b"] == ["GET ads.ex.com/imp"]

    def test_goal_is_media_request(self):
        goals = [r["key"] for r in self.syn["requests"] if r["goal"]]
        assert goals == ["GET cdn.ex.com/v/master.m3u8"]

    def test_id_param_traced_to_page_url(self):
        item = next(r for r in self.syn["requests"]
                    if r["key"] == "GET members.ex.com/api/item")
        idp = next(p for p in item["params"] if p["key"] == "id")
        assert idp["type"] == "id"
        assert idp["credential"] is False
        assert idp["source"] == "page_url"

    def test_unresolved_param_reported(self):
        extra = next(r for r in self.syn["requests"]
                     if r["key"] == "GET members.ex.com/api/extra")
        zp = next(p for p in extra["params"] if p["key"] == "z")
        assert zp["source"] == "source_unknown"
        assert any(u["param"] == "z" for u in self.syn["unresolved"])


class TestPostureBoundary:
    """The synthesizer must SURFACE credential requirements but never
    trace, template, or reconstruct credential values."""

    def setup_method(self):
        self.a = _build_capture("10001", "SECRETA", "111")
        self.b = _build_capture("10002", "SECRETB", "222")
        self.syn = synthesize(self.a, self.b)
        self.media = next(r for r in self.syn["requests"]
                          if r["key"] == "GET cdn.ex.com/v/master.m3u8")

    def test_token_and_expires_are_surfaced_as_credentials(self):
        assert "token (query)" in self.syn["credentials_required"]
        assert "expires (query)" in self.syn["credentials_required"]

    def test_cookie_header_surfaced_as_credential(self):
        assert "Cookie" in self.media["credential_headers"]
        assert "Cookie (header)" in self.syn["credentials_required"]

    def test_credential_params_marked_and_not_traced(self):
        for pk in ("token", "expires"):
            slot = next(p for p in self.media["params"] if p["key"] == pk)
            assert slot["credential"] is True
            assert slot["type"] == "redacted"
            assert slot["source"] == "redacted_credential"

    def test_credential_values_never_reconstructed(self):
        # The real secret must never appear in the output; only the
        # placeholder may, and it is never traced as a sourced value.
        import json
        blob = json.dumps(self.syn)
        assert "SECRETA" not in blob and "SECRETB" not in blob
        # url_template must slot credentials, not inline a value
        assert "token={token}" in self.media["url_template"]

    def test_credentials_not_in_unresolved(self):
        # Redacted credentials are intentionally NOT "unresolved params";
        # they are required inputs, not dataflow gaps.
        bad = [u for u in self.syn["unresolved"]
               if u["param"] in ("token", "expires")]
        assert bad == []


class TestBodyProvenance:
    """C-T2 body slice: a param value that originates in a PRIOR response
    body should resolve to that body once body capture is on — closing the
    source_unknown gap. These tests pin the three properties a future edit
    could silently break: (1) the value resolves to the right response,
    (2) it stays source_unknown when body capture is OFF (the default path
    is inert), (3) credentials are never traced to a body.

    We reuse a non-sensitive param key ('z') so the value reaches
    _trace_source rather than being short-circuited as a credential by its
    key name (the SENSITIVE_QS_KEY gate fires before tracing)."""

    def _build(self, item_id, *, bodies_on):
        import os
        host = "members.ex.com"
        # The value the later URL embeds, seeded into a prior response body.
        z_val = f"ZZZ_{item_id}"
        prev = os.environ.get("BD_CAPTURE_BODIES")
        if bodies_on:
            os.environ["BD_CAPTURE_BODIES"] = "1"
        else:
            os.environ.pop("BD_CAPTURE_BODIES", None)
        try:
            cap = SessionCapture(url=f"https://{host}/watch?v={item_id}")
            cap.set_page_context(host=host)
            # Prior API response whose JSON body carries z_val under a benign
            # key. With bodies on this is retained+redacted; off → marker.
            cap.record_network(
                type="xhr", method="GET",
                url=f"https://{host}/api/item?id={item_id}",
                response_status=200,
                response_headers={"Content-Type": "application/json"},
                response_body=f'{{"item":"{item_id}","ctx":"{z_val}"}}')
            # Later request embeds z_val as a non-sensitive query param.
            cap.record_network(
                type="xhr", method="GET",
                url=f"https://{host}/api/extra?z={z_val}",
                response_status=200,
                response_headers={"Content-Type": "application/json"})
            return cap.to_capture_dict()
        finally:
            if prev is None:
                os.environ.pop("BD_CAPTURE_BODIES", None)
            else:
                os.environ["BD_CAPTURE_BODIES"] = prev

    def test_value_resolves_to_prior_response_body_when_on(self):
        a = self._build("10001", bodies_on=True)
        b = self._build("10002", bodies_on=True)
        syn = synthesize(a, b)
        extra = next(r for r in syn["requests"]
                     if r["key"] == "GET members.ex.com/api/extra")
        zp = next(p for p in extra["params"] if p["key"] == "z")
        # Resolves to the API response body that carried the value.
        assert zp["source"] == "response_body:GET members.ex.com/api/item"
        assert zp["credential"] is False
        # And it's no longer reported as unresolved.
        assert not any(u["param"] == "z" for u in syn["unresolved"])

    def test_value_stays_unknown_when_bodies_off(self):
        # Default path: bodies are length markers → body search is inert,
        # value remains source_unknown exactly as before C-T2.
        a = self._build("10001", bodies_on=False)
        b = self._build("10002", bodies_on=False)
        syn = synthesize(a, b)
        extra = next(r for r in syn["requests"]
                     if r["key"] == "GET members.ex.com/api/extra")
        zp = next(p for p in extra["params"] if p["key"] == "z")
        assert zp["source"] == "source_unknown"
        assert any(u["param"] == "z" for u in syn["unresolved"])

    def test_credential_in_body_is_not_traceable(self):
        # A signing token in a body is <scrubbed> at capture time, so even
        # with bodies on it can never be traced to a body — credentials are
        # surfaced as credential params, never given a benign body source.
        from bulk_downloader.capture_synth import _trace_source, _request_key
        # Simulate a retained body where the token was redacted.
        prior = [{"method": "GET",
                  "url": "https://members.ex.com/api/auth",
                  "response_body": '{"token":"<scrubbed>","ok":true}'}]
        # The real token value must NOT resolve to the body.
        assert _trace_source("REALTOKENVALUE", [], prior) == "source_unknown"
    """A varying id embedded in the URL PATH yields different request keys
    across captures (both land in only_in_*); the recovery pass re-pairs
    them into a path-parameterized request."""

    def _caps(self, slug_a, slug_b, *, host="cdn.ex.com",
              path="videos/{}/master.m3u8"):
        a = SessionCapture(url=f"https://members.ex.com/watch?v={slug_a}")
        a.record_network(type="xhr", method="GET",
                         url=f"https://{host}/{path.format(slug_a)}")
        b = SessionCapture(url=f"https://members.ex.com/watch?v={slug_b}")
        b.record_network(type="xhr", method="GET",
                         url=f"https://{host}/{path.format(slug_b)}")
        return a.to_capture_dict(), b.to_capture_dict()

    def test_path_id_recovered_and_templated(self):
        a, b = self._caps("10001", "10002")
        syn = synthesize(a, b)
        assert len(syn["requests"]) == 1
        r = syn["requests"][0]
        assert r["recovered_path_param"] is True
        assert r["classification"] == "varying"
        assert r["url_template"] == \
            "https://cdn.ex.com/videos/{path2}/master.m3u8"
        pslot = next(p for p in r["params"] if p["in_path"])
        assert pslot["type"] == "id"
        assert pslot["source"] == "page_url"  # 10001 is in the entry URL
        assert syn["session_specific"] == {"only_in_a": [], "only_in_b": []}

    def test_two_seg_diff_not_paired(self):
        # Different endpoints sharing only the host must NOT be paired.
        a = SessionCapture(url="https://members.ex.com/watch?v=1")
        a.record_network(type="xhr", method="GET",
                        url="https://cdn.ex.com/videos/1/master.m3u8")
        b = SessionCapture(url="https://members.ex.com/watch?v=2")
        b.record_network(type="xhr", method="GET",
                        url="https://cdn.ex.com/clips/2/preview.m3u8")
        syn = synthesize(a.to_capture_dict(), b.to_capture_dict())
        assert all(not r.get("recovered_path_param") for r in syn["requests"])
        assert syn["session_specific"]["only_in_a"]
        assert syn["session_specific"]["only_in_b"]


class TestPageContextDataflow:
    """Non-credential param values are traced to whichever page-context
    source they came from — entry URL, referrer, meta tags, player
    attributes, inline script config, or js_globals."""

    def _caps(self):
        a = SessionCapture(url="https://m.ex.com/watch?v=PAGEID1234")
        a.set_page_context(
            referrer="https://ref.ex.com/r?x=REFID5678",
            meta_tags=[{"property": "og:video",
                        "content": "https://cdn/META9012.mp4"}],
            player_elements=[{"attributes": {"data-asset": "PLAYER7890"}}],
            script_tags_of_interest=[{"content": "var c={asset:'SCRIPT2345'}"}],
            js_globals={"player": {"vid": "JSVID3456"}})
        a.record_network(
            type="xhr", method="GET",
            url="https://api.ex.com/x?pg=PAGEID1234&ref=REFID5678"
                "&og=META9012&pl=PLAYER7890&sc=SCRIPT2345&js=JSVID3456")
        b = SessionCapture(url="https://m.ex.com/watch?v=PAGEID0000")
        b.record_network(
            type="xhr", method="GET",
            url="https://api.ex.com/x?pg=PAGEID0000&ref=REFID0000"
                "&og=META0000&pl=PLAYER0000&sc=SCRIPT0000&js=JSVID0000")
        return synthesize(a.to_capture_dict(), b.to_capture_dict())

    def test_each_param_traced_to_its_source(self):
        syn = self._caps()
        r = next(r for r in syn["requests"]
                 if r["key"] == "GET api.ex.com/x")
        src = {p["key"]: p["source"] for p in r["params"]}
        assert src["pg"] == "page_url"
        assert src["ref"] == "referrer"
        assert src["og"] == "meta:og:video"
        assert src["pl"] == "player_attr:data-asset"
        assert src["sc"] == "script_config"
        assert src["js"] == "js_global"
        # All resolved → none unresolved.
        assert syn["unresolved"] == []


class TestSignedUrlDefense:
    """Defense-in-depth: if a capture is UNDER-scrubbed and a signed URL
    survives URL-encoded inside a param value, the synthesizer must still
    slot it as a credential and never echo it. Uses redact=False to
    simulate the under-scrubbed input (after the scrubber fix, captures
    won't contain raw signed URLs, but this guard must hold regardless)."""

    def _synth(self):
        import urllib.parse as up
        signed = ("https://cdn.x.com/v.mp4?Policy=eyJ0ZXN0IjoxfQ"
                  "&Signature=SIGTOKEN123ABC&Key-Pair-Id=KPID456")
        enc = up.quote(signed, safe="")
        a = SessionCapture(url="https://m.x.com/watch?v=1", redact=False)
        a.record_network(type="xhr", method="GET",
                        url=f"https://api.x.com/p?mediaResource={enc}&v=1")
        b = SessionCapture(url="https://m.x.com/watch?v=2", redact=False)
        b.record_network(type="xhr", method="GET",
                        url=f"https://api.x.com/p?mediaResource={enc}&v=2")
        return synthesize(a.to_capture_dict(), b.to_capture_dict())

    def test_nested_signed_url_slotted_and_masked(self):
        syn = self._synth()
        r = next(r for r in syn["requests"] if r["key"] == "GET api.x.com/p")
        mr = next(p for p in r["params"] if p["key"] == "mediaResource")
        assert mr["credential"] is True
        assert mr["type"] == "signed_url"
        assert mr["value_a"] == "<signed_url>"
        assert "mediaResource (query)" in syn["credentials_required"]

    def test_signature_token_never_appears(self):
        import json
        from urllib.parse import unquote
        syn = self._synth()
        blob = unquote(json.dumps(syn))
        assert "SIGTOKEN123ABC" not in blob
        assert "Key-Pair-Id" not in blob


class TestCrossCheck:

    def test_host_match_subdomain(self):
        syn = synthesize(_build_capture("1", "s", "1"),
                         _build_capture("2", "s", "2"))
        rep = cross_check(syn, {"domain": "ex.com"})
        assert rep["host_match"] is True
        assert rep["checked"] == "host_only"

    def test_host_mismatch(self):
        syn = synthesize(_build_capture("1", "s", "1"),
                         _build_capture("2", "s", "2"))
        rep = cross_check(syn, {"domain": "other.net"})
        assert rep["host_match"] is False
