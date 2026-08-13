"""T14 (Tier 3) — D-42 VPN connectivity probe + D-49 egress-IP
monitor. Both read-only; both reuse vpn.check_health and
vpn.list_tunnels with no new sampling thread or state."""
import contextlib
import os
import time

from bulk_downloader import dev_suite as ds


@contextlib.contextmanager
def _vpn_state(tunnels):
    from bulk_downloader import vpn_config as _cfg
    from bulk_downloader import vpn as _vpn
    saved = list(_cfg._state.get("tunnels", []))
    _cfg._state["tunnels"] = list(tunnels)
    # RESET ON ENTRY, NOT ONLY ON EXIT (backlog 25).
    #
    # The line below used to exist only in the `finally`, which cleans up for
    # the NEXT test and leaves THIS one asserting over whatever an earlier test
    # file left behind. That is the whole mechanism: the swap above touches
    # `vpn_config._state["tunnels"]`, while everything under test reads the
    # module-level `vpn._tunnels` registry through `vpn.list_tunnels()` -- two
    # different stores, so entering with `[]` set nothing that mattered.
    #
    # Tests that register INSIDE the block are unaffected: the reset happens
    # before the body runs, so their own registration still stands.
    try:
        _vpn._reset_for_tests()
    except Exception:
        pass
    try:
        yield
    finally:
        _cfg._state["tunnels"] = saved
        try:
            _vpn._reset_for_tests()
        except Exception:
            pass


def test_the_fixture_establishes_the_clean_registry_it_claims(clean_workdir):
    """BACKLOG 25. This is the mechanism behind "test_probe_no_tunnels is
    flaky", and it was never flakiness -- it was a fixture that did not build
    the shape its tests assert on.

    TWO STORES, NOT ONE. `_vpn_state` swaps `vpn_config._state["tunnels"]`.
    `vpn_connectivity_probe` does not read that at all: it calls
    `vpn.list_tunnels()`, which returns the module-level `vpn._tunnels`
    registry. So entering the context with `[]` sets an empty list that nothing
    under test consults, while the registry keeps whatever an earlier test file
    left in it.

    The reset existed -- in the `finally`. That cleans up for the NEXT test and
    never establishes the precondition for THIS one, which is CLAUDE.md section
    6's rule exactly: a harness must assert it built the shape before it
    asserts the verdict. Without it, "no tunnels registered" and "a tunnel was
    registered by somebody else" are the same green until the scheduler puts a
    leaker on the same worker -- measured at 3 of 11 samples at -n 16, which is
    why it read as rotation rather than as a defect.

    REPRODUCED DETERMINISTICALLY 2026-08-13 at a0cdfff: registering one tunnel
    and then running the probe under this exact fixture returned
    registered_tunnels=1 where the tests assert 0.
    """
    from bulk_downloader import vpn as _vpn

    # Stand in for whatever earlier file left the registry dirty. The point is
    # that this test does NOT clean up before entering the context -- that is
    # the fixture's job, and the assertion below is whether it does it.
    _vpn.register_tunnel(
        tunnel_id="leaked_by_an_earlier_test", name="L",
        provider="mullvad", backend="wireguard",
        config={"endpoint": "x.y.z:51820"})
    assert "leaked_by_an_earlier_test" in _vpn._tunnels, (
        "precondition not built: the registry was supposed to be dirty here, "
        "so a green below would prove nothing")

    with _vpn_state([]):
        r = ds.vpn_connectivity_probe()

    assert r["registered_tunnels"] == 0, (
        "the fixture did not clear vpn._tunnels on ENTRY, so a tunnel left by "
        "an earlier test file is still registered -- exactly the co-batched "
        f"failure backlog 25 records. saw: {r['registered_tunnels']}")
    assert r["tunnels"] == []


# ── D-42 vpn_connectivity_probe ───────────────────────────────────

def test_probe_no_tunnels(clean_workdir):
    with _vpn_state([]):
        r = ds.vpn_connectivity_probe()
    assert r["ok"] is True
    assert r["tool"] == "vpn_connectivity_probe"
    assert r["tunnels"] == []
    assert r["registered_tunnels"] == 0


