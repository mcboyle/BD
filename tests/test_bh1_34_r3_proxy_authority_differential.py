"""BH1-34 r3 (SEC+CORR REFUTE F1b) -- the proxy judge judges the host the transport connects to.

``outbound_guard.judge_proxy_url`` read the host with ``urllib.parse.urlsplit`` (host after the
last '@'); yt-dlp / gallery-dl / requests hand the same string to urllib3 ``parse_url``, whose
authority ends at '\\'.  ``http://169.254.169.254:80\\@127.0.0.1:8118`` was judged as loopback
(admitted) and connected to cloud metadata.  The judge now fails closed: an authority character
outside RFC 3986 is refused, and urllib3's host must equal urlsplit's host.

RED on tree c8417c26 (BH1-34 r2): every backslash form is admitted and mounted.  Distinctive
diagnostic: ``BH1-34-R3-PROXY-PARSER-DIFFERENTIAL``.

Hermetic: every judged host is an IP literal (no DNS); the subprocess runner and binary resolver
are injected (no yt-dlp spawn, no egress).
"""
import json

import pytest
import urllib3

from bulk_downloader import outbound_guard as og

BD_GATE_SCOPE = "module"

DIAG = "BH1-34-R3-PROXY-PARSER-DIFFERENTIAL"
CRED = "s3cr3tR3pw"
LAN_PROXY = "http://10.0.0.5:3128"

# (judged-by-urlsplit, connected-by-urllib3): each admitted by the r2 judge.
DIFFERENTIAL = {
    "http-v4-metadata": "http://169.254.169.254:80\\@127.0.0.1:8118",
    "https-v4-metadata": "https://169.254.169.254:443\\@127.0.0.1:8118",
    "http-v4-metadata-lan-decoy": "http://169.254.169.254:80\\@10.0.0.5:3128",
    "http-v6-metadata": "http://[fd00:ec2::254]:80\\@[::1]:8118",
    "https-v6-metadata": "https://[fd00:ec2::254]:443\\@[fd12::5]:3128",
    "http-v6-link-local": "http://[fe80::1]:80\\@[::1]:8118",
    "loopback-differential": "http://127.0.0.1:1111\\@127.0.0.2:2222",
    "credentials": f"http://op:{CRED}@169.254.169.254:80\\@127.0.0.1:8118",
    "socks5h-v4-metadata": "socks5h://169.254.169.254:1080\\@127.0.0.1:9050",
    "tab-in-authority": "http://127.0.0.1:1\t@10.0.0.1:2",
}
META = DIFFERENTIAL["http-v4-metadata"]


def _refused(fn, *a, **k):
    try:
        got = fn(*a, **k)
    except og.OutboundRefused as exc:
        assert exc.reason == "proxy-refused", f"{DIAG}: wrong reason {exc.reason!r}"
        assert CRED not in str(exc) and CRED not in str(exc.url), f"{DIAG}: userinfo leak {exc}"
        return exc
    pytest.fail(f"{DIAG}: admitted -> {got!r}")


@pytest.mark.parametrize("case", sorted(DIFFERENTIAL))
def test_judge_refuses_a_parser_differential_authority(case):
    _refused(og.judge_proxy_url, DIFFERENTIAL[case])


def test_the_differential_is_real_urllib3_connects_elsewhere():
    # Positive control for the cases above: the consumer's parser does disagree with urlsplit.
    assert urllib3.util.parse_url(META).host == "169.254.169.254"
    assert urllib3.util.parse_url(DIFFERENTIAL["loopback-differential"]).host == "127.0.0.1"


def test_judge_refuses_when_the_transport_parser_disagrees(monkeypatch):
    """Cross-check independent of the character check: urllib3's host is the arbiter."""
    real = urllib3.util.parse_url
    monkeypatch.setattr(urllib3.util, "parse_url",
                        lambda text: real(text)._replace(host="169.254.169.254"))
    exc = _refused(og.judge_proxy_url, LAN_PROXY)
    assert "transport" in str(exc), exc


def test_judge_refuses_when_the_transport_parser_cannot_parse(monkeypatch):
    def boom(text):
        raise urllib3.exceptions.LocationParseError(text)
    monkeypatch.setattr(urllib3.util, "parse_url", boom)
    _refused(og.judge_proxy_url, LAN_PROXY)


