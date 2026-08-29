"""Row 390 -- a download hold must survive a restart.

MEASURED CONTEXT (2026-08-29). BD saved 5.1 GB of the WRONG scene and wrote the
history row as ``done``. The operator held downloading across the fleet with
/api/pause_all -- a RUNTIME call into runner.pause(), living only in process
memory. scripts/deploy.sh restarts the app on every deployment; systemd restarts
it on every crash. Measured on the held hosts that day: ``paused: false``,
``state: idle``, 53 URLs still queued, and nothing durable recording that a hold
was ever intended. A restart silently re-armed unattended downloading.

WHAT THIS FILE PROVES

  1. RESTART BOUNDARY. A hold recorded by one process is honoured by a
     DIFFERENT process (asserted by PID), which refuses to start workers and
     leaves the queued URL ``pending``.
  2. FAIL CLOSED (CLAUDE.md A7). Store unreadable / corrupt JSON / non-object
     store / malformed record / non-bool ``held`` all resolve to UNKNOWN, and
     UNKNOWN REFUSES. An unavailable measurement is never "no hold, carry on".
  3. VISIBLE. /api/health carries a ``download_hold`` block that distinguishes
     HELD from CLEAR from UNKNOWN, and from an empty queue. A deliberate hold
     keeps ok=true (a held fleet must not report itself unhealthy to the deploy
     that ships this); UNKNOWN degrades.
  4. LIFTABLE AND DURABLE. A lift writes ``held: false`` rather than deleting
     the record, and the lift itself survives a restart.
  5. NEGATIVE CONTROLS. An unheld host still reaches the download path; a
     corrupt record does NOT read as unheld.

RED-first on pristine origin/main (measured at a1a5d5c9; the cut then rebased
onto 1e566fc2 when main advanced): ``runner.start()`` never consults a durable
hold, so phase 2 below reaches the auto-teach pre-flight and reports
``needs_review`` instead of refusing -- 'start() after a restart landed in
'idle' ... (got job 'needs_review')'.

Preconditions are asserted, never assumed: the fixture's record is read back
before the second phase runs, the two phases are proven to be different
processes, the queue is proven non-empty, and the job's status is captured
BEFORE start() so an empty queue cannot manufacture green.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_REPO_ROOT = Path(__file__).resolve().parent.parent
_URL = "https://example.invalid/row390-v1"


# ── restart-boundary harness ────────────────────────────────────────────────

def _run_phase(workdir: Path, body: str) -> dict:
    """Run `body` in a FRESH interpreter rooted at `workdir`.

    A fresh process is the whole point: mutating a module global in place would
    not test the thing this row is about. Everything is isolated -- cwd, HOME,
    TMPDIR, BD_HOME, BD_INSTALL_DIR (so the sqlite file lands in the tmpdir) --
    and BD_DISABLE_KEEPALIVE suppresses every background thread.
    """
    script = textwrap.dedent(body)
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(_REPO_ROOT),
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "BD_HOME": str(workdir),
        "BD_INSTALL_DIR": str(workdir),
        "BD_DISABLE_KEEPALIVE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    proc = subprocess.run([sys.executable, "-c", script], cwd=str(workdir),
                          env=env, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, (
        f"phase exited {proc.returncode}\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr[-4000:]}")
    marker = "<<<ROW390>>>"
    assert marker in proc.stdout, (
        f"phase produced no result marker\nSTDOUT:\n{proc.stdout}\n"
        f"STDERR:\n{proc.stderr[-4000:]}")
    return json.loads(proc.stdout.split(marker, 1)[1].strip().splitlines()[0])


_ARM = """
    import json, os
    from bulk_downloader import download_hold as dh
    written = dh.hold("row390-wrong-scene", note="held after the 5.1GB miss")
    st = dh.hold_state()
    print("<<<ROW390>>>")
    print(json.dumps({"pid": os.getpid(), "written": bool(written),
                      "state": st["state"], "reason": st["reason"],
                      "store_exists": os.path.exists("app_config.json")}))
"""

_LIFT = """
    import json, os
    from bulk_downloader import download_hold as dh
    written = dh.lift(note="incident closed")
    raw = json.loads(open("app_config.json", encoding="utf-8").read())
    st = dh.hold_state()
    print("<<<ROW390>>>")
    print(json.dumps({"pid": os.getpid(), "written": bool(written),
                      "state": st["state"],
                      "key_present": "download_hold" in raw,
                      "held_value": raw.get("download_hold", {}).get("held")}))
