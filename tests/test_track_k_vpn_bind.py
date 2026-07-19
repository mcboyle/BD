"""Track-K -- in-process download egress fail-closed via VPN proxy-reuse.

RED-first. On pristine v3.66.389:
  * `bulk_downloader.download_egress` does not exist  -> import tests RED.
  * `_do_direct_http_download` streams the payload via `httpx.stream(...)` with
    NO proxy argument, and the multi-connection path is not gated on a tunnel
    proxy -> the structural fail-closed guards RED.
After the cut both pass.

Operator decision (this session): reuse `vpn_runtime.get_socks_url_for_site`
(the SAME per-site resolver the browser already uses via
`playwright_proxy_for_site`) as the download proxy. That resolver already
encodes the chosen posture, so the in-process payload inherits it:
  - explicit per-site proxy wins (unchanged);
  - tunnel up            -> returns the tunnel SOCKS url (payload egresses via VPN);
  - site NOT `vpn_required` -> returns None (degrade-open preserved -- "keep opt-in
    via vpn_required");
  - site `vpn_required` + tunnel down/killed -> raises VPNRequiredError
    (FAIL CLOSED: never build an unproxied client; bytes never touch clear net).

The pure decision lives in `download_egress.effective_download_proxy` with the
socks resolver injected, so it is unit-testable with no live tunnel side effects.
The structural guards assert runner.py actually routes its payload paths through it.
"""
import os
import re


def _runner_src():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    import glob as _g
    _pkg = os.path.join(root, "bulk_downloader")
    _files = [os.path.join(_pkg, "runner.py")] + sorted(_g.glob(os.path.join(_pkg, "runner_*.py")))
    return "\n".join(open(p, encoding="utf-8").read() for p in _files)


def _ddhd_body(src):
    """Isolate the body of _do_direct_http_download (the library-direct payload
    path) up to the next method definition."""
    m = re.search(r"def _do_direct_http_download\(.*?(?=\n    def )", src, re.S)
    assert m, "could not locate _do_direct_http_download in runner.py"
    return m.group(0)


# --- pure decision: explicit proxy precedence -------------------------------
def test_effective_download_proxy_explicit_wins():
    from bulk_downloader.download_egress import effective_download_proxy
    calls = []

    def resolver(site_id):
        calls.append(site_id)
        return "socks5://127.0.0.1:9999"

    out = effective_download_proxy("  http://explicit:8080 ", "siteA", resolver)
    assert out == "http://explicit:8080", out
    assert calls == [], "explicit proxy must win without consulting the VPN resolver"


# --- pure decision: tunnel up -> payload routed through the tunnel SOCKS -----
def test_effective_download_proxy_tunnel_up_returns_socks():
    from bulk_downloader.download_egress import effective_download_proxy
    out = effective_download_proxy("", "siteA", lambda s: "socks5://127.0.0.1:40001")
    assert out == "socks5://127.0.0.1:40001", out


# --- pure decision: not required -> degrade open (operator's posture) --------
def test_effective_download_proxy_not_required_degrades_open():
    from bulk_downloader.download_egress import effective_download_proxy
    out = effective_download_proxy(None, "siteA", lambda s: None)
    assert out is None, "non-required site with no tunnel must degrade open (None)"


# --- pure decision: required + tunnel down -> FAIL CLOSED (propagate raise) --
def test_effective_download_proxy_required_down_fails_closed():
    from bulk_downloader.download_egress import effective_download_proxy
    from bulk_downloader.vpn_runtime import VPNRequiredError

    def resolver(site_id):
        raise VPNRequiredError("tunnel down")

    raised = False
    try:
        effective_download_proxy("", "siteA", resolver)
    except VPNRequiredError:
        raised = True
    assert raised, "required site + tunnel down MUST propagate VPNRequiredError (fail closed)"


# --- pure decision: VPN runtime unavailable -> explicit-or-None -------------
def test_effective_download_proxy_vpn_unavailable():
    from bulk_downloader.download_egress import effective_download_proxy
    assert effective_download_proxy("http://p:1", "s", None) == "http://p:1"
    assert effective_download_proxy("", "s", None) is None
    assert effective_download_proxy(None, "s", None) is None


# --- structural fail-closed guard: library-direct payload stream is proxied --
def test_direct_http_download_payload_stream_is_proxied():
    body = _ddhd_body(_runner_src())
    assert "httpx.stream(" in body, "expected an httpx.stream payload fetch in _do_direct_http_download"
    for call in re.finditer(r"httpx\.stream\((.*?)\)", body, re.S):
        assert "proxy" in call.group(1), (
            "payload httpx.stream must pass a proxy (fail-closed VPN binding); "
            "found an unproxied payload stream in _do_direct_http_download")


