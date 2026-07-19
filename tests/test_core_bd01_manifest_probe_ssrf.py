"""F-CBD01-01 (CORE_BD-01, medium): the dev manifest-probe fetch helper must
validate that a request-supplied URL resolves to a PUBLIC host before it does
the outbound GET, so it cannot be turned into an SSRF read primitive against
internal targets (cloud metadata 169.254.169.254, loopback, RFC1918/6598, ULA).

``dev_suite.capture_diag._fetch_manifest_text`` previously did ONLY a scheme
check (http/https) with no host validation, then returned the response body to
the caller (witness core_bd01_witnesses.py::F-CBD01-01). The fix validates the
resolved host via the canonical ``_is_safe_public_host`` and refuses non-public
targets BEFORE ``urlopen`` -- using the same ``(ok, msg)`` return convention the
helper already had, so the route's existing 200+{ok:false} contract is
preserved (no route change).

These tests monkeypatch ``urllib.request.urlopen`` with a call-recording spy so
they are deterministic and never touch the network; literal IPs are used so no
DNS is required either.
"""
import urllib.request as _u

from bulk_downloader.dev_suite import capture_diag


# Literal IPs -> no DNS. Each must be refused BEFORE the fetch is attempted.
_INTERNAL_URLS = (
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata / IAM creds
    "http://127.0.0.1:6379/",                     # loopback (local Redis)
    "http://10.0.0.5:8080/internal",              # RFC1918 LAN
    "http://[::1]/x",                             # IPv6 loopback
)


def test_fetch_manifest_text_refuses_internal_host_before_fetch():
    """The SSRF core: for a non-public host, urlopen must NOT be reached and the
    helper must return (False, <reason mentioning host>). A public literal IP
    must still reach the fetch layer (proves we did not over-block)."""
    orig = _u.urlopen
    calls = {"n": 0}

    def _spy(*a, **k):
        calls["n"] += 1
        raise RuntimeError("sentinel: urlopen reached")

    _u.urlopen = _spy
    try:
        for url in _INTERNAL_URLS:
            calls["n"] = 0
            ok, msg = capture_diag._fetch_manifest_text(url)
            assert ok is False, f"internal host must be refused: {url}"
            assert calls["n"] == 0, (
                f"SSRF: urlopen must NOT be reached for internal host {url}; "
                f"it was called {calls['n']}x"
            )
            assert "host" in msg.lower(), (
                f"refusal reason should cite the host, got: {msg!r}"
            )
        # A public host must still pass the guard through to the fetch layer.
        calls["n"] = 0
        ok, msg = capture_diag._fetch_manifest_text("http://1.1.1.1/x.m3u8")
        assert calls["n"] == 1, (
            "a public host must still reach the fetch layer (no over-block); "
            f"urlopen was called {calls['n']}x"
        )
    finally:
        _u.urlopen = orig


def test_manifest_probe_route_contract_refuses_internal_host():
    """Route-facing contract: a blocked internal host yields the same
    {ok: False, error: ...} shape the route already returns (jsonify -> HTTP
    200), with the error citing the host -- so the fix does not change the
    endpoint's error convention (F-APP04-01 stays independently deferred)."""
    orig = _u.urlopen

    def _spy(*a, **k):
        raise RuntimeError("sentinel: urlopen reached")

    _u.urlopen = _spy
    try:
        res = capture_diag.manifest_probe(
            url="http://169.254.169.254/latest/meta-data/"
        )
        assert res.get("ok") is False, "a blocked host must yield ok=False"
        assert "host" in (res.get("error", "")).lower(), (
            "blocked-host error should cite the host (preserving the "
            f"200+ok:false convention), got: {res.get('error')!r}"
        )
    finally:
        _u.urlopen = orig
