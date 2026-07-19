"""PHC-2 / L17 / P11-A — SSRF transport guard regression pin (option A).

The SSRF guard in ``provider_resolve`` (pre-fetch host classification +
redirect-target re-check + connect-time ``_SSRFGuardedTransport`` that pins to a
vetted IP) was BUILT at v3.66.25; this suite does NOT add the guard — it PINS it,
so any future edit that weakens ``_classify_ip`` / ``_is_safe_public_host`` /
``_make_default_http_get`` turns a test red instead of silently re-opening SSRF.

Robustness comes from a committed test, not a per-session manual check
(RELEASE_DISCIPLINE_TIERS). Because nothing is being fixed these pass on pristine;
the guard was validated the equivalent way (weakening ``_classify_ip`` to return
(True, "") was confirmed to turn these red, then reverted).

Sandbox: network is OFF, so every assertion is offline. Literal-IP classification
needs no DNS; the cloud-metadata pre-fetch case fires the guard BEFORE any
connect, so it is provable without the network. ``localhost`` resolution is
guarded (skipped if the sandbox cannot resolve it). Zero-arg test fns; root from
__file__.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bulk_downloader import provider_resolve as pr  # noqa: E402

# Private / loopback / link-local / reserved / multicast / unspecified literals
# — must ALL be refused. 169.254.169.254 is the canonical cloud-metadata SSRF
# target; ::1 / fc00:: cover IPv6 loopback + ULA.
_BLOCKED = [
    "127.0.0.1", "127.0.0.53",          # loopback
    "10.0.0.1", "172.16.5.5", "192.168.1.1",   # RFC1918 private
    "169.254.169.254", "169.254.0.1",   # link-local (metadata endpoint)
    "0.0.0.0",                          # unspecified
    "224.0.0.1", "239.255.255.250",     # multicast
    "::1", "fc00::1", "fe80::1",        # IPv6 loopback / ULA / link-local
]

# Public unicast literals — must be allowed by classification (no DNS needed).
_ALLOWED = ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700:4700::1111"]


def test_classify_ip_refuses_non_public_literals():
    """Every private/loopback/link-local/reserved/multicast/unspecified literal
    is refused with a reason; public literals pass."""
    import ipaddress
    for ip in _BLOCKED:
        ok, reason = pr._classify_ip(ipaddress.ip_address(ip), ip)
        assert ok is False, "SSRF guard let through %s" % ip
        assert reason, "refusal of %s must carry a reason" % ip
    for ip in _ALLOWED:
        ok, reason = pr._classify_ip(ipaddress.ip_address(ip), ip)
        assert ok is True, "SSRF guard wrongly refused public %s (%s)" % (ip, reason)


def test_is_safe_public_host_literal_paths():
    """The host-level predicate agrees with _classify_ip for literal IPs and
    refuses the empty host."""
    for ip in _BLOCKED:
        ok, _ = pr._is_safe_public_host(ip)
        assert ok is False, "host predicate let through %s" % ip
    for ip in _ALLOWED:
        ok, reason = pr._is_safe_public_host(ip)
        assert ok is True, "host predicate refused public %s (%s)" % (ip, reason)
    ok, reason = pr._is_safe_public_host("")
    assert ok is False and reason, "empty host must be refused"


def test_prefetch_guard_blocks_cloud_metadata_without_network():
    """The default fetcher refuses the cloud-metadata endpoint at the PRE-FETCH
    stage — proven offline because SSRFBlocked is raised before any connect."""
    get = pr._make_default_http_get()
    raised = False
    try:
        get("http://169.254.169.254/latest/meta-data/")
    except pr.SSRFBlocked:
        raised = True
    except Exception as e:  # any non-SSRF error means the guard did NOT fire first
        raise AssertionError(
            "expected SSRFBlocked at pre-fetch, got %s: %s" % (type(e).__name__, e)
        )
    assert raised, "fetcher must refuse the cloud-metadata IP before connecting"


def test_prefetch_guard_blocks_loopback_and_private():
    """Loopback and RFC1918 URLs are refused at pre-fetch (offline)."""
    get = pr._make_default_http_get()
    for url in ("http://127.0.0.1/", "http://10.0.0.1/admin",
                "http://192.168.0.1:8080/"):
        try:
            get(url)
            raise AssertionError("fetcher did not refuse %s" % url)
        except pr.SSRFBlocked:
            pass


def test_localhost_hostname_is_guarded():
    """A hostname that RESOLVES to loopback is refused (DNS path), not just IP
    literals. Skipped if the sandbox cannot resolve 'localhost'."""
    import socket
    try:
        socket.getaddrinfo("localhost", None, type=socket.SOCK_STREAM)
    except Exception:
        return  # no resolver for localhost in this sandbox — skip
    ok, reason = pr._is_safe_public_host("localhost")
    assert ok is False, "localhost (→loopback) must be refused; got ok=%r" % ok