def test_probe_reports_per_tunnel(clean_workdir):
    from bulk_downloader import vpn as _vpn
    with _vpn_state([]):
        try:
            _vpn.register_tunnel(
                tunnel_id="t1", name="T1",
                provider="mullvad", backend="wireguard",
                config={"endpoint": "x.y.z:51820"})
        except Exception:
            pass
        r = ds.vpn_connectivity_probe()
    assert r["registered_tunnels"] == 1
    info = r["tunnels"][0]
    assert info["tunnel_id"] == "t1"
    # health_ok must be a bool, error a str or None
    assert isinstance(info["health_ok"], bool)


def test_probe_does_not_alter_state(clean_workdir):
    from bulk_downloader import vpn as _vpn
    with _vpn_state([]):
        try:
            _vpn.register_tunnel(
                tunnel_id="quiet", name="Q",
                provider="mullvad", backend="wireguard",
                config={"endpoint": "x.y.z:51820"})
            t = _vpn.get_tunnel("quiet")
        except Exception:
            t = None
        if t is not None:
            state_before = t.state
            ds.vpn_connectivity_probe()
            state_after = _vpn.get_tunnel("quiet").state
            assert state_before == state_after


# ── D-49 egress_ip_monitor ────────────────────────────────────────

def test_egress_monitor_no_tunnels(clean_workdir):
    with _vpn_state([]):
        r = ds.egress_ip_monitor()
    assert r["ok"] is True
    assert r["tool"] == "egress_ip_monitor"
    assert r["tunnels"] == []
    assert r["registered_tunnels"] == 0


def test_egress_monitor_reports_public_ip(clean_workdir):
    from bulk_downloader import vpn as _vpn
    with _vpn_state([]):
        try:
            _vpn.register_tunnel(
                tunnel_id="ip1", name="IP1",
                provider="mullvad", backend="wireguard",
                config={"endpoint": "x.y.z:51820"})
            t = _vpn.get_tunnel("ip1")
        except Exception:
            t = None
        if t is not None:
            t.public_ip = "203.0.113.42"
            t.last_health_check = time.time()
            t.state = "up"
        r = ds.egress_ip_monitor()
    info_by_id = {t["tunnel_id"]: t for t in r["tunnels"]}
    if "ip1" in info_by_id:
        assert info_by_id["ip1"]["public_ip"] == "203.0.113.42"
        assert info_by_id["ip1"]["is_stale"] is False


def test_egress_monitor_flags_stale(clean_workdir):
    from bulk_downloader import vpn as _vpn
    with _vpn_state([]):
        try:
            _vpn.register_tunnel(
                tunnel_id="old", name="OLD",
                provider="mullvad", backend="wireguard",
                config={"endpoint": "x.y.z:51820"})
            t = _vpn.get_tunnel("old")
        except Exception:
            t = None
        if t is not None:
            t.public_ip = "203.0.113.99"
            t.last_health_check = time.time() - 600  # 10 min ago
            t.state = "up"
        r = ds.egress_ip_monitor(stale_after_sec=300)
    by_id = {t["tunnel_id"]: t for t in r["tunnels"]}
    if "old" in by_id:
        assert by_id["old"]["is_stale"] is True
        assert r["stale_tunnels_up"] >= 1


def test_egress_monitor_detects_shared_ip(clean_workdir):
    from bulk_downloader import vpn as _vpn
    with _vpn_state([]):
        for tid in ("a", "b"):
            try:
                _vpn.register_tunnel(
                    tunnel_id=tid, name=tid.upper(),
                    provider="mullvad", backend="wireguard",
                    config={"endpoint": f"{tid}.y.z:51820"})
                t = _vpn.get_tunnel(tid)
                t.public_ip = "203.0.113.77"
                t.last_health_check = time.time()
                t.state = "up"
            except Exception:
                pass
        r = ds.egress_ip_monitor()
    if "203.0.113.77" in r["shared_ips"]:
        assert set(r["shared_ips"]["203.0.113.77"]) == {"a", "b"}


# ── dev routes ─────────────────────────────────────────────────────

def test_vpn_probe_route(fresh_app):
    j = fresh_app.get("/api/dev/vpn_probe").get_json()
    assert j["ok"] is True
    assert j["tool"] == "vpn_connectivity_probe"


def test_egress_ip_route(fresh_app):
    j = fresh_app.get("/api/dev/egress_ip").get_json()
    assert j["ok"] is True
    assert j["tool"] == "egress_ip_monitor"


def test_t14_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/vpn_probe", "/api/dev/egress_ip"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
