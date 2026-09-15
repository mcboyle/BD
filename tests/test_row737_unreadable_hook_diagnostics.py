"""Row 737: deploy names the unlock-hook state and its probed path."""
from __future__ import annotations

from pathlib import Path

import test_deploy_script as deploy_support
import test_row697_deploy_unlock_pending_vault as row697


BD_GATE_SCOPE = "repo-wide"


def test_unlock_hook_diagnostics_distinguish_absent_unreadable_and_success():
    first = row697._locked_missing_payload()
    cases = []

    absent_fx, absent_events = row697._fixture(
        first, row697._healthy_payload(), second_code=200, hook=None
    )
    absent_hook = Path(absent_fx.env["HOME"]) / "bd-vault-unlock.sh"
    assert not absent_hook.exists(), "precondition: absent hook fixture was populated"
    cases.append(("absent", absent_fx, absent_events, absent_hook, "VAULT-UNLOCK-HOOK-ABSENT", "install"))

    unreadable_fx, unreadable_events = row697._fixture(
        first, row697._healthy_payload(), second_code=200, hook=None
    )
    unreadable_hook = Path(unreadable_fx.env["HOME"]) / "bd-vault-unlock.sh"
    unreadable_hook.mkdir()
    assert unreadable_hook.exists() and not unreadable_hook.is_file(), (
        "precondition: unreadable fixture did not build a non-regular hook"
    )
    cases.append(("unreadable", unreadable_fx, unreadable_events, unreadable_hook, "VAULT-UNLOCK-HOOK-UNREADABLE", "fix"))

    success_fx, success_events = row697._fixture(
        first, row697._healthy_payload(), second_code=200
    )
    success_hook = Path(success_fx.env["HOME"]) / "bd-vault-unlock.sh"
    assert success_hook.is_file(), "precondition: success hook was not installed"
    cases.append(("success", success_fx, success_events, success_hook, "UNLOCK-PENDING", ""))

    assert len(cases) == 3, "precondition: three hook classifications are required"
    for mode, fx, events, hook, diagnostic, remedy in cases:
        result = deploy_support._deploy(fx)
        output = deploy_support._out(result)
        assert str(hook) in output, f"{mode} did not name its probed hook path"
        assert output.count(str(hook)) == 1, (
            f"{mode} named the probed hook path more than once: {output!r}"
        )
        assert diagnostic in output, f"{mode} lost its diagnostic: {output!r}"
        if remedy:
            assert remedy in output.lower(), f"{mode} omitted its {remedy} remedy"
            assert result.returncode == 1, deploy_support._ctx(result)
            assert row697._events(events) == ["health:1"], deploy_support._ctx(result)
        else:
            assert result.returncode == 0, deploy_support._ctx(result)
            assert row697._events(events) == ["health:1", "unlock:local", "health:2", "root"]
