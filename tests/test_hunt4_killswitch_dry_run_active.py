"""Previewing kill-switch rules must preserve an active tunnel's rules."""

from bulk_downloader import vpn_kill_switch_system as kill_switch

BD_GATE_SCOPE = "module"


def test_dry_run_preserves_active_uncommitted_rules(monkeypatch):
    commands = []
    monkeypatch.setattr(
        kill_switch, "_run_netsh",
        lambda cmd: (commands.append(cmd) or (0, "", "")),
    )
    endpoint = "1.2.3.4:51820"
    tunnel_id = "hunt4-preview-active"

    # Positive control: preview produces a plan for a tunnel without rules.
    empty_preview = kill_switch.apply("hunt4-preview-empty", endpoint, dry_run=True)
    assert empty_preview["ok"] is True
    assert empty_preview["applied"] is False
    assert empty_preview["commands"]
    assert commands == []

    entry = kill_switch._Active(
        tunnel_id=tunnel_id,
        vpn_endpoint=endpoint,
        rule_names=["BD-hunt4-existing-rule"],
        committed=False,
    )
    try:
        with kill_switch._lock:
            kill_switch._active[tunnel_id] = entry
        preview = kill_switch.apply(tunnel_id, "5.6.7.8:1194", dry_run=True)
        assert preview["ok"] is True
        assert preview["dry_run"] is True
        assert preview["applied"] is False
        assert kill_switch.is_active(tunnel_id) is True
        assert commands == []
    finally:
        with kill_switch._lock:
            kill_switch._active.pop(tunnel_id, None)


def test_real_apply_still_reverts_stale_rules_before_adding(monkeypatch):
    # lens (B1): moving the revert after validation must not drop it -- a real
    # re-apply deletes the stale uncommitted rules BEFORE the new adds run.
    commands = []
    monkeypatch.setattr(kill_switch, "_run_netsh", lambda cmd: (commands.append(cmd) or (0, "", "")))
    monkeypatch.setattr(kill_switch, "available", lambda: (True, ""))
    monkeypatch.setattr(kill_switch, "_rdp_in_use", lambda: False)
    monkeypatch.setattr(kill_switch.threading, "Timer",
                        lambda *_a, **_k: type("T", (), {"daemon": False, "start": lambda self: None, "cancel": lambda self: None})())
    tunnel_id = "hunt4-reapply-active"
    entry = kill_switch._Active(tunnel_id=tunnel_id, vpn_endpoint="1.2.3.4:51820",
                                rule_names=["BD-hunt4-stale-rule"], committed=False)
    try:
        with kill_switch._lock:
            kill_switch._active[tunnel_id] = entry
        result = kill_switch.apply(tunnel_id, "5.6.7.8:1194", dry_run=False)
        assert result["ok"] is True and result["applied"] is True
        deletes = [i for i, c in enumerate(commands) if "delete rule" in c and "BD-hunt4-stale-rule" in c]
        adds = [i for i, c in enumerate(commands) if " add rule" in c]
        assert deletes and adds and max(deletes) < min(adds)
    finally:
        kill_switch.revert(tunnel_id)
        with kill_switch._lock:
            kill_switch._active.pop(tunnel_id, None)
