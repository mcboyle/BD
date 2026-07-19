"""v3.66.72 — temporal benchmark harness.

Proves the harness on SYNTHETIC temporal scenarios so it is a measurement the
moment a real later same-title capture lands. Each test constructs a "before"
state (the original same-title pair / the template) and a "later" capture that
exhibits exactly one churn type, then asserts the harness lands it in the right
bucket. The transition tests assert that an inferred slot becoming observed
reads as STRENGTHENED, etc. Recognition-only — no signing values in output.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader.capture_synth import synthesize
from bulk_downloader import capture_workbench as wb
from bulk_downloader import capture_template as ct
from bulk_downloader import temporal_benchmark as tb


# ── fixtures ───────────────────────────────────────────────────────
def _entry(seq, url, body=None):
    return {"seq": seq, "url": url, "method": "GET", "request_headers": {},
            "response_headers": {}, "response_status": 200,
            "response_body": body, "type": "xhr"}


_CID = "53eb2252"
_RENDS = ["1280x720_60FPS.mp4", "1920x1080_60FPS.mp4", "3840x2160_60FPS_S.mp4"]


def _capture(cid, rends, expires, token, session, cdn="dd.example.test",
             segs_prefix=""):
    log = [
        _entry(1, "https://site.test/play", body='{"session":"%s"}' % session),
        _entry(2, f"https://api.site.test/cfg?session={session}"),
    ]
    for i, r in enumerate(rends):
        log.append(_entry(
            10 + i,
            f"https://{cdn}/{segs_prefix}{cid}/{r}?expires={expires}&token={token}"))
    return {"host": "site.test", "network_log": log}


def _template_and_pair():
    a = _capture(_CID, _RENDS, "1700000000", "TOKA", "sA")
    b = _capture(_CID, _RENDS, "1700009999", "TOKB", "sB")
    draft = wb.build_workbench(synthesize(a, b), captures=(a, b))
    return ct.build_template(draft), (a, b)


# ── churn classification: one test per category ────────────────────
class TestChurnClassification:
    def _run(self, later, baseline=False):
        tmpl, pair = _template_and_pair()
        return tb.temporal_run(tmpl, later,
                               baseline_pair=pair if baseline else None)

    def test_no_churn_when_same_everything(self):
        # a later capture identical in shape/identity/recorded-rendition + fresh
        # tokens of the same NAMES -> no real drift (signing rotation is fine)
        later = _capture(_CID, _RENDS, "1800000000", "TOKLATER", "sLater")
        churn = self._run(later)["churn"]
        # tokens rotated but marker names unchanged -> not flagged as signing drift
        assert churn["real_drift"] is False
        assert tb.NO_CHURN in churn["churn_categories"] or \
            churn["churn_categories"] == [tb.SIGNING_CHURN]

    def test_signing_churn_new_marker(self):
        # a new signing-like marker appears -> SIGNING_CHURN, but goal still holds
        later = _capture(_CID, _RENDS, "1800000000", "T", "s")
        for e in later["network_log"]:
            if "dd.example.test" in e["url"]:
                e["url"] = e["url"] + "&signature=abc"
        churn = self._run(later)["churn"]
        assert tb.SIGNING_CHURN in churn["churn_categories"]

    def test_rendition_churn_recorded_member_gone(self):
        # same title, but the recorded 3840x2160_S rendition is no longer served
        later = _capture(_CID, ["1280x720_60FPS.mp4", "1920x1080_60FPS.mp4"],
                         "1800000000", "T", "s")
        churn = self._run(later)["churn"]
        assert tb.RENDITION_CHURN in churn["churn_categories"]
        assert churn["real_drift"] is True

    def test_cdn_churn_same_shape_other_host(self):
        # goal host moved, but the same path shape exists on a new CDN host
        later = _capture(_CID, _RENDS, "1800000000", "T", "s",
                         cdn="cdn2.example.test")
        churn = self._run(later)["churn"]
        assert tb.CDN_CHURN in churn["churn_categories"]
        assert tb.BREAKAGE not in churn["churn_categories"]

    def test_structural_drift_added_segment(self):
        # an extra path segment is inserted -> the shape no longer matches
        later = _capture(_CID, _RENDS, "1800000000", "T", "s", segs_prefix="v2/")
        churn = self._run(later)["churn"]
        assert tb.STRUCTURAL_DRIFT_CHURN in churn["churn_categories"]

    def test_breakage_goal_host_absent(self):
        # the goal host is gone and nothing of the same shape exists anywhere
        later = {"host": "site.test", "network_log": [
            _entry(1, "https://site.test/play"),
            _entry(2, "https://api.site.test/cfg")]}
        churn = self._run(later)["churn"]
        assert tb.BREAKAGE in churn["churn_categories"]
        assert churn["real_drift"] is True


# ── transition report (second-order: before vs later) ─────────────
class TestTransition:
    def test_transition_pending_without_baseline(self):
        tmpl, pair = _template_and_pair()
        later = _capture(_CID, _RENDS, "1800000000", "T", "s")
        res = tb.temporal_run(tmpl, later)  # no baseline
        assert res["transition"] is None
        assert "PENDING" in res["transition_status"]

    def test_transition_complete_with_baseline_pair(self):
        tmpl, pair = _template_and_pair()
        later = _capture(_CID, _RENDS, "1800000000", "T", "s")
        res = tb.temporal_run(tmpl, later, baseline_pair=pair)
        assert res["transition_status"] == "complete"
        t = res["transition"]
        assert "assumptions" in t and "confidence" in t and "sensitivity" in t
        assert "summary" in t

    def test_identity_observed_strengthens_skeleton_slot(self):
        # before: same-title pair (content_id constant -> inferred skeleton slot)
        # later-synth pairs an original with a DIFFERENT-title capture, so the
        # content id now VARIES -> it should no longer be an inferred shape-slot
        # (it becomes an observed synth slot). The transition for the skeleton
        # content_id assumption should be strengthened or changed_category, not
        # weakened.
        tmpl, pair = _template_and_pair()
        diff_title = _capture("ffffffff", _RENDS, "1800000000", "T", "s")
        res = tb.temporal_run(tmpl, diff_title, baseline_pair=pair)
        t = res["transition"]
        # the content_id skeleton assumption should not WEAKEN; identity being
        # observed is a strengthening signal (or it changes category / drops out)
        cid = next((a for a in t["assumptions"]
                    if a["assumption"] == "assume:skeleton:content_id"), None)
        if cid is not None:
            assert cid["transition"] in (
                "strengthened", "changed_category", "stable", "disappeared")

    def test_band_delta_helper(self):
        assert tb._band_delta("low", "medium") == "strengthened"
        assert tb._band_delta("medium", "low") == "weakened"
        assert tb._band_delta("medium", "medium") == "stable"


# ── posture ────────────────────────────────────────────────────────
class TestPosture:
    def test_no_signing_values_in_output(self):
        tmpl, pair = _template_and_pair()
        later = _capture(_CID, _RENDS, "1800000000", "TOKSECRET", "s")
        res = tb.temporal_run(tmpl, later, baseline_pair=pair)
        blob = str(res)
        assert "TOKSECRET" not in blob and "1700000000" not in blob
        # echoed URLs in the diff carry no query
        gm = res["diff"]["goal_match"]
        for u in gm.get("matched_urls", []):
            assert "?" not in u and "token" not in u
