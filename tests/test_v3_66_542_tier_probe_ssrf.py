"""RED-first guard for v3.66.542: tier_probe SSRF host-guard (F-SWEEP-N2).

probe_higher_tiers builds tier-substituted candidate URLs (same host as the input
URL) and HEAD/GET-probes them through httpx.Client(follow_redirects=True) with NO
host validation. A download of an internal URL (or a public candidate that 30x's
to internal) would drive the server to probe internal space.

The fix installs an httpx 'request' event hook that revalidates the host of every
outgoing request (initial candidate AND redirect targets) via the canonical
provider_resolve_impl._common._is_safe_public_host and raises SSRFBlocked on a
non-public host -- which the probe loop already catches and records as an error,
skipping the candidate.

RED on the pre-542 tree: tier_probe defines no _ssrf_guard_hook and wires no
event_hooks. GREEN once the hook exists, rejects non-public hosts, allows public,
and is wired into the Client.

Runner convention: zero-arg fns; no global mutation.
"""
import io
import os
import types

import bulk_downloader.tier_probe as tp


def _fake_req(host):
    return types.SimpleNamespace(url=types.SimpleNamespace(host=host))


def test_tier_probe_ssrf_hook_rejects_non_public_hosts():
    assert hasattr(tp, "_ssrf_guard_hook"), \
        "tier_probe must define the SSRF request guard hook (F-SWEEP-N2)"
    from bulk_downloader.provider_resolve_impl._common import SSRFBlocked
    for h in ("10.0.0.1", "127.0.0.1", "169.254.1.1", "100.64.0.1", "192.168.1.1"):
        try:
            tp._ssrf_guard_hook(_fake_req(h))
            assert False, f"{h} must be refused by the tier_probe SSRF hook"
        except SSRFBlocked:
            pass


def test_tier_probe_ssrf_hook_allows_public_host():
    # a public unicast host must pass (no raise) -> the guard doesn't over-block.
    tp._ssrf_guard_hook(_fake_req("8.8.8.8"))


def test_probe_higher_tiers_wires_the_ssrf_hook():
    src = io.open(os.path.join(os.path.dirname(tp.__file__), "tier_probe.py"),
                  encoding="utf-8").read()
    assert "event_hooks" in src and "_ssrf_guard_hook" in src, \
        "probe_higher_tiers must wire _ssrf_guard_hook into its httpx.Client event_hooks"
