"""v3.66.714 (A-GUI Cut 5) -- endpoint reachability: the ledger, the gate, the killswitch.

An /api route is not operator-reachable. A CONTROL that calls it is. 397 mutating
endpoints exist; 77 are called by nothing -- no SPA component, no server-rendered
console, no extension. They are reachable only by someone who already knows they are
there and hand-crafts a POST.

The one that matters:

    /api/vpn/system_killswitch/<tunnel_id>/plan
    /api/vpn/system_killswitch/<tunnel_id>/apply
    /api/vpn/system_killswitch/<tunnel_id>/commit
    /api/vpn/system_killswitch/<tunnel_id>/revert

Vpn.tsx READS `system_killswitch_available`, `system_killswitch_active` and
`system_killswitch_reason` and renders them. It calls NONE of the four endpoints --
zero apiPost/fetch sites. The GUI shows you the state of a killswitch it cannot
operate. That is the same shape as automation.master_off_switch before 709/711: a
safety control the operator can see and cannot reach.

The gate: every mutating endpoint must be either WIRED (a control calls it) or
CLASSIFIED with a reason (dev-only, internal, machine-to-machine). A new endpoint
cannot land dark and silent. The dark count is a RATCHET -- it may only fall.
"""
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "reports", "endpoint_reachability.json")


def _ledger():
    from tools import endpoint_reachability as er

    return er.build(ROOT)


def test_ledger_exists_and_covers_every_mutating_endpoint():
    from bulk_downloader.app import app

    muta = {str(r.rule) for r in app.url_map.iter_rules()
            if r.methods & {"POST", "PUT", "PATCH", "DELETE"}}
    led = _ledger()
    covered = {e["rule"] for e in led["endpoints"]}
    missing = sorted(muta - covered)
    assert not missing, "mutating endpoints absent from the ledger: %s" % missing[:5]


def test_every_dark_endpoint_is_classified():
    """Dark is allowed. Dark AND unexplained is not: an endpoint nobody can reach and
    nobody has justified is either an operator gap or dead code, and it must be said
    which."""
    led = _ledger()
    unexplained = sorted(e["rule"] for e in led["endpoints"]
                         if e["reach"] == "dark" and not e.get("why"))
    assert not unexplained, (
        "%d dark endpoints with no classification: %s"
        % (len(unexplained), unexplained[:8]))


def test_dark_count_is_ratcheted():
    """The dark count may only FALL. A new endpoint that lands with no control and no
    classification fails the build."""
    led = _ledger()
    base = json.load(open(LEDGER, encoding="utf-8"))
    now = len([e for e in led["endpoints"] if e["reach"] == "dark"])
    assert now <= base["dark_count"], (
        "dark endpoints rose %d -> %d; wire a control or classify it"
        % (base["dark_count"], now))


def test_vpn_system_killswitch_has_controls():
    """The GUI already DISPLAYS this killswitch's state. It must also be able to
    operate it -- a safety control you can watch but not touch is not a control."""
    src = open(os.path.join(ROOT, "frontend", "src", "routes", "Vpn.tsx"),
               encoding="utf-8", errors="replace").read()
    for verb in ("plan", "apply", "commit", "revert"):
        assert "system_killswitch" in src and verb in src, verb
    calls = re.findall(r"system_killswitch/\$\{[^}]+\}/(plan|apply|commit|revert)", src)
    assert set(calls) >= {"plan", "apply", "commit", "revert"}, (
        "Vpn.tsx reads the killswitch state but calls none of its endpoints "
        "(found call sites: %s)" % sorted(set(calls)))


def test_killswitch_apply_is_confirm_gated():
    """It changes system-level network state. Same discipline as the automation kill
    switch: plan first, then an explicit confirm."""
    src = open(os.path.join(ROOT, "frontend", "src", "routes", "Vpn.tsx"),
               encoding="utf-8", errors="replace").read()
    i = src.index("system_killswitch")
    window = src[max(0, i - 4000):i + 6000]
    assert any(w in window for w in ("Confirm", "confirm")), (
        "the killswitch apply path has no confirm step")
