"""v3.66.171 — configurable redaction profile + export-boundary DOM/email scrub.

SYNTHETIC fixtures only (fabricated tokens/URLs; no real capture values). Proves:
  * defaults (dom_embedded_urls=keep_structure, emails=redact) and the two shifts
  * the export-boundary scrub closes the DOM-embedded-URL / path-JWT / email gap
    the frozen rrweb recorder cannot, while keeping host/path usable
  * keep_full keeps signed-URL *structure* but the credential FLOOR still holds
  * strip_all collapses the whole query
  * additive custom header widens the floor only (never shrinks it)
  * the WACZ stamps the active profile + a value-free capture-health block
  * a reduced profile marks the capture local_only
  * the fail-loud floor gate raises rather than shipping a residual
"""
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from bulk_downloader import capture_artifact_redact as car
from bulk_downloader import redaction_profile as rp
from bulk_downloader import wacz_export as wx
from bulk_downloader.capture_redact import PLACEHOLDER, scrub_headers

_JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkoifQ.dQw4w9WgXcQ_aBcDeFgHiJ")


class _Env:
    def __init__(self, **kv):
        self.kv = kv
        self.saved = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _cap():
    return {
        "url": "https://members.x.com/v/1", "dom_log_count": 2,
        "dom_log": [
            {"type": "full_snapshot", "data": {"node": {"attributes": {
                "src": "https://cdn.x/seg.ts?Signature=ABC&Expires=9&Key-Pair-Id=KP",
                "srcset": "https://cdn.x/a.jpg?sig=AA 2x, https://cdn.x/b.jpg?sig=BB 3x"},
                "childNodes": [{"textContent": "hi user@example.com"}]}}},
            {"type": "incremental", "data": {"adds": [{"node": {"attributes": {
                "href": "https://api.x/p/" + _JWT + "/x", "data-tok": _JWT}}}]}},
        ],
        "network_log": [{"url": "https://cdn.x/seg.ts?token=<scrubbed>",
                         "response_status": 200}],
    }


def test_profile_defaults_and_two_shifts():
    p = rp.current_profile()
    assert p["dom_embedded_urls"] == rp.KEEP_STRUCTURE   # shift from keep_full
    assert p["emails"] == "redact"                       # shift from keep
    assert p["network_signed_urls"] == rp.KEEP_STRUCTURE  # == 170
    assert "instance" in p["custom_sensitive_headers"]
    assert rp.reduced_redaction(p) is False


def test_default_scrubs_dom_urls_emails_and_path_jwt():
    red = car.redact_capture(_cap())
    blob = json.dumps(red)
    assert "Signature=ABC" not in blob and "sig=AA" not in blob   # signing gone
    assert "cdn.x/seg.ts" in blob                                  # host/path kept
    assert "2x" in blob and "3x" in blob                           # srcset descriptors
    assert "user@example.com" not in blob                          # email
    assert _JWT not in blob                                        # path + attr JWT
    assert car.scan_floor_secrets(red) == []


def test_keep_full_keeps_signing_but_floor_holds():
    with _Env(BD_REDACT_DOM_URLS="keep_full", BD_REDACT_EMAILS="keep",
              BD_REDACT_NETWORK_URLS="keep_full"):
        p = rp.current_profile()
        assert rp.reduced_redaction(p) is True
        red = car.redact_capture(_cap(), p)
        blob = json.dumps(red)
        assert "Signature=ABC" in blob          # signed URL kept (function)
        assert "user@example.com" in blob        # email kept
        assert _JWT not in blob                  # JWT STILL floor-scrubbed
        assert car.scan_floor_secrets(red, p) == []   # signed allowed; floor clean


def test_strip_all_collapses_query_keeps_path():
    with _Env(BD_REDACT_DOM_URLS="strip_all"):
        p = rp.current_profile()
        red = car.redact_capture(_cap(), p)
        blob = json.dumps(red)
        assert "Signature" not in blob and "Expires" not in blob
        assert "cdn.x/seg.ts" in blob


