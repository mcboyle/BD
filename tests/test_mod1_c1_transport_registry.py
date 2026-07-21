"""MOD-1 C-1 (RED-first): transport-tagged takeover registry + sweep denominator.

The concurrency cap and the no-orphan sweep must SEE a VNC takeover session even
though it has no SSE frame channel. On pristine source `open_channel` takes no
`kind` argument and there is no `channel_kind` / `register_vnc_census`, so every
assertion here fails -- which is the point. The fix makes one registry span both
transports (do NOT fork it) and lets the sweep report an unverifiable VNC backend
as a loud third state, never a silent 0.

Faithful to BD_MOD1_COEXIST_PLAN C-1:
  - a fake vnc-kind session is COUNTED by the cap and SEEN by the sweep
  - unknown-fails-loud: a wired-but-unreachable vnc backend -> "vnc" in unverified
"""
from __future__ import annotations

import bulk_downloader.takeover as tk
from bulk_downloader import captcha_relay as cr


def _reset():
    for sid in list(tk.list_channel_sids()):
        tk.close_channel(sid)
    cr._reset_for_tests()


# ── the registry carries a transport kind ───────────────────────────────────

def test_open_channel_accepts_kind_and_cap_counts_both_transports():
    _reset()
    tk.open_channel("cdp-1")                 # default kind == cdp
    tk.open_channel("vnc-1", kind="vnc")     # RED on pristine: open_channel(kind=) TypeError
    # the concurrency-cap denominator (active_channel_count) spans BOTH transports
    assert tk.active_channel_count() == 2, "cap must count the vnc session"
    assert set(tk.list_channel_sids()) == {"cdp-1", "vnc-1"}
    _reset()


def test_kind_is_queryable_and_filterable():
    _reset()
    tk.open_channel("cdp-2")
    tk.open_channel("vnc-2", kind="vnc")
    assert tk.channel_kind("vnc-2") == "vnc"          # RED on pristine: no channel_kind
    assert tk.channel_kind("cdp-2") == "cdp"
    assert tk.list_channel_sids(kind="vnc") == ["vnc-2"]
    assert tk.active_channel_count(kind="vnc") == 1
    assert tk.active_channel_count(kind="cdp") == 1
    _reset()


def test_default_kind_is_cdp_backward_compatible():
    _reset()
    tk.open_channel("legacy")
    assert tk.channel_kind("legacy") == "cdp"
    # existing single-arg callers keep working and count as before
    assert tk.active_channel_count() == 1
    _reset()


# ── the sweep's denominator now spans the vnc transport ──────────────────────

def test_sweep_reaps_orphan_vnc_channel():
    # a vnc-kind channel bound to no active solving entry is an orphan the sweep
    # must reap -- its denominator (list_channel_sids) already spans it once the
    # registry is transport-tagged.
    _reset()
    tk.open_channel("vnc-orphan", kind="vnc")     # RED on pristine: cannot register a vnc kind
    rep = cr.sweep_report(now=1.0)
    assert rep["orphan_channels"] >= 1
    assert "vnc-orphan" not in tk.list_channel_sids(), "orphan vnc channel not reaped"
    _reset()


def test_sweep_reports_vnc_backend_unverified_when_unreachable():
    # unknown-fails-loud: a vnc session exists but the vnc backend census raises
    # -> "vnc" in unverified, never a silent 0 (mirrors the browsers census path).
    _reset()

    def _boom():
        raise RuntimeError("vnc backend down")

    cr.register_vnc_census(_boom)                 # RED on pristine: no register_vnc_census
    tk.open_channel("vnc-live", kind="vnc")
    rep = cr.sweep_report(now=1.0)
    assert "vnc" in rep["unverified"], "unreachable vnc backend must be a loud third state"
    _reset()


def test_no_vnc_sessions_means_no_vnc_noise():
    # a pure-CDP host never using vnc must NOT see spurious "vnc" unverified:
    # an empty vnc denominator is empty because unused, not because unseen.
    _reset()
    tk.open_channel("cdp-only")
    rep = cr.sweep_report(now=1.0)
    assert "vnc" not in rep["unverified"]
    _reset()
