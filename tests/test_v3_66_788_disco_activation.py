"""v3.66.788 -- A-DISCO cut 4: activation. Wire disco_triage to the live app via
disco_runner, register the DEFAULT-OFF toggle + the behaviour-neutral scheduler
task, and fold A-DISCO's run readout into automation_status as a third net.

The load-bearing properties:
  * DEFAULT-OFF + behaviour-neutral: scheduled_disco no-ops unless the auto_disco
    toggle is on, so registering the bg task changes nothing until an operator
    opts in;
  * the master off-switch still dominates (per-site run goes through
    run_discovery_triage, which is inert when the switch is engaged);
  * VISIBLE: A-DISCO is an automation net, so its state shows up in
    automation_status.status() -- and, per this codebase's rule, "enabled but
    never ran" is UNKNOWN (a third state that is not a green light), while
    "disabled" is neutral (an opt-in feature being off is not a red light).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bulk_downloader import disco_runner as dr
from bulk_downloader import lifecycle_automation as la
from bulk_downloader import automation_status as as_
from bulk_downloader import bg_scheduler as bg


# ── fixtures ─────────────────────────────────────────────────────────────────

class _FakeRunner:
    def __init__(self):
        self.loaded = []

    def load_urls(self, urls):
        self.loaded.extend(urls)


def _links_html(*urls):
    return "".join(f'<a href="{u}">x</a>' for u in urls)


def _fetch_from(pages):
    def _f(url):
        return pages.get(url)
    return _f


def _enable(monkeypatch):
    monkeypatch.setattr(dr, "_enabled", lambda: True)


# ── scheduled_disco: DEFAULT-OFF is behaviour-neutral ────────────────────────

def test_scheduled_disco_is_default_off():
    # No monkeypatch: the toggle is off by default, so the run is a no-op.
    out = dr.scheduled_disco(s_cfg={"s1": {"disco": {"enabled": True,
                                                     "root_url": "https://h.com/lib"}}},
                             runners={"s1": _FakeRunner()})
    assert out["ran"] is False
    assert out["reason"] == "disabled"


def test_scheduled_disco_enabled_enumerates_and_enqueues(monkeypatch):
    _enable(monkeypatch)
    runner = _FakeRunner()
    pages = {"https://en.h.com/lib": _links_html(
        "https://en.h.com/get/1.mp4",       # high (media) -> enqueue
        "https://en.h.com/v/1",             # review -> not enqueued
        "https://en.h.com/login",           # reject
    )}
    out = dr.scheduled_disco(
        s_cfg={"s1_en": {"disco": {"enabled": True, "root_url": "https://en.h.com/lib",
                                "max_depth": 1}}},
        runners={"s1_en": runner},
        fetch_fn=_fetch_from(pages),
        off_switch_fn=lambda: False)
    assert out["ran"] is True and out["sites"] == 1
    assert runner.loaded == ["https://en.h.com/get/1.mp4"]
    run = out["runs"][0]
    assert run["site_id"] == "s1_en" and run["enqueued"] == 1


def test_content_pattern_from_config_drives_auto_queue(monkeypatch):
    _enable(monkeypatch)
    runner = _FakeRunner()
    pages = {"https://cp.h.com/lib": _links_html("https://cp.h.com/v/1", "https://cp.h.com/v/2",
                                              "https://cp.h.com/about")}
    out = dr.scheduled_disco(
        s_cfg={"s1_cp": {"disco": {"enabled": True, "root_url": "https://cp.h.com/lib",
                                "url_pattern": r"/v/\d+", "max_depth": 1}}},
        runners={"s1_cp": runner}, fetch_fn=_fetch_from(pages),
        off_switch_fn=lambda: False)
    assert set(runner.loaded) == {"https://cp.h.com/v/1", "https://cp.h.com/v/2"}


def test_ar4_enqueue_cap_from_config(monkeypatch):
    _enable(monkeypatch)
    runner = _FakeRunner()
    links = [f"https://ar4.h.com/v/{i}" for i in range(20)]
    pages = {"https://ar4.h.com/lib": _links_html(*links)}
    dr.scheduled_disco(
        s_cfg={"s1_ar4": {"disco": {"enabled": True, "root_url": "https://ar4.h.com/lib",
                                "url_pattern": r"/v/\d+", "max_enqueue": 3,
                                "max_depth": 1}}},
        runners={"s1_ar4": runner}, fetch_fn=_fetch_from(pages),
        off_switch_fn=lambda: False)
    assert len(runner.loaded) == 3


def test_off_switch_makes_per_site_inert(monkeypatch):
    _enable(monkeypatch)
    runner = _FakeRunner()
    pages = {"https://off.h.com/lib": _links_html("https://off.h.com/get/1.mp4")}
    out = dr.scheduled_disco(
        s_cfg={"s1_off": {"disco": {"enabled": True, "root_url": "https://off.h.com/lib"}}},
        runners={"s1_off": runner}, fetch_fn=_fetch_from(pages),
        off_switch_fn=lambda: True)                 # off-switch engaged
    assert runner.loaded == []                      # nothing enqueued


def test_sites_without_disco_config_are_skipped(monkeypatch):
    _enable(monkeypatch)
    r1, r2 = _FakeRunner(), _FakeRunner()
    pages = {"https://skip.h.com/lib": _links_html("https://skip.h.com/get/1.mp4")}
    out = dr.scheduled_disco(
        s_cfg={"s1_skip": {"disco": {"enabled": True, "root_url": "https://skip.h.com/lib",
                                "max_depth": 1}},
               "s2_skip": {},                            # no disco cfg -> skipped
               "s3_skip": {"disco": {"enabled": False}}},  # disabled -> skipped
        runners={"s1_skip": r1, "s2_skip": r2},
        fetch_fn=_fetch_from(pages), off_switch_fn=lambda: False)
    assert out["sites"] == 1
    assert r2.loaded == []


def test_scheduled_disco_never_raises(monkeypatch):
    _enable(monkeypatch)

    class _BoomRunner:
        def load_urls(self, urls):
            raise RuntimeError("queue exploded")
    pages = {"https://boom.h.com/lib": _links_html("https://boom.h.com/get/1.mp4")}
    out = dr.scheduled_disco(
        s_cfg={"s1_boom": {"disco": {"enabled": True, "root_url": "https://boom.h.com/lib",
                                "max_depth": 1}}},
        runners={"s1_boom": _BoomRunner()}, fetch_fn=_fetch_from(pages),
        off_switch_fn=lambda: False)
    assert out["ran"] is True                        # a bad runner did not kill the pass


# ── disco_status: disabled / unknown / ok (UNKNOWN is a third state) ─────────

def test_disco_status_disabled_is_neutral():
    # Toggle off by default: disabled is not a red light.
    st = dr.disco_status()
    assert st["state"] == "disabled"
    assert st["ok"] is True and st["enabled"] is False


def test_disco_status_enabled_never_ran_is_unknown(monkeypatch):
    _enable(monkeypatch)
    # Force "never ran": no rows for this fresh BD_HOME. If a prior test in-process
    # wrote rows, clear them.
    monkeypatch.setattr(dr, "latest_run", lambda: None)
    st = dr.disco_status()
    assert st["state"] == "unknown"
    assert st["ok"] is False and st["enabled"] is True


def test_disco_status_ok_after_a_run(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(dr, "latest_run", lambda: {
        "ts": 1.0, "site_id": "s1", "enumerated": 5, "enqueued": 2,
        "review": 1, "reject": 2})
    st = dr.disco_status()
    assert st["state"] == "ok" and st["ok"] is True and st["enabled"] is True
    assert st["enqueued"] == 2


# ── toggle + scheduler + status fold ─────────────────────────────────────────

def test_auto_disco_toggle_registered_non_keystone():
    assert "auto_disco" in la.AUTOMATION_TOGGLES
    assert "auto_disco" not in la.KEYSTONE_REQUIRED   # queueing is reversible


def test_scheduler_registers_disco_task():
    bg.register_default_tasks()
    names = {t["name"] for t in bg.status()["tasks"]}
    assert "disco.scheduled_run" in names


def test_automation_status_includes_disco_net():
    s = as_.status()
    assert "disco" in s
    # aggregate ok must NOT be dragged by an opt-in net (disabled by default).
    assert s["ok"] == bool(s["rehearsal"]["ok"] and s["pipeline"]["ok"])
