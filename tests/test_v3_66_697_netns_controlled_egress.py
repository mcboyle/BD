"""697 (v3.66.697, F5) -- controlled egress: route a per-capture netns's traffic
through a WireGuard tunnel (VPN-into-ns) [safety].

The @686 engine creates an EGRESS-LESS namespace (loopback only + an nftables
default-drop output policy) -- so a confined capture cannot reach the network at
all. This cut adds the "unbuilt piece": a controlled-egress posture that moves a
WireGuard interface INTO the namespace and makes it the only route, so a confined
capture egresses ONLY through the VPN tunnel and nowhere else.

Grounded in the canonical WireGuard netns sequence (wireguard.com/netns): create
the wg interface in the init ns, ``wg setconf`` it, MOVE it into the target ns
(its UDP socket stays bound to the physical ns so the tunnel still egresses, but
processes inside see only wg0), assign the tunnel address, bring it up, and set
it as the ns default route. Because wg0 is then the ns's ONLY interface, the
posture is fail-closed BY CONSTRUCTION -- if the tunnel drops, all traffic halts
(no killswitch needed). This is defensive isolation, not evasion.

Pure/unit only -- command generation + create/destroy via a FAKE runner, exactly
like @686. The LIVE exercise (a real wg tunnel moved into a real ns) needs
CAP_NET_ADMIN on the stash service and is deferred, like every runtime-gated F5
piece.

RED-first on pristine v3.66.696: EgressSpec / egress_commands /
egress_spec_from_cfg do not exist, and setup_commands/create/capture_netns take
no ``egress`` -> import/TypeError.
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


# ── pure: egress_commands is the grounded wg-in-netns sequence ─────────
def test_egress_commands_grounded_sequence():
    cmds = ni.egress_commands("bd_cap_x", _spec())
    # create wg in init ns -> setconf -> MOVE into ns -> addr -> up -> route
    assert ["ip", "link", "add", "wg-bd0", "type", "wireguard"] in cmds
    assert ["wg", "setconf", "wg-bd0", "/etc/wireguard/bd.conf"] in cmds
    assert ["ip", "link", "set", "wg-bd0", "netns", "bd_cap_x"] in cmds
    assert ["ip", "-n", "bd_cap_x", "addr", "add", "10.9.0.2/32",
            "dev", "wg-bd0"] in cmds
    assert ["ip", "-n", "bd_cap_x", "link", "set", "wg-bd0", "up"] in cmds
    assert ["ip", "-n", "bd_cap_x", "route", "add", "default",
            "dev", "wg-bd0"] in cmds


def test_egress_commands_ordering_move_before_route():
    """The interface must be moved into the ns and brought up BEFORE the
    in-ns default route is added, or the route add targets a missing dev."""
    cmds = ni.egress_commands("bd_cap_x", _spec())
    joined = ["|".join(c) for c in cmds]
    i_move = joined.index("ip|link|set|wg-bd0|netns|bd_cap_x")
    i_up = joined.index("ip|-n|bd_cap_x|link|set|wg-bd0|up")
    i_route = joined.index("ip|-n|bd_cap_x|route|add|default|dev|wg-bd0")
    assert i_move < i_up < i_route


def test_egress_commands_mtu_optional():
    no_mtu = ni.egress_commands("bd_cap_x", _spec())
    assert not any("mtu" in c for c in no_mtu)
    with_mtu = ni.egress_commands("bd_cap_x", _spec(mtu=1420))
    assert ["ip", "-n", "bd_cap_x", "link", "set", "mtu", "1420",
            "dev", "wg-bd0"] in with_mtu


# ── setup_commands: egress posture supersedes default-drop ────────────
def test_setup_commands_with_egress_has_no_default_drop():
    cmds = ni.setup_commands("bd_cap_x", egress=_spec())
    # ns + loopback still created
    assert ["ip", "netns", "add", "bd_cap_x"] in cmds
    assert ["ip", "netns", "exec", "bd_cap_x", "ip", "link", "set", "lo", "up"] in cmds
    # NO nftables default-drop (wg0-only is the confinement)
    assert not any("nft" in c for c in cmds), "controlled egress must not default-drop"
    # DOES include the egress bring-up
    assert ["ip", "link", "add", "wg-bd0", "type", "wireguard"] in cmds


def test_setup_commands_without_egress_is_backward_identical():
    """No egress -> byte-identical to the @686 default (default-drop)."""
    cmds = ni.setup_commands("bd_cap_x")            # drop_egress default True
    assert ["ip", "netns", "add", "bd_cap_x"] in cmds
    assert any("nft" in c and "drop" in " ".join(c) for c in cmds)
    assert not any("wireguard" in c for c in cmds)


# ── create threads egress + stays fail-closed ─────────────────────────
def test_create_with_egress_runs_all_and_returns_true():
    r = _FakeRunner()
    ok = ni.create("bd_cap_x", egress=_spec(), runner=r)
    assert ok is True
    assert ["ip", "link", "set", "wg-bd0", "netns", "bd_cap_x"] in r.calls


def test_create_with_egress_fail_closed_tears_down():
    r = _FakeRunner(fail_on="route add default")   # tunnel route fails
    ok = ni.create("bd_cap_x", egress=_spec(), runner=r)
    assert ok is False
    assert ["ip", "netns", "del", "bd_cap_x"] in r.calls   # cleaned up


# ── egress_spec_from_cfg: undeclared backend-only cfg ─────────────────
def test_egress_spec_from_cfg_reads_dict_form():
    cfg = {"netns_isolation": {"enabled": True, "egress": {
        "wg_iface": "wg-bd0", "wg_conf": "/etc/wireguard/bd.conf",
        "address": "10.9.0.2/32", "mtu": 1420}}}
    spec = ni.egress_spec_from_cfg(cfg)
    assert spec is not None
    assert spec.wg_iface == "wg-bd0" and spec.address == "10.9.0.2/32"
    assert spec.mtu == 1420


def test_egress_spec_from_cfg_absent_or_incomplete_is_none():
    assert ni.egress_spec_from_cfg({}) is None
    assert ni.egress_spec_from_cfg({"netns_isolation": True}) is None   # no egress
    # incomplete (missing address) -> None, never a half-built spec
    assert ni.egress_spec_from_cfg({"netns_isolation": {"egress": {
        "wg_iface": "wg0", "wg_conf": "/x.conf"}}}) is None


# ── capture_netns wires the egress posture end-to-end ─────────────────
def test_capture_netns_uses_egress_when_configured():
    cfg = {"netns_isolation": {"enabled": True, "egress": {
        "wg_iface": "wg-bd0", "wg_conf": "/etc/wireguard/bd.conf",
        "address": "10.9.0.2/32"}}}
    r = _FakeRunner()
    with ni.capture_netns(cfg, "dl", "https://ex/v", runner=r) as ns:
        assert ns is not None
    # the wg move happened -> controlled egress, not default-drop
    assert any(c[:3] == ["ip", "link", "add"] and "wireguard" in c for c in r.calls)
    assert not any("nft" in c for c in r.calls)


def test_capture_netns_egress_create_fail_is_fail_closed():
    import pytest
    cfg = {"netns_isolation": {"enabled": True, "egress": {
        "wg_iface": "wg-bd0", "wg_conf": "/etc/wireguard/bd.conf",
        "address": "10.9.0.2/32"}}}
    r = _FakeRunner(fail_on="wg setconf")     # tunnel config fails
    with pytest.raises(ni.NetnsRequiredError):
        with ni.capture_netns(cfg, "dl", "https://ex/v", runner=r):
            pass


def test_capture_netns_no_egress_still_isolates_egress_less():
    """Opt-in isolation WITHOUT an egress spec keeps the @686 egress-less
    posture (default-drop) -- unchanged."""
    cfg = {"netns_isolation": {"enabled": True}}
    r = _FakeRunner()
    with ni.capture_netns(cfg, "dl", "https://ex/v", runner=r) as ns:
        assert ns is not None
    assert any("nft" in c for c in r.calls)               # default-drop present
    assert not any("wireguard" in c for c in r.calls)     # no tunnel
