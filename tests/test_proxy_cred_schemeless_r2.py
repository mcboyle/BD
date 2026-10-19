"""proxy-cred-schemeless r2: a proxy's userinfo is withheld whatever its host
looks like (deny-by-default; lens REFUTE F1 of r1, both legs).

r1 URL-shaped a scheme-less ``//user:pw@host`` only when the host matched
``[A-Za-z0-9.-]+``, so a host it rejected failed OPEN: ``proxy_host`` (docker /
compose names), ``my~proxy``, sub-delims, an IDN host. Here the host never gates
redaction: any ``//...@`` authority and any bare ``user:pw@`` / ``TOKEN@...:port``
token is withheld; only an XPath/CSS step (``[@attr``, ``fn(@attr``) is spared.

Generic structural text keeps the e-mail boundary (both lens legs: NOT a
finding): a bare ``TOKEN@host`` with no port and no ``:`` in the userinfo is an
e-mail address shape (``a@b.com``) and is kept. Under a proxy-named key (ORDER
ANSWER 09:3xZ) any ``@`` after the first character is userinfo and is withheld.
The values below are fixed test strings, not credentials.
"""
from __future__ import annotations

import json

import pytest

from bulk_downloader import diagnostics_bundle, egress_withhold as ew, marketplace

BD_GATE_SCOPE = "module"

SECRET = "PXSECRET"
PROXY_KEYS = ("proxy", "socks_proxy", "http_proxy", "proxy_pool")

# The r1 lens evidence (correctness PCSC-C + security PCSS-D), verbatim.
LENS_FORMS = (
    f"//u:{SECRET}@proxy_host:3128",
    f"//u:{SECRET}@squid_proxy",
    f"{SECRET}@proxy_host:3128",
    f"//u:{SECRET}@my_proxy:3128",
    f"{SECRET}@my_proxy:3128",
    f"//u:{SECRET}@прокси.рф:3128",
)

HOSTS = ("h", "proxy_host", "squid_proxy", "my~proxy", "h!$&'()*+,;=x", "[::1]",
         "[2001:db8::1]", "bücher.example", "прокси.рф", "10.0.0.1", "h.example", "-h_1-", "")
PORTS = ("", ":3128", ":1", ":123456")
USERINFO = (f"u:{SECRET}", SECRET, f"u~x!$:{SECRET}(", f"{SECRET}:", f"a(b:{SECRET}")
LEADS = ("//", "///", "\\\\", "/\\", "\\/", "http://", "socks5h://", "")


def _shapes():
    for lead in LEADS:
        for ui in USERINFO:
            for host in HOSTS:
                for port in PORTS:
                    if lead == "" and ":" not in ui and not port:
                        continue  # e-mail boundary, see the module docstring
                    yield f"{lead}{ui}@{host}{port}"


SHAPES = tuple(_shapes())


def _paths(key, value):
    cfg = {"name": "x", "url": "https://example.com/", key: value}
    return {
        "bdtpl": json.dumps(marketplace.export_template("x", cfg)),
        "diag": json.dumps(diagnostics_bundle.bundle({"x": cfg}, include_history=False)["sites"]),
        "export_view": json.dumps(ew.export_view(cfg)),
        "site_view": json.dumps(ew.site_view(cfg)),
    }


@pytest.mark.parametrize("key", PROXY_KEYS)
@pytest.mark.parametrize("value", LENS_FORMS, ids=repr)
def test_lens_host_shapes_are_withheld(key, value):
    leaked = sorted(name for name, body in _paths(key, value).items() if SECRET in body)
    assert not leaked, f"PCS2_HOST_GATED_LEAK: {key}={value!r} leaks in {leaked}"


def test_property_host_x_scheme_x_port_never_leaks():
    # denominator: every generated shape, counted from the generator
    n_email = sum(":" not in ui for ui in USERINFO) * len(HOSTS)
    assert len(SHAPES) == len(LEADS) * len(USERINFO) * len(HOSTS) * len(PORTS) - n_email
    leaked = [v for v in SHAPES for body in _paths("proxy", v).values() if SECRET in body]
    assert not leaked, f"PCS2_HOST_GATED_LEAK: {len(leaked)}/{len(SHAPES)} e.g. {leaked[:5]!r}"


@pytest.mark.parametrize("value", [
    f"'//u:{SECRET}@h:1'", f"(//u:{SECRET}@h)", f"--proxy=//u:{SECRET}@h:1",
    f"//u:{SECRET}@h:1,//u:{SECRET}@h2:1", f"//u:{SECRET}@", f"//u:{SECRET}@h.",
    f"//u:{SECRET}@h:1/p?q=1#f", f"//u:p@{SECRET}@proxy_host", f"//{SECRET}(@h",
    f"//u:{SECRET}(@h)", f"//u:{SECRET}(@h):3128", f"//u:{SECRET}(@h!$&'()*+,;=x"], ids=repr)
