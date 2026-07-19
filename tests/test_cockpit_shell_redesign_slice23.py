"""Cockpit shell redesign — Slice 2 (toasts) + Slice 3 (hash routing).

Custom-runner friendly: zero-arg tests, repo root from __file__, no pytest
builtins. Structural text assertions over the server-rendered cockpit blob.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"


def _src():
    return COCKPIT.read_text(encoding="utf-8")


# ── Slice 2: non-blocking toasts replace native alert ───────────────────────
def test_toast_system_present():
    src = _src()
    assert 'id="toasts"' in src, "no toast container (#toasts)"
    assert "function toast(" in src, "no toast() function"
    assert ".toast{" in src, "no toast CSS"


def test_no_native_alert_calls_remain():
    """Every blocking alert('…') call must be gone (the literal display text
    'alert(s)' in a notification description is not a call and is allowed)."""
    src = _src()
    assert "alert('" not in src, "a native alert('…') call still remains"


def test_toast_is_xss_safe():
    """The message must be set via textContent, not innerHTML, since error
    bodies can carry markup."""
    src = _src()
    assert ".tx').textContent=" in src, "toast message not set via textContent"


# ── Slice 3: hash-routing / state persistence ───────────────────────────────
def test_hash_router_present():
    src = _src()
    assert "function routeFromHash" in src, "no hash router"
    assert "location.hash" in src, "router does not read/write location.hash"
    assert "'hashchange'" in src, "no hashchange listener"


def test_boot_routes_from_hash_with_home_fallback():
    """Boot must go through the router (reload-restore), and 'home' stays the
    no-hash fallback (preserves the appearance-test landing invariant)."""
    src = _src()
    assert "routeFromHash();\n</script>" in src, "boot does not route from hash"
    assert "go('home');return;" in src, "home is not the no-hash fallback"


def test_subtab_reflected_in_hash():
    """mountTab must persist the active sub-tab into the hash so reload and
    back/forward restore the tab, not just the page."""
    src = _src()
    assert "function _writeHash" in src, "no hash writer"
    assert "function _activeSub" in src, "sub-tab not captured for the hash"
