"""v3.66.640 -- INTEROP-JD-1: the jd_plugin governance gate.

INTEROP-GOV-1 (v3.66.638) built the interop_registry keystone and wired its FIRST
consumer -- the chromium_extension gate in runner_browser. This cut adds the JD
consumer: when interop governance is enabled, the JD download path routes a URL to
a JDownloader hoster plugin ONLY if that plugin (identified by the URL host) is
registered + risk-acknowledged + enabled in the registry. Default-OFF: with the
toggle absent, JD behaves exactly as before (EXT/JD-bridge behavior unchanged).

The JD "plugin identity" is the URL host -- JD's hoster plugins are per-hoster, so
the operator registers a jd_plugin item keyed by the hostname it handles.

Mirrors the proven runner_browser chromium_extension gate pattern. The gate lives
in IntegrationsMixin._try_jd_download and short-circuits BEFORE any JD client is
obtained, so a non-permitted host never reaches JD -- it falls through to the
teach path exactly like an unreachable JD.

Sandbox-safe: pure logic; no DISPLAY, no network, no pytest builtins. BD_HOME is
pointed at a tempdir per-test for registry isolation and restored in finally.
"""
from __future__ import annotations

import os
import tempfile
import types
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RI_PY = _REPO_ROOT / "bulk_downloader" / "runner_integrations.py"


# ---- harness -------------------------------------------------------------

def _mk_runner(config, client_sentinel):
    """A minimal object bound to the real _try_jd_download. _get_jd_client records
    whether it was reached: the gate must short-circuit BEFORE it when blocking."""
    from bulk_downloader import runner_integrations as ri

    class _Fake:
        pass

    r = _Fake()
    r.config = config
    r.site_id = "s1"
    r._events = []
    r._client_called = False

    def _log_event(kind, msg, **kw):
        r._events.append((kind, msg, kw))

    def _get_jd_client():
        r._client_called = True
        return client_sentinel  # None -> method returns "JD client unavailable"

    def _read_cookies_for_jd():
        return ""

    r.log_event = _log_event
    r._get_jd_client = _get_jd_client
    r._read_cookies_for_jd = _read_cookies_for_jd
    r._try_jd_download = types.MethodType(ri.IntegrationsMixin._try_jd_download, r)
    return r


def _with_bd_home(fn):
    """Run fn() with BD_HOME pointed at a fresh tempdir; restore after."""
    prev = os.environ.get("BD_HOME")
    d = tempfile.mkdtemp(prefix="jdgate_")
    os.environ["BD_HOME"] = d
    try:
        return fn()
    finally:
        if prev is None:
            os.environ.pop("BD_HOME", None)
        else:
            os.environ["BD_HOME"] = prev


# ---- behavioral tests ----------------------------------------------------

def test_governance_on_blocks_unpermitted_host_before_client():
    """Governance ON + host not registered -> return (False, <not-permitted>) and
    NEVER reach _get_jd_client. RED on pristine (no gate -> client is reached)."""
    def body():
        r = _mk_runner({"interop_governance_enabled": True}, None)
        ok, reason = r._try_jd_download("https://blocked.example/video", "/tmp")
        assert ok is False, f"blocked host should not succeed: {reason!r}"
        assert r._client_called is False, (
            "gate must short-circuit BEFORE any JD client is obtained; "
            "_get_jd_client was reached -> the jd_plugin gate is absent"
        )
        assert "not permitted" in reason.lower(), (
            f"reason should name the governance block, got {reason!r}"
        )
    _with_bd_home(body)


def test_governance_on_allows_permitted_host_past_gate():
    """Governance ON + host registered+acked+enabled -> the gate lets the method
    proceed to the JD client (which we stub as None -> 'JD client unavailable')."""
    def body():
        from bulk_downloader import interop_registry as ir
        ir.register("jd_plugin", "ok.example", source="operator")
        ir.acknowledge("jd_plugin", "ok.example")
        ir.set_enabled("jd_plugin", "ok.example", True)
        assert ir.is_permitted("jd_plugin", "ok.example") is True
        r = _mk_runner({"interop_governance_enabled": True}, None)
        ok, reason = r._try_jd_download("https://ok.example/video", "/tmp")
        assert r._client_called is True, (
            "a permitted host must pass the gate and reach the JD client; "
            f"it was blocked instead ({reason!r})"
        )
        assert ok is False and "unavailable" in reason.lower(), (
            f"stubbed client is None -> expected 'JD client unavailable', got {reason!r}"
        )
    _with_bd_home(body)


def test_governance_off_is_unchanged():
    """Toggle absent -> no registry consulted, method proceeds exactly as before."""
    def body():
        r = _mk_runner({}, None)  # no interop_governance_enabled key
        ok, reason = r._try_jd_download("https://anything.example/x", "/tmp")
        assert r._client_called is True, (
            "with governance OFF the gate must not run; JD path must proceed unchanged"
        )
        assert ok is False and "unavailable" in reason.lower()
    _with_bd_home(body)


# ---- source-level assertion (house style for the mixin) ------------------

def test_jd_gate_consults_registry_in_source():
    """runner_integrations must consult is_permitted for the jd_plugin kind, guarded
    by interop_governance_enabled. RED on pristine (neither token present)."""
    src = _RI_PY.read_text(encoding="utf-8")
    assert 'interop_governance_enabled' in src, (
        "the JD gate must be guarded by the interop_governance_enabled toggle"
    )
    assert 'is_permitted' in src and 'jd_plugin' in src, (
        "runner_integrations must call interop_registry.is_permitted for jd_plugin"
    )
