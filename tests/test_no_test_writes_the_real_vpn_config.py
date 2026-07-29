"""No test may write the operator's real VPN config.

THE DEFECT, caught 2026-07-29 by instrumenting `vpn_config.save()` and recording
the running test's nodeid whenever the resolved path was the real user config:

    test   : tests/test_v3_66_729_body_contract_fixtures.py::
             test_no_control_sends_a_body_its_endpoint_refuses
    path   : ~/.config/bulk-downloader/vpn/tunnels.json
    env    : BD_VPN_CONFIG_PATH=<unset>
    tunnels: ['tun-ccc']
    global : {'leak_test_interval_s': 1, 'kill_switch_auto_recover': False, ...}
    stack  : app_vpn_api.py:391 vpn_settings_update
               -> vpn_config.update_global_settings(**data)
               -> vpn_config.py:435 save()

The body-contract probe PUTs synthetic bodies at every endpoint to check they
refuse malformed input. `PUT /api/vpn/settings` accepts one, and
`update_global_settings()` saves unconditionally. `_config_path()` resolves
`BD_VPN_CONFIG_PATH` at CALL time, and by then the probe's process has no
override set, so the write lands on the real file.

Two things made it damaging rather than merely untidy:

  * `save()` serialises module-global `_state["tunnels"]`, which still held the
    `tun-ccc` fixture from tests/test_v3_66_507_bucket3b_store_raw.py:221. A
    malformed test tunnel (no `name`, no `backend`) was written into the
    operator's config, where it quarantines on every load and blocks
    `--vpn-tunnel` seeding on every capture.
  * The probe's payload became the operator's live settings:
    `leak_test_interval_s` 1 against a default of 1800, so VPN leak tests ran
    every second instead of every 30 minutes, and `kill_switch_auto_recover`
    false against a default of true.

WHY PER-TEST OVERRIDES WERE NOT ENOUGH, AND ARE NOT THE FIX. The tests that
touch this already set `BD_VPN_CONFIG_PATH` and restore it in a `finally`. That
is exactly what fails: `vpn_config`'s state is module-global and outlives the
test, the override is popped on the way out, and any later save in the same
process resolves to the real path. A protection each test opts into is a
denominator that excludes every test which forgot -- and the one that forgot was
not even a VPN test.

So the guard is session-wide and enforced in conftest, in two layers: the
session points BD_VPN_CONFIG_PATH somewhere disposable, and `save()` is wrapped
so a call that still resolves to the real config raises instead of writing. The
second layer matters because the first is exactly the kind of thing a future
test can pop.

THREE TARGETED REPRODUCTIONS FAILED before instrumenting: the three VPN
store-raw files, all 19 files importing vpn_config, and a bare
`import bulk_downloader.app`. All reported the file UNCHANGED. Do not try to
confirm this by reading or by running a subset -- the trigger is module state
from one test plus an HTTP write from another.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _real_config_path() -> Path:
    """Where vpn_config writes when nothing overrides it."""
    return (Path(os.path.expanduser("~")) / ".config" / "bulk-downloader"
            / "vpn" / "tunnels.json")


@pytest.fixture()
def vpn_config_mod():
    try:
        from bulk_downloader import vpn_config
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.fail(f"bulk_downloader.vpn_config did not import, so this gate "
                    f"cannot verify its subject: {exc}")
    return vpn_config


# ── denominator canary ───────────────────────────────────────────────────────

def test_the_real_path_is_still_what_we_think_it_is(vpn_config_mod, monkeypatch):
    """If the default moved, every assertion below is aimed at the wrong file."""
    monkeypatch.delenv("BD_VPN_CONFIG_PATH", raising=False)
    resolved = vpn_config_mod._config_path()
    assert resolved == _real_config_path(), (
        f"vpn_config's unoverridden path is {resolved}, not {_real_config_path()}. "
        f"This gate is pointed at the wrong file and would pass while the real "
        f"one was written."
    )


# ── the defect ───────────────────────────────────────────────────────────────

def test_the_session_does_not_target_the_operators_real_config(vpn_config_mod):
    """With no per-test override in force, where would a save land?

    This is the state every test starts in. Before the session-wide guard it
    resolved to the operator's own file, which is how a body-contract probe --
    not even a VPN test -- came to rewrite their VPN settings.
    """
    resolved = vpn_config_mod._config_path().resolve()
    assert resolved != _real_config_path().resolve(), (
        f"the test session resolves vpn_config to {resolved}, the operator's "
        f"real config. Any save() reached from any test writes their VPN "
        f"settings and tunnels."
    )


def test_a_save_that_would_hit_the_real_config_raises_instead_of_writing(
        vpn_config_mod, monkeypatch):
    """The second layer: the guard must survive a popped override.

    Layer one (a session-wide BD_VPN_CONFIG_PATH) is precisely the kind of thing
    a test pops in a finally -- which is the original defect. So point the
    override AT the real file and require save() to refuse.

    The real file is snapshotted and restored either way, so a RED run of this
    test cannot damage it.
    """
    real = _real_config_path()
    before = real.read_bytes() if real.is_file() else None
    monkeypatch.setenv("BD_VPN_CONFIG_PATH", str(real))
    try:
        with pytest.raises(Exception) as caught:
            vpn_config_mod.save()
        assert "vpn" in str(caught.value).lower() or "real" in str(caught.value).lower(), (
            f"save() raised, but not with a message identifying the guard: "
            f"{caught.value!r}"
        )
    finally:
        after = real.read_bytes() if real.is_file() else None
        if before is None:
            if real.is_file():
                real.unlink()
        elif after != before:
            real.write_bytes(before)
    assert (real.read_bytes() if real.is_file() else None) == before, (
        "the real VPN config was modified by this test despite restoration"
    )
