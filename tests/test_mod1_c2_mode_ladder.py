"""MOD-1 C-2 (RED-first): the self-downgrade mode ladder + derived vnc probe.

    remote_vnc --(stack absent | probe unknown)--> remote
    remote     --(kill-switch off | over cap)-----> visible
    visible    = always available, never blocked

The probe is DERIVED (a stub returning unavailable until the C-4 backend),
UNKNOWN downgrades (never assume vnc works), the kill-switch masters BOTH remote
paths, and any downgrade carries a non-empty operator-facing reason (a silent
downgrade is a lie by omission, plan 1.2).

RED on pristine @805: _TAKEOVER_MODES lacks remote_vnc; _resolve_effective_mode,
_vnc_available and register_vnc_probe are absent.
"""
from __future__ import annotations

from bulk_downloader import runner_auth as ra

ON = {"captcha_takeover_enabled": True, "captcha_takeover_max_concurrent": "2"}


def _reset():
    ra._vnc_probe = None


def test_remote_vnc_is_a_recognized_mode():
    _reset()
    assert "remote_vnc" in ra._TAKEOVER_MODES          # RED: modes == (visible, remote)
    assert ra._resolve_takeover_mode({"captcha_takeover_mode": "remote_vnc"}) == "remote_vnc"


def test_unknown_mode_still_falls_to_visible():
    # regression guard: widening the enum must NOT weaken the typo fallback.
    _reset()
    assert ra._resolve_takeover_mode({"captcha_takeover_mode": "bogus"}) == "visible"
    assert ra._resolve_takeover_mode({}) == "visible"


def test_visible_is_always_effective_visible_no_reason():
    _reset()
    assert ra._resolve_effective_mode({"captcha_takeover_mode": "visible"}, 0) == ("visible", "")


def test_remote_path_unchanged_by_the_ladder():
    _reset()
    cfg = dict(ON, captcha_takeover_mode="remote")
    assert ra._resolve_effective_mode(cfg, 0) == ("remote", "")
    eff, why = ra._resolve_effective_mode(cfg, 2)   # at cap
    assert eff == "visible" and why


def test_remote_vnc_downgrades_to_remote_when_stack_absent():
    _reset()                                        # no probe -> stub unavailable
    cfg = dict(ON, captcha_takeover_mode="remote_vnc")
    eff, why = ra._resolve_effective_mode(cfg, 0)
    assert eff == "remote", "absent/unknown vnc stack must fall to remote, never assume vnc"
    assert why and "remote_vnc" in why


def test_remote_vnc_promotes_only_when_probe_reports_available():
    _reset()
    ra.register_vnc_probe(lambda cfg: (True, ""))
    cfg = dict(ON, captcha_takeover_mode="remote_vnc")
    assert ra._resolve_effective_mode(cfg, 0) == ("remote_vnc", "")
    _reset()


def test_kill_switch_masters_both_remote_paths():
    _reset()
    ra.register_vnc_probe(lambda cfg: (True, ""))    # vnc available, but...
    off = {"captcha_takeover_mode": "remote_vnc", "captcha_takeover_enabled": False}
    eff, why = ra._resolve_effective_mode(off, 0)
    assert eff == "visible", "kill-switch off must stop BOTH remote paths"
    assert why
    _reset()


def test_vnc_probe_is_derived_default_unavailable():
    _reset()
    available, reason = ra._vnc_available({})
    assert available is False and reason               # stub: not provisioned
    _reset()


def test_probe_that_raises_is_treated_as_unavailable():
    # UNKNOWN downgrades: a probe that cannot determine state never promotes.
    _reset()
    ra.register_vnc_probe(lambda cfg: (_ for _ in ()).throw(RuntimeError("boom")))
    cfg = dict(ON, captcha_takeover_mode="remote_vnc")
    assert ra._resolve_effective_mode(cfg, 0)[0] == "remote"
    _reset()


def test_downgrade_reason_empty_iff_effective_equals_requested():
    _reset()
    ra.register_vnc_probe(lambda cfg: (True, ""))
    cfg = dict(ON, captcha_takeover_mode="remote_vnc")
    assert ra._resolve_effective_mode(cfg, 0) == ("remote_vnc", "")
    _reset()
