"""v3.66.706 -- X-AUTO-1: restore REHEARSAL (the first cut of the A0-unblocked track).

A0 (declared landed @705, shipped @471) guarantees a backup is TAKEN and is RESTORABLE
AT WRITE TIME: template_keystone.safe_overwrite aborts the write if the generational
backup cannot be taken.

But `backup_verify.smoke_restore()` -- the function that PROVES a backup on disk still
restores -- was reachable ONLY by a manual API call (POST /api/backup/smoke_restore).
NOTHING ever ran it. So nobody had ever confirmed that the backups sitting on disk still
restore. backup_verify's own docstring says it: "A backup that's never restored is a wish,
not a backup... Designed to run nightly via bg_scheduler" -- and it never was.

This cut runs the rehearsal on a schedule and reports the verdict.

THE LOAD-BEARING DESIGN POINT: the daily digest is ZERO-DELTA-SILENT (a quiet day sends
nothing, to protect the operator's inbox). A FAILED REHEARSAL MUST NOT BE SILENCED BY THAT
RULE. A broken backup is exactly the thing you need to hear about on a quiet day -- a
digest that stays silent about an unrestorable backup is worse than no digest, because it
looks like everything is fine.

RED-first: every assertion below fails on pristine v3.66.705 (rehearse() does not exist).
"""
import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bulk_downloader import backup_verify as BV
from bulk_downloader import daily_digest as DD


def _good_backup(tmp_path) -> str:
    """A real, restorable backup zip (what smoke_restore should accept)."""
    p = tmp_path / "bd-backup-good.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("sites_config.json", json.dumps({"demo": {"name": "demo"}}))
        z.writestr("templates/reviewed/demo.template.json", json.dumps({"host": "demo"}))
    return str(p)


def _corrupt_backup(tmp_path) -> str:
    """A backup that CANNOT be restored -- the fault-injection control. Without this,
    a rehearsal that always says 'ok' is theatre."""
    p = tmp_path / "bd-backup-corrupt.zip"
    p.write_bytes(b"PK\x03\x04 this is not a real zip at all")
    return str(p)


# ── the rehearsal itself ─────────────────────────────────────────────────
def test_rehearse_reports_ok_on_a_restorable_backup(tmp_path):
    r = BV.rehearse(backup_path=_good_backup(tmp_path))
    assert r["ok"] is True
    assert r["checked_at"] > 0
    assert r["path"].endswith("bd-backup-good.zip")


def test_rehearse_DETECTS_an_unrestorable_backup(tmp_path):
    """THE control. A rehearsal that cannot fail proves nothing. If this ever passes
    silently, the whole feature is decorative."""
    r = BV.rehearse(backup_path=_corrupt_backup(tmp_path))
    assert r["ok"] is False, "an unrestorable backup MUST be reported as not-ok"
    assert r.get("error")


def test_rehearse_with_no_backup_at_all_is_not_ok(tmp_path):
    """No backup on disk is not 'fine' -- it is the loudest possible failure of a
    backup system. It must NOT read as ok."""
    r = BV.rehearse(backup_path=str(tmp_path / "does-not-exist.zip"))
    assert r["ok"] is False


def test_rehearse_never_touches_the_live_tree(tmp_path):
    """smoke_restore restores into a temp sandbox. The rehearsal must inherit that:
    a verification that mutates what it verifies is not a verification."""
    live = tmp_path / "live"
    live.mkdir()
    canary = live / "sites_config.json"
    canary.write_text('{"untouched": true}')
    before = canary.read_bytes()
    BV.rehearse(backup_path=_good_backup(tmp_path))
    assert canary.read_bytes() == before, "the rehearsal must not write to live"


def test_rehearse_records_the_verdict(tmp_path):
    """The verdict must persist -- otherwise the digest can only report on the run that
    just happened, and an overnight failure is invisible by morning."""
    BV.rehearse(backup_path=_good_backup(tmp_path))
    last = BV.last_rehearsal()
    assert last is not None
    assert "ok" in last and "checked_at" in last


# ── the digest wiring, and the silence rule ──────────────────────────────
def test_digest_body_carries_the_rehearsal_verdict():
    body = DD.build_body({"captures": 0}, {"captures": 0},
                         rehearsal={"ok": True, "checked_at": 1.0, "age_days": 2})
    assert "restore" in body.lower()


def test_a_FAILED_rehearsal_BREAKS_the_zero_delta_silence():
    """THE load-bearing assertion of this cut.

    The digest stays silent on a quiet day. But a broken backup on a quiet day is
    exactly what must NOT be silent -- silence would look like 'all fine'. A failed
    rehearsal must force the notification through."""
    sent = {}

    def _notify(title, body):
        sent["title"] = title
        sent["body"] = body

    res = DD.run_digest(metrics={"captures": 0},
                        state_path=None,
                        _notifier=_notify,
                        rehearsal={"ok": False, "error": "corrupt archive",
                                   "checked_at": 1.0})
    assert res["sent"] is True, "a FAILED rehearsal must break the zero-delta silence"
    assert "restore" in (sent.get("body") or "").lower()


def test_a_PASSING_rehearsal_does_NOT_break_the_silence():
    """NEG control: the escape hatch must be narrow. A HEALTHY backup on a quiet day is
    still a quiet day -- if a passing rehearsal notified, the digest would spam daily and
    the operator would mute it, defeating the point."""
    sent = {}
    res = DD.run_digest(metrics={"captures": 0},
                        state_path=None,
                        _notifier=lambda t, b: sent.setdefault("x", (t, b)),
                        rehearsal={"ok": True, "checked_at": 1.0})
    assert res["sent"] is False
    assert not sent


# ── opt-in: default OFF, byte-identical when unused ──────────────────────
def test_rehearsal_is_opt_in_and_default_off():
    assert BV.REHEARSAL_ENABLE_KEY == "automation.restore_rehearsal_enabled"
    assert BV.rehearsal_enabled() is False, "must default OFF (opt-in, like the digest)"


def test_digest_unchanged_when_no_rehearsal_is_supplied():
    """NEG control: with no rehearsal the digest behaves EXACTLY as before -- this cut
    must be inert until the operator turns it on."""
    body = DD.build_body({"captures": 3}, {"captures": 1})
    assert "restore" not in body.lower()
    res = DD.run_digest(metrics={"captures": 0}, state_path=None,
                        _notifier=lambda t, b: None)
    assert res["sent"] is False          # zero delta, no rehearsal -> silent as always