@pytest.mark.parametrize("key", ("proxy", "upstream"))
def test_wrapped_and_embedded_network_path_is_withheld(key, value):
    leaked = sorted(name for name, body in _paths(key, value).items() if SECRET in body)
    assert not leaked, f"PCS2_HOST_GATED_LEAK: {key}={value!r} leaks in {leaked}"


@pytest.mark.parametrize("value", [
    '//div[contains(@class,', '//a[@x', '//*[@id="u@h"]', "//@href", "//ns:div[@x]",
    '//div[@class="a', "@scope/pkg", "//share/path", "\\\\server\\share", "a@b.com",
    "user@example.org", "git@github.com:org/repo", "//h.example:3128", "//proxy_host:3128", "//a(@x)", "//count(@id,"])
def test_control_selectors_paths_and_emails_kept(value):
    assert ew.struct_text(value) == value, f"PCS2_OVERMATCH: {value!r} -> {ew.struct_text(value)!r}"


def test_control_probe_can_say_redacted():
    # positive control: the probe sees the secret in the stored value, and the
    # schemed form of the same proxy is withheld on BASE already
    assert SECRET in json.dumps({"proxy": f"//u:{SECRET}@proxy_host:3128"})
    shown = _paths("proxy", f"http://u:{SECRET}@proxy_host:3128")
    assert not [n for n, b in shown.items() if SECRET in b]
    assert ew.is_withheld_marker(ew.export_view({"proxy": f"http://u:{SECRET}@proxy_host:3128"})["proxy"])


PROXY_NAMED = ("proxy", "http_proxy", "https_proxy", "ftp_proxy", "socks_proxy", "socks5",
               "proxy_pool", "HTTPS_PROXY")


@pytest.mark.parametrize("key", PROXY_NAMED)
@pytest.mark.parametrize("value", [f"{SECRET}@proxy_host", f"{SECRET}@h.example", f"u@{SECRET}",
                                   f"x {SECRET}@squid", [f"{SECRET}@h"]], ids=repr)
def test_proxy_named_key_withholds_any_userinfo(key, value):
    leaked = sorted(name for name, body in _paths(key, value).items() if SECRET in body)
    assert not leaked, f"PCS2_HOST_GATED_LEAK: {key}={value!r} leaks in {leaked}"


@pytest.mark.parametrize("key", PROXY_NAMED)
@pytest.mark.parametrize("value", ["h:3128", "//h.example:3128", "@cred:px1", "10.0.0.1:1080"])
def test_control_proxy_named_key_without_userinfo_kept(key, value):
    cfg = {"name": "x", key: value}
    assert ew.export_view(cfg)[key] == value and ew.site_view(cfg)[key] == value


@pytest.mark.parametrize("key", ("contact", "owner", "selector"))
def test_control_other_keys_keep_email_shape(key):
    cfg = {"name": "x", key: "a@b.com"}
    assert ew.export_view(cfg)[key] == "a@b.com" and ew.site_view(cfg)[key] == "a@b.com"


# Generic structural text (ORDER ANSWER: "Test both"): the same shapes under a key
# that is neither proxy-named, secret-named, URL-named nor free text, so only the
# deny-by-default authority rule of struct_text can withhold them.
GENERIC_KEYS = ("upstream", "gateway")


@pytest.mark.parametrize("key", GENERIC_KEYS)
def test_generic_key_routes_to_struct_text(key):
    # not proxy-named by the ORDER's rule (stated by name: valid on BASE too)
    assert "proxy" not in key and not key.startswith("socks")
    assert not (ew.is_secret_field(key) or ew.is_url_field(key)
                or ew.is_credential_url_field(key) or key in ew.FREE_TEXT_FIELDS)


@pytest.mark.parametrize("key", GENERIC_KEYS)
@pytest.mark.parametrize("value", LENS_FORMS, ids=repr)
def test_generic_text_lens_host_shapes_are_withheld(key, value):
    leaked = sorted(name for name, body in _paths(key, value).items() if SECRET in body)
    assert not leaked, f"PCS2_HOST_GATED_LEAK: {key}={value!r} leaks in {leaked}"


def test_property_generic_text_never_leaks():
    leaked = [v for v in SHAPES if SECRET in ew.struct_text(v)
              or any(SECRET in body for body in _paths("upstream", v).values())]
    assert not leaked, f"PCS2_HOST_GATED_LEAK: {len(leaked)}/{len(SHAPES)} e.g. {leaked[:5]!r}"
