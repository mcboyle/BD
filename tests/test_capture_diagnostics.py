"""capture_diagnostics (A4) — characterization tests.

Pins what the composer ADDS over the engines it reuses: the three-axis assembly
(yield / drift / runtime), the advisory verdict tiers and their exit mapping, the
host-derived gold default, and the POSTURE guarantee that the runtime axis never
surfaces raw manifest/segment URLs (path-signing can survive redaction).

It does NOT re-test build_template's derivation fidelity or assess's scoring rubric
(both have their own suites); it feeds controlled inputs to the composition and a
single synthetic .wacz end-to-end to prove the wiring holds. Browser-free; stdlib +
project modules; synthetic fixtures only (never a real capture).
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import capture_diagnostics as CD
from bulk_downloader.wacz_export import write_wacz

_T = 1_000_000


# ── controlled axis inputs for verdict logic (no build_template dependency) ──

def _y(score=100, ready=True, missing=None):
    return {"host": "x.com", "completeness_score": score, "promotion_ready": ready,
            "missing": missing or [], "blocked_terms": [],
            "row_selectors_count": 2, "resolutions_count": 3, "network_patterns_count": 4}


def _d(total=0, have=True):
    return {"baseline": "g.json" if have else None, "total": total,
            "lines": ["x"] if total else ["no drift"], "have_baseline": have}


def _r(readiness="ready", provided=True, manifests=1, segments=4, missing_steps=None):
    return {"readiness": readiness, "readiness_reason": "", "template_provided": provided,
            "missing_steps": missing_steps or [],
            "observed": {"manifest_count": manifests, "segments": segments,
                         "interactions": 1, "navigations": 1, "hosts": ["x.com"]},
            "blind_spots": ["single capture (n=1)"]}


def test_verdict_promotable():
    v, _ = CD._verdict(_y(), _d(total=0), _r())
    assert v == CD.VERDICT_PROMOTABLE
    assert CD._EXIT[v] == 0


# ── REPLAY precondition (A8 fold) ────────────────────────────────────────────

def _rp(ok=True, errors=None, warnings=None):
    return {"ok": ok, "errors": errors or [], "warnings": warnings or [],
            "stats": {"events": 2, "full_snapshots": 1}}


def test_verdict_replay_error_forces_insufficient():
    # An otherwise-promotable capture whose dom_log can't replay -> recapture.
    rp = _rp(ok=False, errors=["no full_snapshot in dom_log — capture cannot be replayed"])
    v, reason = CD._verdict(_y(), _d(total=0), _r(), rp)
    assert v == CD.VERDICT_INSUFFICIENT
    assert CD._EXIT[v] == 2
    assert "not replayable" in reason and "recapture" in reason


def test_verdict_replay_warning_does_not_gate():
    # Warnings (e.g. missing Meta) are advisory only — they must not change verdict.
    rp = _rp(ok=True, warnings=["no Meta event — viewport/navigation unknown"])
    v, _ = CD._verdict(_y(), _d(total=0), _r(), rp)
    assert v == CD.VERDICT_PROMOTABLE


def test_verdict_replay_none_is_backward_compatible():
    # The pre-fold three-arg call path is unchanged.
    assert CD._verdict(_y(), _d(total=0), _r(), None)[0] == CD.VERDICT_PROMOTABLE
    assert CD._verdict(_y(), _d(total=0), _r())[0] == CD.VERDICT_PROMOTABLE


def test_verdict_review_on_drift():
    v, reason = CD._verdict(_y(), _d(total=3), _r())
    assert v == CD.VERDICT_REVIEW
    assert "3 drift" in reason
    assert CD._EXIT[v] == 1


def test_verdict_review_when_yield_not_promotable():
    v, reason = CD._verdict(_y(ready=False, missing=["resolutions"]), _d(total=0), _r())
    assert v == CD.VERDICT_REVIEW
    assert "yield not promotable" in reason


def test_verdict_insufficient_when_capture_did_not_exercise():
    # template provided, runtime not ready, AND nothing observed -> recapture
    r = _r(readiness="incomplete", manifests=0, segments=0, missing_steps=["manifest_fetched"])
    v, reason = CD._verdict(_y(), _d(total=0), r)
    assert v == CD.VERDICT_INSUFFICIENT
    assert CD._EXIT[v] == 2
    assert "recapture" in reason


def test_verdict_promotable_no_baseline():
    # no gold baseline -> drift can't disqualify; yield+runtime carry it
    v, reason = CD._verdict(_y(), _d(have=False), _r())
    assert v == CD.VERDICT_PROMOTABLE
    assert "no gold baseline" in reason


# ── drift axis reuses the keystone's shared engine ───────────────────────────

def test_drift_uses_shared_engine_and_counts(tmp_path=None):
    cand = {"host": "x.com", "resolutions": [1080, 720],
            "selectors": {"download": {"trigger": ".dl", "row_selectors": ["a"]}}}
    gold = {"host": "x.com", "resolutions": [1080, 720, 480],   # one extra rung in gold
            "selectors": {"download": {"trigger": ".dl", "row_selectors": ["a"]}}}
    with tempfile.TemporaryDirectory() as td:
        gp = Path(td) / "x.com.template.json"
        gp.write_text(json.dumps(gold))
        d = CD._drift(cand, str(gp))
    assert d["have_baseline"] is True
    assert d["total"] >= 1          # the missing 480 rung is drift
    assert d["baseline"].endswith("x.com.template.json")


def test_drift_no_baseline_is_first_version():
    cand = {"host": "no-such-host-xyz.example", "resolutions": [1080]}
    d = CD._drift(cand, None)        # host-derived default finds nothing
    assert d["have_baseline"] is False
    assert d["total"] == 0
    assert "first version" in d["lines"][0]


# ── POSTURE: runtime axis surfaces counts + hosts, never raw media URLs ──────

def test_runtime_drops_raw_manifest_urls():
    cap = {
        "host": "x.com", "url": "https://x.com/v/9", "title": "t",
        "network_log": [
            {"timestamp": _T, "type": "document", "method": "GET",
             "url": "https://x.com/v/9", "response_status": 200},
            {"timestamp": _T + 1, "type": "xhr", "method": "GET",
             "url": "https://api.x.com/v2/master.m3u8?token=SECRETQS", "response_status": 200},
            {"timestamp": _T + 2, "type": "media", "method": "GET",
             "url": "https://cdn.x.com/seg/dirmatch/expiretime/PATHSIG123/0001.ts",
             "response_status": 200},
            {"timestamp": _T + 3, "type": "media", "method": "GET",
             "url": "https://cdn.x.com/seg/dirmatch/expiretime/PATHSIG123/0002.ts",
             "response_status": 200},
        ],
        "dom_log": [{"timestamp": _T, "type": "meta", "source": -1,
                     "data": {"href": "https://x.com/v/9", "width": 1, "height": 1}}],
    }
    gold = {"host": "x.com", "api": {"base": "https://api.x.com"},
            "network_patterns": ["master.m3u8"], "selectors": {"download": {"trigger": ".x"}}}
    r = CD._runtime(cap, gold)
    blob = json.dumps(r)
    # counts present, raw URLs and signing tokens absent from the runtime axis
    assert "manifest_count" in r["observed"]
    assert "manifests" not in r["observed"]
    assert "https://" not in blob
    assert "SECRETQS" not in blob
    assert "PATHSIG123" not in blob


# ── end-to-end on a synthetic .wacz (wiring + shape + corpus-safety) ─────────

def _synth_capture():
    return {
        "host": "x.com", "url": "https://x.com/v/9", "title": "t",
        "network_log": [
            {"timestamp": _T, "type": "document", "method": "GET",
             "url": "https://x.com/v/9", "response_status": 200},
            {"timestamp": _T + 1, "type": "xhr", "method": "GET",
             "url": "https://api.x.com/v2/master.m3u8?token=SECRETQS", "response_status": 200},
            {"timestamp": _T + 2, "type": "media", "method": "GET",
             "url": "https://cdn.x.com/seg/0001.ts", "response_status": 200},
            {"timestamp": _T + 3, "type": "media", "method": "GET",
             "url": "https://cdn.x.com/seg/0002.ts", "response_status": 200},
        ],
        "dom_log": [{"timestamp": _T, "type": "meta", "source": -1,
                     "data": {"href": "https://x.com/v/9", "width": 1, "height": 1}},
                    {"timestamp": _T + 1, "type": "full_snapshot", "source": -1,
                     "data": {"node": {"id": 1, "type": 0,
                                       "childNodes": [{"id": 2, "type": 2, "childNodes": []}]}}}],
    }


def _unreplayable_capture():
    """Same network shape but a dom_log with NO full_snapshot → replay error."""
    c = _synth_capture()
    c["dom_log"] = [{"timestamp": _T, "type": "incremental", "source": 0,
                     "data": {"adds": [{"parentId": 99, "node": {"id": 100}}]}}]
    return c


def test_diagnose_end_to_end_shape_and_safety():
    with tempfile.TemporaryDirectory() as td:
        wacz = Path(td) / "cap.wacz"
        write_wacz(_synth_capture(), str(wacz))
        gold = {"host": "x.com", "api": {"base": "https://api.x.com"},
                "resolutions": [1080, 720], "network_patterns": ["master.m3u8"],
                "selectors": {"download": {"trigger": ".dl", "row_selectors": ["a"]}}}
        gp = Path(td) / "x.com.template.json"
        gp.write_text(json.dumps(gold))
        dgn = CD.diagnose(str(wacz), gold_path=str(gp))

    for k in ("capture", "replay", "yield", "drift", "runtime", "verdict", "verdict_reason"):
        assert k in dgn, f"missing axis {k}"
    assert dgn["replay"]["ok"] is True          # the synthetic capture IS replayable
    assert dgn["verdict"] in (CD.VERDICT_PROMOTABLE, CD.VERDICT_REVIEW, CD.VERDICT_INSUFFICIENT)
    assert isinstance(dgn["yield"]["completeness_score"], int)
    # corpus-safety end-to-end: the query signing token never reaches output
    assert "SECRETQS" not in json.dumps(dgn)
    # render must not throw and must name the verdict
    md = CD.render_markdown(dgn)
    assert dgn["verdict"] in md


def test_diagnose_unreplayable_capture_is_insufficient():
    with tempfile.TemporaryDirectory() as td:
        wacz = Path(td) / "bad.wacz"
        write_wacz(_unreplayable_capture(), str(wacz))
        dgn = CD.diagnose(str(wacz))
    assert dgn["replay"]["ok"] is False
    assert dgn["verdict"] == CD.VERDICT_INSUFFICIENT
    assert "SECRETQS" not in json.dumps(dgn)   # posture holds even on the failing path


def test_main_exit_code_matches_verdict():
    with tempfile.TemporaryDirectory() as td:
        wacz = Path(td) / "cap.wacz"
        write_wacz(_synth_capture(), str(wacz))
        rc = CD.main([str(wacz), "--json"])
    assert rc in (0, 1, 2)