def test_floor_uncrossable_by_any_profile():
    # all grey dials maximally relaxed; credential floor must still hold.
    with _Env(BD_REDACT_DOM_URLS="keep_full", BD_REDACT_NETWORK_URLS="keep_full",
              BD_REDACT_EMAILS="keep"):
        cap = {"dom_log": [{"data": {"node": {
            "attributes": {"data-jwt": _JWT},
            "childNodes": [{"textContent": "sessionid=SUPERSECRETVALUE1234567890"}]}}}]}
        red = car.redact_capture(cap)
        blob = json.dumps(red)
        assert _JWT not in blob                            # JWT floor
        assert "SUPERSECRETVALUE1234567890" not in blob     # kv-in-text floor
        assert car.scan_floor_secrets(red) == []


def test_additive_header_widens_floor_only():
    hs = [{"name": "instance", "value": "secrettoken"},
          {"name": "x-keep", "value": "ok"},
          {"name": "Cookie", "value": "a=b"}]
    out = scrub_headers(hs, ("instance",))
    d = {h["name"]: h["value"] for h in out}
    assert d["instance"] == PLACEHOLDER     # additive name scrubbed
    assert d["Cookie"] == PLACEHOLDER       # floor still scrubbed
    assert d["x-keep"] == "ok"              # non-sensitive kept
    # an empty explicit override cannot un-scrub a floor header
    out2 = scrub_headers(hs, ())
    d2 = {h["name"]: h["value"] for h in out2}
    assert d2["Cookie"] == PLACEHOLDER and d2["instance"] == "secrettoken"


def test_wacz_stamps_profile_and_health():
    b = wx.build_wacz_bytes(_cap())
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        cap = json.loads(z.read("archive/capture.json"))
    st = cap["redaction_profile"]
    assert st["schema"].startswith("v3.66.")
    assert st["dom_embedded_urls"] == "keep_structure" and st["emails"] == "redact"
    assert st["reduced_redaction"] is False
    h = cap["capture_health"]
    assert h["dom_log_len"] == 2 and h["dom_integrity_ok"] is True
    assert "dom_events_dropped" in h and "arm_fail_streak" in h


def test_wacz_marks_local_only_when_reduced():
    with _Env(BD_REDACT_DOM_URLS="keep_full"):
        b = wx.build_wacz_bytes(_cap())
        with zipfile.ZipFile(io.BytesIO(b)) as z:
            cap = json.loads(z.read("archive/capture.json"))
        assert cap.get("local_only") is True and cap.get("reduced_redaction") is True
        assert cap["redaction_profile"]["reduced_redaction"] is True


def test_floor_scanner_flags_raw_secrets_pre_redaction():
    kinds = {k for _p, k in car.scan_floor_secrets(_cap())}
    assert "jwt" in kinds   # raw capture: the JWT is detectable before scrub


def test_wacz_gate_scrubs_then_succeeds_when_redaction_bypassed(monkeypatch):
    # v3.66.470 DEFER-FLOOR-FAILOPEN contract change: if the capture-time scrub is
    # bypassed and a floor secret would otherwise reach capture.json, the export
    # boundary now FORCE-SCRUBS the flagged leaves to the placeholder and re-scans
    # rather than aborting. No secret ships (the safety invariant is preserved and
    # self-heals); a value-free forced_floor_scrub count records the recovery. The
    # loud raise is reserved for residual that SURVIVES the forced scrub -- covered
    # by tests/test_v3_66_470_floor_failopen.py::...still_fails_closed...
    monkeypatch.setattr(car, "redact_capture", lambda cap, profile=None: cap)
    b = wx.build_wacz_bytes(_cap())
    with zipfile.ZipFile(io.BytesIO(b)) as z:
        cap = json.loads(z.read("archive/capture.json"))
    # the raw JWT must NOT survive anywhere in the serialized capture
    assert _JWT not in json.dumps(cap), "a floor secret reached capture.json"
    # recovery is auditable + value-free
    assert cap["redaction_profile"]["forced_floor_scrub"] >= 1
