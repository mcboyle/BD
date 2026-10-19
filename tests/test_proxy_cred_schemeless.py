"""proxy-cred-schemeless: a proxy's userinfo is withheld in every export and
diagnostic path whether or not the stored value carries a scheme.

egress_withhold only treated ``user:pw@host`` as URL-shaped when no ``/``
preceded the ``@`` and the userinfo held a ``:``, so a network-path proxy
(``//user:pw@host``) and a token-only ``TOKEN@host:port`` left the .bdtpl
export, the config export and the diagnostics bundle verbatim.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

from bulk_downloader import diagnostics_bundle, egress_withhold as ew, marketplace

BD_GATE_SCOPE = "module"

SECRET = "PXSECRET"
PROXY_KEYS = ("proxy", "socks_proxy", "http_proxy", "proxy_pool")
LEAKING_FORMS = (
    f"//u:{SECRET}@h:1",
    f"//{SECRET}@h:1",
    f"///u:{SECRET}@h:1",
    f"//u:{SECRET}@h",
    f" //u:{SECRET}@h:1",
    f"a //u:{SECRET}@h:1",
    [f"//u:{SECRET}@h:1"],
    f"{SECRET}@h:3128",
    f"/\\u:{SECRET}@h:1",
    f"\\/u:{SECRET}@h:1",
    f"//u:p@{SECRET}@h:3128",
    f"//u:{SECRET}@h:3128/path",
)
# Already withheld on BASE: the control that the probe can say "redacted".
SCHEMED_FORMS = (f"http://u:{SECRET}@h:3128", f"u:{SECRET}@h:3128", f"socks5h://u:{SECRET}@h:1")


def _paths(key, value):
    cfg = {"name": "x", "url": "https://example.com/", key: value}
    return {
        "bdtpl": json.dumps(marketplace.export_template("x", cfg)),
        "diag": json.dumps(diagnostics_bundle.bundle({"x": cfg}, include_history=False)["sites"]),
        "export_view": json.dumps(ew.export_view(cfg)),
        "site_view": json.dumps(ew.site_view(cfg)),
    }


@pytest.mark.parametrize("key", PROXY_KEYS)
@pytest.mark.parametrize("value", LEAKING_FORMS, ids=repr)
def test_schemeless_proxy_userinfo_is_withheld(key, value):
    leaked = sorted(name for name, body in _paths(key, value).items() if SECRET in body)
    assert not leaked, f"PCS_PROXY_USERINFO_LEAK: {key}={value!r} leaks in {leaked}"


@pytest.mark.parametrize("key", PROXY_KEYS)
@pytest.mark.parametrize("value", SCHEMED_FORMS)
def test_control_schemed_proxy_userinfo_is_withheld(key, value):
    assert not [n for n, b in _paths(key, value).items() if SECRET in b]


@pytest.mark.parametrize("key", PROXY_KEYS)
@pytest.mark.parametrize("value", ["http://h:3128", "h:3128", "https://proxy.example:8443"])
def test_control_credential_free_proxy_kept(key, value):
    cfg = {"name": "x", key: value}
    assert ew.export_view(cfg)[key] == value and ew.site_view(cfg)[key] == value


@pytest.mark.parametrize("value", [
    '//div[@class="x"]', "//a[@href]", '//*[@id="m"]/a', "//a/@href", "//a(@x)",
    "a@b.com", "user@example.org", "//cdn.example/@user", "/home/u@h", 'div > a[href*="@"]',
    "//img[@src]/@src", "git@github.com:org/repo", "//h.example:3128", "10.0.0.1:8080"])
def test_control_selectors_and_emails_kept(value):
    assert ew.struct_text(value) == value, f"PCS_OVERMATCH: {value!r} -> {ew.struct_text(value)!r}"


def test_schemeless_proxy_round_trips_on_import():
    stored = {"name": "x", "proxy": f"//u:{SECRET}@h:1"}
    incoming = ew.export_view(stored)
    assert ew.is_withheld_marker(incoming["proxy"]), f"PCS_PROXY_USERINFO_LEAK: {incoming['proxy']!r}"
    ew.restore_withheld(incoming, stored)
    assert incoming["proxy"] == stored["proxy"]


def _route_egress(client, sid) -> dict:
    served = {}
    for name, verb, url, body in (
            ("config export", "get", "/api/config/export", None),
            ("site export", "get", f"/api/sites/{sid}/export", None),
            ("marketplace .bdtpl", "post", f"/api/marketplace/export/{sid}", {}),
            ("marketplace .bdtpl signed", "post", f"/api/marketplace/export/{sid}", {"sign_with": "k1"}),
            ("diagnostics bundle", "get", "/api/diagnostics/bundle", None)):
        r = client.post(url, json=body) if verb == "post" else client.get(url)
        served[name] = f"HTTP{r.status_code} " + r.get_data(as_text=True)
    r = client.get("/api/diagnostics/download")
    if r.status_code == 200 and r.data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(r.data)) as z:
            served["diagnostics zip"] = "HTTP200 " + " ".join(
                z.read(n).decode("utf-8", "replace") for n in z.namelist())
    else:
        served["diagnostics zip"] = f"HTTP{r.status_code} " + r.get_data(as_text=True)[:2000]
    return served


def _plant(client, name, fields):
    from bulk_downloader import app as A
    sid = client.post("/api/sites", json={"name": name}).get_json()["id"]
    A.s_cfg[sid].update(fields)
    assert all(A.s_cfg[sid][k] == v for k, v in fields.items())  # the secret is really stored
    return A, sid


def test_routes_withhold_schemeless_proxy(fresh_app):
    want = {"proxy": f"//u:{SECRET}@h.example:3128", "socks_proxy": f"{SECRET}@h.example:1080"}
    A, sid = _plant(fresh_app, "pcs-routes", want)
    served = _route_egress(fresh_app, sid)
    broken = sorted(n for n, b in served.items() if not b.startswith("HTTP200"))
    assert not broken, f"route not served: {broken}"
    leaked = sorted(n for n, b in served.items() if SECRET in b)
    assert not leaked, f"PCS_PROXY_USERINFO_LEAK: routes {leaked}"
    r = fresh_app.post("/api/config/import?mode=merge",
                       json=json.loads(served["config export"].split(" ", 1)[1]))
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    # Only ``proxy`` is a site field: merge import drops ``socks_proxy`` (a VPN
    # handle key) even with no credentials in it.
    assert A.s_cfg[sid].get("proxy") == want["proxy"], "merge import of a default export changed the proxy"


def test_control_routes_serve_credential_free_proxy(fresh_app):
    _A, sid = _plant(fresh_app, "pcs-plain", {"proxy": "//h.example:3128", "socks_proxy": "10.0.0.1:1080"})
    served = _route_egress(fresh_app, sid)
    missing = sorted(n for n, b in served.items() if "//h.example:3128" not in b or "10.0.0.1:1080" not in b)
    assert not missing, f"PCS_OVERMATCH: credential-free proxy not served by {missing}"