"""

# Phase 2 uses auto_teach_first_run=True deliberately: on a tree WITHOUT the
# hold gate, start() runs all the way to the auto-teach pre-flight and marks the
# URL needs_review with zero worker threads -- a real traversal of the download
# admission path that spawns no browser in the sandbox. So "reached the download
# path" and "was refused before it" are distinguishable outcomes, and an empty
# queue cannot fake either one.
_START = """
    import json, os
    os.environ.setdefault("BD_DISABLE_KEEPALIVE", "1")
    from bulk_downloader.db import db_init
    db_init()
    from bulk_downloader import download_hold as dh
    from bulk_downloader.runner import SiteRunner
    pre = dh.hold_state()
    r = SiteRunner("row390_hold", {"name": "row390", "auto_teach_first_run": True})
    r.load_urls([%(url)r])
    before = dict(r.jobs)[%(url)r]["status"]
    queued = len(r.jobs)
    r.start()
    after = dict(r.jobs)[%(url)r]["status"]
    out = {"pid": os.getpid(), "pre_state": pre["state"], "state": r._state,
           "queued": queued, "job_before": before, "job_after": after,
           "workers": len(r._worker_threads)}
    try: r.stop()
    except Exception: pass
    try: r._stop_auto_retry()
    except Exception: pass
    print("<<<ROW390>>>")
    print(json.dumps(out))