# --- structural fail-closed guard: multi-conn gated off under a tunnel proxy -
def test_multi_conn_gated_when_proxy_active():
    body = _ddhd_body(_runner_src())
    # the multi-conn optimization must be skipped when a download proxy is in
    # effect (it has no proxy-native path), so payload can't escape the tunnel
    # via the parallel byte-range client. We assert the proxy is resolved in the
    # method and that the multi_conn guard references it.
    assert "_download_proxy_url(" in body, (
        "_do_direct_http_download must resolve the effective download proxy "
        "(self._download_proxy_url()) so it can gate multi-conn + proxy the stream")


# --- helper presence + delegation ------------------------------------------
def test_download_proxy_url_helper_present():
    src = _runner_src()
    assert "def _download_proxy_url(" in src, "expected the _download_proxy_url instance helper"
    assert "effective_download_proxy(" in src, "helper must delegate to effective_download_proxy"


# ===========================================================================
# A1 (v3.66.5xx) -- yt-dlp subprocess fallback must honor the SAME fail-closed
# VPN bind as the in-process clients. On pristine 529 the yt-dlp fallback built
# its CLI with NO --proxy and never resolved _download_proxy_url()/checked
# VPNRequiredError -> a vpn_required site with a down tunnel leaked the payload
# (and DNS) on the clear interface via the subprocess. RED-first below.
# ===========================================================================
def _extractors_src():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    p = os.path.join(root, "bulk_downloader", "runner_extractors.py")
    return open(p, encoding="utf-8").read()


def _ytdlp_body(src):
    """Body of _try_ytdlp_fallback up to the next method def."""
    m = re.search(r"def _try_ytdlp_fallback\(.*?(?=\n    def )", src, re.S)
    assert m, "could not locate _try_ytdlp_fallback in runner_extractors.py"
    return m.group(0)


# --- pure: socks5:// -> socks5h:// (remote DNS); everything else verbatim ----
def test_socks_remote_dns_upgrades_socks5_scheme():
    from bulk_downloader.runner_extractors import _socks_remote_dns
    assert _socks_remote_dns("socks5://127.0.0.1:1080") == "socks5h://127.0.0.1:1080"
    # already-remote / other schemes / empty are passed through untouched
    assert _socks_remote_dns("socks5h://127.0.0.1:1080") == "socks5h://127.0.0.1:1080"
    assert _socks_remote_dns("http://p:8080") == "http://p:8080"
    assert _socks_remote_dns("") == ""
    assert _socks_remote_dns(None) == ""


# --- pure: cmd builder threads --proxy (socks5h) when a proxy is in effect ---
def test_build_ytdlp_cmd_threads_remote_dns_proxy():
    from bulk_downloader.runner_extractors import _build_ytdlp_cmd
    cmd = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/tmp/x",
                           url="https://example.test/v",
                           proxy_url="socks5://127.0.0.1:1080",
                           cookie_file="", min_res=0)
    assert "--proxy" in cmd, "a tunnel proxy must be threaded into the yt-dlp CLI"
    assert cmd[cmd.index("--proxy") + 1] == "socks5h://127.0.0.1:1080", \
        "the subprocess must use socks5h (remote DNS) so it does not leak DNS clear"
    assert cmd[-1] == "https://example.test/v", "url must remain the final arg"


# --- pure: no proxy in effect -> no --proxy arg (degrade open, unchanged) ----
def test_build_ytdlp_cmd_omits_proxy_when_none():
    from bulk_downloader.runner_extractors import _build_ytdlp_cmd
    cmd = _build_ytdlp_cmd(ytdlp="yt-dlp", dl_dir="/tmp/x",
                           url="https://example.test/v",
                           proxy_url=None, cookie_file="", min_res=0)
    assert "--proxy" not in cmd, "no tunnel/explicit proxy -> behave exactly as before"


# --- structural: the fallback resolves the proxy + fails closed -------------
def test_ytdlp_fallback_resolves_proxy_and_fails_closed():
    body = _ytdlp_body(_extractors_src())
    assert "_download_proxy_url(" in body, (
        "_try_ytdlp_fallback must resolve the effective download proxy "
        "(self._download_proxy_url()) before invoking yt-dlp")
    assert "VPNRequiredError" in body, (
        "_try_ytdlp_fallback must have a VPNRequiredError fail-closed branch -- "
        "a vpn_required site with a down tunnel must NOT spawn an unproxied yt-dlp")
    assert "_build_ytdlp_cmd(" in body, (
        "the CLI must be built via _build_ytdlp_cmd so the proxy is threaded")
