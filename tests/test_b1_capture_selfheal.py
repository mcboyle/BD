"""B1 residual — capture_in_flight self-heal hardening.

The 515 self-heal cleared a stale ``cfg["template_capture"]`` marker ONLY when the
pipeline's draft exists. A capture that DIES after launch but before a draft is
built (SIGTERM, crash, killed shell) left the marker stuck forever -> a phantom
Finish/Cancel control on the site card.

B1 closes that gap with a PID-liveness check (primary) + a conservative age
backstop, scoped to the no-draft case. Explicitly NOT wacz-exists (the wacz exists
for the whole legitimate in-flight window).

Errs SAFE in every direction:
  * a live capture with the recorded process identity is NEVER cleared;
  * a dead pid reused by an unrelated process reaches conservative age recovery;
  * a marker with neither a usable pid nor a usable started_at -> treated as
    in-flight (the conservative default; the pre-B1 markers look like this).

Companion to test_cap_finish.py (the draft-exists self-heal) + test_cap_cancel.py.
"""
from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

import bulk_downloader.app as bd_app


def _seed(sid, *, draft_exists=False, pid=None, started_at=None):
    """Seed bd_app.s_cfg[sid] with an in-flight capture marker. The draft path is
    a real tempdir path that does NOT exist unless ``draft_exists`` is True."""
    d = Path(tempfile.mkdtemp())
    wacz = d / f"host_{sid}_ts.wacz"
    draft = d / "host.template-draft.json"
    if draft_exists:
        draft.write_text("{}", encoding="utf-8")
    marker = {
        "profile_dir": str(d / "profiles" / f"{sid}-cloak"),
        "wacz": str(wacz),
        "draft": str(draft),
        "display": ":99",
    }
    if pid is not None:
        marker["pid"] = pid
    if started_at is not None:
        marker["started_at"] = started_at
    bd_app.s_cfg[sid] = {
        "name": sid,
        "login_url": "https://example.test/",
        "template_onboarding": "capture_required",
        "template_capture": marker,
    }
    return sid


def _dead_pid():
    """A pid that is guaranteed not alive (the child has exited + been reaped)."""
    p = subprocess.Popen(["sh", "-c", "exit 0"])
    p.wait()
    return p.pid


def _status_in_flight(client, sid):
    r = client.get(f"/api/sites/{sid}/template_status")
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()["capture_in_flight"]


# (a) dead pid + no draft -> marker cleared
def test_dead_pid_no_draft_clears_marker(fresh_app):
    import os as _os
    sid = _seed("b1a", pid=_dead_pid(), started_at=time.time())
    assert _status_in_flight(fresh_app, sid) is False
    assert "template_capture" not in bd_app.s_cfg[sid]
    _os.name  # keep import used


# (b) live pid + no draft -> NOT cleared (a live capture is never cleared)
def test_live_pid_no_draft_keeps_marker(fresh_app):
    import os as _os
    sid = _seed("b1b", pid=_os.getpid(), started_at=time.time())
    assert _status_in_flight(fresh_app, sid) is True
    assert "template_capture" in bd_app.s_cfg[sid]


# (c) stale started_at (no usable pid) + no draft -> cleared by the age backstop
def test_stale_started_at_no_pid_clears_marker(fresh_app):
    sid = _seed("b1c", started_at=time.time() - (41 * 60))
    assert _status_in_flight(fresh_app, sid) is False
    assert "template_capture" not in bd_app.s_cfg[sid]


# (d) fresh started_at + live pid + no draft -> NOT cleared
def test_fresh_started_at_live_pid_keeps_marker(fresh_app):
    import os as _os
    sid = _seed("b1d", pid=_os.getpid(), started_at=time.time())
    assert _status_in_flight(fresh_app, sid) is True
    assert "template_capture" in bd_app.s_cfg[sid]


# (e) regression: the draft-exists path still clears
def test_draft_exists_still_clears(fresh_app):
    sid = _seed("b1e", draft_exists=True, started_at=time.time())
    assert _status_in_flight(fresh_app, sid) is False
    assert "template_capture" not in bd_app.s_cfg[sid]


# (f) a marker with NEITHER pid NOR started_at is the conservative default: it
# is treated as in-flight (this is the pre-B1 marker shape; never auto-clear it).
def test_no_pid_no_age_keeps_marker(fresh_app):
    sid = _seed("b1f")
    assert _status_in_flight(fresh_app, sid) is True
    assert "template_capture" in bd_app.s_cfg[sid]


# (g) the exact live identity older than the age limit is STILL kept. Numeric
# liveness alone is not authority because a PID can be recycled.
def test_live_pid_old_age_still_kept(fresh_app):
    import os as _os
    pid = _os.getpid()
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    fields = raw.rsplit(") ", 1)[1].split()
    assert len(fields) > 19
    pid_start = fields[19]
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(
        encoding="ascii"
    ).strip()
    assert pid_start and boot_id
    sid = _seed("b1g", pid=pid, started_at=time.time() - (41 * 60))
    bd_app.s_cfg[sid]["template_capture"].update({
        "pid_start": pid_start,
        "boot_id": boot_id,
    })
    assert _status_in_flight(fresh_app, sid) is True
    assert "template_capture" in bd_app.s_cfg[sid]
