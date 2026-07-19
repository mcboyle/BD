"""A3 -- `quarantined` as a first-class runtime state + auto-quarantine (v3.66.473).

Today a quarantine is just "status != enabled" -- indistinguishable from a
manual disable. A3 makes it first-class: a quarantine takes an A0 GENERATIONAL
backup first (restorable; aborts if the backup fails), records its KIND +
EVIDENCE, fires `template.quarantined`, and -- for a risky-selector quarantine --
marks the template `auto_promotable=False` so A5 can never auto-promote it.
A1's over-threshold recommendation routes into the auto-quarantine wrapper.

Contract proven here (RED-first):
  1. _quarantine takes an A0 generational backup (templates/.gold_backups/...)
     in addition to the single gold .bak -- the write is restorable.
  2. a quarantine fires `template.quarantined` (captured via plugins.fire_hook).
  3. a quarantine records quarantine_kind + quarantine_evidence (distinct from
     a manual disable, which carries neither).
  4. a risky quarantine sets auto_promotable=False (A5 must skip it).
  5. _quarantine ABORTS (live untouched) if the A0 backup cannot be taken.
  6. auto_quarantine_on_drift_if_enabled acts ONLY on an over-threshold
     ("quarantine") bundle, and only with toggle + keystone; below-threshold or
     toggle-off is a no-op.

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


def _tpl(v, status="enabled"):
    return {"host": "example.com", "status": status, "version": str(v),
            "selectors": {"player": {"play_button": f".p{v}"}},
            "api": {}, "network_patterns": []}


def _setup(d, live, gold=None):
    rd = Path(d) / "templates" / "reviewed"
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "example.com.template.json").write_text(json.dumps(live), "utf-8")
    if gold is not None:
        (rd / "example.com.template.json.bak").write_text(json.dumps(gold), "utf-8")
    return rd


def _live(rd):
    return json.loads((rd / "example.com.template.json").read_text("utf-8"))


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
def _toggles(on_set, keystone=True):
    orig_read, orig_keystone = la._read_toggle, la.keystone_available
    on_keys = {la.AUTOMATION_TOGGLES[n] for n in on_set}
    la._read_toggle = lambda key: key in on_keys
    la.keystone_available = lambda: keystone
    try:
        yield
    finally:
        la._read_toggle, la.keystone_available = orig_read, orig_keystone


def _gold_backups(rd, host="example.com"):
    root = rd.parent / ".gold_backups" / host
    return [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []


def test_quarantine_takes_generational_backup():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1))
    r = ld._quarantine("example.com", "drift", reviewed_dir=rd)
    assert r["ok"] is True, r
    # the single gold .bak (legacy recovery) AND the A0 generational backup
    assert (rd / "example.com.template.json.bak").is_file()
    gens = _gold_backups(rd)
    assert len(gens) == 1, gens
    assert (gens[0] / "manifest.json").is_file()
    shutil.rmtree(d, ignore_errors=True)


def test_quarantine_fires_event():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1))
    with _captured_events() as fired:
        ld._quarantine("example.com", "drift", reviewed_dir=rd)
    names = [n for (n, _p) in fired]
    assert names.count("template.quarantined") == 1, fired
    payload = [p for (n, p) in fired if n == "template.quarantined"][0]
    assert payload.get("host") == "example.com"
    shutil.rmtree(d, ignore_errors=True)


def test_quarantine_records_kind_and_evidence():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1))
    ev = {"drift": 7, "ts": "x"}
    ld._quarantine("example.com", "over threshold", reviewed_dir=rd,
                   kind="drift", evidence=ev)
    t = _live(rd)
    assert t["status"] == "quarantined"
    assert t["quarantine_kind"] == "drift", t
    assert t.get("quarantine_evidence", {}).get("drift") == 7, t
    shutil.rmtree(d, ignore_errors=True)


def test_risky_quarantine_is_not_auto_promotable():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1))
    ld._quarantine("example.com", "risky selector", reviewed_dir=rd, kind="risky")
    t = _live(rd)
    assert t["status"] == "quarantined"
    assert t["quarantine_kind"] == "risky"
    assert t.get("auto_promotable") is False, t
    shutil.rmtree(d, ignore_errors=True)


def test_quarantine_aborts_when_backup_fails():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1))
    live_before = (rd / "example.com.template.json").read_bytes()
    # Occupy the .gold_backups dir name with a file -> generational backup fails.
    blocker = rd.parent / ".gold_backups"
    blocker.write_text("blocked", "utf-8")
    r = ld._quarantine("example.com", "drift", reviewed_dir=rd)
    assert r["ok"] is False, r
    assert "backup" in (r.get("error") or "").lower(), r
    # live must be UNTOUCHED -- still enabled, no quarantine
    assert (rd / "example.com.template.json").read_bytes() == live_before
    shutil.rmtree(d, ignore_errors=True)


def test_auto_quarantine_on_drift_only_over_threshold():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1))
    review_bundle = {"host": "example.com", "drift": 1, "recommendation": "review",
                     "ts": "t1", "threshold": 5, "diff_lines": []}
    quar_bundle = {"host": "example.com", "drift": 9, "recommendation": "quarantine",
                   "ts": "t2", "threshold": 0, "diff_lines": ["[a.b] CHANGED"]}

    with _toggles(on_set={"auto_quarantine"}, keystone=True):
        # below threshold -> no-op
        r1 = ld.auto_quarantine_on_drift_if_enabled("example.com", review_bundle,
                                                    reviewed_dir=rd)
        assert r1.get("skipped"), r1
        assert _live(rd)["status"] == "enabled"
        # over threshold -> quarantines with evidence
        r2 = ld.auto_quarantine_on_drift_if_enabled("example.com", quar_bundle,
                                                    reviewed_dir=rd)
        assert r2.get("quarantined") == "example.com", r2
        assert _live(rd)["status"] == "quarantined"
    shutil.rmtree(d, ignore_errors=True)


def test_auto_quarantine_on_drift_gated_off_is_noop():
    d = tempfile.mkdtemp()
    rd = _setup(d, live=_tpl(1))
    quar_bundle = {"host": "example.com", "drift": 9, "recommendation": "quarantine",
                   "ts": "t", "threshold": 0, "diff_lines": []}
    # toggle off -> no-op even for an over-threshold bundle
    r = ld.auto_quarantine_on_drift_if_enabled("example.com", quar_bundle, reviewed_dir=rd)
    assert r.get("skipped"), r
    assert _live(rd)["status"] == "enabled"
    shutil.rmtree(d, ignore_errors=True)
