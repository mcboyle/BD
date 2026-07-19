"""v3.66.522 -- dead-endpoint / swallowed-signature fixes (VR-P04/P06/P07).

These three handlers 500 (or silently no-op) on EVERY call because a caller
passed an argument the callee never accepted. The pre-existing suites only
asserted the routes were *registered* (test_v3_43_68_jsonapi.py
::test_probe_endpoint_registered) -- never that a POST returns non-500 -- so the
breakage shipped invisibly. These tests exercise the real call path.

RED on pristine v3.66.521:
  * VR-P04  POST /api/jsonapi/probe        -> 500 (``_check_csrf(request)`` forwards
            ``request`` to the 0-arg ``app._check_csrf()`` -> TypeError, raised
            ABOVE the handler's try/except).
  * VR-P06  POST /api/library/regen_nfos   -> 500 (handler passes ``dry_run=`` but
            ``regen_nfos_from_history`` has no such kwarg -> TypeError, caught ->
            500). Also: the documented default ``dry_run=True`` (preview) must NOT
            write .nfo sidecars.
  * VR-P07  runner.start() smart-wakeup    -> dead-when-enabled: the call passes
            ``site_id=`` (no such param -> swallowed TypeError -> "allowing
            start") AND reads ``decision.get("decision") == "stay_asleep"`` -- a
            key ``should_wake_now`` never returns (its contract is ``{"wake":...}``)
            -- so even with the TypeError gone the deferral branch can never fire.

Run under run_tests.py (zero-arg functions, in-test imports, try/finally global
restore) and real pytest.
"""
from __future__ import annotations

import os
import tempfile