""" % {"url": _URL}


# ── 1. the headline: a hold survives a process restart ──────────────────────

def test_hold_recorded_by_one_process_is_enforced_by_the_next(tmp_path):
    from bulk_downloader import download_hold as dh

    arm = _run_phase(tmp_path, _ARM)
    # PRECONDITIONS: the record was really written, and the store really exists.
    assert arm["written"] is True, arm
    assert arm["store_exists"] is True, arm
    assert arm["state"] == dh.HELD, arm
    # Read it back from OUTSIDE the writing process before relying on it.
    on_disk = json.loads((tmp_path / "app_config.json").read_text(encoding="utf-8"))
    assert on_disk["download_hold"]["held"] is True, on_disk
    assert dh.hold_state(tmp_path / "app_config.json")["state"] == dh.HELD

    run = _run_phase(tmp_path, _START)
    # PRECONDITION: this is genuinely a different process -- a restart, not a
    # mutated module global.
    assert run["pid"] != arm["pid"], (arm["pid"], run["pid"])
    # PRECONDITION: there was something to download.
    assert run["queued"] == 1, run
    assert run["job_before"] == "pending", run
    # The verdict.
    assert run["pre_state"] == dh.HELD, run
    assert run["state"] == dh.STATE_HELD, (
        f"start() after a restart landed in {run['state']!r}; a durable hold "
        f"must refuse before the download path (got job {run['job_after']!r})")
    assert run["workers"] == 0, run
    assert run["job_after"] == "pending", run   # held, not failed, not consumed


# ── 2. negative control: an unheld host still downloads ─────────────────────

def test_unheld_host_reaches_the_download_path(tmp_path):
    """No hold record at all -> start() proceeds. Proves the gate is not a
    blanket refusal and that the phase-2 harness really reaches the download
    path when nothing holds it."""
    from bulk_downloader import download_hold as dh

    assert not (tmp_path / "app_config.json").exists()
    run = _run_phase(tmp_path, _START)
    assert run["queued"] == 1 and run["job_before"] == "pending", run
    assert run["pre_state"] == dh.CLEAR, run
    assert run["state"] not in (dh.STATE_HELD, dh.STATE_UNKNOWN), run
    # It got all the way to the auto-teach pre-flight: the download admission
    # path was genuinely traversed.
    assert run["job_after"] == "needs_review", run


# ── 3. the lift is explicit AND durable ─────────────────────────────────────

def test_lift_survives_a_restart_and_does_not_delete_the_record(tmp_path):
    from bulk_downloader import download_hold as dh

    arm = _run_phase(tmp_path, _ARM)
    assert arm["state"] == dh.HELD, arm

    lift = _run_phase(tmp_path, _LIFT)
    assert lift["pid"] != arm["pid"], (arm["pid"], lift["pid"])
    assert lift["written"] is True, lift
    # A lift is a POSITIVE record, not an absence: a hold must never be
    # clearable by the record simply going missing.
    assert lift["key_present"] is True, lift
    assert lift["held_value"] is False, lift
    assert lift["state"] == dh.CLEAR, lift

    run = _run_phase(tmp_path, _START)
    assert run["pid"] not in (arm["pid"], lift["pid"]), run
    assert run["pre_state"] == dh.CLEAR, run
    assert run["state"] not in (dh.STATE_HELD, dh.STATE_UNKNOWN), run
    assert run["job_after"] == "needs_review", run


# ── 4. fail closed: UNKNOWN refuses, and never reads as unheld ──────────────

def _write_store(tmp_path, text: str) -> Path:
    p = tmp_path / "app_config.json"
    p.write_text(text, encoding="utf-8")
    return p


@pytest.mark.parametrize("label,text", [
    ("corrupt_json", "{not json at all"),
    ("truncated_json", '{"download_hold": {"held": tru'),
    ("store_not_object", '["download_hold"]'),
    ("record_not_object", '{"download_hold": true}'),
    ("record_is_string", '{"download_hold": "held"}'),
    ("held_is_string_false", '{"download_hold": {"held": "false"}}'),
    ("held_is_string_true", '{"download_hold": {"held": "true"}}'),
    ("held_is_number", '{"download_hold": {"held": 1}}'),
    ("held_is_null", '{"download_hold": {"held": null}}'),
    ("held_missing", '{"download_hold": {"reason": "operator"}}'),
])
def test_unreadable_or_corrupt_hold_state_is_unknown_not_unheld(
        tmp_path, label, text):
    from bulk_downloader import download_hold as dh
    p = _write_store(tmp_path, text)
    st = dh.hold_state(p)
    assert st["state"] == dh.UNKNOWN, (label, st)
    assert st["held"] is True, (label, st)          # UNKNOWN refuses
    allowed, st2 = dh.downloads_allowed(p)
    assert allowed is False, (label, st2)
    assert dh.runner_state_token(st) == dh.STATE_UNKNOWN, (label, st)


def test_unopenable_store_is_unknown(tmp_path):
    """A store that will not open (EACCES) is UNKNOWN, never CLEAR."""
    from bulk_downloader import download_hold as dh
    if os.geteuid() == 0:
        pytest.skip("root ignores the permission bits this asserts")
    p = _write_store(tmp_path, json.dumps({"download_hold": {"held": False}}))
    # PRECONDITION: readable right now, and reading it says CLEAR.
    assert dh.hold_state(p)["state"] == dh.CLEAR
    os.chmod(p, 0)
    try:
        # PRECONDITION: the chmod actually took -- an open must now raise.
        with pytest.raises(OSError):
            p.read_text(encoding="utf-8")
        st = dh.hold_state(p)
        assert st["state"] == dh.UNKNOWN, st
        assert st["reason"] == "store_unreadable", st
        assert dh.downloads_allowed(p)[0] is False
    finally:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)


def test_clear_states_are_positive_evidence(tmp_path):
    from bulk_downloader import download_hold as dh
    missing = tmp_path / "nope" / "app_config.json"
    assert dh.hold_state(missing)["state"] == dh.CLEAR
    assert dh.hold_state(missing)["reason"] == "store_absent"
    p = _write_store(tmp_path, json.dumps({"log_level": "INFO"}))
    st = dh.hold_state(p)
    assert st["state"] == dh.CLEAR and st["reason"] == "no_record", st
    p2 = _write_store(tmp_path, json.dumps(
        {"download_hold": {"held": True, "reason": "operator"}}))
    assert dh.hold_state(p2)["state"] == dh.HELD


def test_unknown_refuses_start_in_a_fresh_process(tmp_path):
    """End-to-end fail-closed: a corrupt record in the real store makes a
    freshly started process refuse, with the UNKNOWN token -- distinguishable
    from a clean hold."""
    from bulk_downloader import download_hold as dh
    _write_store(tmp_path, '{"download_hold": {"held": "yes"}}')
    run = _run_phase(tmp_path, _START)
    assert run["queued"] == 1 and run["job_before"] == "pending", run
    assert run["pre_state"] == dh.UNKNOWN, run
    assert run["state"] == dh.STATE_UNKNOWN, run
    assert run["state"] != dh.STATE_HELD, run
    assert run["workers"] == 0 and run["job_after"] == "pending", run


# ── 5. resume() is gated too ────────────────────────────────────────────────

def test_resume_refuses_under_a_hold(clean_workdir):
    """resume() flips paused -> running WITHOUT passing through start(); left
    ungated it would let /api/resume_all defeat the durable hold."""
    from bulk_downloader import download_hold as dh
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner
    db_init()

    r = SiteRunner("row390_resume", {"name": "row390",
                                     "auto_teach_first_run": True})
    try:
        r.load_urls([_URL])
        assert len(r.jobs) == 1                      # precondition: non-empty
        r._state = "paused"
        # Control: with no hold, resume() works exactly as before.
        assert dh.hold_state()["state"] == dh.CLEAR
        r.resume()
        assert r._state == "running", r._state

        r._state = "paused"
        assert dh.hold("row390-resume-test") is True
        assert dh.hold_state()["state"] == dh.HELD   # precondition: recorded
        r.resume()
        assert r._state == dh.STATE_HELD, r._state
    finally:
        try: r.stop()
        except Exception: pass
        try: r._stop_auto_retry()
        except Exception: pass


# ── 6. the hold is VISIBLE on the health surface ────────────────────────────

def test_health_reports_the_hold_distinctly_from_idle(clean_workdir):
    from bulk_downloader import download_hold as dh
    import bulk_downloader.app as a
    client = a.app.test_client()

    clear = client.get("/api/health").get_json()
    assert clear["download_hold"]["state"] == dh.CLEAR, clear["download_hold"]
    assert clear["download_hold"]["downloads_allowed"] is True

    assert dh.hold("row390-health", note="wrong scene") is True
    held = client.get("/api/health").get_json()
    block = held["download_hold"]
    assert block["state"] == dh.HELD, block
    assert block["downloads_allowed"] is False, block
    assert block["reason"] == "row390-health", block
    assert block["note"] == "wrong scene", block
    assert isinstance(block["since"], (int, float)), block
    # A deliberate hold must NOT report the host unhealthy: /api/health is what
    # scripts/deploy.sh checks, and the fleet is held right now.
    assert held["ok"] is True, held
    # ...and it is distinguishable from "nothing queued".
    assert "queue_depth" in held and block["state"] != "idle"

    Path("app_config.json").write_text('{"download_hold": {"held": 7}}',
                                       encoding="utf-8")
    unknown = client.get("/api/health").get_json()
    assert unknown["download_hold"]["state"] == dh.UNKNOWN, unknown["download_hold"]
    assert unknown["download_hold"]["downloads_allowed"] is False
    assert unknown["ok"] is False, unknown
    assert unknown.get("degraded") == "download_hold_unknown", unknown


def test_health_v2_reports_the_hold(clean_workdir):
    from bulk_downloader import download_hold as dh
    import bulk_downloader.app as a
    assert dh.hold("row390-health-v2") is True
    body = a.app.test_client().get("/api/health/v2").get_json()
    assert body["download_hold"]["state"] == dh.HELD, body.get("download_hold")
    assert body["download_hold"]["downloads_allowed"] is False


# ── 7. the operator surface: set / read / lift over the API ─────────────────

def test_api_sets_reads_and_lifts_the_hold_durably(clean_workdir):
    from bulk_downloader import download_hold as dh
    import bulk_downloader.app as a
    client = a.app.test_client()

    before = client.get("/api/download_hold").get_json()
    assert before["state"] == dh.CLEAR, before

    res = client.post("/api/download_hold",
                      json={"reason": "wrong-scene", "note": "5.1GB miss",
                            "by": "operator"})
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["state"] == dh.HELD, res.get_json()
    # DURABLE: the record is on disk, not in a module global.
    raw = json.loads(Path("app_config.json").read_text(encoding="utf-8"))
    assert raw["download_hold"]["held"] is True, raw
    assert raw["download_hold"]["reason"] == "wrong-scene", raw
    assert client.get("/api/download_hold").get_json()["state"] == dh.HELD

    res = client.post("/api/download_hold/lift", json={"note": "closed"})
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["state"] == dh.CLEAR, res.get_json()
    raw = json.loads(Path("app_config.json").read_text(encoding="utf-8"))
    assert "download_hold" in raw and raw["download_hold"]["held"] is False, raw


def test_hold_key_is_not_schema_declared(clean_workdir):
    """global_config.apply_fail_closed would rewrite a malformed declared value
    to its safe_default -- turning a corrupt hold record into a confident
    'not held'. The key must stay out of that coercion path."""
    from bulk_downloader import global_config as gc
    from bulk_downloader import download_hold as dh
    assert dh.HOLD_KEY not in gc.GLOBAL_CONFIG_SCHEMA
