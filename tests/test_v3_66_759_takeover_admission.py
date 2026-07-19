"""v3.66.759 -- MOD-1 A-5a: remote-takeover admission controls.

Two safety controls that gate whether a captcha solve session runs in REMOTE
(headless + screencast) mode; either one failing downgrades to VISIBLE, which
always works (a solve is never blocked, only kept off the remote path):

  * KILL-SWITCH captcha_takeover_enabled (bool, safety-bearing, fail-closed to
    OFF): remote takeover is disabled unless explicitly enabled. An operator
    flips it False to instantly stop all remote takeover.
  * CONCURRENCY CAP captcha_takeover_max_concurrent (~2): no more than N remote
    solve sessions (open takeover channels) at once.

RED-first on pristine v3.66.758 (keys + helpers ABSENT).
"""
from __future__ import annotations


def test_kill_switch_declared_fail_closed_off():
    from bulk_downloader import global_config as gc
    e = gc.GLOBAL_CONFIG_SCHEMA.get("captcha_takeover_enabled")
    assert e is not None, "captcha_takeover_enabled not declared"
    assert e.get("type") is bool and e.get("safety") is True, "must be a safety-bearing bool"
    assert e.get("safe_default") is False, "fail-closed default must be OFF (remote disabled)"


def test_concurrency_cap_declared():
    from bulk_downloader import global_config as gc
    e = gc.GLOBAL_CONFIG_SCHEMA.get("captcha_takeover_max_concurrent")
    assert e is not None, "captcha_takeover_max_concurrent not declared"
    assert e.get("safe_default") == "2"


def test_takeover_enabled_reads_fail_closed():
    from bulk_downloader import runner_auth as ra
    assert ra._takeover_enabled({}) is False                                  # absent -> off
    assert ra._takeover_enabled({"captcha_takeover_enabled": True}) is True
    assert ra._takeover_enabled({"captcha_takeover_enabled": "true"}) is True
    assert ra._takeover_enabled({"captcha_takeover_enabled": "false"}) is False


def test_max_concurrent_reads_with_floor():
    from bulk_downloader import runner_auth as ra
    assert ra._takeover_max_concurrent({}) == 2                                # default
    assert ra._takeover_max_concurrent({"captcha_takeover_max_concurrent": "5"}) == 5
    assert ra._takeover_max_concurrent({"captcha_takeover_max_concurrent": "0"}) == 1   # floor
    assert ra._takeover_max_concurrent({"captcha_takeover_max_concurrent": "junk"}) == 2  # bad -> default


def test_remote_admitted_requires_all_three_gates():
    from bulk_downloader import runner_auth as ra
    on = {"captcha_takeover_mode": "remote", "captcha_takeover_enabled": True,
          "captcha_takeover_max_concurrent": "2"}
    assert ra._remote_admitted(on, active_count=0) is True     # all gates pass
    assert ra._remote_admitted(on, active_count=1) is True     # still under cap (1 < 2)
    assert ra._remote_admitted(on, active_count=2) is False    # at cap -> visible
    # kill-switch off -> visible even in remote mode
    off = dict(on); off["captcha_takeover_enabled"] = False
    assert ra._remote_admitted(off, active_count=0) is False
    # visible mode -> never remote
    vis = dict(on); vis["captcha_takeover_mode"] = "visible"
    assert ra._remote_admitted(vis, active_count=0) is False


def test_active_channel_count():
    from bulk_downloader import takeover
    base = takeover.active_channel_count()
    takeover.close_channel("a5a-1"); takeover.close_channel("a5a-2")
    takeover.open_channel("a5a-1"); takeover.open_channel("a5a-2")
    try:
        assert takeover.active_channel_count() == base + 2
    finally:
        takeover.close_channel("a5a-1"); takeover.close_channel("a5a-2")
    assert takeover.active_channel_count() == base
