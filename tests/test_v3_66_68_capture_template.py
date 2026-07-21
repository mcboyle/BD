"""Tests for capture_template: build a template from a confirmed draft, and
diff a fresh capture against it to detect drift. Recognition-only — the static
guard test asserts no http/replay/synthesis verbs leaked in."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader.capture_synth import synthesize
from bulk_downloader import capture_workbench as wb
from bulk_downloader import capture_template as ct


# ── fixtures: a tiny two-capture pair with a clear goal shape ──────
def _entry(seq, url, method="GET", body=None):
    return {"seq": seq, "url": url, "method": method, "request_headers": {},
            "response_headers": {}, "response_status": 200,
            "response_body": body, "type": "xhr"}


def _capture(content_id, filename, expires, token, session):
    """A capture whose goal is cdn.example.com/{content_id}/{filename}?expires&token."""
    goal = (f"https://cdn.example.com/{content_id}/{filename}"
            f"?expires={expires}&token={token}")
    return {
        "host": "watch.example.com",
        "network_log": [
            _entry(1, "https://watch.example.com/play",
                   body='{"id":"%s","session":"%s"}' % (content_id, session)),
            _entry(2, f"https://api.example.com/cfg?session={session}&_=1"),
            _entry(9, goal),
        ],
    }


def _draft_and_template():
    # Two sessions of the same title: a full hex identifier is stable while
    # session/signing values rotate.  This preserves the goal's document path
    # so skeleton inference can expose its content_id slot.
    content_id = "a1b2c3d4e5f67890123456789abcdef"
    a = _capture(content_id, "2160p.mp4", "1700000000", "TOKAAA", "sessAAA")
    b = _capture(content_id, "2160p.mp4", "1700009999", "TOKBBB", "sessBBB")
    draft = wb.build_workbench(synthesize(a, b), captures=(a, b))
    return draft, ct.build_template(draft), (a, b)


# ── build_template ────────────────────────────────────────────────
class TestBuildTemplate:
    def test_template_has_durable_predictions(self):
        _, tmpl, _ = _draft_and_template()
        assert tmpl["template_version"] == ct.TEMPLATE_VERSION
        g = tmpl["goal"]
        assert g["host"] == "cdn.example.com"
        assert g["path_template"]
        assert g["classification"]  # classify_url ran
        # slots carry regexes
        for s in g["slots"]:
            assert s["regex"]

    def test_signing_recorded_as_markers_not_values(self):
        _, tmpl, _ = _draft_and_template()
        # expires/token are signing markers; values must NOT be in the template
        blob = str(tmpl)
        assert "1700000000" not in blob and "TOKAAA" not in blob
        assert set(tmpl["goal"]["signing_expected"]) >= {"expires", "token"}

    def test_goal_selection_starts_unconfirmed(self):
        _, tmpl, _ = _draft_and_template()
        assert tmpl["confirmed"]["goal_selection"]["status"] == "unconfirmed"


# ── diff_template: clean case (capture the template was built from) ─
class TestDiffClean:
    def test_source_capture_holds_clean(self):
        # diffing one of the source captures against the template should be
        # clean — the template still recognizes it
        _, tmpl, (a, b) = _draft_and_template()
        res = ct.diff_template(tmpl, b)
        assert res["drift_verdict"] == "clean"
        assert res["decayed"] == []
        assert res["goal_match"]["status"] == ct.HELD

    def test_goal_selection_shape_present(self):
        _, tmpl, (a, b) = _draft_and_template()
        res = ct.diff_template(tmpl, a)
        assert res["goal_selection_check"]["shape_still_present"] is True
        # not human-confirmed yet -> the note says so
        assert "not yet human-confirmed" in res["goal_selection_check"]["note"]


# ── diff_template: drift cases (synthetic changed captures) ────────
class TestDiffDrift:
    def test_host_change_is_missing(self):
        # the CDN host changed entirely -> goal host MISSING
        _, tmpl, _ = _draft_and_template()
        moved = _capture("a1b2c3d4", "2160p.mp4", "1", "T", "s")
        for e in moved["network_log"]:
            e["url"] = e["url"].replace("cdn.example.com", "cdn2.example.com")
        res = ct.diff_template(tmpl, moved)
        assert res["drift_verdict"] == "drifted"
        assert res["goal_match"]["status"] == ct.MISSING
        assert "goal_url_shape" in res["decayed"]

    def test_added_path_segment_is_drift(self):
        # site inserts an extra path segment -> shape no longer matches
        _, tmpl, _ = _draft_and_template()
        changed = _capture("a1b2c3d4", "2160p.mp4", "1", "T", "s")
        for e in changed["network_log"]:
            if "cdn.example.com" in e["url"]:
                e["url"] = e["url"].replace("cdn.example.com/",
                                            "cdn.example.com/v2/")
        res = ct.diff_template(tmpl, changed)
        assert res["goal_match"]["status"] == ct.DRIFTED
        assert "goal_url_shape" in res["decayed"]

    def test_new_signing_param_is_drift(self):
        # site adds a new signing-like param -> signing check drifts
        _, tmpl, _ = _draft_and_template()
        changed = _capture("a1b2c3d4", "2160p.mp4", "1", "T", "s")
        for e in changed["network_log"]:
            if "cdn.example.com" in e["url"]:
                e["url"] = e["url"] + "&signature=abc123"
        res = ct.diff_template(tmpl, changed)
        signing = next(c for c in res["checks"] if c["prediction"] == "signing")
        assert signing["status"] == ct.DRIFTED
        assert "signature" in signing["observed_new"]

    def test_slot_pattern_change_is_drift(self):
        # the id segment becomes a shape the slot regex won't match.
        # content_id regex is hex-ish; use a segment with characters outside it
        # AND keep the segment count the same so the matcher reaches the slot.
        _, tmpl, _ = _draft_and_template()
        cid_slot = next((s for s in tmpl["goal"]["slots"]
                         if s["name"] == "content_id"), None)
        if cid_slot is None:
            pytest.skip("no content_id slot in this fixture")
        changed = _capture("ZZ__not_hex__ZZ", "2160p.mp4", "1", "T", "s")
        res = ct.diff_template(tmpl, changed)
        # the goal shape itself may DRIFT (slot regex is part of the matcher);
        # either way the content_id prediction must not be HELD
        cid = next((c for c in res["checks"]
                    if c["prediction"] == "slot:content_id"), None)
        assert cid is not None and cid["status"] != ct.HELD


# ── posture: recognition-only ──────────────────────────────────────
class TestPosture:
    def test_no_network_or_synthesis_verbs(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bulk_downloader",
            "capture_template.py")).read()
        # strip the module docstring (it discusses what we DON'T do)
        body = src.split('"""', 2)[-1]
        banned = [r"\brequests\.(get|post)\b", r"\burllib\.request\b",
                  r"\bhttpx\b", r"\baiohttp\b", r"\.download\(",
                  r"\breassemble\b", r"\bsynthesize_signed\b", r"\breplay\b"]
        for pat in banned:
            assert not re.search(pat, body), f"posture: matched {pat}"

    def test_diff_never_emits_signing_values(self):
        # the diff result must not contain the captured signing values
        _, tmpl, (a, b) = _draft_and_template()
        res = ct.diff_template(tmpl, a)
        assert "1700000000" not in str(res)
        assert "TOKAAA" not in str(res)
