"""v3.66.686 (F5) — per-capture network-namespace isolation engine [safety].

Defensive isolation, NOT evasion: confine a capture/download to a dedicated
network namespace that has no external egress by default (a fresh netns has
only loopback and no routes), optionally hardened with an nftables
default-drop output policy. This module is the reusable MECHANISM
(deterministic naming + command generation + fail-closed create/destroy +
an injected-runner so unit tests never need root); routing the actual
browser/download launch through it is a separate, CAP_NET_ADMIN-gated
follow-on.

Two test tiers:
  * pure/unit — command generation + create/destroy via a FAKE runner.
  * integration — actually creates + isolates + tears down a REAL netns;
    auto-skips when not root or `ip` is unavailable, so the suite stays
    portable, but it exercises the true path in a root sandbox.
"""
import os
import shutil
import subprocess

import pytest

from bulk_downloader import netns_isolation as ni


# ── pure: naming ────────────────────────────────────────────────────

def test_netns_name_is_deterministic_and_safe():
    a = ni.netns_name("cap", "site-42/weird:chars")
    b = ni.netns_name("cap", "site-42/weird:chars")
    assert a == b                                    # deterministic
    assert a.startswith("bd_cap_")
    assert all(c.isalnum() or c == "_" for c in a)   # netns-safe chars only
    assert len(a) <= 40


def test_netns_name_varies_by_identity():
    assert ni.netns_name("cap", "a") != ni.netns_name("cap", "b")


# ── pure: command generation ────────────────────────────────────────

def test_setup_commands_create_isolate_and_drop_egress():
    cmds = ni.setup_commands("bd_cap_x", drop_egress=True)
    assert ["ip", "netns", "add", "bd_cap_x"] in cmds
    # loopback brought up inside the ns
    assert any(c[:4] == ["ip", "netns", "exec", "bd_cap_x"]
               and "lo" in c and c[-1] == "up" for c in cmds)
    # an nft default-drop output policy is applied inside the ns
    joined = [" ".join(c) for c in cmds]
    assert any("nft" in j and "drop" in j and "bd_cap_x" in j for j in joined)


def test_setup_commands_without_drop_egress_omits_nft():
    cmds = ni.setup_commands("bd_cap_x", drop_egress=False)
    assert ["ip", "netns", "add", "bd_cap_x"] in cmds
    assert not any("nft" in " ".join(c) for c in cmds)


def test_teardown_commands_delete_the_ns():
    assert ni.teardown_commands("bd_cap_x") == [["ip", "netns", "del", "bd_cap_x"]]


def test_netns_exec_argv_wraps_a_command():
    assert ni.netns_exec_argv("bd_cap_x", ["curl", "https://h"]) == \
        ["ip", "netns", "exec", "bd_cap_x", "curl", "https://h"]


# ── executable via injected runner (no root needed) ─────────────────

class _FakeRunner:
    def __init__(self, fail_on=None):
        self.calls = []
        self._fail_on = fail_on            # substring that triggers rc=1

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc = 1 if (self._fail_on and self._fail_on in " ".join(argv)) else 0
        return subprocess.CompletedProcess(argv, rc, "", "")


def test_create_runs_setup_and_returns_true():
    r = _FakeRunner()
    ok = ni.create("bd_cap_x", drop_egress=True, runner=r)
    assert ok is True
    assert ["ip", "netns", "add", "bd_cap_x"] in r.calls


def test_create_is_fail_closed_and_tears_down_on_error():
    # if any setup step fails, create must return False AND clean up the ns
    r = _FakeRunner(fail_on="nft")
    ok = ni.create("bd_cap_x", drop_egress=True, runner=r)
    assert ok is False
    assert ["ip", "netns", "del", "bd_cap_x"] in r.calls   # cleaned up


def test_destroy_deletes_the_ns():
    r = _FakeRunner()
    ni.destroy("bd_cap_x", runner=r)
    assert ["ip", "netns", "del", "bd_cap_x"] in r.calls


def test_isolated_context_manager_creates_and_destroys():
    r = _FakeRunner()
    with ni.isolated("cap", "site1", runner=r) as ns:
        assert ns.startswith("bd_cap_")
    assert any(c[:3] == ["ip", "netns", "add"] for c in r.calls)
    assert any(c[:3] == ["ip", "netns", "del"] for c in r.calls)


# ── config plumbing (opt-in) ────────────────────────────────────────

def test_site_wants_isolation_opt_in():
    assert ni.site_wants_isolation({"netns_isolation": True}) is True
    assert ni.site_wants_isolation({"netns_isolation": {"enabled": True}}) is True
    assert ni.site_wants_isolation({}) is False
    assert ni.site_wants_isolation({"netns_isolation": False}) is False


# ── REAL integration (root sandbox only; auto-skips otherwise) ──────

def _can_netns():
    return os.geteuid() == 0 and shutil.which("ip") is not None


@pytest.mark.skipif(not _can_netns(), reason="needs root + iproute2 for real netns")
def test_real_netns_lifecycle_and_isolation():
    ns = ni.netns_name("cap", "integ-test")
    ni.destroy(ns)  # ensure clean slate
    try:
        assert ni.create(ns, drop_egress=True) is True
        # the ns exists
        listed = subprocess.run(["ip", "netns", "list"], capture_output=True,
                                text=True).stdout
        assert ns in listed
        # isolated: no default route inside the ns (egress blocked)
        routes = ni.run_in_netns(ns, ["ip", "route", "show"]).stdout
        assert "default" not in routes
        # a command runs inside the ns
        cp = ni.run_in_netns(ns, ["true"])
        assert cp.returncode == 0
    finally:
        ni.destroy(ns)
        listed = subprocess.run(["ip", "netns", "list"], capture_output=True,
                                text=True).stdout
        assert ns not in listed
