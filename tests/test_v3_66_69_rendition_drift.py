"""v3.66.69 — rendition/identity split + five-way drift verdict.

The pre-.69 template diff returned only HELD/DRIFTED/MISSING and a permissive
filename regex let "the family still exists" read as "the same goal still
holds". A real second-title capture (content_id 53eb2252 -> ba7f0af0, recorded
rendition 3840x2160_60FPS_S.mp4 absent) falsified that — it read CLEAN while the
served rendition had silently changed. These tests pin the corrected behaviour:

  * the workbench classifies an opaque id segment as IDENTITY and a
    resolution-descriptor segment as RENDITION;
  * build_template freezes role + recorded value per slot;
  * the matcher collects the WHOLE matched set (not the first request) and
    compares observed-vs-recorded per role, emitting the five-way verdict;
  * IDENTITY_CHANGE is informational (clean); RENDITION_DRIFT and
    IDENTITY_AND_RENDITION_CHANGE are drift; STRUCTURAL_DRIFT/MISSING unchanged;
  * migrate_template upgrades a v1 template with no recapture;
  * recognition-only: no signing values are ever emitted.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader.capture_synth import synthesize
from bulk_downloader import capture_workbench as wb
from bulk_downloader import capture_template as ct


# ── fixtures ───────────────────────────────────────────────────────
def _entry(seq, url, method="GET", body=None):
    return {"seq": seq, "url": url, "method": method, "request_headers": {},
            "response_headers": {}, "response_status": 200,
            "response_body": body, "type": "xhr"}


def _capture(content_id, renditions, expires, token, session, host_cdn="dd.example.com"):
    """A capture whose goal family is {host_cdn}/{content_id}/{rendition}.mp4.

    `renditions` is a list so a single title can expose its whole quality menu,
    exactly like the real ultrafilms capture (1280x720 ... 5568x3132).
    """
    log = [
        _entry(1, "https://watch.example.com/play",
               body='{"id":"%s","session":"%s"}' % (content_id, session)),
        _entry(2, f"https://api.example.com/cfg?session={session}&_=1"),
    ]
    for i, r in enumerate(renditions):
        log.append(_entry(
            10 + i,
            f"https://{host_cdn}/{content_id}/{r}?expires={expires}&token={token}"))
    return {"host": "watch.example.com", "network_log": log}


# The original pair is SAME-TITLE (content id constant) so content_id is a
# skeleton IDENTITY slot with a recorded value — the case the real ultrafilms
# template was built from.
_RENDITIONS = ["1280x720_60FPS.mp4", "1920x1080_60FPS.mp4",
               "3840x2160_60FPS_S.mp4"]


def _same_title_template():
    a = _capture("53eb2252", _RENDITIONS, "1700000000", "TOKAAA", "sessA")
    b = _capture("53eb2252", _RENDITIONS, "1700009999", "TOKBBB", "sessB")
    draft = wb.build_workbench(synthesize(a, b), captures=(a, b))
    return ct.build_template(draft), (a, b)


# ── workbench role classifier ──────────────────────────────────────
class TestSegmentRole:
    def test_opaque_id_is_identity(self):
        assert wb._segment_role("53eb2252") == wb.IDENTITY
        assert wb._segment_role("a1b2c3d4e5") == wb.IDENTITY

    def test_resolution_descriptor_is_rendition(self):
        for seg in ("1280x720_60FPS.mp4", "3840x2160_60FPS_S.mp4",
                    "2160p.mp4", "4k_hdr.mp4", "1080p_5000kbps.mp4"):
            assert wb._segment_role(seg) == wb.RENDITION, seg

    def test_uuid_filename_stays_identity(self):
        # "<uuid>.mp4" — the id IS the title key, not a rendition descriptor
        assert wb._segment_role(
            "12345678-1234-1234-1234-123456789abc.mp4") == wb.IDENTITY


# ── build_template freezes role + recorded ─────────────────────────
class TestBuildTemplateRoles:
    def test_slots_carry_role_and_recorded(self):
        tmpl, _ = _same_title_template()
        by_name = {s["name"]: s for s in tmpl["goal"]["slots"]}
        assert tmpl["template_version"] == 2
        # content_id was constant across the same-title pair -> identity slot
        assert "content_id" in by_name
        assert by_name["content_id"]["role"] == "identity"
        assert by_name["content_id"]["recorded"] == "53eb2252"
        # the resolution segment -> rendition slot with the recorded member
        rend = next(s for s in tmpl["goal"]["slots"] if s["role"] == "rendition")
        assert rend["recorded"] in _RENDITIONS

    def test_recorded_values_are_path_not_signing(self):
        tmpl, _ = _same_title_template()
        blob = str(tmpl)
        assert "TOKAAA" not in blob and "1700000000" not in blob


# ── the five-way verdict ───────────────────────────────────────────
class TestVerdicts:
    def test_held_same_title_same_rendition(self):
        # diff a capture identical in identity+recorded-rendition -> HELD/clean
        tmpl, (a, b) = _same_title_template()
        res = ct.diff_template(tmpl, b)
        assert res["verdict"] == ct.HELD
        assert res["drift_verdict"] == "clean"
        assert res["decayed"] == []

    def test_identity_change_is_informational(self):
        # a different title, SAME rendition menu -> IDENTITY_CHANGE, clean
        tmpl, _ = _same_title_template()
        other = _capture("ffffffff", _RENDITIONS, "1", "T", "s")
        res = ct.diff_template(tmpl, other)
        assert res["verdict"] == ct.IDENTITY_CHANGE
        assert res["drift_verdict"] == "clean"           # expected, not drift
        cid = next(c for c in res["checks"]
                   if c["prediction"] == "slot:content_id")
        assert cid["status"] == ct.HELD and cid.get("identity_changed") is True

    def test_rendition_drift_same_title_member_gone(self):
        # SAME title, but the recorded rendition member is absent from the menu
        tmpl, _ = _same_title_template()
        no_recorded = _capture(
            "53eb2252",
            ["1280x720_60FPS.mp4", "1920x1080_60FPS.mp4"],  # 3840x2160_S gone
            "1", "T", "s")
        res = ct.diff_template(tmpl, no_recorded)
        assert res["verdict"] == ct.RENDITION_DRIFT
        assert res["drift_verdict"] == "drifted"
        assert "slot:filename" in res["decayed"] or any(
            c["prediction"].startswith("slot:") and c["role"] == "rendition"
            and c["status"] == ct.DRIFTED for c in res["checks"])

    def test_identity_and_rendition_change(self):
        # different title AND recorded rendition absent (the real title2 case)
        tmpl, _ = _same_title_template()
        both = _capture(
            "ba7f0af0",
            ["1280x720_60FPS.mp4", "3840x2160_60FPS.mp4"],  # no _S, new id
            "1", "T", "s")
        res = ct.diff_template(tmpl, both)
        assert res["verdict"] == ct.IDENTITY_AND_RENDITION_CHANGE
        assert res["drift_verdict"] == "drifted"           # rendition called out

    def test_structural_drift_added_segment(self):
        tmpl, _ = _same_title_template()
        changed = _capture("53eb2252", _RENDITIONS, "1", "T", "s")
        for e in changed["network_log"]:
            if "dd.example.com" in e["url"]:
                e["url"] = e["url"].replace("dd.example.com/",
                                            "dd.example.com/v2/")
        res = ct.diff_template(tmpl, changed)
        assert res["verdict"] == ct.STRUCTURAL_DRIFT
        assert res["drift_verdict"] == "drifted"

    def test_missing_host(self):
        tmpl, _ = _same_title_template()
        moved = _capture("53eb2252", _RENDITIONS, "1", "T", "s",
                         host_cdn="other-cdn.example.com")
        res = ct.diff_template(tmpl, moved)
        assert res["verdict"] == ct.MISSING
        assert res["drift_verdict"] == "drifted"


# ── matched-set reporting (the "don't grab the first" fix) ─────────
class TestMatchedSet:
    def test_reports_full_rendition_set_not_first(self):
        tmpl, (a, b) = _same_title_template()
        res = ct.diff_template(tmpl, b)
        rend_name = next(s["name"] for s in tmpl["goal"]["slots"]
                         if s["role"] == "rendition")
        observed = res["goal_match"]["observed_values"][rend_name]
        # all three renditions present, not just the first matched request
        assert set(observed) == set(_RENDITIONS)


# ── migration ──────────────────────────────────────────────────────
class TestMigration:
    def test_v1_template_upgrades_in_place(self):
        tmpl, _ = _same_title_template()
        # synthesize a v1 template: strip role/recorded + set version 1
        v1 = {**tmpl, "template_version": 1}
        v1_goal = {**v1["goal"],
                   "slots": [{"name": s["name"], "regex": s["regex"],
                              "shape": s.get("shape")}
                             for s in tmpl["goal"]["slots"]]}
        v1["goal"] = v1_goal
        mig = ct.migrate_template(v1)
        assert mig["template_version"] == 2
        for s in mig["goal"]["slots"]:
            assert "role" in s and "recorded" in s
        # idempotent
        assert ct.migrate_template(mig) is mig or \
            ct.migrate_template(mig)["template_version"] == 2

    def test_migrated_v1_detects_rendition_drift(self):
        # a v1 template must, after auto-migration on read, catch the member
        # change that v1 masked
        tmpl, _ = _same_title_template()
        v1 = {**tmpl, "template_version": 1}
        v1["goal"] = {**v1["goal"],
                      "slots": [{"name": s["name"], "regex": s["regex"],
                                 "shape": s.get("shape")}
                                for s in tmpl["goal"]["slots"]]}
        member_gone = _capture(
            "53eb2252", ["1280x720_60FPS.mp4"], "1", "T", "s")
        res = ct.diff_template(v1, member_gone)
        assert res["verdict"] == ct.RENDITION_DRIFT
        assert res["drift_verdict"] == "drifted"


# ── posture ────────────────────────────────────────────────────────
class TestPosture:
    def test_diff_never_emits_signing_values(self):
        tmpl, (a, b) = _same_title_template()
        res = ct.diff_template(tmpl, a)
        blob = str(res)
        assert "TOKAAA" not in blob and "1700000000" not in blob
        # no raw query in any echoed URL
        for u in res["goal_match"]["matched_urls"]:
            assert "?" not in u and "token" not in u and "expires" not in u

    def test_no_synthesis_verbs_in_new_code(self):
        src = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "bulk_downloader",
            "capture_template.py")).read()
        body = src.split('"""', 2)[-1]
        for pat in [r"\brequests\.(get|post)\b", r"\burllib\.request\b",
                    r"\bhttpx\b", r"\.download\(", r"\breassemble\b",
                    r"\breplay\b", r"\bsynthesize_signed\b"]:
            assert not re.search(pat, body), f"posture: matched {pat}"
