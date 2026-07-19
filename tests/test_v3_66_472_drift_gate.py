"""A1 -- drift-as-a-scheduled-gate (v3.66.472).

sweep_and_respond already applies the (gated) MUTATING responses (quarantine /
flag). The missing layer is the STAGE-AND-ROUTE review path: on a drift hit,
stage a review bundle (diff lines + recommendation) under a review queue and
fire the `drift.detected` plugin event -- WITHOUT auto-changing any live
template. Above the drift threshold the recommendation escalates to
`quarantine` (the signal A3 consumes).

Contract proven here (RED-first -- stage_drift_reviews does not exist yet):
  1. a drift hit STAGES a review bundle (diff_lines + recommendation) to the
     review queue, and the LIVE template is byte-identical afterward.
  2. `drift.detected` fires once per offender (captured via plugins.fire_hook).
  3. recommendation = "review" at/below threshold, "quarantine" above it.
  4. no offenders -> no bundle, no event.
  5. scheduled_sweep with the drift_sweep toggle ON stages reviews; OFF -> skip.

Zero-arg functions + tempfile so this runs under run_tests.py AND pytest.
"""
import contextlib
import json
import shutil
import tempfile
from pathlib import Path

from bulk_downloader import lifecycle_drift as ld
from bulk_downloader import lifecycle_automation as la
from bulk_downloader import plugins as bd_plugins


def _tpl(v):
    return {"host": "example.com", "status": "enabled", "version": str(v),
            "selectors": {"player": {"play_button": f".p{v}"}},
            "api": {}, "network_patterns": []}


def _setup(d, live, gold):
    rd = Path(d) / "templates" / "reviewed"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "example.com.template.json").write_text(json.dumps(live), "utf-8")
    if gold is not None:
        (rd / "example.com.template.json.bak").write_text(json.dumps(gold), "utf-8")
    return rd


@contextlib.contextmanager
def _captured_events():
    fired = []
    orig = bd_plugins.fire_hook
    bd_plugins.fire_hook = lambda name, payload: fired.append((name, payload))
    try:
        yield fired
    finally:
        bd_plugins.fire_hook = orig


@contextlib.contextmanager
def _toggles(on_set):
    orig = la._read_toggle
    on_keys = {la.AUTOMATION_TOGGLES[n] for n in on_set}
    la._read_toggle = lambda key: key in on_keys
    try:
        yield
    finally:
        la._read_toggle = orig


def _bundles(rd, host="example.com"):
    root = rd.parent / ".drift_review" / host
    if not root.is_dir():
        return []
    return [p / "bundle.json" for p in sorted(root.iterdir())
            if (p / "bundle.json").is_file()]


def test_drift_hit_stages_bundle_live_untouched():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(2), gold=_tpl(1))
    live_before = (rd / "example.com.template.json").read_bytes()

    res = ld.stage_drift_reviews(reviewed_dir=rd, threshold=0, fire_events=False)
    assert res["ok"] is True, res
    assert res["staged"] >= 1, res

    bundles = _bundles(rd)
    assert len(bundles) == 1, bundles
    b = json.loads(bundles[0].read_text("utf-8"))
    assert b["host"] == "example.com"
    assert b["drift"] >= 1
    assert isinstance(b.get("diff_lines"), list) and b["diff_lines"]
    assert "recommendation" in b
    # live must be byte-identical -- staging never touches the template
    assert (rd / "example.com.template.json").read_bytes() == live_before
    shutil.rmtree(d, ignore_errors=True)


def test_drift_detected_event_fires_per_offender():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(2), gold=_tpl(1))
    with _captured_events() as fired:
        ld.stage_drift_reviews(reviewed_dir=rd, threshold=0, fire_events=True)
    names = [n for (n, _p) in fired]
    assert names.count("drift.detected") == 1, fired
    payload = [p for (n, p) in fired if n == "drift.detected"][0]
    assert payload.get("host") == "example.com"
    assert "recommendation" in payload
    shutil.rmtree(d, ignore_errors=True)


def test_recommendation_review_vs_quarantine_by_threshold():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(2), gold=_tpl(1))   # drift == 1 (play_button differs)

    # Read each call's recommendation from its OWN returned bundle, never from
    # _bundles(rd)[-1]. The ts is second-granular with a NON-monotonic suffix
    # (time.monotonic_ns() & 0xFFFFFF), so two same-second stagings sort by a
    # pseudo-random hex tiebreak and [-1] is not guaranteed to be the later
    # call -- the v3.66.472 drift_gate flaky-parallel failure (on-stash @491:
    # b2 read the threshold=5 bundle and asserted quarantine != review).

    # threshold 5: drift(1) <= 5 -> "review"
    r1 = ld.stage_drift_reviews(reviewed_dir=rd, threshold=5, fire_events=False)
    b1 = json.loads(Path(r1["bundles"][0]["bundle"]).read_text("utf-8"))
    assert b1["recommendation"] == "review", b1

    # threshold 0: drift(1) > 0 -> "quarantine" (feeds A3)
    r2 = ld.stage_drift_reviews(reviewed_dir=rd, threshold=0, fire_events=False)
    b2 = json.loads(Path(r2["bundles"][0]["bundle"]).read_text("utf-8"))
    assert b2["recommendation"] == "quarantine", b2
    shutil.rmtree(d, ignore_errors=True)


def test_no_offenders_no_bundle_no_event():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1), gold=_tpl(1))   # identical -> zero drift
    with _captured_events() as fired:
        res = ld.stage_drift_reviews(reviewed_dir=rd, threshold=0, fire_events=True)
    assert res["staged"] == 0, res
    assert _bundles(rd) == []
    assert [n for (n, _p) in fired if n == "drift.detected"] == []
    shutil.rmtree(d, ignore_errors=True)


def test_scheduled_sweep_stages_when_on_skips_when_off():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(2), gold=_tpl(1))

    # toggle OFF -> scheduled_sweep is a no-op, no bundle staged
    res_off = ld.scheduled_sweep(reviewed_dir=rd)
    assert res_off.get("skipped") == "drift_sweep disabled", res_off
    assert _bundles(rd) == []

    # toggle ON -> scheduled_sweep stages the review bundle
    with _toggles(on_set={"drift_sweep"}):
        res_on = ld.scheduled_sweep(reviewed_dir=rd)
    assert res_on["ok"] is True, res_on
    assert res_on.get("review_staged", 0) >= 1, res_on
    assert len(_bundles(rd)) >= 1
    shutil.rmtree(d, ignore_errors=True)
