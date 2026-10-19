"""F051: the VPN + Secrets report says so when a subsystem is unavailable.

collect_vpn_status / collect_secrets_status answer {"available": False,
"error": ...} when their subsystem cannot be read, and the data route still
wraps that as ok:true. The page then drew every requested key as a bare dash:
neither data nor an explicit empty state. The page now draws a fixed
"unavailable" row and never the collector error text, which can carry
exception detail (F051 r2).

The tests run the REAL page script under node against a stub DOM and a stub
fetch, so they judge what the browser would draw, not the page text.
"""
import json
import re
import shutil
import subprocess

import pytest

from bulk_downloader import app_data_layer as DL
from bulk_downloader import app_report_center as RC

BD_GATE_SCOPE = "module"

VPN_URL = "/api/data/vpn_status"
SEC_URL = "/api/data/secrets_status"
DASH = "—"
UNAVAILABLE = "unavailable"
CANARY_MARKER = "SYNTHETIC_SECRET_F051_8391"

VPN_OK = {"available": True, "tunnel_count": 2, "provider_count": 1,
          "providers": ["Mullvad"], "system_killswitch_available": False,
          "system_killswitch_reason": "windows only", "active_killswitches": 0}
SEC_OK = {"available": True, "backend": "master_password", "is_unlocked": False,
          "credential_count": 3, "credentials_enumerable": True}

_HARNESS = r"""
const [script, payloadJson] = process.argv.slice(-2);
const payloads = JSON.parse(payloadJson);
function el(tag) {
  return {tag: tag, style: {}, textContent: "", children: [],
          appendChild(c) { this.children.push(c); return c; }};
}
const byId = {};
global.document = {
  createElement: el,
  getElementById(id) { return byId[id] || (byId[id] = el("#" + id)); },
};
const fetched = [];
global.fetch = function (url) {
  fetched.push(url);
  return Promise.resolve({json() { return Promise.resolve(payloads[url]); }});
};
new Function(script)();
setTimeout(function () {
  const rows = function (id) {
    return document.getElementById(id).children.map(function (tr) {
      return tr.children.map(function (td) { return td.textContent; });
    });
  };
  process.stdout.write(JSON.stringify({
    fetched: fetched, vpn: rows("vpn"), sec: rows("sec"),
    err: document.getElementById("err").textContent}));
}, 20);
"""


def _render(vpn, sec):
    node = shutil.which("node")
    assert node is not None, "node not on PATH: the page script cannot be run"
    page = RC.cockpit_vpn_secrets_status_page()
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(scripts) == 1, "expected exactly one inline script on the page"
    r = subprocess.run(
        [node, "-e", _HARNESS, "--", scripts[0],
         json.dumps({VPN_URL: vpn, SEC_URL: sec})],
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert sorted(out["fetched"]) == sorted([VPN_URL, SEC_URL])
    return out


def _ok(data):
    return {"ok": True, "data": data}


def test_available_subsystems_render_every_requested_value():
    out = _render(_ok(VPN_OK), _ok(SEC_OK))
    assert out["vpn"] == [
        ["tunnel_count", "2"], ["provider_count", "1"], ["providers", "Mullvad"],
        ["system_killswitch_available", "no"],
        ["system_killswitch_reason", "windows only"], ["active_killswitches", "0"]]
    assert out["sec"] == [
        ["backend", "master_password"], ["is_unlocked", "no"],
        ["credential_count", "3"], ["credentials_enumerable", "yes"]]
    assert out["err"] == ""


def test_unavailable_vpn_is_explicit_instead_of_blank_rows():
    out = _render(_ok({"available": False, "error": "boom-vpn"}), _ok(SEC_OK))
    assert out["vpn"] == [["status", UNAVAILABLE]]
    assert "boom-vpn" not in json.dumps(out), "collector error text must not render"
    assert len(out["sec"]) == 4, "the healthy panel must be unaffected"


def test_unavailable_secrets_is_explicit_instead_of_blank_rows():
    out = _render(_ok(VPN_OK), _ok({"available": False, "error": "boom-sec"}))
    assert out["sec"] == [["status", UNAVAILABLE]]
    assert "boom-sec" not in json.dumps(out), "collector error text must not render"
    assert len(out["vpn"]) == 6, "the healthy panel must be unaffected"


def test_unavailable_without_a_reason_is_still_explicit():
    out = _render(_ok({"available": False}), _ok(SEC_OK))
    assert out["vpn"] == [["status", UNAVAILABLE]]


def test_route_error_stays_in_the_error_line_not_the_table():
    out = _render({"ok": False, "error": "kaput"}, _ok(SEC_OK))
    assert out["vpn"] == []
    assert "kaput" in out["err"] and VPN_URL in out["err"]


@pytest.mark.parametrize("collector,module,call", [
    (DL.collect_vpn_status, "bulk_downloader.vpn", "list_tunnels"),
    (DL.collect_secrets_status, "bulk_downloader.secrets_store", "get_backend"),
])
def test_collector_failure_reaches_the_page_as_unavailable(
        monkeypatch, collector, module, call):
    """Seam: the shape the page branches on is the one the collectors emit."""
    import importlib

    def boom(*a, **k):
        raise RuntimeError("subsystem-down")
    monkeypatch.setattr(importlib.import_module(module), call, boom)
    data = collector()
    assert data == {"available": False, "error": "subsystem-down"}
    vpn = collector is DL.collect_vpn_status
    out = _render(_ok(data if vpn else VPN_OK), _ok(SEC_OK if vpn else data))
    assert out["vpn" if vpn else "sec"] == [["status", UNAVAILABLE]]
    assert DASH not in json.dumps(out["vpn" if vpn else "sec"], ensure_ascii=False)


def test_collector_exception_text_never_reaches_the_page(monkeypatch):
    """F051 r2 (REFUTE F1): collect_secrets_status carries str(exc) in "error";
    a backend exception that quotes a token must not be drawn on the page."""
    from bulk_downloader import secrets_store

    def boom(*a, **k):
        raise RuntimeError("backend rejected token=" + CANARY_MARKER)
    monkeypatch.setattr(secrets_store, "get_backend", boom)
    data = DL.collect_secrets_status()
    assert data["available"] is False
    assert CANARY_MARKER in data["error"], "seam: the collector must carry the marker"
    out = _render(_ok(VPN_OK), _ok(data))
    assert CANARY_MARKER not in json.dumps(out), (
        "F051-R2 secret marker reached rendered report")
    assert out["sec"] == [["status", UNAVAILABLE]]
    assert len(out["vpn"]) == 6, "the healthy panel must be unaffected"
