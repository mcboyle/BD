"""698 (v3.66.698, F5) -- per-namespace DNS for controlled egress [safety].

697 moved a WireGuard tunnel into a per-capture netns and made it the only
route, but a fresh netns does NOT inherit the host's /etc/resolv.conf -- a
confined process egresses through the tunnel yet cannot resolve hostnames
(the well-documented wg-in-netns gotcha: "nobody is listening on 127.0.0.1:53
within the vpn namespace"). This completes controlled egress by writing a
per-namespace resolver at /etc/netns/<ns>/resolv.conf (the standard netns DNS
override, read when a process enters the ns), so name resolution also goes
through the tunnel.

Grounded, low-risk: only the nameserver VALUE is a config choice (an
EgressSpec.dns IP, cfg-overridable). The mechanism is the standard
/etc/netns/<ns>/resolv.conf write. The nameserver is IP-validated before it is
ever interpolated into a command (no smuggling). Pure/unit only via the injected
runner; the LIVE exercise is deferred with the rest of F5 (CAP_NET_ADMIN).

RED-first on pristine v3.66.697: EgressSpec has no ``dns``; egress_commands
writes no resolv.conf; ns_resolv_conf_path does not exist.
"""
import subprocess

from bulk_downloader import netns_isolation as ni


class _FakeRunner:
    def __init__(self, fail_on=None):
        self.calls = []
        self._fail_on = fail_on

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc = 1 if (self._fail_on and self._fail_on in " ".join(argv)) else 0
        return subprocess.CompletedProcess(argv, rc, "", "")


def _spec(**kw):
    base = dict(wg_iface="wg-bd0", wg_conf="/etc/wireguard/bd.conf",
                address="10.9.0.2/32")
    base.update(kw)
    return ni.EgressSpec(**base)


def test_ns_resolv_conf_path():
    assert ni.ns_resolv_conf_path("bd_cap_x") == "/etc/netns/bd_cap_x/resolv.conf"


def test_egress_commands_writes_resolv_when_dns_set():
    cmds = ni.egress_commands("bd_cap_x", _spec(dns="10.9.0.1"))
    # the /etc/netns/<ns> dir is created and a resolv.conf is written
    assert ["mkdir", "-p", "/etc/netns/bd_cap_x"] in cmds
    writes = [c for c in cmds if c[:2] == ["sh", "-c"]
              and "/etc/netns/bd_cap_x/resolv.conf" in c[2]]
    assert len(writes) == 1
    assert "nameserver 10.9.0.1" in writes[0][2]


def test_egress_commands_no_dns_writes_no_resolv():
    cmds = ni.egress_commands("bd_cap_x", _spec())        # dns unset
    assert not any("resolv.conf" in " ".join(c) for c in cmds)
    assert not any(c[:2] == ["mkdir", "-p"] for c in cmds)


def test_egress_commands_invalid_dns_is_ignored():
    """A non-IP dns value must never be interpolated into a command."""
    cmds = ni.egress_commands("bd_cap_x", _spec(dns="; rm -rf /"))
    assert not any("resolv.conf" in " ".join(c) for c in cmds)


def test_dns_write_runs_after_interface_up():
    """resolv.conf write comes after the tunnel is up (ordering sanity)."""
    cmds = ni.egress_commands("bd_cap_x", _spec(dns="10.9.0.1"))
    joined = ["|".join(c) for c in cmds]
    i_up = joined.index("ip|-n|bd_cap_x|link|set|wg-bd0|up")
    i_dns = next(i for i, c in enumerate(cmds)
                 if c[:2] == ["mkdir", "-p"])
    assert i_up < i_dns


def test_egress_spec_from_cfg_reads_dns():
    cfg = {"netns_isolation": {"enabled": True, "egress": {
        "wg_iface": "wg-bd0", "wg_conf": "/etc/wireguard/bd.conf",
        "address": "10.9.0.2/32", "dns": "10.9.0.1"}}}
    spec = ni.egress_spec_from_cfg(cfg)
    assert spec is not None and spec.dns == "10.9.0.1"


def test_egress_spec_from_cfg_dns_optional():
    cfg = {"netns_isolation": {"egress": {
        "wg_iface": "wg-bd0", "wg_conf": "/x.conf", "address": "10.9.0.2/32"}}}
    spec = ni.egress_spec_from_cfg(cfg)
    assert spec is not None and spec.dns is None


# ── teardown cleans the per-ns resolv dir (only when DNS was used) ─────
def test_teardown_without_egress_is_backward_identical():
    assert ni.teardown_commands("bd_cap_x") == [["ip", "netns", "del", "bd_cap_x"]]


def test_teardown_with_dns_removes_resolv_dir():
    cmds = ni.teardown_commands("bd_cap_x", egress=_spec(dns="10.9.0.1"))
    assert ["ip", "netns", "del", "bd_cap_x"] in cmds
    assert ["rm", "-rf", "/etc/netns/bd_cap_x"] in cmds


def test_teardown_egress_without_dns_leaves_no_resolv_cleanup():
    cmds = ni.teardown_commands("bd_cap_x", egress=_spec())   # no dns
    assert not any("resolv" in " ".join(c) or "/etc/netns" in " ".join(c)
                   for c in cmds)


# ── capture_netns writes + cleans up the resolver end-to-end ──────────
def test_capture_netns_writes_and_cleans_resolv():
    cfg = {"netns_isolation": {"enabled": True, "egress": {
        "wg_iface": "wg-bd0", "wg_conf": "/etc/wireguard/bd.conf",
        "address": "10.9.0.2/32", "dns": "10.9.0.1"}}}
    r = _FakeRunner()
    with ni.capture_netns(cfg, "dl", "https://ex/v", runner=r) as ns:
        assert ns is not None
    # both the write (during create) and the cleanup (during destroy) ran
    assert any("resolv.conf" in " ".join(c) for c in r.calls)
    assert any(c[:2] == ["rm", "-rf"] and "/etc/netns/" in c[2] for c in r.calls)