# --------------------------------------------------------------------------
# VR-P04 -- /api/jsonapi/probe no longer 500s on the CSRF path
# --------------------------------------------------------------------------
def test_vr_p04_jsonapi_probe_does_not_500():
    """The lone outlier that did ``if not _check_csrf(request):`` -- forwarding
    ``request`` into the 0-arg app._check_csrf -> TypeError -> 500. After the fix
    it matches the 164 sibling handlers (bare ``_check_csrf()``) and proceeds to
    the body-validation path."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    r = c.post("/api/jsonapi/probe", json={})
    body = r.get_data(as_text=True)[:200]
    assert r.status_code != 500, f"VR-P04: probe 500s on every call -> {r.status_code} {body}"
    # ``if not _check_csrf():`` would also 403 on SUCCESS (None is falsy); the
    # correct fix must not spuriously reject the sessionless test client either.
    assert r.status_code != 403, f"VR-P04: spurious 403 on success path -> {body}"
    # Empty body proceeds past CSRF then fails validation: "site_root required" -> 400.
    assert r.status_code == 400, f"VR-P04: expected 400 (site_root required), got {r.status_code} {body}"


# --------------------------------------------------------------------------
# VR-P06 -- /api/library/regen_nfos no longer 500s + dry_run is honored
# --------------------------------------------------------------------------
def test_vr_p06_regen_nfos_endpoint_does_not_500():
    from bulk_downloader import app as a
    from bulk_downloader import db
    db.db_init()
    c = a.app.test_client()
    r = c.post("/api/library/regen_nfos", json={})
    body = r.get_data(as_text=True)[:200]
    assert r.status_code != 500, f"VR-P06: regen_nfos 500s on every call -> {body}"
    assert r.status_code == 200, f"VR-P06: expected 200, got {r.status_code} {body}"
    j = r.get_json()
    for k in ("written", "skipped", "missing_files", "errors"):
        assert k in j, f"VR-P06: result missing key {k!r}: {j}"


def test_vr_p06_dry_run_does_not_write_nfo():
    """The handler defaults ``dry_run=True`` (preview). The callee must HONOR it:
    count what would be written but never touch the filesystem. Dropping the
    kwarg instead would silently make the default destructive."""
    from bulk_downloader import library_final as lf
    from bulk_downloader import db
    db.db_init()

    d = tempfile.mkdtemp()
    clip = os.path.join(d, "clip.mp4")
    with open(clip, "w", encoding="utf-8") as fh:
        fh.write("x")
    nfo = os.path.join(d, "clip.nfo")

    sid = "vrp06_dryrun_site"
    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history (site_id, site_name, url, status, filename, message) "
            "VALUES (?,?,?,?,?,?)",
            (sid, "Demo", "https://x/clip", "done", clip, "msg"))

    calls = []
    orig_write = lf.write_nfo
    lf.write_nfo = lambda *a, **k: (calls.append((a, k)), True)[1]
    try:
        # dry_run=True -> preview only: NO write_nfo call, no .nfo on disk,
        # but the row is still COUNTED as a would-write.
        out_dry = lf.regen_nfos_from_history(site_id=sid, dry_run=True)
        assert calls == [], f"VR-P06: dry_run still called write_nfo: {calls}"
        assert not os.path.exists(nfo), "VR-P06: dry_run wrote a .nfo sidecar to disk"
        assert out_dry.get("written", 0) >= 1, f"VR-P06: dry_run did not count would-writes: {out_dry}"

        # dry_run=False -> the write actually happens.
        out_wet = lf.regen_nfos_from_history(site_id=sid, dry_run=False)
        assert len(calls) == 1, f"VR-P06: non-dry_run did not write: calls={calls}"
    finally:
        lf.write_nfo = orig_write
        with db.db_conn() as cx:
            cx.execute("DELETE FROM history WHERE site_id=?", (sid,))


# --------------------------------------------------------------------------
# VR-P07 -- smart_wakeup actually defers when enabled
# --------------------------------------------------------------------------
def test_vr_p07_should_wake_now_contract():
    """Pins the real contract the runner call-site got wrong on two counts:
    ``should_wake_now`` returns ``{"wake": ...}`` (NOT ``{"decision": ...}``) and
    has no ``site_id`` parameter."""
    from bulk_downloader import smart_wakeup as sw
    out = sw.should_wake_now(quiet_hours=[])
    assert "wake" in out, f"VR-P07: contract changed, no 'wake' key: {out}"
    assert "decision" not in out, "VR-P07: runner read a 'decision' key that never existed"
    raised = False
    try:
        sw.should_wake_now(quiet_hours=[], site_id="x")  # the broken :755 signature
    except TypeError:
        raised = True
    assert raised, "VR-P07: should_wake_now accepts site_id? then the call-site was fine"


def test_vr_p07_smart_wakeup_defers_when_not_wake():
    """End-to-end on runner.start(): with use_smart_wakeup enabled and the wakeup
    module deciding wake=False, start() must DEFER (state -> wakeup_deferred) and
    must call should_wake_now WITHOUT a site_id kwarg.

    Tripwire: admission_hold is stubbed to 'cookies_expired' so that if the
    deferral branch does NOT fire (the pristine bug), start() falls through to the
    admission gate and returns safely with state='cookies_expired' instead of
    launching real worker threads -- making the RED deterministic and side-effect
    free.
    """
    from bulk_downloader import runner as R
    from bulk_downloader import smart_wakeup as sw
    from bulk_downloader import admission as adm
    from bulk_downloader import db
    db.db_init()

    captured = {}

    def fake_wake(**kw):
        captured.update(kw)
        return {"wake": False, "reason": "quiet"}

    def fake_hold(config, *a, **k):
        return "cookies_expired"

    orig_wake = sw.should_wake_now
    orig_hold = adm.admission_hold
    sw.should_wake_now = fake_wake
    adm.admission_hold = fake_hold
    try:
        r = R.SiteRunner("vrp07_wake_site", {"name": "t", "use_smart_wakeup": True})
        r.start()
        assert "site_id" not in captured, \
            f"VR-P07 (#1): call-site still passes site_id= to should_wake_now: {captured}"
        assert r._state == "wakeup_deferred", \
            f"VR-P07 (#2): deferral did not fire (wrong decision key); state={r._state!r}"
    finally:
        sw.should_wake_now = orig_wake
        adm.admission_hold = orig_hold