# -- positive controls: case / brackets / encoded userinfo still admitted --------------------
@pytest.mark.parametrize("url", [
    LAN_PROXY,
    "http://127.0.0.2:2222",
    f"http://OP:{CRED}%40x@192.168.1.9:8080",
    "socks5://[FD12:3456::1]:1080",
    "socks5h://[::1]:1080",
    "https://[fd12:3456::1]:8443/",
    "http://100.64.0.1:3128",
])
def test_judge_still_admits_operator_proxies(url):
    assert og.judge_proxy_url(url) == url, f"BH1-34-R3-PROXY-ADMIT-BROKEN: {url}"


# -- every mount site ----------------------------------------------------------------------
def _shim(cfg):
    from bulk_downloader import ytdlp_extractor as y
    seen = []

    def run(cmd, *a, **k):
        seen.append(list(cmd))
        return (0, json.dumps({"url": "https://example.com/v.mp4", "protocol": "https"}), "")

    return y.make_ytdlp_extractor(dict(cfg, ytdlp_extractor=True),
                                  run=run, resolve=lambda: ["yt-dlp"]), seen


@pytest.mark.parametrize("key", ["proxy_url", "vpn_socks_url"])
@pytest.mark.parametrize("case", ["http-v4-metadata", "https-v6-metadata", "loopback-differential"])
def test_ytdlp_shim_never_spawns_on_a_differential_proxy(key, case):
    ex, seen = _shim({key: DIFFERENTIAL[case]})
    _refused(ex, "https://example.com/watch")
    assert seen == [], f"{DIAG}: refused yet spawned {seen!r}"


def test_ytdlp_shim_control_a_lan_proxy_spawns_with_it():
    ex, seen = _shim({"proxy_url": LAN_PROXY})
    ex("https://example.com/watch")
    assert len(seen) == 1 and seen[0][seen[0].index("--proxy") + 1] == LAN_PROXY, seen


def test_download_egress_mounts_refuse_a_differential_proxy():
    from bulk_downloader import download_egress as de
    with pytest.raises(de.EgressCarrierError, match="proxy-refused"):
        de.prepare_http_proxy(META)
    _refused(de.effective_download_proxy, META, "r3site", None)
    assert de.effective_download_proxy(LAN_PROXY, "r3site", None) == LAN_PROXY


def test_httpx_mounts_refuse_a_differential_proxy():
    from bulk_downloader import ssrf_transport
    from bulk_downloader.egress_transport import HttpxEgressTransport
    _refused(ssrf_transport.guarded_transport, ssrf_transport.PINNED, proxy=META)
    with pytest.raises(og.OutboundRefused):
        HttpxEgressTransport(proxy_url=META)
    assert ssrf_transport.guarded_transport(ssrf_transport.PINNED, proxy=LAN_PROXY) is not None


def test_login_egress_refuses_a_differential_proxy():
    from bulk_downloader.login_impl.submit import _login_egress
    proxy, _req, refusal = _login_egress({"proxy": DIFFERENTIAL["credentials"]}, "r3login")
    assert proxy is None and refusal and refusal.startswith("proxy-refused"), (
        f"{DIAG}: login mounted {proxy!r} / {refusal!r}")
    assert CRED not in refusal


def test_runner_download_proxy_refuses_a_differential_proxy(monkeypatch):
    """rt._download_proxy_url feeds runner_extractors _build_ytdlp_cmd / _build_gallerydl_cmd --proxy."""
    from bulk_downloader import runner_transport, vpn_runtime

    class _Runner(runner_transport.TransportMixin):
        pass

    monkeypatch.setattr(vpn_runtime, "_site_to_tunnel", dict(vpn_runtime._site_to_tunnel))
    monkeypatch.setattr(vpn_runtime, "_site_required", dict(vpn_runtime._site_required))
    monkeypatch.delenv("BD_DISABLE_VPN_RUNTIME", raising=False)
    r = object.__new__(_Runner)
    r.site_id, r.config, r.events = "r3site", {"name": "r3", "proxy": META}, []
    r._update_job = lambda *a, **k: None
    r.log_event = lambda kind, msg="", **k: r.events.append((kind, msg))
    with pytest.raises(vpn_runtime.EgressRefusedError) as ei:
        got = r._download_proxy_url()
        pytest.fail(f"{DIAG}: _download_proxy_url returned {got!r}")
    assert ei.value.code == "proxy-refused", ei.value.code
    r.config["proxy"] = LAN_PROXY
    assert r._download_proxy_url() == LAN_PROXY
