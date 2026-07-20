"""MOD-1 C-4 (RED-first): the runtime admission path must route through the
C-2 self-downgrade ladder, so `remote_vnc` is a VISIBLE downgrade -- never a
silent dead toggle (plan 1.2 / 2 / CONFIG_GUIDE 2).

C-2 shipped `_resolve_effective_mode` but nothing called it: the admission in
`start_captcha_solve_session` still used `_remote_admitted`, which only knows
"remote". So requesting `remote_vnc` fell straight to headless=False (visible)
with NO reason -- the operator's choice vanished silently. This wires the ladder
in via a pure, testable helper.

Contract for `_admit_takeover(config, active_count) -> (headless, mode, reason)`:
- headless is True iff the effective mode is not "visible" (remote rides the
  proven Arch-A screencast; remote_vnc rides it too until C-5 gives it an X
  display -- and cannot promote past remote while the probe is the C-4 stub);
- mode is the EFFECTIVE mode from the ladder;
- reason is "" iff effective == requested, else the operator-facing downgrade.

Equivalence guard: for `remote` and `visible` the decision must be byte-identical
to the old `_remote_admitted` path -- only `remote_vnc` changes.

RED on pristine @805: `_admit_takeover` does not exist (AttributeError).
"""
from __future__ import annotations

from bulk_downloader import runner_auth as ra

ON = {"captcha_takeover_enabled": True, "captcha_takeover_max_concurrent": "2"}


def _reset():
    ra._vnc_probe = None


def test_admit_helper_exists():
    _reset()
    assert hasattr(ra, "_admit_takeover"), "the ladder must have a runtime entry point"


def test_remote_vnc_is_not_a_silent_dead_toggle():
    # THE bug: today remote_vnc -> visible, no reason. It must downgrade to
    # remote (the working transport) and SAY why (stub probe -> not provisioned).
    _reset()
    cfg = dict(ON, captcha_takeover_mode="remote_vnc")
    headless, mode, reason = ra._admit_takeover(cfg, 0)
    assert mode == "remote", "remote_vnc must fall to remote, never silently to visible"
    assert headless is True, "remote (screencast) runs headless"
    assert reason and "remote_vnc" in reason, "the downgrade must be visible, not silent"


def test_remote_decision_is_byte_identical_to_old_path():
    # equivalence: for remote/visible the ladder must match _remote_admitted.
    _reset()
    import bulk_downloader.takeover as tk
    tk._reset_for_tests() if hasattr(tk, "_reset_for_tests") else None
    for mode in ("remote", "visible"):
        cfg = dict(ON, captcha_takeover_mode=mode)
        for n in (0, 1, 2, 3):
            old = ra._remote_admitted(cfg, n)
            headless, _eff, _why = ra._admit_takeover(cfg, n)
            assert headless == old, f"{mode} @cap{n}: {headless} != old {old}"


def test_kill_switch_off_downgrades_remote_vnc_to_visible_with_reason():
    _reset()
    cfg = {"captcha_takeover_mode": "remote_vnc", "captcha_takeover_enabled": False}
    headless, mode, reason = ra._admit_takeover(cfg, 0)
    assert mode == "visible" and headless is False
    assert reason, "kill-switch downgrade must carry a reason"


def test_over_cap_downgrades_with_reason():
    _reset()
    cfg = dict(ON, captcha_takeover_mode="remote_vnc")
    headless, mode, reason = ra._admit_takeover(cfg, 2)  # at cap
    assert mode == "visible" and headless is False and reason


def test_visible_request_is_clean_no_reason():
    _reset()
    headless, mode, reason = ra._admit_takeover({"captcha_takeover_mode": "visible"}, 0)
    assert (headless, mode, reason) == (False, "visible", "")
